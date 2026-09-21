"""Testes do painel administrativo de documentos:
Fase 5.1 — acesso restrito a Global Admin e a agregação de estatisticas_por_empresa() sem N+1.
Fase 5.2 — detalhe por empresa (lista + paginação) e config rápida (documentos_habilitados/retencao_anos/quota_documentos_gb).
Fase 5.3 — filtro de PII na lista de documentos e log de acesso filtrável por ação.
Fase 5.6 — gráfico de crescimento mensal na tela principal do painel."""
import json
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from df.models import DocumentoDF, Fundo, LogAcessoDocumento, PeriodoDF
from df.services.checklist_service import criar_checklist_para_periodo
from df.services.documento_service import estatisticas_por_empresa, registrar_acesso
from usuarios.models import Empresa, Membership, Usuario

PDF_BYTES = b"%PDF-1.4\n%mock pdf content for tests\n"


class PainelDocumentosAcessoTests(TestCase):

    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Painel Acesso", documentos_habilitados=True)
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_painel", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.master = Usuario.objects.create_user(username="master_painel", password="x")
        Membership.objects.create(
            usuario=self.master, empresa=self.empresa, role=Membership.Role.MASTER, is_active=True
        )

    def test_master_de_empresa_nao_acessa(self):
        """MASTER de uma empresa cliente não é Global Admin — o painel vê todas as empresas."""
        self.client.force_login(self.master)
        response = self.client.get(reverse("painel_documentos"))
        self.assertEqual(response.status_code, 403)

    def test_usuario_sem_vinculo_nao_acessa(self):
        outro = Usuario.objects.create_user(username="sem_vinculo_painel", password="x")
        self.client.force_login(outro)
        response = self.client.get(reverse("painel_documentos"))
        self.assertEqual(response.status_code, 403)

    def test_global_admin_acessa(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos"))
        self.assertEqual(response.status_code, 200)

    def test_superuser_acessa_mesmo_sem_global_role(self):
        superuser = Usuario.objects.create_superuser(
            username="super_painel", email="super_painel@example.com", password="x"
        )
        self.client.force_login(superuser)
        response = self.client.get(reverse("painel_documentos"))
        self.assertEqual(response.status_code, 200)

    def test_anonimo_redireciona_para_login(self):
        response = self.client.get(reverse("painel_documentos"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class EstatisticasPorEmpresaTests(TestCase):

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_stats", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self._docs_to_clean = []

    def tearDown(self):
        # Mesmo cuidado de core/tests/test_documentos_views.py: libera handles
        # de arquivo pendentes no Windows antes de tentar apagar.
        import gc
        gc.collect()
        for doc in self._docs_to_clean:
            if doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass

    def _criar_empresa_com_documentos(self, nome, n_docs, n_pii, quota_gb=50, habilitado=True):
        empresa = Empresa.objects.create(nome=nome, documentos_habilitados=habilitado, quota_documentos_gb=quota_gb)
        fundo = Fundo.objects.create(
            empresa=empresa, nome=f"Fundo {nome}", cnpj=f"{empresa.id}.000.000/0001-00", tipo_fundo="FIDC",
        )
        periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(periodo)
        for i in range(n_docs):
            doc = DocumentoDF.objects.create(
                empresa=empresa, periodo_df=periodo,
                arquivo=SimpleUploadedFile(f"doc{i}.pdf", PDF_BYTES, content_type="application/pdf"),
                nome_original=f"doc{i}.pdf", tamanho_bytes=len(PDF_BYTES),
                contem_dados_pessoais=(i < n_pii),
            )
            self._docs_to_clean.append(doc)
        return empresa

    def test_agrega_uso_contagem_e_pii_corretamente(self):
        empresa = self._criar_empresa_com_documentos("Empresa Stats A", n_docs=3, n_pii=1)

        stats = {e.id: e for e in estatisticas_por_empresa()}
        row = stats[empresa.id]

        self.assertEqual(row.documentos_ativos, 3)
        self.assertEqual(row.documentos_pii, 1)
        self.assertEqual(row.uso_bytes, 3 * len(PDF_BYTES))

    def test_documento_excluido_nao_conta(self):
        empresa = self._criar_empresa_com_documentos("Empresa Stats B", n_docs=2, n_pii=0)
        doc = DocumentoDF.objects.filter(empresa=empresa).first()
        doc.excluido_em = timezone.now()
        doc.save(update_fields=["excluido_em"])

        stats = {e.id: e for e in estatisticas_por_empresa()}
        row = stats[empresa.id]
        self.assertEqual(row.documentos_ativos, 1)
        self.assertEqual(row.uso_bytes, len(PDF_BYTES))

    def test_empresa_sem_documentos_nao_quebra(self):
        empresa = Empresa.objects.create(nome="Empresa Stats Vazia")

        stats = {e.id: e for e in estatisticas_por_empresa()}
        row = stats[empresa.id]
        self.assertEqual(row.documentos_ativos, 0)
        self.assertIsNone(row.uso_bytes)  # Sum sem linhas retorna None — a view trata com `or 0`

    def test_painel_nao_gera_n_mais_1_com_varias_empresas(self):
        for i in range(5):
            self._criar_empresa_com_documentos(f"Empresa N+1 {i}", n_docs=2, n_pii=1)

        self.client.force_login(self.global_admin)
        url = reverse("painel_documentos")

        # O que importa não é o número exato, é que ele NÃO cresça com a
        # quantidade de empresas/documentos (mesma disciplina da Fase 3):
        # sessão + usuário + a UMA query agregada de estatisticas_por_empresa()
        # + a UMA query agregada de serie_crescimento_mensal() (Fase 5.6)
        # + a query de empresas_disponiveis do context processor da navbar.
        with self.assertNumQueries(5):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class PainelDocumentosEmpresaTests(TestCase):

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_empresa_detail", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.master = Usuario.objects.create_user(username="master_empresa_detail", password="x")
        self.empresa = Empresa.objects.create(
            nome="Empresa Detalhe", documentos_habilitados=True, quota_documentos_gb=1,
        )
        Membership.objects.create(
            usuario=self.master, empresa=self.empresa, role=Membership.Role.MASTER, is_active=True
        )
        fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Detalhe", cnpj="99.000.000/0001-00", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)
        self._docs_to_clean = []

    def tearDown(self):
        import gc
        gc.collect()
        for doc in self._docs_to_clean:
            if doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass

    def _criar_documento(self, nome="doc.pdf"):
        doc = DocumentoDF.objects.create(
            empresa=self.empresa, periodo_df=self.periodo,
            arquivo=SimpleUploadedFile(nome, PDF_BYTES, content_type="application/pdf"),
            nome_original=nome, tamanho_bytes=len(PDF_BYTES),
        )
        self._docs_to_clean.append(doc)
        return doc

    def test_master_nao_acessa_detalhe(self):
        self.client.force_login(self.master)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.status_code, 403)

    def test_global_admin_acessa_e_ve_documentos(self):
        self._criar_documento("kit_cadastral.pdf")
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kit_cadastral.pdf")

    def test_paginacao_25_por_pagina(self):
        for i in range(30):
            self._criar_documento(f"doc{i}.pdf")
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(len(response.context["page_obj"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.count, 30)

    def test_documento_excluido_nao_aparece(self):
        doc = self._criar_documento("excluido.pdf")
        doc.excluido_em = timezone.now()
        doc.save(update_fields=["excluido_em"])
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertNotContains(response, "excluido.pdf")


class PainelDocumentosConfigTests(TestCase):

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_config", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.master = Usuario.objects.create_user(username="master_config", password="x")
        self.empresa_a = Empresa.objects.create(nome="Empresa Config A")
        self.empresa_b = Empresa.objects.create(
            nome="Empresa Config B", documentos_habilitados=True, retencao_anos=7, quota_documentos_gb=99,
        )
        Membership.objects.create(
            usuario=self.master, empresa=self.empresa_a, role=Membership.Role.MASTER, is_active=True
        )

    def test_master_nao_pode_alterar_config(self):
        self.client.force_login(self.master)
        response = self.client.post(reverse("painel_documentos_config", args=[self.empresa_a.id]), {
            "documentos_habilitados": "on", "retencao_anos": 10, "quota_documentos_gb": 5,
        })
        self.assertEqual(response.status_code, 403)
        self.empresa_a.refresh_from_db()
        self.assertFalse(self.empresa_a.documentos_habilitados)

    def test_global_admin_atualiza_config_da_empresa_certa(self):
        self.client.force_login(self.global_admin)
        response = self.client.post(reverse("painel_documentos_config", args=[self.empresa_a.id]), {
            "documentos_habilitados": "on", "retencao_anos": 10, "quota_documentos_gb": 5,
        })
        self.assertRedirects(response, reverse("painel_documentos_empresa", args=[self.empresa_a.id]))

        self.empresa_a.refresh_from_db()
        self.assertTrue(self.empresa_a.documentos_habilitados)
        self.assertEqual(self.empresa_a.retencao_anos, 10)
        self.assertEqual(self.empresa_a.quota_documentos_gb, 5)

        # Isolamento entre tenants: a empresa B não pode ter sido tocada.
        self.empresa_b.refresh_from_db()
        self.assertTrue(self.empresa_b.documentos_habilitados)
        self.assertEqual(self.empresa_b.retencao_anos, 7)
        self.assertEqual(self.empresa_b.quota_documentos_gb, 99)

    def test_desmarcar_checkbox_desabilita(self):
        self.client.force_login(self.global_admin)
        # documentos_habilitados ausente do POST = desmarcado (padrão HTML de checkbox).
        self.client.post(reverse("painel_documentos_config", args=[self.empresa_b.id]), {
            "retencao_anos": 7, "quota_documentos_gb": 99,
        })
        self.empresa_b.refresh_from_db()
        self.assertFalse(self.empresa_b.documentos_habilitados)

    def test_valor_invalido_nao_salva(self):
        self.client.force_login(self.global_admin)
        self.client.post(reverse("painel_documentos_config", args=[self.empresa_a.id]), {
            "documentos_habilitados": "on", "retencao_anos": -1, "quota_documentos_gb": 5,
        })
        self.empresa_a.refresh_from_db()
        self.assertFalse(self.empresa_a.documentos_habilitados)  # form inválido, nada foi salvo


class PainelDocumentosLogEFiltroPiiTests(TestCase):

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_log", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.empresa = Empresa.objects.create(nome="Empresa Log", documentos_habilitados=True)
        fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Log", cnpj="88.000.000/0001-00", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)

        self.doc_pii = self._criar_documento(self.empresa, self.periodo, "kit_cadastral.pdf", contem_dados_pessoais=True)
        self.doc_normal = self._criar_documento(self.empresa, self.periodo, "balancete.pdf", contem_dados_pessoais=False)
        self._docs_to_clean = [self.doc_pii, self.doc_normal]

        # Eventos de log via registrar_acesso — exercita o fluxo de verdade,
        # não LogAcessoDocumento.objects.create() direto.
        req = RequestFactory().get("/")
        req.user = self.global_admin
        req.META["REMOTE_ADDR"] = "203.0.113.9"
        registrar_acesso(self.doc_pii, req, LogAcessoDocumento.Acao.UPLOAD)
        registrar_acesso(self.doc_pii, req, LogAcessoDocumento.Acao.DOWNLOAD)
        registrar_acesso(self.doc_normal, req, LogAcessoDocumento.Acao.NEGADO)

    def tearDown(self):
        import gc
        gc.collect()
        for doc in self._docs_to_clean:
            if doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass

    def _criar_documento(self, empresa, periodo, nome, contem_dados_pessoais=False):
        return DocumentoDF.objects.create(
            empresa=empresa, periodo_df=periodo,
            arquivo=SimpleUploadedFile(nome, PDF_BYTES, content_type="application/pdf"),
            nome_original=nome, tamanho_bytes=len(PDF_BYTES),
            contem_dados_pessoais=contem_dados_pessoais,
        )

    def test_somente_pii_filtra_documentos(self):
        # As duas abas ficam no DOM ao mesmo tempo (Bootstrap só alterna a
        # visibilidade via CSS) — "balancete.pdf" ainda aparece legitimamente
        # na aba de log (o evento NEGADO referencia esse documento). Por isso
        # a asserção é escopada só ao conteúdo da aba de documentos.
        self.client.force_login(self.global_admin)
        response = self.client.get(
            reverse("painel_documentos_empresa", args=[self.empresa.id]), {"pii": "1"}
        )
        html = response.content.decode("utf-8")
        tab_documentos = html.split('id="tab-documentos"')[1].split('id="tab-log"')[0]
        self.assertIn("kit_cadastral.pdf", tab_documentos)
        self.assertNotIn("balancete.pdf", tab_documentos)

    def test_sem_filtro_mostra_todos(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertContains(response, "kit_cadastral.pdf")
        self.assertContains(response, "balancete.pdf")

    def test_log_mostra_eventos_da_empresa(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.context["log_page_obj"].paginator.count, 3)

    def test_filtro_por_acao_no_log(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(
            reverse("painel_documentos_empresa", args=[self.empresa.id]), {"log_acao": "negado"}
        )
        logs = list(response.context["log_page_obj"])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].acao, LogAcessoDocumento.Acao.NEGADO)

    def test_tab_ativa_muda_para_log_quando_filtro_de_log_presente(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(
            reverse("painel_documentos_empresa", args=[self.empresa.id]), {"log_acao": "negado"}
        )
        self.assertEqual(response.context["tab_ativa"], "log")

    def test_log_de_outra_empresa_nao_aparece(self):
        outra_empresa = Empresa.objects.create(nome="Outra Empresa Log")
        outro_fundo = Fundo.objects.create(
            empresa=outra_empresa, nome="Outro Fundo Log", cnpj="77.000.000/0001-00", tipo_fundo="FIDC",
        )
        outro_periodo = PeriodoDF.objects.create(
            fundo=outro_fundo, empresa=outra_empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(outro_periodo)
        outro_doc = self._criar_documento(outra_empresa, outro_periodo, "outro.pdf")
        self._docs_to_clean.append(outro_doc)

        req = RequestFactory().get("/")
        req.user = self.global_admin
        req.META["REMOTE_ADDR"] = "203.0.113.10"
        registrar_acesso(outro_doc, req, LogAcessoDocumento.Acao.UPLOAD)

        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.context["log_page_obj"].paginator.count, 3)  # não cresce com o log da outra empresa


class PainelDocumentosExpurgoTests(TestCase):
    """Fase 5.5: lista de vencidos + expurgo físico manual, um documento por vez."""

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_expurgo", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.master = Usuario.objects.create_user(username="master_expurgo", password="x")
        self.empresa = Empresa.objects.create(nome="Empresa Expurgo", retencao_anos=5)
        Membership.objects.create(
            usuario=self.master, empresa=self.empresa, role=Membership.Role.MASTER, is_active=True
        )
        fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Expurgo", cnpj="66.000.000/0001-00", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)
        self._docs_to_clean = []

    def tearDown(self):
        import gc
        gc.collect()
        for doc in self._docs_to_clean:
            if doc.pk and doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass

    def _criar_documento(self, nome, retencao_ate=None, excluido=False):
        doc = DocumentoDF.objects.create(
            empresa=self.empresa, periodo_df=self.periodo,
            arquivo=SimpleUploadedFile(nome, PDF_BYTES, content_type="application/pdf"),
            nome_original=nome, tamanho_bytes=len(PDF_BYTES),
            retencao_ate=retencao_ate,
        )
        if excluido:
            doc.excluido_em = timezone.now()
            doc.excluido_por = self.master
            doc.save(update_fields=["excluido_em", "excluido_por"])
        self._docs_to_clean.append(doc)
        return doc

    def test_documento_nao_vencido_nao_aparece(self):
        self._criar_documento("futuro.pdf", retencao_ate=date(2099, 1, 1))
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.context["vencidos_page_obj"].paginator.count, 0)

    def test_documento_vencido_ativo_aparece(self):
        self._criar_documento("vencido_ativo.pdf", retencao_ate=date(2020, 1, 1))
        self.client.force_login(self.global_admin)
        response = self.client.get(
            reverse("painel_documentos_empresa", args=[self.empresa.id]), {"vencidos": "1"}
        )
        self.assertContains(response, "vencido_ativo.pdf")
        self.assertEqual(response.context["tab_ativa"], "vencidos")

    def test_documento_vencido_e_excluido_tambem_aparece(self):
        """Vencidos inclui os dois conjuntos: nunca-excluídos E já-excluídos-logicamente."""
        self._criar_documento("vencido_excluido.pdf", retencao_ate=date(2020, 1, 1), excluido=True)
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos_empresa", args=[self.empresa.id]))
        self.assertEqual(response.context["vencidos_page_obj"].paginator.count, 1)

    def test_master_nao_pode_expurgar(self):
        doc = self._criar_documento("protegido.pdf", retencao_ate=date(2020, 1, 1))
        self.client.force_login(self.master)
        response = self.client.post(reverse("expurgar_documento", args=[doc.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(DocumentoDF.objects.filter(id=doc.id).exists())

    def test_get_nao_expurga(self):
        doc = self._criar_documento("get_nao_apaga.pdf", retencao_ate=date(2020, 1, 1))
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("expurgar_documento", args=[doc.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(DocumentoDF.objects.filter(id=doc.id).exists())

    def test_expurgo_apaga_arquivo_e_registro_mas_preserva_log(self):
        doc = self._criar_documento("expurgar_de_verdade.pdf", retencao_ate=date(2020, 1, 1))
        doc_id = doc.id
        caminho = doc.arquivo.name
        storage = doc.arquivo.storage
        self.assertTrue(storage.exists(caminho))

        # Log de upload prévio — precisa sobreviver ao expurgo.
        req = RequestFactory().get("/")
        req.user = self.global_admin
        req.META["REMOTE_ADDR"] = "203.0.113.20"
        registrar_acesso(doc, req, LogAcessoDocumento.Acao.UPLOAD)

        self.client.force_login(self.global_admin)
        response = self.client.post(reverse("expurgar_documento", args=[doc_id]))

        self.assertRedirects(
            response,
            reverse("painel_documentos_empresa", args=[self.empresa.id]) + "?vencidos=1",
        )
        self.assertFalse(DocumentoDF.objects.filter(id=doc_id).exists())
        self.assertFalse(storage.exists(caminho), "O arquivo físico precisa ter sido removido do disco.")

        logs = LogAcessoDocumento.objects.filter(documento_id_hist=doc_id).order_by("ocorrido_em")
        self.assertEqual(logs.count(), 2)  # upload + expurgo
        self.assertEqual(logs.last().acao, LogAcessoDocumento.Acao.EXPURGO)
        for log in logs:
            self.assertIsNone(log.documento_id, "FK deveria virar NULL (SET_NULL) após o expurgo.")

        self._docs_to_clean.remove(doc)  # já foi apagado pela própria view; nada a limpar no tearDown


class PainelDocumentosCrescimentoTests(TestCase):
    """Fase 5.6: o gráfico só aparece quando há dados, e o JSON servido pro
    Chart.js bate com o que a função de dados produz."""

    def setUp(self):
        self.global_admin = Usuario.objects.create_user(
            username="global_admin_cresc", password="x",
            global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )

    def test_sem_documentos_esconde_o_grafico(self):
        self.client.force_login(self.global_admin)
        response = self.client.get(reverse("painel_documentos"))
        self.assertFalse(response.context["crescimento_tem_dados"])
        self.assertNotIn("chartCrescimento", response.content.decode("utf-8"))

    def test_com_documentos_mostra_o_grafico_e_dados_validos(self):
        empresa = Empresa.objects.create(nome="Empresa Grafico Painel")
        fundo = Fundo.objects.create(
            empresa=empresa, nome="Fundo Grafico", cnpj="33.500.000/0001-00", tipo_fundo="FIDC",
        )
        periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(periodo)
        doc = DocumentoDF.objects.create(
            empresa=empresa, periodo_df=periodo,
            arquivo=SimpleUploadedFile("g.pdf", PDF_BYTES, content_type="application/pdf"),
            nome_original="g.pdf", tamanho_bytes=len(PDF_BYTES),
        )
        try:
            self.client.force_login(self.global_admin)
            response = self.client.get(reverse("painel_documentos"))

            self.assertTrue(response.context["crescimento_tem_dados"])
            self.assertIn("chartCrescimento", response.content.decode("utf-8"))

            data = json.loads(response.context["crescimento_data"])
            self.assertEqual(data["gatilho_gb"], 100)
            self.assertEqual(len(data["series"]), 1)
            self.assertEqual(data["series"][0]["empresa"], "Empresa Grafico Painel")
            self.assertEqual(len(data["labels"]), len(data["total_gb"]))
        finally:
            import gc
            gc.collect()
            if doc.arquivo:
                try:
                    doc.arquivo.delete(save=False)
                except PermissionError:
                    pass
