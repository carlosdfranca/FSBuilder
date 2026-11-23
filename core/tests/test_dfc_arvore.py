from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.processing.dfc_arvore_service import gerar_dfc_arvore
from df.models import Conta, LinhaDFC
from df.services import movimentacao_service as ms
from df.tests.test_saldo_calculo import PlanoMinimoMixin, conta

FIM_2025, FIM_2024 = date(2025, 12, 31), date(2024, 12, 31)
VARIACAO = "Variação no caixa e equivalentes de caixa"
CAIXA_INICIO = "Caixa e equivalentes de caixa no início do período"
CAIXA_FIM = "Caixa e equivalentes de caixa no final do período"
CONCILIACAO = "Contas sem linha na DFC (conciliação com o caixa)"


class DfcMixin(PlanoMinimoMixin):
    def montar_dfc(self):
        self.montar_plano()
        imob_s = conta("1.02", "NÃO CIRCULANTE", "S", 2, 1, Conta.objects.get(codigo="1"))
        self.imobilizado = conta("1.02.01", "Imobilizado", "A", 3, 1, imob_s)
        pass_s = conta("2.01", "PASSIVO CIRCULANTE", "S", 2, 2, Conta.objects.get(codigo="2"))
        self.fornecedores = conta("2.01.01", "Fornecedores", "A", 3, 2, pass_s)
        self.fundo = self.montar_fundo()

        LinhaDFC.objects.all().delete()  # a migration semeia o plano real; aqui a árvore é mínima
        nova = LinhaDFC.objects.create
        nova(secao="operacional", ordem=1, descricao="Resultado líquido", prefixos="3,2.03.02,2.03.03", sinal=1)
        nova(secao="operacional", ordem=2, descricao="Fornecedores", prefixos="2.01", sinal=1)
        nova(secao="investimento", ordem=1, descricao="Imobilizado", prefixos="1.02", sinal=-1)
        nova(secao="financiamento", ordem=1, descricao="Capital", prefixos="2.03.01", sinal=1)

    def cenario_2025(self):
        """Caixa 1000 / capital 1000 em 31/12/2024. Em 2025: +500 receita, −200 despesa,
        −100 despesa a pagar (fornecedor) e −300 de compra de imobilizado.
        Resultado 2025 = 200; caixa termina em 1000."""
        ms.definir_saldo_inicial(self.fundo, self.caixa, FIM_2024, D("1000"))
        ms.definir_saldo_inicial(self.fundo, self.capital, FIM_2024, D("1000"))
        self.mov(self.fundo, self.caixa, date(2025, 3, 1), "500")
        self.mov(self.fundo, self.receita, date(2025, 3, 1), "500")
        self.mov(self.fundo, self.caixa, date(2025, 4, 1), "-200")
        self.mov(self.fundo, self.despesa, date(2025, 4, 1), "-200")
        self.mov(self.fundo, self.despesa, date(2025, 5, 1), "-100")
        self.mov(self.fundo, self.fornecedores, date(2025, 5, 1), "100")
        self.mov(self.fundo, self.caixa, date(2025, 6, 1), "-300")
        self.mov(self.fundo, self.imobilizado, date(2025, 6, 1), "300")

    def dfc(self, atual=FIM_2025, anterior=FIM_2024, fundo=None, **kw):
        kw.setdefault("dividir_por_mil", False)
        return gerar_dfc_arvore((fundo or self.fundo).id, atual, anterior, **kw)

    @staticmethod
    def por_desc(rows):
        return {r["descricao"]: r for r in rows}


class DfcArvoreTests(DfcMixin, TestCase):
    def setUp(self):
        self.montar_dfc()
        self.cenario_2025()

    def test_fluxos_por_atividade(self):
        r = self.por_desc(self.dfc()["linhas"])
        self.assertEqual(r["Resultado líquido"]["atual"], D("200"))
        self.assertEqual(r["Fornecedores"]["atual"], D("100"))
        self.assertEqual(r["Imobilizado"]["atual"], D("-300"))

    def test_totais_por_secao_e_caixa(self):
        r = self.por_desc(self.dfc()["linhas"])
        self.assertEqual(r["Caixa líquido das atividades operacionais"]["atual"], D("300"))
        self.assertEqual(r["Caixa líquido das atividades de investimento"]["atual"], D("-300"))
        self.assertEqual(r[VARIACAO]["atual"], D("0"))
        self.assertEqual(r[CAIXA_INICIO]["atual"], D("1000"))
        self.assertEqual(r[CAIXA_FIM]["atual"], D("1000"))

    def test_fecha_com_a_variacao_do_caixa_sem_conciliacao(self):
        d = self.dfc()
        r = self.por_desc(d["linhas"])
        self.assertEqual(r[VARIACAO]["atual"], r[CAIXA_FIM]["atual"] - r[CAIXA_INICIO]["atual"])
        self.assertFalse(d["tem_conciliacao"])
        self.assertNotIn(CONCILIACAO, r)

    def test_coluna_anterior_e_o_fluxo_do_ano_anterior(self):
        # em 2024 só houve a integralização inicial: capital +1000, caixa de 0 para 1000
        r = self.por_desc(self.dfc()["linhas"])
        self.assertEqual(r["Capital"]["anterior"], D("1000"))
        self.assertEqual(r[VARIACAO]["anterior"], D("1000"))
        self.assertEqual(r[CAIXA_INICIO]["anterior"], D("0"))
        self.assertEqual(r[CAIXA_FIM]["anterior"], D("1000"))

    def test_sem_data_anterior_parte_do_zero_e_anterior_vazio(self):
        r = self.por_desc(self.dfc(anterior=None)["linhas"])
        self.assertEqual(r["Resultado líquido"]["anterior"], D("0"))
        self.assertEqual(r[VARIACAO]["anterior"], D("0"))
        # sem início, tudo conta no período: capital 1000 + operacional 300 − investimento 300
        self.assertEqual(r[VARIACAO]["atual"], D("1000"))

    def test_zerar_anterior(self):
        r = self.por_desc(self.dfc(zerar_anterior=True)["linhas"])
        self.assertEqual(r[VARIACAO]["anterior"], D("0"))
        self.assertEqual(r[CAIXA_FIM]["anterior"], D("0"))

    def test_fundo_sem_movimento_mostra_so_o_resultado_liquido(self):
        outro = self.montar_fundo("Fundo B", "22.222.222/0001-22")
        linhas = self.dfc(fundo=outro)["linhas"]
        itens = [l["descricao"] for l in linhas if l["tipo_linha"] == "conta" and l["nivel"] == 2]
        self.assertEqual(itens, ["Resultado líquido"])

    def test_conta_sem_linha_gera_conciliacao_e_a_dfc_continua_fechando(self):
        outra = conta("1.03", "OUTROS ATIVOS", "S", 2, 1, Conta.objects.get(codigo="1"))
        sem_linha = conta("1.03.01", "Ativo sem linha", "A", 3, 1, outra)
        self.mov(self.fundo, self.caixa, date(2025, 8, 1), "-50")
        self.mov(self.fundo, sem_linha, date(2025, 8, 1), "50")
        d = self.dfc()
        r = self.por_desc(d["linhas"])
        self.assertTrue(d["tem_conciliacao"])
        self.assertEqual(r[CONCILIACAO]["atual"], D("-50"))
        self.assertEqual(r[VARIACAO]["atual"], D("-50"))
        self.assertEqual(r[CAIXA_FIM]["atual"], D("950"))

    def test_segundo_ano_sem_lancamentos(self):
        # o resultado de 2025 migra para Lucros Acumulados; o líquido de 2026 é zero
        r = self.por_desc(self.dfc(date(2026, 12, 31), FIM_2025)["linhas"])
        self.assertEqual(r["Resultado líquido"]["atual"], D("0"))
        self.assertEqual(r[VARIACAO]["atual"], D("0"))
        self.assertEqual(r["Resultado líquido"]["anterior"], D("200"))  # fluxo de 2025

    def test_dividir_por_mil_arredonda(self):
        r = self.por_desc(self.dfc(dividir_por_mil=True)["linhas"])
        self.assertEqual(r["Resultado líquido"]["atual"], D("0"))  # 200 reais = 0,2 mil


class LinhaDfcCleanTests(DfcMixin, TestCase):
    def setUp(self):
        self.montar_dfc()

    def test_codigo_inexistente(self):
        with self.assertRaises(ValidationError):
            LinhaDFC(secao="operacional", descricao="x", prefixos="9.99", sinal=1).clean()

    def test_sobreposicao_com_outra_linha(self):
        with self.assertRaises(ValidationError):
            LinhaDFC(secao="operacional", descricao="x", prefixos="2.01.01", sinal=1).clean()  # dentro de 2.01
        with self.assertRaises(ValidationError):
            LinhaDFC(secao="operacional", descricao="y", prefixos="2", sinal=1).clean()  # contém 2.01

    def test_linha_valida(self):
        outra = conta("1.03", "OUTROS", "S", 2, 1, Conta.objects.get(codigo="1"))
        LinhaDFC(secao="operacional", descricao="ok", prefixos=outra.codigo, sinal=-1).clean()

    def test_prefixos_vazios(self):
        with self.assertRaises(ValidationError):
            LinhaDFC(secao="operacional", descricao="x", prefixos=" , ", sinal=1).clean()


class SeedDaMigrationTests(TestCase):
    def test_linhas_iniciais_foram_semeadas(self):
        self.assertEqual(LinhaDFC.objects.count(), 15)
        self.assertEqual(LinhaDFC.objects.filter(secao="financiamento").count(), 3)
