"""Exportações Excel e Word da DF geradas pela árvore do plano de contas."""
import io
from datetime import date
from decimal import Decimal as D

import openpyxl
from django.test import TestCase
from django.urls import reverse
from docx import Document

from core.tests.test_dfc_arvore import DfcMixin
from df.models import HistoricoEmissaoDF, PeriodoDF
from usuarios.models import Membership, Usuario

FIM_2025, FIM_2024 = date(2025, 12, 31), date(2024, 12, 31)


class ExportacaoDfTests(DfcMixin, TestCase):
    def setUp(self):
        self.montar_dfc()
        self.cenario_2025()
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=FIM_2025, data_anterior=FIM_2024,
            status="em_andamento",
        )
        self.user = Usuario.objects.create_user(username="viewer_exp", password="x")
        Membership.objects.create(
            usuario=self.user, empresa=self.fundo.empresa, role=Membership.Role.VIEWER, is_active=True
        )
        self.client.force_login(self.user)

    def _excel(self, query=""):
        r = self.client.get(reverse("exportar_dfs_excel", args=[self.periodo.id]) + query)
        self.assertEqual(r.status_code, 200)
        return r, openpyxl.load_workbook(io.BytesIO(r.content))

    @staticmethod
    def _linhas(ws):
        """{descrição: (valor da col B, valor da col C)} das linhas com descrição."""
        return {
            str(row[0].value).strip(): (row[1].value, row[2].value)
            for row in ws.iter_rows(min_row=11)
            if row[0].value and not str(row[0].value).startswith("As notas")
        }

    def test_excel_tem_as_quatro_abas(self):
        _, wb = self._excel()
        self.assertEqual(wb.sheetnames, ["DPF", "DRE", "DMPL", "DFC"])

    def test_excel_dre_traz_resultado_da_arvore(self):
        _, wb = self._excel("?nivel=3")
        linhas = self._linhas(wb["DRE"])
        resultado = linhas["Resultado do exercício"]
        self.assertEqual(resultado[0], "-")  # 200 reais = 0 em R$ mil (arredondado)
        self.assertTrue(any("RESULTADO" in k.upper() for k in linhas))

    def test_excel_dpf_fecha_e_tem_secoes_e_totais(self):
        _, wb = self._excel()
        ws = wb["DPF"]
        textos = [str(c.value) for c in ws["A"] if c.value]
        for esperado in ("Ativo", "Passivo", "Patrimônio líquido", "Total do Ativo",
                         "Total do Passivo", "Total do Patrimônio Líquido", "Total do Passivo e do PL"):
            self.assertIn(esperado, textos)
        self.assertEqual(ws["B10"].value, "31/12/2025")

    def test_excel_dfc_tem_linhas_e_caixa(self):
        _, wb = self._excel()
        textos = [str(c.value) for c in wb["DFC"]["A"] if c.value]
        for esperado in ("Fluxo de caixa das atividades operacionais", "Resultado líquido",
                         "Variação no caixa e equivalentes de caixa",
                         "Caixa e equivalentes de caixa no final do período"):
            self.assertIn(esperado, textos)

    def test_excel_nome_do_arquivo_e_historico(self):
        r, _ = self._excel()
        self.assertIn("DFs_20251231_20241231_Fundo_A.xlsx", r["Content-Disposition"])
        emissao = HistoricoEmissaoDF.objects.get(periodo_df=self.periodo)
        self.assertEqual((emissao.tipo_exportacao, emissao.data_referencia_df), ("excel", FIM_2025))

    def test_excel_respeita_o_nivel(self):
        _, wb2 = self._excel("?nivel=2")
        _, wb4 = self._excel("?nivel=4")
        self.assertLess(wb2["DPF"].max_row, wb4["DPF"].max_row)

    def test_word_gera_documento_valido(self):
        r = self.client.get(reverse("exportar_dfs_docx", args=[self.periodo.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("wordprocessingml", r["Content-Type"])
        texto = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
        self.assertIn("31 de dezembro de 2025", texto)
        self.assertIn("31 de dezembro de 2024", texto)
        emissao = HistoricoEmissaoDF.objects.get(periodo_df=self.periodo)
        self.assertEqual(emissao.tipo_exportacao, "word")

    def test_contexto_do_word_tem_linhas_formatadas(self):
        from core.export.df_docx import build_docx_context
        from core.processing.df_service import gerar_dfs

        ctx = build_docx_context(self.fundo, gerar_dfs(self.periodo, 3))
        self.assertTrue(ctx["ativo_rows"])
        self.assertTrue(ctx["pl_rows"])
        self.assertTrue(ctx["dfc_rows"])
        linha = ctx["ativo_rows"][0]
        self.assertEqual(set(linha), {"descricao", "atual", "anterior", "perc_atual", "perc_anterior", "tipo", "nivel"})
        self.assertEqual(ctx["data_atual"], "31/12/2025")
        self.assertEqual(ctx["fundo_nome"], "FUNDO A")

    def test_exportacao_exige_acesso_a_empresa(self):
        outro = Usuario.objects.create_user(username="sem_acesso", password="x")
        self.client.force_login(outro)
        r = self.client.get(reverse("exportar_dfs_excel", args=[self.periodo.id]))
        self.assertNotEqual(r.status_code, 200)
