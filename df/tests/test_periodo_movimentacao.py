from datetime import date
from decimal import Decimal as D

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from df.forms import PeriodoDFManualForm
from df.models import ConfiguracaoDF, Movimentacao, PeriodoDF
from df.services import movimentacao_service as ms
from df.services import periodo_service as ps
from df.tests.test_saldo_calculo import PlanoMinimoMixin
from usuarios.models import Membership, Usuario


class DatasDoPeriodoTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()

    def test_datas_padrao(self):
        self.assertEqual(
            ps.datas_padrao_periodo("anual", 2025, date(2026, 3, 31)), (date(2025, 12, 31), date(2024, 12, 31))
        )
        self.assertEqual(
            ps.datas_padrao_periodo("transitoria", 2026, date(2026, 6, 30)), (date(2026, 6, 30), date(2025, 12, 31))
        )

    def test_periodo_anual_nasce_com_as_datas(self):
        ConfiguracaoDF.objects.create(fundo=self.fundo, anual_dia=31, anual_mes=3)
        periodo, criado = ps.obter_ou_criar_periodo(self.fundo, "anual", 2025)
        self.assertTrue(criado)
        self.assertEqual((periodo.data_referencia, periodo.data_anterior), (date(2025, 12, 31), date(2024, 12, 31)))

    def test_periodos_gerados_em_lote_nascem_com_as_datas(self):
        ConfiguracaoDF.objects.create(fundo=self.fundo, anual_dia=31, anual_mes=3)
        (periodo,) = ps.gerar_periodos_anuais(self.fundo, 2024)
        self.assertEqual((periodo.data_referencia, periodo.data_anterior), (date(2024, 12, 31), date(2023, 12, 31)))

    def test_periodo_manual_pelo_servico(self):
        periodo, _ = ps.obter_ou_criar_periodo(self.fundo, "transitoria", 2026, data_vencimento=date(2026, 6, 30))
        self.assertEqual((periodo.data_referencia, periodo.data_anterior), (date(2026, 6, 30), date(2025, 12, 31)))

    def test_periodo_manual_pelo_formulario(self):
        form = PeriodoDFManualForm(
            data={"tipo_periodo": "encerramento", "data_vencimento": "2026-09-30", "descricao": "x"},
            fundo=self.fundo,
        )
        self.assertTrue(form.is_valid(), form.errors)
        periodo = form.save()
        self.assertEqual((periodo.data_referencia, periodo.data_anterior), (date(2026, 9, 30), date(2025, 12, 31)))


class PeriodoTemMovimentacaoTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2025, 12, 31),
            data_anterior=date(2024, 12, 31), status="nao_iniciada",
        )

    def test_sem_nada(self):
        self.assertFalse(ps.periodo_tem_movimentacao(self.periodo))
        self.assertFalse(ps.periodo_tem_dados(self.periodo))

    def test_movimentacao_na_janela(self):
        Movimentacao.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, conta=self.caixa, data=date(2025, 6, 1), valor=D("1")
        )
        self.assertTrue(ps.periodo_tem_movimentacao(self.periodo))
        self.assertTrue(ps.periodo_tem_dados(self.periodo))

    def test_movimentacao_fora_da_janela_nao_conta(self):
        for d in (date(2024, 12, 31), date(2026, 1, 1)):  # a data_anterior é exclusiva
            Movimentacao.objects.create(
                fundo=self.fundo, empresa=self.fundo.empresa, conta=self.caixa, data=d, valor=D("1")
            )
        self.assertFalse(ps.periodo_tem_movimentacao(self.periodo))

    def test_saldo_inicial_so_conta_se_a_data_base_cai_na_janela(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2022, 12, 31), D("10"))
        self.assertFalse(ps.periodo_tem_movimentacao(self.periodo))
        ms.definir_saldo_inicial(self.fundo, self.capital, date(2025, 1, 1), D("10"))
        self.assertTrue(ps.periodo_tem_movimentacao(self.periodo))

    def test_periodo_sem_data_referencia(self):
        self.periodo.data_referencia = None
        self.assertFalse(ps.periodo_tem_movimentacao(self.periodo))

    def test_outro_fundo_nao_conta(self):
        outro = self.montar_fundo("Fundo B", "22.222.222/0001-22")
        ms.criar_movimentacao(outro, self.caixa, date(2025, 6, 1), D("5"))
        self.assertFalse(ps.periodo_tem_movimentacao(self.periodo))


class StatusAutomaticoTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2025, 12, 31),
            data_anterior=date(2024, 12, 31), status="nao_iniciada",
        )

    def status(self):
        return PeriodoDF.objects.get(pk=self.periodo.pk).status

    def test_lancar_move_para_em_andamento_e_excluir_volta(self):
        mov = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 6, 1), D("10"))
        self.assertEqual(self.status(), "em_andamento")
        ms.excluir_movimentacao(mov)
        self.assertEqual(self.status(), "nao_iniciada")

    def test_movimentacao_de_outro_ano_nao_mexe_no_status(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(2026, 2, 1), D("10"))
        self.assertEqual(self.status(), "nao_iniciada")

    def test_editar_data_para_fora_da_janela(self):
        mov = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 6, 1), D("10"))
        ms.editar_movimentacao(mov, data=date(2026, 2, 1))
        self.assertEqual(self.status(), "nao_iniciada")

    def test_saldo_inicial_move_para_em_andamento(self):
        saldo = ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("10"))
        self.assertEqual(self.status(), "em_andamento")
        ms.excluir_saldo_inicial(saldo)
        self.assertEqual(self.status(), "nao_iniciada")

    def test_periodo_vencido_sem_dados(self):
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(data_vencimento=date(2000, 1, 1))
        ps.atualizar_status_automatico(PeriodoDF.objects.get(pk=self.periodo.pk))
        self.assertEqual(self.status(), "vencida")


class ExcluirPeriodoTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        user = Usuario.objects.create_user(username="master_ep", password="x")
        Membership.objects.create(usuario=user, empresa=self.fundo.empresa, role=Membership.Role.MASTER, is_active=True)
        self.client.force_login(user)

    def _periodo(self, tipo, ano=2025, **extra):
        return PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo=tipo, ano=ano,
            data_vencimento=date(2099, 3, 31), data_referencia=date(ano, 12, 31),
            data_anterior=date(ano - 1, 12, 31), status="nao_iniciada", **extra,
        )

    def test_anual_com_movimentacao_nao_pode_ser_excluido(self):
        periodo = self._periodo("anual")
        ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 6, 1), D("10"))
        r = self.client.post(reverse("excluir_periodo", args=[self.fundo.id, periodo.id]), follow=True)
        self.assertTrue(PeriodoDF.objects.filter(pk=periodo.pk).exists())
        textos = [m.message for m in get_messages(r.wsgi_request)]
        self.assertTrue(any("não pode ser excluído" in t for t in textos), textos)

    def test_anual_vazio_pode_ser_excluido(self):
        periodo = self._periodo("anual")
        self.client.post(reverse("excluir_periodo", args=[self.fundo.id, periodo.id]), follow=True)
        self.assertFalse(PeriodoDF.objects.filter(pk=periodo.pk).exists())

    def test_transitoria_exclui_e_as_movimentacoes_sobrevivem(self):
        periodo = self._periodo("transitoria", descricao="parcial")
        ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 6, 1), D("10"))
        r = self.client.post(reverse("excluir_periodo", args=[self.fundo.id, periodo.id]), follow=True)
        self.assertFalse(PeriodoDF.objects.filter(pk=periodo.pk).exists())
        self.assertEqual(Movimentacao.objects.filter(fundo=self.fundo).count(), 1)
        textos = [m.message for m in get_messages(r.wsgi_request)]
        self.assertTrue(any("mantidas" in t for t in textos), textos)
