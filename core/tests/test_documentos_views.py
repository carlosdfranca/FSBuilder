"""Testes das views de upload/download/exclusão de documentos da DF (Fase 3)."""
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from df.models import ChecklistItemPeriodo, DocumentoDF, Fundo, LogAcessoDocumento, PeriodoDF
from df.services.checklist_service import criar_checklist_para_periodo
from usuarios.models import Empresa, Membership, Usuario

PDF_BYTES = b"%PDF-1.4\n%mock pdf content for tests\n"


class DocumentosViewsTestCase(TestCase):
    """Fixture base compartilhada: Empresa A (com um segundo tenant, Empresa B,
    para os testes de isolamento) + um fundo/período/checklist em cada uma."""

    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa A Docs", documentos_habilitados=True)
        self.outra_empresa = Empresa.objects.create(nome="Empresa B Docs", documentos_habilitados=True)

        self.master = self._criar_usuario_com_papel("master_a", self.empresa, Membership.Role.MASTER)
        self.member = self._criar_usuario_com_papel("member_a", self.empresa, Membership.Role.MEMBER)
        self.viewer = self._criar_usuario_com_papel("viewer_a", self.empresa, Membership.Role.VIEWER)
        self.master_b = self._criar_usuario_com_papel("master_b", self.outra_empresa, Membership.Role.MASTER)

        self.fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo A", cnpj="33.333.333/0001-33", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa,
            tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)
        self.item = self.periodo.checklist_items.first()

        self.fundo_b = Fundo.objects.create(
            empresa=self.outra_empresa, nome="Fundo B", cnpj="44.444.444/0001-44", tipo_fundo="FIDC",
        )
        self.periodo_b = PeriodoDF.objects.create(
            fundo=self.fundo_b, empresa=self.outra_empresa,
            tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo_b)

        self._docs_to_clean = []

    def tearDown(self):
        # gc.collect() força a liberação de handles de arquivo abertos pela
        # FileResponse do teste de download antes de tentar apagar — no Windows
        # o SO pode manter o arquivo "em uso" por um instante após a resposta
        # ser consumida, até o objeto File ser coletado. Não é um problema em
        # produção (Linux não bloqueia remoção de arquivo aberto).
        import gc
        gc.collect()
        # Documentos criados pela view de upload não passam por _criar_documento;
        # recolhe do banco os que o teste deixou (o rollback só desfaz as linhas).
        self._docs_to_clean.extend(DocumentoDF.objects.exclude(pk__in=[d.pk for d in self._docs_to_clean]))
        for doc in self._docs_to_clean:
            if doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass

    def _criar_usuario_com_papel(self, username, empresa, role):
        usuario = Usuario.objects.create_user(username=username, password="x")
        Membership.objects.create(usuario=usuario, empresa=empresa, role=role, is_active=True)
        return usuario

    def _criar_documento(self, empresa=None, periodo=None, checklist_item=None,
                          tamanho_bytes=None, conteudo=None, enviado_por=None):
        empresa = empresa or self.empresa
        periodo = periodo or self.periodo
        conteudo = conteudo if conteudo is not None else PDF_BYTES
        doc = DocumentoDF.objects.create(
            empresa=empresa, periodo_df=periodo, checklist_item=checklist_item,
            checklist_texto=checklist_item.texto if checklist_item else "",
            arquivo=SimpleUploadedFile("teste.pdf", conteudo, content_type="application/pdf"),
            nome_original="teste.pdf",
            tamanho_bytes=tamanho_bytes if tamanho_bytes is not None else len(conteudo),
            enviado_por=enviado_por,
        )
        self._docs_to_clean.append(doc)
        return doc


class UploadDocumentoTests(DocumentosViewsTestCase):

    def _post_upload(self, arquivo, checklist_item_id=None, usuario=None):
        self.client.force_login(usuario or self.master)
        data = {"arquivo": arquivo}
        if checklist_item_id is not None:
            data["checklist_item_id"] = checklist_item_id
        return self.client.post(
            reverse("upload_documento_periodo", args=[self.periodo.id]),
            data=data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_bloqueado_sem_dpa(self):
        self.empresa.documentos_habilitados = False
        self.empresa.save(update_fields=["documentos_habilitados"])
        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")

        response = self._post_upload(arquivo)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DocumentoDF.objects.filter(periodo_df=self.periodo).exists())

    def test_extensao_invalida_rejeitada(self):
        arquivo = SimpleUploadedFile("virus.exe", b"MZ\x90\x00fake exe", content_type="application/octet-stream")

        response = self._post_upload(arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DocumentoDF.objects.filter(periodo_df=self.periodo).exists())

    def test_header_nao_bate_extensao_rejeitado(self):
        # .exe renomeado para .pdf: extensão passa na allowlist, mas o conteúdo
        # real não tem a assinatura %PDF — pego pelo sniff de magic bytes.
        arquivo = SimpleUploadedFile(
            "fingido.pdf", b"MZ\x90\x00isto eh um exe fingindo ser pdf", content_type="application/pdf"
        )

        response = self._post_upload(arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DocumentoDF.objects.filter(periodo_df=self.periodo).exists())

    @override_settings(DOCUMENTOS_MAX_UPLOAD_MB=0)
    def test_tamanho_acima_do_limite_rejeitado(self):
        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")

        response = self._post_upload(arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DocumentoDF.objects.filter(periodo_df=self.periodo).exists())

    def test_quota_excedida_rejeitada(self):
        self.empresa.quota_documentos_gb = 1
        self.empresa.save(update_fields=["quota_documentos_gb"])
        self._criar_documento(tamanho_bytes=1024 ** 3, enviado_por=self.master)  # já no limite exato
        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")

        response = self._post_upload(arquivo)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Quota", response.json()["error"])

    def test_member_pode_anexar(self):
        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")
        response = self._post_upload(arquivo, usuario=self.member)
        self.assertEqual(response.status_code, 200)

    def test_viewer_nao_pode_anexar(self):
        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")
        response = self._post_upload(arquivo, usuario=self.viewer)
        self.assertEqual(response.status_code, 403)

    def test_upload_ok_vincula_item_e_sugere_pii_automaticamente(self):
        item_pii = ChecklistItemPeriodo.objects.create(
            periodo_df=self.periodo, secao="Cadastro", texto="Kit cadastral do cotista",
            ordem=999, recebido=False,
        )
        arquivo = SimpleUploadedFile("kit.pdf", PDF_BYTES, content_type="application/pdf")

        response = self._post_upload(arquivo, checklist_item_id=item_pii.id)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["checklist_item_id"], item_pii.id)
        self.assertTrue(data["documento"]["contem_dados_pessoais"])

        documento = DocumentoDF.objects.get(id=data["documento"]["id"])
        self._docs_to_clean.append(documento)
        self.assertEqual(documento.checklist_texto, item_pii.texto)

        item_pii.refresh_from_db()
        self.assertFalse(item_pii.recebido, "Upload não deve marcar recebido sozinho — checklist continua manual.")
        self.assertTrue(
            LogAcessoDocumento.objects.filter(documento=documento, acao=LogAcessoDocumento.Acao.UPLOAD).exists()
        )

    def test_upload_grava_retencao_ate(self):
        """Fase 5.4: 31/dez do ano do período + empresa.retencao_anos (padrão 5)."""
        from datetime import date

        arquivo = SimpleUploadedFile("doc.pdf", PDF_BYTES, content_type="application/pdf")
        response = self._post_upload(arquivo)

        documento = DocumentoDF.objects.get(id=response.json()["documento"]["id"])
        self._docs_to_clean.append(documento)
        self.assertEqual(documento.retencao_ate, date(self.periodo.ano + 5, 12, 31))


class DownloadDocumentoTests(DocumentosViewsTestCase):

    def setUp(self):
        super().setUp()
        self.documento = self._criar_documento(enviado_por=self.master)
        self.documento_b = self._criar_documento(
            empresa=self.outra_empresa, periodo=self.periodo_b, enviado_por=self.master_b,
        )

    def test_outro_tenant_403_e_loga_negado(self):
        self.client.force_login(self.master)  # membro da Empresa A, doc pertence à B
        response = self.client.get(reverse("download_documento", args=[self.documento_b.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            LogAcessoDocumento.objects.filter(
                documento=self.documento_b, acao=LogAcessoDocumento.Acao.NEGADO
            ).exists()
        )

    def test_viewer_nao_baixa_e_loga_negado(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("download_documento", args=[self.documento.id]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            LogAcessoDocumento.objects.filter(
                documento=self.documento, acao=LogAcessoDocumento.Acao.NEGADO
            ).exists()
        )

    def test_member_baixa_com_sucesso(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("download_documento", args=[self.documento.id]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(self.documento.nome_original, response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(
            LogAcessoDocumento.objects.filter(
                documento=self.documento, acao=LogAcessoDocumento.Acao.DOWNLOAD
            ).exists()
        )
        # Não fechar `response` manualmente: o Django test client já faz isso
        # internamente, e um segundo close() dispara request_finished de novo,
        # o que corrompe a transação do PRÓXIMO teste (close_old_connections
        # rodando dentro do atomic block do TestCase).

    def test_documento_excluido_bloqueado(self):
        self.documento.excluido_em = timezone.now()
        self.documento.excluido_por = self.master
        self.documento.save(update_fields=["excluido_em", "excluido_por"])

        self.client.force_login(self.member)
        response = self.client.get(reverse("download_documento", args=[self.documento.id]))

        self.assertEqual(response.status_code, 403)


class ExcluirDocumentoTests(DocumentosViewsTestCase):

    def setUp(self):
        super().setUp()
        self.documento = self._criar_documento(enviado_por=self.master)

    def _post_excluir(self, documento_id, usuario):
        self.client.force_login(usuario)
        return self.client.post(
            reverse("excluir_documento", args=[documento_id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_exclusao_ok_marca_soft_delete_e_loga(self):
        response = self._post_excluir(self.documento.id, self.master)

        self.assertEqual(response.status_code, 200)
        self.documento.refresh_from_db()
        self.assertIsNotNone(self.documento.excluido_em)
        self.assertEqual(self.documento.excluido_por, self.master)
        self.assertTrue(
            LogAcessoDocumento.objects.filter(
                documento=self.documento, acao=LogAcessoDocumento.Acao.EXCLUSAO
            ).exists()
        )

    def test_bloqueada_em_periodo_finalizado(self):
        self.periodo.status = "finalizada"
        self.periodo.save(update_fields=["status"])

        response = self._post_excluir(self.documento.id, self.master)

        self.assertEqual(response.status_code, 403)
        self.documento.refresh_from_db()
        self.assertIsNone(self.documento.excluido_em, "Documento não pode ser excluído com o período finalizado.")

    def test_outro_tenant_403_e_loga_negado(self):
        documento_b = self._criar_documento(
            empresa=self.outra_empresa, periodo=self.periodo_b, enviado_por=self.master_b,
        )

        response = self._post_excluir(documento_b.id, self.master)  # master é da Empresa A

        self.assertEqual(response.status_code, 403)
        documento_b.refresh_from_db()
        self.assertIsNone(documento_b.excluido_em)
        self.assertTrue(
            LogAcessoDocumento.objects.filter(
                documento=documento_b, acao=LogAcessoDocumento.Acao.NEGADO
            ).exists()
        )


class ApiChecklistPeriodoDocumentosTests(DocumentosViewsTestCase):

    def test_flags_pode_baixar_pode_anexar_por_papel(self):
        self.client.force_login(self.viewer)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()
        self.assertFalse(data["pode_baixar"])
        self.assertFalse(data["pode_anexar"])

        self.client.force_login(self.member)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()
        self.assertTrue(data["pode_baixar"])
        self.assertTrue(data["pode_anexar"])

    def test_pode_anexar_falso_sem_dpa(self):
        self.empresa.documentos_habilitados = False
        self.empresa.save(update_fields=["documentos_habilitados"])

        self.client.force_login(self.master)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()

        self.assertFalse(data["pode_anexar"])

    def test_periodo_finalizado_flag(self):
        self.periodo.status = "finalizada"
        self.periodo.save(update_fields=["status"])

        self.client.force_login(self.master)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()

        self.assertTrue(data["periodo_finalizado"])

    def test_documentos_agrupados_por_item_e_avulsos(self):
        doc_vinculado = self._criar_documento(checklist_item=self.item, enviado_por=self.master)
        doc_avulso = self._criar_documento(enviado_por=self.master)
        doc_avulso.checklist_texto = "Item que sumiu do template"
        doc_avulso.save(update_fields=["checklist_texto"])

        self.client.force_login(self.master)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()

        item_data = next(i for i in data["items"] if i["id"] == self.item.id)
        self.assertEqual([d["id"] for d in item_data["documentos"]], [doc_vinculado.id])
        self.assertEqual([d["id"] for d in data["documentos_avulsos"]], [doc_avulso.id])

    def test_documento_excluido_nao_aparece(self):
        doc = self._criar_documento(checklist_item=self.item, enviado_por=self.master)
        doc.excluido_em = timezone.now()
        doc.save(update_fields=["excluido_em"])

        self.client.force_login(self.master)
        data = self.client.get(reverse("api_checklist_periodo", args=[self.periodo.id])).json()

        item_data = next(i for i in data["items"] if i["id"] == self.item.id)
        self.assertEqual(item_data["documentos"], [])

    def test_sem_n_mais_1_ao_carregar_muitos_itens_e_documentos(self):
        # Anexa um documento em vários itens do checklist e confere que o
        # número de queries não escala com a quantidade de itens/documentos —
        # nunca uma query por item (seriam ~38 por abertura de modal).
        itens = list(self.periodo.checklist_items.all())
        for item in itens[:10]:
            self._criar_documento(checklist_item=item, enviado_por=self.master)

        self.client.force_login(self.master)
        url = reverse("api_checklist_periodo", args=[self.periodo.id])

        # Número calibrado pela execução real (sessão/permissões + 1 query de
        # período + 1 de itens + 1 de documentos + 1 de sessão) — o que importa
        # não é o valor exato, é que ele NÃO cresce com a quantidade de itens
        # ou documentos anexados.
        with self.assertNumQueries(12):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class ExclusaoProtegidaTests(DocumentosViewsTestCase):
    """Fundo/período com documento anexado não podem ser excluídos: a cascata não
    removeria o arquivo do storage e deixaria PII órfã (S3 incluso)."""

    def test_excluir_periodo_com_documento_e_bloqueado(self):
        doc = self._criar_documento()
        self.client.force_login(self.master)

        response = self.client.post(
            reverse("excluir_periodo", args=[self.fundo.id, self.periodo.id]), follow=True,
        )

        self.assertTrue(PeriodoDF.objects.filter(id=self.periodo.id).exists())
        self.assertTrue(DocumentoDF.objects.filter(id=doc.id).exists())
        self.assertTrue(doc.arquivo.storage.exists(doc.arquivo.name))
        self.assertTrue(any("documentos anexados" in str(m) for m in response.context["messages"]))

    def test_excluir_fundo_com_documento_e_bloqueado(self):
        doc = self._criar_documento()
        self.client.force_login(self.master)

        response = self.client.post(reverse("excluir_fundo", args=[self.fundo.id]), follow=True)

        self.assertTrue(Fundo.objects.filter(id=self.fundo.id).exists())
        self.assertTrue(DocumentoDF.objects.filter(id=doc.id).exists())
        self.assertTrue(any("documentos anexados" in str(m) for m in response.context["messages"]))

    def test_documento_excluido_logicamente_tambem_protege(self):
        # Exclusão lógica não remove o arquivo; enquanto a linha existe, o arquivo existe.
        doc = self._criar_documento()
        DocumentoDF.objects.filter(pk=doc.pk).update(excluido_em=timezone.now())
        self.client.force_login(self.master)

        self.client.post(reverse("excluir_periodo", args=[self.fundo.id, self.periodo.id]))

        self.assertTrue(PeriodoDF.objects.filter(id=self.periodo.id).exists())

    def test_sem_documentos_continua_excluindo_normalmente(self):
        self.client.force_login(self.master)
        self.client.post(reverse("excluir_periodo", args=[self.fundo.id, self.periodo.id]))
        self.assertFalse(PeriodoDF.objects.filter(id=self.periodo.id).exists())
