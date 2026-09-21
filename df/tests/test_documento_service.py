"""Testes de df/services/documento_service.py: heurística de PII, log de
acesso (LGPD Art. 37), cálculo de retenção (Fase 5.4) e série de crescimento
mensal de armazenamento (Fase 5.6)."""
from datetime import date, datetime

from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.test import RequestFactory, TestCase
from django.utils import timezone

from df.data.checklist_padrao_seed import SEED
from df.models import DocumentoDF, Fundo, LogAcessoDocumento, PeriodoDF
from df.services.checklist_service import criar_checklist_para_periodo
from df.services.documento_service import (
    calcular_retencao_ate,
    registrar_acesso,
    serie_crescimento_mensal,
    sugerir_contem_dados_pessoais,
)
from usuarios.models import Empresa, Usuario


class SugerirContemDadosPessoaisTests(TestCase):

    def test_marca_textos_com_padroes_conhecidos(self):
        casos = [
            "Kit cadastral do cotista",
            "Comprovante de residência dos sócios",
            "Extrato bancário da conta corrente",
            "Extrato bancario da conta corrente",  # sem acento também precisa marcar
            "Documento de identificação do titular",
            "KYC do cliente",
            "Relatório de PLD/FT",
        ]
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertTrue(sugerir_contem_dados_pessoais(texto))

    def test_nao_marca_textos_sem_relacao_com_pii(self):
        casos = [
            "",
            None,
            "Balancete Analítico do período",
            "Carta de representação de Bancos / Advogados / Partes Relacionadas",
            "Posição de Fechamento",
        ]
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertFalse(sugerir_contem_dados_pessoais(texto))

    def test_heuristica_discrimina_contra_o_seed_real(self):
        """Sanity check contra os dados de produção: nem tudo marca, nem nada marca.
        Roda contra o seed de verdade (não literais reinventados) — se o texto do
        seed mudar de um jeito que quebre a detecção, é aqui que aparece."""
        total = 0
        positivos = 0
        for itens in SEED.values():
            for _secao, texto, _prazo, _responsavel in itens:
                total += 1
                if sugerir_contem_dados_pessoais(texto):
                    positivos += 1
        self.assertGreater(positivos, 0, "A heurística deveria reconhecer ao menos um item do seed real.")
        self.assertLess(positivos, total, "A heurística não deveria marcar o checklist inteiro.")


class RegistrarAcessoTests(TestCase):

    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Teste Log Acesso")
        self.usuario = Usuario.objects.create_user(username="tester_log", password="x")
        self.fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Teste Log",
            cnpj="22.222.222/0001-22", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa,
            tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)
        item = self.periodo.checklist_items.first()
        conteudo = b"conteudo de teste para o log de acesso"
        self.documento = DocumentoDF.objects.create(
            empresa=self.empresa, periodo_df=self.periodo, checklist_item=item,
            checklist_texto=item.texto, arquivo=ContentFile(conteudo, name="teste.pdf"),
            nome_original="teste.pdf", tamanho_bytes=len(conteudo), enviado_por=self.usuario,
        )
        self.factory = RequestFactory()

    def tearDown(self):
        if self.documento.arquivo:
            self.documento.arquivo.delete(save=False)

    def _request(self, user=None, ip="203.0.113.7", forwarded=None, agent="pytest-agent/1.0"):
        request = self.factory.get("/documento/1/download/")
        request.user = user if user is not None else self.usuario
        request.META["REMOTE_ADDR"] = ip
        if forwarded:
            request.META["HTTP_X_FORWARDED_FOR"] = forwarded
        request.META["HTTP_USER_AGENT"] = agent
        return request

    def test_registra_download_com_usuario_ip_e_user_agent(self):
        request = self._request()
        registrar_acesso(self.documento, request, LogAcessoDocumento.Acao.DOWNLOAD)

        log = LogAcessoDocumento.objects.get(documento=self.documento)
        self.assertEqual(log.acao, LogAcessoDocumento.Acao.DOWNLOAD)
        self.assertEqual(log.usuario, self.usuario)
        self.assertEqual(log.empresa, self.empresa)
        self.assertEqual(log.documento_id_hist, self.documento.id)
        self.assertEqual(log.ip_address, "203.0.113.7")
        self.assertEqual(log.user_agent, "pytest-agent/1.0")

    def test_prioriza_x_forwarded_for_sobre_remote_addr(self):
        request = self._request(ip="10.0.0.5", forwarded="198.51.100.9, 10.0.0.5")
        registrar_acesso(self.documento, request, LogAcessoDocumento.Acao.NEGADO)

        log = LogAcessoDocumento.objects.get(documento=self.documento)
        self.assertEqual(log.ip_address, "198.51.100.9")
        self.assertEqual(log.acao, LogAcessoDocumento.Acao.NEGADO)

    def test_usuario_anonimo_nao_quebra(self):
        request = self._request(user=AnonymousUser())
        registrar_acesso(self.documento, request, LogAcessoDocumento.Acao.DOWNLOAD)

        log = LogAcessoDocumento.objects.get(documento=self.documento)
        self.assertIsNone(log.usuario)

    def test_log_sobrevive_a_exclusao_fisica_do_documento(self):
        """documento_id_hist preserva o rastro quando o FK vira NULL (expurgo)."""
        request = self._request()
        registrar_acesso(self.documento, request, LogAcessoDocumento.Acao.UPLOAD)
        doc_id = self.documento.id

        self.documento.delete()

        log = LogAcessoDocumento.objects.get(documento_id_hist=doc_id)
        self.assertIsNone(log.documento_id, "O FK deveria virar NULL (SET_NULL) quando o documento é apagado.")
        self.assertEqual(log.documento_id_hist, doc_id, "O id histórico precisa sobreviver ao SET_NULL.")


class CalcularRetencaoAteTests(TestCase):

    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Retencao", retencao_anos=5)
        fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Retencao", cnpj="55.000.000/0001-00", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )

    def test_31_de_dezembro_do_ano_mais_retencao_anos(self):
        resultado = calcular_retencao_ate(self.periodo, self.empresa)
        self.assertEqual(resultado, date(2031, 12, 31))

    def test_retencao_anos_configuravel_por_empresa(self):
        self.empresa.retencao_anos = 10
        self.empresa.save(update_fields=["retencao_anos"])
        resultado = calcular_retencao_ate(self.periodo, self.empresa)
        self.assertEqual(resultado, date(2036, 12, 31))


class SerieCrescimentoMensalTests(TestCase):

    def setUp(self):
        self.empresa_a = Empresa.objects.create(nome="Empresa Crescimento A")
        self.empresa_b = Empresa.objects.create(nome="Empresa Crescimento B")
        self.periodo_a = self._criar_periodo(self.empresa_a, "A")
        self.periodo_b = self._criar_periodo(self.empresa_b, "B")
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

    def _criar_periodo(self, empresa, sufixo):
        fundo = Fundo.objects.create(
            empresa=empresa, nome=f"Fundo Cresc {sufixo}", cnpj=f"{empresa.id}1.000.000/0001-00", tipo_fundo="FIDC",
        )
        periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(periodo)
        return periodo

    def _criar_documento_em(self, empresa, periodo, nome, quando, tamanho_bytes, excluido=False):
        # auto_now_add ignora enviado_em passado no create() — cria e então
        # "volta a data" com um update() direto na queryset.
        doc = DocumentoDF.objects.create(
            empresa=empresa, periodo_df=periodo,
            arquivo=ContentFile(b"x", name=nome),
            nome_original=nome, tamanho_bytes=tamanho_bytes,
        )
        DocumentoDF.objects.filter(pk=doc.pk).update(enviado_em=quando)
        doc.refresh_from_db()
        if excluido:
            doc.excluido_em = timezone.now()
            doc.save(update_fields=["excluido_em"])
        self._docs_to_clean.append(doc)
        return doc

    def test_sem_documentos_retorna_vazio(self):
        labels, total_gb, series = serie_crescimento_mensal()
        self.assertEqual(labels, [])
        self.assertEqual(total_gb, [])
        self.assertEqual(series, [])

    def test_acumula_por_mes_sem_pular_meses_vazios(self):
        self._criar_documento_em(
            self.empresa_a, self.periodo_a, "jan.pdf",
            timezone.make_aware(datetime(2026, 1, 15)), 1024 ** 3,
        )
        self._criar_documento_em(
            self.empresa_a, self.periodo_a, "mar.pdf",
            timezone.make_aware(datetime(2026, 3, 10)), 2 * 1024 ** 3,
        )
        labels, total_gb, series = serie_crescimento_mensal()
        self.assertEqual(labels, ["2026-01", "2026-02", "2026-03"])
        # fevereiro repete o acumulado de janeiro — não pula pro valor de março direto.
        self.assertEqual(total_gb, [1.0, 1.0, 3.0])

    def test_documento_excluido_logicamente_continua_na_serie(self):
        """A regressão que motivou a correção desta seção do plano: um
        documento excluído continua fisicamente no disco até o expurgo, então
        precisa continuar contando aqui — diferente da quota."""
        self._criar_documento_em(
            self.empresa_a, self.periodo_a, "excluido.pdf",
            timezone.make_aware(datetime(2026, 1, 15)), 1024 ** 3, excluido=True,
        )
        labels, total_gb, series = serie_crescimento_mensal()
        self.assertEqual(total_gb, [1.0])

    def test_multi_empresa_series_alinhadas_e_total_e_soma(self):
        self._criar_documento_em(
            self.empresa_a, self.periodo_a, "a_jan.pdf",
            timezone.make_aware(datetime(2026, 1, 10)), 1024 ** 3,
        )
        self._criar_documento_em(
            self.empresa_b, self.periodo_b, "b_fev.pdf",
            timezone.make_aware(datetime(2026, 2, 10)), 2 * 1024 ** 3,
        )
        labels, total_gb, series = serie_crescimento_mensal()

        self.assertEqual(labels, ["2026-01", "2026-02"])
        self.assertEqual(len(series), 2)

        por_nome = {s["empresa"]: s["valores_gb"] for s in series}
        self.assertEqual(por_nome["Empresa Crescimento A"], [1.0, 1.0])
        self.assertEqual(por_nome["Empresa Crescimento B"], [0.0, 2.0])
        self.assertEqual(total_gb, [1.0, 3.0])

    def test_empresa_sem_documento_nao_aparece_na_serie(self):
        Empresa.objects.create(nome="Empresa Sem Documento Nenhum")
        self._criar_documento_em(
            self.empresa_a, self.periodo_a, "unico.pdf",
            timezone.make_aware(datetime(2026, 1, 10)), 1024 ** 3,
        )
        labels, total_gb, series = serie_crescimento_mensal()
        nomes = [s["empresa"] for s in series]
        self.assertNotIn("Empresa Sem Documento Nenhum", nomes)
        self.assertEqual(nomes, ["Empresa Crescimento A"])
