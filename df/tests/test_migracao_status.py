"""Migration 0035: com a saída do balancete, o status dos períodos volta ao que os dados reais dizem."""
import importlib
from datetime import date, timedelta

from django.apps import apps
from django.test import TestCase

from df.models import Fundo, HistoricoEmissaoDF, MecItem, PeriodoDF
from usuarios.models import Empresa

migracao = importlib.import_module("df.migrations.0035_recalcular_status_periodos")


class RecalcularStatusTests(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa A")
        self.fundo = Fundo.objects.create(empresa=self.empresa, nome="Fundo A", cnpj="11.111.111/0001-11", tipo_fundo="FIDC")
        hoje = date.today()
        self.futuro, self.passado = hoje + timedelta(days=60), hoje - timedelta(days=60)
        self.n = 0

    def periodo(self, status, vencimento):
        self.n += 1
        return PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa, tipo_periodo="transitoria", ano=2000 + self.n,
            data_vencimento=vencimento, status=status, criado_manualmente=True,
        )

    def status(self, periodo):
        return PeriodoDF.objects.get(pk=periodo.pk).status

    def rodar(self):
        migracao.recalcular(apps, None)

    def test_em_andamento_so_por_causa_do_balancete_volta_ao_prazo(self):
        no_prazo = self.periodo("em_andamento", self.futuro)
        atrasado = self.periodo("em_andamento", self.passado)
        self.rodar()
        self.assertEqual((self.status(no_prazo), self.status(atrasado)), ("nao_iniciada", "vencida"))

    def test_periodo_com_mec_continua_em_andamento(self):
        p = self.periodo("em_andamento", self.passado)
        MecItem.objects.create(fundo=self.fundo, periodo_df=p, data_posicao=date(2024, 12, 31))
        self.rodar()
        self.assertEqual(self.status(p), "em_andamento")

    def test_finalizada_e_mantida(self):
        p = self.periodo("finalizada", self.passado)
        self.rodar()
        self.assertEqual(self.status(p), "finalizada")

    def test_periodo_com_emissao_registrada_vira_finalizado(self):
        p = self.periodo("em_andamento", self.futuro)
        HistoricoEmissaoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa, periodo_df=p, data_referencia_df=date(2024, 12, 31),
            tipo_exportacao="excel",
        )
        self.rodar()
        self.assertEqual(self.status(p), "finalizada")

    def test_nao_iniciada_vencida_e_a_vencer_seguem_o_prazo(self):
        a_vencer = self.periodo("vencida", self.futuro)       # status velho, prazo ainda não passou
        vencida = self.periodo("nao_iniciada", self.passado)  # prazo passou e ninguém atualizou
        self.rodar()
        self.assertEqual((self.status(a_vencer), self.status(vencida)), ("nao_iniciada", "vencida"))

    def test_idempotente(self):
        p = self.periodo("em_andamento", self.passado)
        self.rodar()
        self.rodar()
        self.assertEqual(self.status(p), "vencida")

    def test_nao_mexe_nas_datas_nem_nas_movimentacoes(self):
        p = self.periodo("em_andamento", self.passado)
        PeriodoDF.objects.filter(pk=p.pk).update(data_referencia=date(2024, 12, 31), data_anterior=date(2023, 12, 31))
        self.rodar()
        p.refresh_from_db()
        self.assertEqual((p.data_referencia, p.data_anterior), (date(2024, 12, 31), date(2023, 12, 31)))
