from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from core.processing.demonstrativo_service import gerar_dpf_arvore, gerar_dre_arvore
from df.models import Fundo, PeriodoDF
from df.services import movimentacao_service as ms
from df.tests.test_saldo_calculo import PlanoMinimoMixin
from usuarios.models import Empresa, Membership, Usuario

FIM_2025 = date(2025, 12, 31)


class CenarioMixin(PlanoMinimoMixin):
    """2025: caixa 1000 / capital 1000; recebe 500 e paga 200 → lucro 300."""

    def montar_cenario(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        base = date(2024, 12, 31)
        ms.definir_saldo_inicial(self.fundo, self.caixa, base, D("1000"))
        ms.definir_saldo_inicial(self.fundo, self.capital, base, D("1000"))
        self.mov(self.fundo, self.caixa, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.receita, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.caixa, date(2025, 6, 10), "-200")
        self.mov(self.fundo, self.despesa, date(2025, 6, 10), "-200")

    def dpf(self, data=FIM_2025, anterior=None, **kw):
        kw.setdefault("dividir_por_mil", False)
        return gerar_dpf_arvore(self.fundo.id, data, anterior, **kw)

    @staticmethod
    def por_codigo(linhas):
        return {l["codigo"]: l for l in linhas}


class DpfArvoreTests(CenarioMixin, TestCase):
    def setUp(self):
        self.montar_cenario()

    def test_totais_e_conferencia_no_primeiro_ano(self):
        dpf = self.dpf()
        t = dpf["totais"]
        self.assertEqual(t["ativo"]["atual"], D("1300"))
        self.assertEqual(t["passivo"]["atual"], D("0"))
        self.assertEqual(t["pl"]["atual"], D("1300"))  # capital 1000 + resultado 300
        self.assertEqual(t["passivo_pl"]["atual"], D("1300"))
        self.assertEqual(dpf["diferenca_atual"], 0)

    def test_resultado_do_exercicio_aparece_dentro_do_pl(self):
        pl = self.por_codigo(self.dpf()["pl"])
        self.assertEqual(pl["2.03"]["atual"], D("1300"))
        self.assertEqual(pl["2.03.01"]["atual"], D("1000"))
        self.assertEqual(pl["3"]["descricao"], "Resultado do exercício")
        self.assertEqual(pl["3"]["tipo_linha"], "virtual")
        self.assertEqual(pl["3"]["atual"], D("300"))

    def test_corte_por_nivel_soma_o_que_esta_abaixo(self):
        codigos = [l["codigo"] for l in self.dpf(nivel_max=2)["ativo"]]
        self.assertEqual(codigos, ["1", "1.01"])
        self.assertEqual(self.por_codigo(self.dpf(nivel_max=2)["ativo"])["1.01"]["atual"], D("1300"))
        self.assertIn("1.01.01", [l["codigo"] for l in self.dpf(nivel_max=3)["ativo"]])

    def test_tipo_linha_grupo_e_conta(self):
        ativo = self.por_codigo(self.dpf()["ativo"])
        self.assertEqual(ativo["1"]["tipo_linha"], "grupo")
        self.assertEqual(ativo["1.01.01"]["tipo_linha"], "conta")

    def test_dividir_por_mil_arredonda_para_inteiro(self):
        self.assertEqual(self.dpf(dividir_por_mil=True)["totais"]["ativo"]["atual"], D("1"))  # 1,3 mil

    def test_conferencia_usa_valor_exato_e_nao_o_arredondado(self):
        # ativo e passivo+PL ficam 400 (0,4 mil) fora de equilíbrio: o exato arredonda para 0
        self.mov(self.fundo, self.caixa, date(2025, 7, 1), "400")
        self.assertEqual(self.dpf(dividir_por_mil=True)["diferenca_atual"], D("0"))
        self.assertEqual(self.dpf(dividir_por_mil=False)["diferenca_atual"], D("400"))

    def test_percentuais_sobre_o_pl(self):
        ativo = self.por_codigo(self.dpf()["ativo"])
        self.assertEqual(ativo["1.01.01"]["perc_atual"], D("100"))
        self.assertIsNone(ativo["1.01.01"]["perc_anterior"])  # base anterior é zero

    def test_comparativo_com_ano_anterior_e_segundo_ano(self):
        self.mov(self.fundo, self.caixa, date(2026, 2, 10), "100")
        self.mov(self.fundo, self.receita, date(2026, 2, 10), "100")
        dpf = self.dpf(date(2026, 3, 31), anterior=FIM_2025)
        t = dpf["totais"]
        self.assertEqual((t["ativo"]["atual"], t["ativo"]["anterior"]), (D("1400"), D("1300")))
        self.assertEqual((t["pl"]["atual"], t["pl"]["anterior"]), (D("1400"), D("1300")))
        self.assertEqual((dpf["diferenca_atual"], dpf["diferenca_anterior"]), (0, 0))
        # o lucro de 2025 virou Lucros Acumulados; o resultado de 2026 só tem a receita nova
        pl = self.por_codigo(dpf["pl"])
        self.assertEqual(pl["2.03.02"]["atual"], D("300"))
        self.assertEqual(pl["3"]["atual"], D("100"))

    def test_zerar_anterior(self):
        dpf = self.dpf(anterior=date(2024, 12, 31), zerar_anterior=True)
        self.assertEqual(dpf["totais"]["ativo"]["anterior"], D("0"))

    def test_linhas_zeradas_somem(self):
        self.mov(self.fundo, self.caixa, date(2026, 2, 10), "100")
        self.mov(self.fundo, self.receita, date(2026, 2, 10), "100")
        pl = self.por_codigo(self.dpf(date(2026, 3, 31))["pl"])
        self.assertNotIn("2.03.03", pl)  # Prejuízos acumulados sem saldo


class DreArvoreTests(CenarioMixin, TestCase):
    def setUp(self):
        self.montar_cenario()

    def test_linhas_e_resultado(self):
        dre = gerar_dre_arvore(self.fundo.id, FIM_2025, None, nivel_max=3, dividir_por_mil=False)
        linhas = self.por_codigo(dre["linhas"])
        self.assertEqual(linhas["3"]["atual"], D("300"))
        self.assertEqual(linhas["3.01.01"]["atual"], D("500"))
        self.assertEqual(linhas["3.01.02"]["atual"], D("-200"))
        self.assertEqual(dre["resultado_atual"], D("300"))

    def test_resultado_zera_no_ano_seguinte(self):
        dre = gerar_dre_arvore(self.fundo.id, date(2026, 3, 31), FIM_2025, dividir_por_mil=False)
        self.assertEqual(dre["resultado_atual"], D("0"))
        self.assertEqual(dre["resultado_anterior"], D("300"))
        self.assertEqual(dre["linhas"][0]["anterior"], D("300"))


class DfResultadoViewTests(CenarioMixin, TestCase):
    """Tela principal da DF (/dre-resultado/<id>/), agora pelo plano de contas."""

    def setUp(self):
        self.montar_cenario()
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=FIM_2025,
            data_anterior=date(2024, 12, 31), status="em_andamento",
        )
        self.user = Usuario.objects.create_user(username="viewer_arv", password="x")
        Membership.objects.create(
            usuario=self.user, empresa=self.fundo.empresa, role=Membership.Role.VIEWER, is_active=True
        )
        self.url = reverse("dre_resultado", args=[self.periodo.id])

    def test_exige_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_renderiza_as_quatro_demonstracoes(self):
        self.client.force_login(self.user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["nivel"], 4)
        self.assertEqual(r.context["dpf"]["totais"]["ativo"]["atual"], D("1"))  # R$ mil arredondado
        for contem in ("Resultado do exercício", "Total do Ativo", "Fluxo de caixa das atividades operacionais"):
            self.assertContains(r, contem)

    def test_pl_e_resultado_alimentam_a_dmpl(self):
        self.client.force_login(self.user)
        ctx = self.client.get(self.url).context
        self.assertEqual(ctx["pl_ajustado_atual"], ctx["dpf"]["totais"]["pl"]["atual"])
        self.assertEqual(ctx["resultado_exercicio"], ctx["dre"]["resultado_atual"])

    def test_alerta_quando_o_ativo_nao_fecha(self):
        self.mov(self.fundo, self.caixa, date(2025, 7, 1), "5000000")  # sem contrapartida
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.url), "não fecha com o passivo")

    def test_sem_alerta_quando_fecha(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(self.url), "não fecha com o passivo")

    def test_nivel_e_limitado(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url + "?nivel=99").context["nivel"], 7)
        self.assertEqual(self.client.get(self.url + "?nivel=0").context["nivel"], 2)
        self.assertEqual(self.client.get(self.url + "?nivel=abc").context["nivel"], 4)

    def test_nao_ve_periodo_de_outra_empresa(self):
        outra = Empresa.objects.create(nome="Empresa B")
        fundo_b = Fundo.objects.create(empresa=outra, nome="Fundo B", cnpj="33.333.333/0001-33", tipo_fundo="FIDC")
        periodo_b = PeriodoDF.objects.create(
            fundo=fundo_b, empresa=outra, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=FIM_2025, status="nao_iniciada",
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("dre_resultado", args=[periodo_b.id])).status_code, 404)

    def test_periodo_antigo_sem_datas_e_preenchido_na_hora(self):
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(data_referencia=None, data_anterior=None)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.periodo.refresh_from_db()
        self.assertEqual((self.periodo.data_referencia, self.periodo.data_anterior), (FIM_2025, date(2024, 12, 31)))
