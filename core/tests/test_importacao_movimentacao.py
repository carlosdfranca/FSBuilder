import io
from datetime import date, datetime
from decimal import Decimal as D

import openpyxl
from django.test import TestCase

from core.processing.importacao_movimentacao_service import (
    ImportacaoInvalida, desfazer_lote, importar_movimentacoes, importar_saldos_iniciais,
)
from core.upload.movimentacao_parser import PlanilhaInvalida, ler_planilha
from df.models import LoteImportacaoMovimentacao, Movimentacao, PeriodoDF, SaldoInicial
from df.services import movimentacao_service as ms
from df.tests.test_saldo_calculo import PlanoMinimoMixin


def xlsx(linhas, cabecalho=("DATA", "CONTA", "VALOR", "HISTÓRICO")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(cabecalho))
    for linha in linhas:
        ws.append(list(linha))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(texto, codificacao="utf-8"):
    return texto.encode(codificacao)


class ParserTests(TestCase):
    def test_xlsx_com_valor_com_sinal(self):
        linhas, erros = ler_planilha("m.xlsx", xlsx([(datetime(2025, 3, 10), "1.01.01", 500.5, "Recebimento")]))
        self.assertEqual(erros, [])
        (l,) = linhas
        self.assertEqual((l.linha, l.data, l.conta, l.valor, l.historico), (2, date(2025, 3, 10), "1.01.01", D("500.50"), "Recebimento"))

    def test_csv_ponto_e_virgula_e_formato_brasileiro(self):
        txt = "Data;Conta;Valor;Histórico\n10/03/2025;1.01.01;1.234,56;Venda\n11/03/2025;3.01.01;(100,00);Estorno\n"
        linhas, erros = ler_planilha("m.csv", csv_bytes(txt))
        self.assertEqual(erros, [])
        self.assertEqual([l.valor for l in linhas], [D("1234.56"), D("-100.00")])
        self.assertEqual(linhas[0].data, date(2025, 3, 10))

    def test_csv_latin1(self):
        txt = "Data;Conta;Valor;Histórico\n10/03/2025;1.01.01;10,00;Ação\n"
        (l,), erros = ler_planilha("m.csv", csv_bytes(txt, "latin-1"))
        self.assertEqual(l.historico, "Ação")

    def test_milhares_com_ponto_sem_virgula(self):
        (l,), _ = ler_planilha("m.csv", csv_bytes("data;conta;valor\n01/01/2025;1.01.01;1.234.567\n"))
        self.assertEqual(l.valor, D("1234567.00"))

    def test_debito_e_credito(self):
        cab = ("Data", "Conta", "Débito", "Crédito")
        linhas, erros = ler_planilha("m.xlsx", xlsx([(datetime(2025, 1, 5), "1.01.01", 100, None), (datetime(2025, 1, 6), "3.01.01", None, 40)], cab))
        self.assertEqual(erros, [])
        self.assertEqual((linhas[0].debito, linhas[0].credito, linhas[0].valor), (D("100.00"), D("0.00"), None))
        self.assertEqual((linhas[1].debito, linhas[1].credito), (D("0.00"), D("40.00")))

    def test_conta_numerica_da_celula(self):
        (l,), _ = ler_planilha("m.xlsx", xlsx([(datetime(2025, 1, 5), 1.0, 10, "")]))
        self.assertEqual(l.conta, "1")

    def test_erros_por_linha(self):
        cab = ("DATA", "CONTA", "VALOR")
        arq = xlsx([
            ("31/02/2025", "1.01.01", 10),            # linha 2: data impossível
            (datetime(2025, 1, 5), "", 10),           # linha 3: sem conta
            (datetime(2025, 1, 5), "1.01.01", None),  # linha 4: sem valor
            (datetime(2025, 1, 5), "1.01.01", "abc"), # linha 5: valor inválido
            (datetime(2025, 1, 5), "1.01.01", 10),    # linha 6: ok
        ], cab)
        linhas, erros = ler_planilha("m.xlsx", arq)
        self.assertEqual([e["linha"] for e in erros], [2, 3, 4, 5])
        self.assertEqual([l.linha for l in linhas], [6])

    def test_debito_e_credito_juntos_na_mesma_linha(self):
        cab = ("Data", "Conta", "Débito", "Crédito")
        _, erros = ler_planilha("m.xlsx", xlsx([(datetime(2025, 1, 5), "1.01.01", 10, 10)], cab))
        self.assertEqual(len(erros), 1)

    def test_colunas_ausentes(self):
        with self.assertRaises(PlanilhaInvalida) as ctx:
            ler_planilha("m.xlsx", xlsx([(1, 2)], ("FOO", "BAR")))
        self.assertIn("DATA", ctx.exception.mensagem)
        self.assertIn("CONTA", ctx.exception.mensagem)

    def test_arquivo_invalido_e_vazio(self):
        with self.assertRaises(PlanilhaInvalida):
            ler_planilha("m.xlsx", b"isto nao e um xlsx")
        with self.assertRaises(PlanilhaInvalida):
            ler_planilha("m.csv", b"")

    def test_linhas_em_branco_sao_ignoradas(self):
        linhas, erros = ler_planilha("m.xlsx", xlsx([(None, None, None, None), (datetime(2025, 1, 5), "1.01.01", 1, "")]))
        self.assertEqual((len(linhas), erros), (1, []))

    def test_saldo_inicial_nao_exige_data(self):
        linhas, erros = ler_planilha("s.csv", csv_bytes("conta;valor\n1.01.01;100,00\n"), exigir_data=False)
        self.assertEqual((linhas[0].data, linhas[0].valor, erros), (None, D("100.00"), []))


class ImportacaoMixin(PlanoMinimoMixin):
    def montar(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2025, 12, 31),
            data_anterior=date(2024, 12, 31), status="nao_iniciada",
        )

    def importar(self, linhas, cabecalho=("DATA", "CONTA", "VALOR", "HISTÓRICO"), nome="mov.xlsx"):
        return importar_movimentacoes(self.fundo, nome, xlsx(linhas, cabecalho))


class ImportarMovimentacoesTests(ImportacaoMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_importa_cria_lote_e_movimentacoes(self):
        lote = self.importar([
            (datetime(2025, 3, 10), "1.01.01", 500, "Recebimento"),
            (datetime(2025, 3, 10), "3.01.01", 500, "Receita"),
        ])
        self.assertEqual((lote.qtd_linhas, lote.valor_total), (2, D("1000.00")))
        movs = Movimentacao.objects.filter(lote=lote)
        self.assertEqual(movs.count(), 2)
        m = movs.get(conta=self.caixa)
        self.assertEqual((m.origem, m.empresa_id, m.fundo_id, m.valor, m.historico), ("importado", self.fundo.empresa_id, self.fundo.id, D("500.00"), "Recebimento"))

    def test_atualiza_o_status_do_periodo(self):
        self.importar([(datetime(2025, 3, 10), "1.01.01", 1, "")])
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.status, "em_andamento")

    def test_debito_credito_conforme_a_natureza(self):
        cab = ("Data", "Conta", "Débito", "Crédito")
        self.importar([
            (datetime(2025, 3, 10), "1.01.01", 100, None),     # ativo: débito aumenta
            (datetime(2025, 3, 11), "1.01.01", None, 30),      # ativo: crédito diminui
            (datetime(2025, 3, 12), "2.03.01", None, 70),      # PL: crédito aumenta
            (datetime(2025, 3, 13), "3.01.02", 20, None),      # resultado: débito (despesa) diminui
        ], cab)
        valores = {m.conta.codigo + str(m.data.day): m.valor for m in Movimentacao.objects.all()}
        self.assertEqual(valores, {"1.01.0110": D("100.00"), "1.01.0111": D("-30.00"), "2.03.0112": D("70.00"), "3.01.0213": D("-20.00")})

    def test_tudo_ou_nada_uma_linha_ruim_recusa_o_arquivo(self):
        with self.assertRaises(ImportacaoInvalida) as ctx:
            self.importar([
                (datetime(2025, 3, 10), "1.01.01", 500, ""),
                (datetime(2025, 3, 10), "9.99.99", 500, ""),
            ])
        self.assertEqual(ctx.exception.erros, [{"linha": 3, "mensagem": "conta 9.99.99 não existe no plano de contas"}])
        self.assertEqual(Movimentacao.objects.count(), 0)
        self.assertEqual(LoteImportacaoMovimentacao.objects.count(), 0)

    def test_erros_de_negocio_vem_com_a_linha(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2025, 1, 1), D("10"))
        with self.assertRaises(ImportacaoInvalida) as ctx:
            self.importar([
                (datetime(2025, 1, 1), "1.01.01", 5, ""),    # linha 2: igual à data-base do saldo inicial
                (datetime(2025, 3, 1), "1.01", 5, ""),       # linha 3: conta sintética
                (datetime(2025, 3, 1), "3.01.01", 0, ""),    # linha 4: valor zero
            ])
        self.assertEqual([e["linha"] for e in ctx.exception.erros], [2, 3, 4])
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_periodo_finalizado_recusa(self):
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(status="finalizada")
        with self.assertRaises(ImportacaoInvalida) as ctx:
            self.importar([(datetime(2025, 6, 1), "1.01.01", 5, "")])
        self.assertIn("finalizado", ctx.exception.erros[0]["mensagem"])

    def test_arquivo_repetido_e_recusado_e_pode_voltar_apos_desfazer(self):
        linhas = [(datetime(2025, 3, 10), "1.01.01", 500, "")]
        lote = self.importar(linhas)
        with self.assertRaises(ImportacaoInvalida) as ctx:
            self.importar(linhas)
        self.assertIn("já foi importado", ctx.exception.erros[0]["mensagem"])
        desfazer_lote(lote)
        self.importar(linhas)
        self.assertEqual(Movimentacao.objects.count(), 1)

    def test_o_mesmo_arquivo_vale_para_outro_fundo(self):
        linhas = [(datetime(2025, 3, 10), "1.01.01", 500, "")]
        self.importar(linhas)
        outro = self.montar_fundo("Fundo B", "22.222.222/0001-22")
        importar_movimentacoes(outro, "mov.xlsx", xlsx(linhas))
        self.assertEqual(Movimentacao.objects.count(), 2)

    def test_arquivo_sem_linhas(self):
        with self.assertRaises(ImportacaoInvalida):
            self.importar([])

    def test_erro_de_estrutura_vira_importacao_invalida(self):
        with self.assertRaises(ImportacaoInvalida) as ctx:
            importar_movimentacoes(self.fundo, "x.xlsx", xlsx([(1, 2)], ("FOO", "BAR")))
        self.assertIn("Colunas ausentes", ctx.exception.erros[0]["mensagem"])


class DesfazerLoteTests(ImportacaoMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_desfazer_remove_so_o_lote_e_volta_o_status(self):
        manual = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 2, 1), D("1"))
        lote = self.importar([(datetime(2025, 3, 10), "1.01.01", 500, ""), (datetime(2025, 3, 11), "3.01.01", 5, "")])
        self.assertEqual(desfazer_lote(lote), 2)
        self.assertEqual(list(Movimentacao.objects.all()), [manual])
        self.assertFalse(LoteImportacaoMovimentacao.objects.exists())

    def test_desfazer_unico_lote_devolve_nao_iniciada(self):
        lote = self.importar([(datetime(2025, 3, 10), "1.01.01", 500, "")])
        desfazer_lote(lote)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.status, "nao_iniciada")

    def test_nao_desfaz_lote_em_periodo_finalizado(self):
        lote = self.importar([(datetime(2025, 3, 10), "1.01.01", 500, "")])
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(status="finalizada")
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            desfazer_lote(lote)
        self.assertEqual(Movimentacao.objects.count(), 1)


class ImportarSaldosIniciaisTests(ImportacaoMixin, TestCase):
    def setUp(self):
        self.montar()
        self.base = date(2024, 12, 31)

    def importar_saldos(self, texto):
        return importar_saldos_iniciais(self.fundo, self.base, "s.csv", csv_bytes(texto))

    def test_grava_saldos(self):
        n = self.importar_saldos("conta;valor\n1.01.01;1.000,00\n2.03.01;1.000,00\n")
        self.assertEqual(n, 2)
        self.assertEqual(SaldoInicial.objects.get(conta=self.caixa).valor, D("1000.00"))
        self.assertEqual(SaldoInicial.objects.get(conta=self.caixa).data_base, self.base)

    def test_atualiza_existente_sem_duplicar(self):
        self.importar_saldos("conta;valor\n1.01.01;10,00\n")
        self.importar_saldos("conta;valor\n1.01.01;20,00\n")
        self.assertEqual(SaldoInicial.objects.filter(conta=self.caixa).count(), 1)
        self.assertEqual(SaldoInicial.objects.get(conta=self.caixa).valor, D("20.00"))

    def test_conta_de_resultado_inexistente_e_repetida(self):
        with self.assertRaises(ImportacaoInvalida) as ctx:
            self.importar_saldos("conta;valor\n3.01.01;10,00\n9.99;10,00\n1.01.01;1,00\n1.01.01;2,00\n")
        self.assertEqual([e["linha"] for e in ctx.exception.erros], [2, 3, 5])
        self.assertEqual(SaldoInicial.objects.count(), 0)

    def test_periodo_finalizado_recusa(self):
        PeriodoDF.objects.filter(pk=self.periodo.pk).update(status="finalizada")
        with self.assertRaises(ImportacaoInvalida):
            self.importar_saldos("conta;valor\n1.01.01;10,00\n")

    def test_saldo_existente_em_periodo_finalizado_nao_muda(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2024, 12, 31), D("10"))
        PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.fundo.empresa, tipo_periodo="anual", ano=2024,
            data_vencimento=date(2025, 3, 31), data_referencia=date(2024, 12, 31), status="finalizada",
        )
        with self.assertRaises(ImportacaoInvalida):
            importar_saldos_iniciais(self.fundo, date(2025, 1, 1), "s.csv", csv_bytes("conta;valor\n1.01.01;99,00\n"))
        self.assertEqual(SaldoInicial.objects.get(conta=self.caixa).valor, D("10.00"))
