"""Períodos anuais criados pelas movimentações, status Histórico e reabertura com motivo."""
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.processing.importacao_movimentacao_service import importar_movimentacoes, importar_saldos_iniciais
from df.models import AlteracaoStatusPeriodo, ConfiguracaoDF, PeriodoDF
from df.services import movimentacao_service as ms
from df.services import periodo_service as ps
from df.tests.test_saldo_calculo import PlanoMinimoMixin
from usuarios.models import Membership, Usuario

HOJE = date.today()
ANO_ANTIGO = HOJE.year - 3  # prazo (31/03 do ano seguinte) já passou
ANO_CORRENTE = HOJE.year  # prazo ainda no futuro


def _planilha(linhas):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["DATA", "CONTA", "VALOR", "HISTÓRICO", "DOCUMENTO"])
    for l in linhas:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class GarantirPeriodosTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()

    def anual(self, ano):
        return PeriodoDF.objects.get(fundo=self.fundo, tipo_periodo="anual", ano=ano)

    def test_vencimento_padrao_e_31_03_do_ano_seguinte(self):
        self.assertEqual(ps.vencimento_anual(self.fundo, 2025), date(2026, 3, 31))

    def test_vencimento_da_configuracao_tem_prioridade(self):
        ConfiguracaoDF.objects.create(fundo=self.fundo, anual_dia=30, anual_mes=4)
        self.assertEqual(ps.vencimento_anual(self.fundo, 2025), date(2026, 4, 30))

    def test_configuracao_incompleta_ou_impossivel_cai_no_padrao(self):
        ConfiguracaoDF.objects.create(fundo=self.fundo, anual_dia=None, anual_mes=None)
        self.assertEqual(ps.vencimento_anual(self.fundo, 2025), date(2026, 3, 31))
        ConfiguracaoDF.objects.update(anual_dia=31, anual_mes=2)
        self.assertEqual(ps.vencimento_anual(self.fundo, 2025), date(2026, 3, 31))

    def test_ano_antigo_nasce_historico_e_o_corrente_nao(self):
        criados = ps.garantir_periodos_anuais(self.fundo, [ANO_ANTIGO, ANO_CORRENTE])
        self.assertEqual(len(criados), 2)
        self.assertEqual(self.anual(ANO_ANTIGO).status, "historico")
        self.assertEqual(self.anual(ANO_CORRENTE).status, "nao_iniciada")
        self.assertFalse(self.anual(ANO_ANTIGO).criado_manualmente)

    def test_e_idempotente_e_so_cria_o_que_falta(self):
        ps.garantir_periodos_anuais(self.fundo, [ANO_ANTIGO])
        criados = ps.garantir_periodos_anuais(self.fundo, [ANO_ANTIGO, ANO_ANTIGO + 1])
        self.assertEqual([p.ano for p in criados], [ANO_ANTIGO + 1])
        self.assertEqual(PeriodoDF.objects.filter(fundo=self.fundo, tipo_periodo="anual").count(), 2)

    def test_datas_do_periodo(self):
        ps.garantir_periodos_anuais(self.fundo, [2022])
        p = self.anual(2022)
        self.assertEqual((p.data_referencia, p.data_anterior), (date(2022, 12, 31), date(2021, 12, 31)))

    def test_saldo_em_31_12_abre_o_ano_seguinte(self):
        self.assertEqual(ps.ano_do_saldo_inicial(date(2019, 12, 31)), 2020)
        self.assertEqual(ps.ano_do_saldo_inicial(date(2020, 6, 30)), 2020)


class CriacaoPelosLancamentosTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()

    def anos(self):
        return sorted(PeriodoDF.objects.filter(fundo=self.fundo, tipo_periodo="anual").values_list("ano", flat=True))

    def test_movimentacao_cria_o_periodo_do_ano(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))
        self.assertEqual(self.anos(), [ANO_ANTIGO])

    def test_movimentacao_no_ano_corrente_fica_em_andamento(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_CORRENTE, 1, 2), D("10"))
        p = PeriodoDF.objects.get(fundo=self.fundo, ano=ANO_CORRENTE)
        self.assertEqual(p.status, "em_andamento")

    def test_ano_historico_continua_historico_apos_lancar(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))
        self.assertEqual(PeriodoDF.objects.get(fundo=self.fundo, ano=ANO_ANTIGO).status, "historico")

    def test_editar_para_outro_ano_cria_o_periodo(self):
        mov = ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))
        ms.editar_movimentacao(mov, data=date(ANO_ANTIGO - 1, 5, 1))
        self.assertEqual(self.anos(), [ANO_ANTIGO - 1, ANO_ANTIGO])

    def test_saldo_inicial_em_31_12_cria_o_ano_seguinte(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2019, 12, 31), D("100"))
        self.assertEqual(self.anos(), [2020])

    def test_lancamento_recusado_nao_cria_periodo(self):
        with self.assertRaises(ValidationError):
            ms.criar_movimentacao(self.fundo, self.caixa, date(2019, 5, 1), D("0"))
        self.assertEqual(self.anos(), [])

    def test_importacao_de_movimentacoes_cria_todos_os_anos(self):
        arquivo = _planilha([
            [date(2021, 3, 1), "1.01.01", 10, "a", ""],
            [date(2022, 3, 1), "1.01.01", 10, "b", ""],
            [date(2023, 3, 1), "1.01.01", 10, "c", ""],
        ])
        criados: list = []
        importar_movimentacoes(self.fundo, "m.xlsx", arquivo, periodos_criados=criados)
        self.assertEqual(self.anos(), [2021, 2022, 2023])
        self.assertEqual(sorted(p.ano for p in criados), [2021, 2022, 2023])

    def test_importacao_recusada_nao_cria_periodo(self):
        from core.processing.importacao_movimentacao_service import ImportacaoInvalida

        arquivo = _planilha([[date(2021, 3, 1), "9.9.9", 10, "x", ""]])
        with self.assertRaises(ImportacaoInvalida):
            importar_movimentacoes(self.fundo, "m.xlsx", arquivo)
        self.assertEqual(self.anos(), [])

    def test_importacao_de_saldos_cria_o_periodo(self):
        arquivo = _planilha([[None, "1.01.01", 100, "", ""]])
        importar_saldos_iniciais(self.fundo, date(2020, 12, 31), "s.xlsx", arquivo)
        self.assertEqual(self.anos(), [2021])


class HistoricoNaoTravaTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()

    def test_importar_ano_antigo_depois_de_um_mais_novo_funciona(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO + 1, 5, 1), D("10"))
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))  # não trava
        self.assertEqual(ms.data_fechamento(self.fundo), None)

    def test_finalizada_continua_travando(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))
        PeriodoDF.objects.filter(fundo=self.fundo, ano=ANO_ANTIGO).update(status="finalizada")
        with self.assertRaises(ValidationError):
            ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 6, 1), D("10"))

    def test_status_automatico_nao_altera_historico(self):
        ps.garantir_periodos_anuais(self.fundo, [ANO_ANTIGO])
        periodo = PeriodoDF.objects.get(fundo=self.fundo, ano=ANO_ANTIGO)
        ps.atualizar_status_automatico(periodo)
        periodo.refresh_from_db()
        self.assertEqual(periodo.status, "historico")

    def test_historico_fora_dos_avisos_de_vencimento(self):
        from df.tasks import verificar_vencimentos_df  # noqa: F401 (o filtro é por status)

        ps.garantir_periodos_anuais(self.fundo, [ANO_ANTIGO])
        qs = PeriodoDF.objects.filter(status__in=["nao_iniciada", "em_andamento"], fundo=self.fundo)
        self.assertFalse(qs.exists())


class StatusManualTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        ps.garantir_periodos_anuais(self.fundo, [ANO_CORRENTE])
        self.periodo = PeriodoDF.objects.get(fundo=self.fundo, ano=ANO_CORRENTE)
        self.master = Usuario.objects.create_user(username="master_st", password="x")
        Membership.objects.create(usuario=self.master, empresa=self.fundo.empresa, role=Membership.Role.MASTER, is_active=True)
        self.url = reverse("atualizar_status_manual", args=[self.periodo.id])

    def post(self, user, **dados):
        self.client.force_login(user)
        return self.client.post(self.url, dados)

    def test_finalizar_nao_exige_motivo_e_e_registrado(self):
        r = self.post(self.master, status="finalizada")
        self.assertEqual(r.status_code, 200)
        reg = AlteracaoStatusPeriodo.objects.get()
        self.assertEqual((reg.status_anterior, reg.status_novo, reg.usuario), ("nao_iniciada", "finalizada", self.master))
        self.assertFalse(reg.e_reabertura)

    def test_reabrir_sem_motivo_e_recusado(self):
        self.post(self.master, status="finalizada")
        r = self.post(self.master, status="em_andamento", motivo="   ")
        self.assertEqual(r.status_code, 400)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.status, "finalizada")

    def test_reabrir_com_motivo_registra_quem_e_por_que(self):
        self.post(self.master, status="finalizada")
        r = self.post(self.master, status="em_andamento", motivo="Ajuste de ágio")
        self.assertEqual(r.status_code, 200)
        reg = AlteracaoStatusPeriodo.objects.first()
        self.assertTrue(reg.e_reabertura)
        self.assertEqual((reg.motivo, reg.usuario), ("Ajuste de ágio", self.master))

    def test_historico_e_um_status_manual_valido(self):
        self.assertEqual(self.post(self.master, status="historico").status_code, 200)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.status, "historico")

    def test_status_invalido(self):
        self.assertEqual(self.post(self.master, status="xyz").status_code, 400)

    def test_vencida_nao_vai_para_em_andamento(self):
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(status="vencida")
        self.assertEqual(self.post(self.master, status="em_andamento").status_code, 400)

    def test_visualizador_nao_altera(self):
        viewer = Usuario.objects.create_user(username="viewer_st", password="x")
        Membership.objects.create(usuario=viewer, empresa=self.fundo.empresa, role=Membership.Role.VIEWER, is_active=True)
        self.assertEqual(self.post(viewer, status="finalizada").status_code, 403)
        self.assertFalse(AlteracaoStatusPeriodo.objects.exists())


class GerarHistoricoTests(PlanoMinimoMixin, TestCase):
    def test_gera_sem_configuracao_com_vencimento_padrao(self):
        fundo = self.montar_fundo()
        user = Usuario.objects.create_user(username="master_gh", password="x")
        Membership.objects.create(usuario=user, empresa=fundo.empresa, role=Membership.Role.MASTER, is_active=True)
        self.client.force_login(user)
        r = self.client.post(
            reverse("gerar_periodos_historicos", args=[fundo.id]),
            {"ano_inicial": ANO_ANTIGO, "ano_final": ANO_ANTIGO + 1},
        )
        self.assertEqual(r.status_code, 302)
        periodos = PeriodoDF.objects.filter(fundo=fundo, tipo_periodo="anual")
        self.assertEqual(periodos.count(), 2)
        self.assertTrue(all(p.status == "historico" for p in periodos))
        self.assertTrue(all(p.data_vencimento == date(p.ano + 1, 3, 31) for p in periodos))


class TelasTests(PlanoMinimoMixin, TestCase):
    def setUp(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.master = Usuario.objects.create_user(username="master_tl", password="x")
        Membership.objects.create(usuario=self.master, empresa=self.fundo.empresa, role=Membership.Role.MASTER, is_active=True)
        self.client.force_login(self.master)

    def test_calendario_conta_historico_como_concluido(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))  # Histórico
        r = self.client.get(reverse("controle_emissoes"))
        self.assertEqual(r.context["metricas"]["finalizadas"], 1)
        self.assertEqual(r.context["metricas"]["quase_vencendo"], 0)
        self.assertContains(r, "Histórico")
        self.assertContains(r, 'id="modalReabrirPeriodo"')

    def test_tela_de_periodos_trava_exclusao_e_mostra_reabertura(self):
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_CORRENTE, 1, 5), D("10"))
        periodo = PeriodoDF.objects.get(fundo=self.fundo, ano=ANO_CORRENTE)
        r = self.client.get(reverse("gerenciar_periodos", args=[self.fundo.id]))
        self.assertContains(r, "seria recriado no próximo lançamento")
        self.assertNotContains(r, "abrirModalExcluir(%d" % periodo.id)

        self.client.post(reverse("atualizar_status_manual", args=[periodo.id]), {"status": "finalizada"})
        self.client.post(reverse("atualizar_status_manual", args=[periodo.id]), {"status": "em_andamento", "motivo": "ajuste"})
        r = self.client.get(reverse("gerenciar_periodos", args=[self.fundo.id]))
        self.assertContains(r, "Reaberto por")

    def test_painel_da_contabilidade_ignora_historico_nos_abertos(self):
        from usuarios.models import EscritorioContabil, MembroEscritorio, VinculoContabil

        escritorio = EscritorioContabil.objects.create(nome="Contab")
        VinculoContabil.objects.create(escritorio=escritorio, empresa=self.fundo.empresa)
        contador = Usuario.objects.create_user(username="contador_tl", password="x")
        MembroEscritorio.objects.create(usuario=contador, escritorio=escritorio)
        ms.criar_movimentacao(self.fundo, self.caixa, date(ANO_ANTIGO, 5, 1), D("10"))  # Histórico
        self.client.force_login(contador)
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_abertos"], 0)
