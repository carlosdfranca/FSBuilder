from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import TestCase

from df.models import Conta, Fundo, Movimentacao, PeriodoDF, SaldoInicial
from df.services import movimentacao_service as ms
from df.services import saldo_calculo_service as sc
from usuarios.models import Empresa


def conta(codigo, descricao, tipo, nivel, natureza, pai=None, **extra):
    return Conta.objects.create(
        codigo=codigo, descricao=descricao, tipo=tipo, nivel=nivel, natureza=natureza, conta_superior=pai, **extra
    )


class PlanoMinimoMixin:
    """Árvore mínima no padrão do plano referencial (PL é filho de Passivo)."""

    def montar_plano(self, com_papeis=True):
        ativo = conta("1", "ATIVO", "S", 1, 1)
        circ = conta("1.01", "CIRCULANTE", "S", 2, 1, ativo)
        self.caixa = conta("1.01.01", "Caixa", "A", 3, 1, circ)
        passivo = conta("2", "PASSIVO", "S", 1, 2)
        pl = conta("2.03", "PL", "S", 2, 3, passivo)
        self.capital = conta("2.03.01", "Capital", "A", 3, 3, pl)
        self.lucros = conta(
            "2.03.02", "Lucros acumulados", "A", 3, 3, pl,
            papel=Conta.Papel.LUCROS_ACUMULADOS if com_papeis else None,
        )
        self.prejuizos = conta(
            "2.03.03", "(-) Prejuízos acumulados", "A", 3, 3, pl,
            papel=Conta.Papel.PREJUIZOS_ACUMULADOS if com_papeis else None,
        )
        res = conta("3", "RESULTADO", "S", 1, 4)
        res1 = conta("3.01", "RESULTADO ANTES DO IR", "S", 2, 4, res)
        self.receita = conta("3.01.01", "Receita", "A", 3, 4, res1)
        self.despesa = conta("3.01.02", "Despesa", "A", 3, 4, res1)

    def montar_fundo(self, nome="Fundo A", cnpj="11.111.111/0001-11"):
        empresa, _ = Empresa.objects.get_or_create(nome="Empresa A")
        return Fundo.objects.create(empresa=empresa, nome=nome, cnpj=cnpj, tipo_fundo="FIDC")

    def mov(self, fundo, conta_, data, valor):
        return ms.criar_movimentacao(fundo, conta_, data, D(valor))


class CenarioDoisAnosTests(PlanoMinimoMixin, TestCase):
    """2025: caixa 1000 / capital 1000; recebe 500 (receita) e paga 200 (despesa) → lucro 300."""

    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        base = date(2024, 12, 31)
        ms.definir_saldo_inicial(self.fundo, self.caixa, base, D("1000"))
        ms.definir_saldo_inicial(self.fundo, self.capital, base, D("1000"))
        self.mov(self.fundo, self.caixa, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.receita, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.caixa, date(2025, 6, 10), "-200")
        self.mov(self.fundo, self.despesa, date(2025, 6, 10), "-200")

    def test_saldos_no_fim_do_primeiro_ano(self):
        s = sc.saldos_por_codigo(self.fundo.id, date(2025, 12, 31))
        self.assertEqual(s["1.01.01"], D("1300"))
        self.assertEqual(s["1"], D("1300"))
        self.assertEqual(s["3"], D("300"))
        self.assertEqual(s["2"], D("1000"))  # passivo já inclui o PL (2.03)
        self.assertEqual(sc.conferencia(self.fundo.id, date(2025, 12, 31)), 0)

    def test_antes_da_data_base_nao_ha_saldo(self):
        self.assertEqual(sc.saldos_analiticas(self.fundo.id, date(2024, 6, 30)), {})

    def test_resultado_zera_em_1_de_janeiro_e_vai_para_lucros(self):
        s = sc.saldos_por_codigo(self.fundo.id, date(2026, 1, 31))
        self.assertNotIn("3", s)
        self.assertEqual(s["2.03.02"], D("300"))
        self.assertEqual(s["2"], D("1300"))
        self.assertEqual(sc.conferencia(self.fundo.id, date(2026, 1, 31)), 0)

    def test_segundo_ano_continua_fechando(self):
        self.mov(self.fundo, self.caixa, date(2026, 2, 10), "100")
        self.mov(self.fundo, self.receita, date(2026, 2, 10), "100")
        d = date(2026, 3, 31)
        s = sc.saldos_por_codigo(self.fundo.id, d)
        self.assertEqual((s["1"], s["2"], s["3"]), (D("1400"), D("1300"), D("100")))
        self.assertEqual(sc.conferencia(self.fundo.id, d), 0)

    def test_prejuizo_vai_para_prejuizos_acumulados_negativo(self):
        outro = self.montar_fundo("Fundo B", "22.222.222/0001-22")
        ms.definir_saldo_inicial(outro, self.caixa, date(2024, 12, 31), D("1000"))
        ms.definir_saldo_inicial(outro, self.capital, date(2024, 12, 31), D("1000"))
        self.mov(outro, self.caixa, date(2025, 5, 1), "-400")
        self.mov(outro, self.despesa, date(2025, 5, 1), "-400")
        s = sc.saldos_por_codigo(outro.id, date(2026, 1, 31))
        self.assertEqual(s["2.03.03"], D("-400"))
        self.assertNotIn("2.03.02", s)
        self.assertEqual(s["2"], D("600"))
        self.assertEqual(sc.conferencia(outro.id, date(2026, 1, 31)), 0)

    def test_movimentacao_anterior_a_data_base_e_ignorada(self):
        Movimentacao.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, conta=self.caixa, data=date(2024, 12, 31), valor=D("9999")
        )
        self.assertEqual(sc.saldos_analiticas(self.fundo.id, date(2025, 12, 31))[self.caixa.id], D("1300"))

    def test_fundos_nao_se_misturam(self):
        outro = self.montar_fundo("Fundo B", "22.222.222/0001-22")
        self.assertEqual(sc.saldos_analiticas(outro.id, date(2025, 12, 31)), {})

    def test_balancete_calculado_dentro_do_ano(self):
        linhas = {l["conta_id"]: l for l in sc.balancete_calculado(self.fundo.id, date(2025, 3, 31), date(2025, 12, 31))}
        caixa = linhas[self.caixa.id]
        self.assertEqual((caixa["anterior"], caixa["movimentos"], caixa["atual"]), (D("1500"), D("-200"), D("1300")))
        desp = linhas[self.despesa.id]
        self.assertEqual((desp["anterior"], desp["movimentos"], desp["atual"]), (D("0"), D("-200"), D("-200")))

    def test_balancete_calculado_cruzando_o_ano_fecha_a_formula(self):
        self.mov(self.fundo, self.caixa, date(2026, 2, 10), "100")
        self.mov(self.fundo, self.receita, date(2026, 2, 10), "100")
        linhas = {l["conta_id"]: l for l in sc.balancete_calculado(self.fundo.id, date(2025, 12, 31), date(2026, 3, 31))}

        for l in linhas.values():
            self.assertEqual(l["anterior"] + l["movimentos"] + l["ajustes"], l["atual"], l)

        rec = linhas[self.receita.id]  # resultado parte de zero no novo ano
        self.assertEqual((rec["anterior"], rec["movimentos"], rec["atual"]), (D("0"), D("100"), D("100")))
        luc = linhas[self.lucros.id]  # o lucro de 2025 entra por ajuste, não por lançamento
        self.assertEqual((luc["anterior"], luc["movimentos"], luc["ajustes"], luc["atual"]), (D("0"), D("0"), D("300"), D("300")))
        self.assertNotIn(self.despesa.id, linhas)  # zerou em 1/1 e não teve lançamento


class AcumuladoSemPapelTests(PlanoMinimoMixin, TestCase):
    def test_sem_conta_de_papel_falha_com_mensagem_clara(self):
        self.montar_plano(com_papeis=False)
        fundo = self.montar_fundo()
        self.mov(fundo, self.receita, date(2025, 3, 1), "50")
        with self.assertRaises(ImproperlyConfigured):
            sc.saldos_analiticas(fundo.id, date(2026, 1, 31))

    def test_sem_resultado_anterior_nao_exige_papel(self):
        self.montar_plano(com_papeis=False)
        fundo = self.montar_fundo()
        self.mov(fundo, self.receita, date(2026, 3, 1), "50")
        self.assertEqual(sc.saldos_analiticas(fundo.id, date(2026, 6, 30)), {self.receita.id: D("50")})


class MovimentacaoServiceTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()

    def test_conta_sintetica_e_recusada(self):
        sintetica = Conta.objects.get(codigo="1.01")
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, sintetica, date(2025, 1, 10), "10")

    def test_conta_inativa_e_recusada(self):
        self.caixa.is_ativa = False
        self.caixa.save()
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2025, 1, 10), "10")

    def test_vigencia_da_conta(self):
        self.caixa.vigencia_inicio = date(2025, 1, 1)
        self.caixa.vigencia_fim = date(2025, 12, 31)
        self.caixa.save()
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2024, 12, 31), "10")
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2026, 1, 1), "10")
        self.mov(self.fundo, self.caixa, date(2025, 6, 1), "10")

    def test_valor_zero_e_recusado(self):
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2025, 1, 10), "0")

    def test_data_deve_ser_posterior_ao_saldo_inicial(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("100"))
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2025, 1, 1), "10")
        self.mov(self.fundo, self.caixa, date(2025, 1, 2), "10")

    def test_saldo_inicial_recusa_conta_de_resultado(self):
        with self.assertRaises(ValidationError):
            ms.definir_saldo_inicial(self.fundo, self.receita, date(2025, 1, 1), D("1"))

    def test_saldo_inicial_e_atualizado_nao_duplicado(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("100"))
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("150"))
        self.assertEqual(SaldoInicial.objects.filter(fundo=self.fundo, conta=self.caixa).count(), 1)
        self.assertEqual(SaldoInicial.objects.get(fundo=self.fundo, conta=self.caixa).valor, D("150"))

    def test_empresa_e_copiada_do_fundo(self):
        m = self.mov(self.fundo, self.caixa, date(2025, 1, 10), "10")
        self.assertEqual(m.empresa_id, self.fundo.empresa_id)


class PeriodoFechadoTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.dentro = self.mov(self.fundo, self.caixa, date(2025, 6, 1), "10")
        PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2026, 3, 31), data_referencia=date(2025, 12, 31), status="finalizada",
        )

    def test_nao_cria_em_periodo_finalizado(self):
        with self.assertRaises(ValidationError):
            self.mov(self.fundo, self.caixa, date(2025, 12, 31), "10")
        self.mov(self.fundo, self.caixa, date(2026, 1, 1), "10")  # logo após o fechamento

    def test_nao_edita_nem_exclui_em_periodo_finalizado(self):
        with self.assertRaises(ValidationError):
            ms.editar_movimentacao(self.dentro, valor=D("99"))
        with self.assertRaises(ValidationError):
            ms.excluir_movimentacao(self.dentro)
        self.assertTrue(Movimentacao.objects.filter(pk=self.dentro.pk).exists())

    def test_nao_move_lancamento_aberto_para_periodo_fechado(self):
        aberto = self.mov(self.fundo, self.caixa, date(2026, 2, 1), "10")
        with self.assertRaises(ValidationError):
            ms.editar_movimentacao(aberto, data=date(2025, 5, 1))

    def test_saldo_inicial_em_periodo_finalizado_e_recusado(self):
        with self.assertRaises(ValidationError):
            ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("1"))

    def test_periodo_nao_finalizado_nao_bloqueia(self):
        PeriodoDF.objects.update(status="em_andamento")
        self.mov(self.fundo, self.caixa, date(2025, 12, 31), "10")
