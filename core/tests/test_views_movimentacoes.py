import io
from datetime import date, datetime
from decimal import Decimal as D

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.tests.test_importacao_movimentacao import xlsx
from df.models import Conta, LoteImportacaoMovimentacao, Movimentacao, PeriodoDF, SaldoInicial
from df.services import movimentacao_service as ms
from df.tests.test_saldo_calculo import PlanoMinimoMixin
from usuarios.models import Empresa, Membership, Usuario


def usuario(nome, empresa, role):
    u = Usuario.objects.create_user(username=nome)  # sem senha: force_login não precisa
    Membership.objects.create(usuario=u, empresa=empresa, role=role, is_active=True)
    return u


class ViewsMixin(PlanoMinimoMixin):
    def montar(self):
        self.montar_plano()
        self.fundo = self.montar_fundo()
        self.empresa = self.fundo.empresa
        self.master = usuario("master_mv", self.empresa, Membership.Role.MASTER)
        self.admin_emp = usuario("admin_mv", self.empresa, Membership.Role.ADMIN)
        self.member = usuario("member_mv", self.empresa, Membership.Role.MEMBER)
        self.viewer = usuario("viewer_mv", self.empresa, Membership.Role.VIEWER)
        self.plataforma = Usuario.objects.create_user(
            username="plat_mv", global_role=Usuario.GlobalRole.PLATFORM_ADMIN
        )
        outra = Empresa.objects.create(nome="Empresa B")
        self.outra_empresa = outra
        self.estranho = usuario("estranho_mv", outra, Membership.Role.MASTER)

    def url(self, nome, *args):
        return reverse(nome, args=[self.fundo.id, *args])

    def como(self, user):
        self.client.force_login(user)

    def periodo(self, status="nao_iniciada"):
        return PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2025, 12, 31),
            data_anterior=date(2024, 12, 31), status=status,
        )

    def form(self, **extra):
        dados = {"data": "2025-03-10", "conta_id": self.caixa.id, "valor": "500,00", "historico": "Teste", "documento": "NF 1"}
        dados.update(extra)
        return dados


class BuscaDeContasTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.url_busca = reverse("contas_buscar")

    def test_so_analiticas_ativas(self):
        self.como(self.viewer)
        codigos = [c["codigo"] for c in self.client.get(self.url_busca).json()["contas"]]
        self.assertIn("1.01.01", codigos)
        self.assertNotIn("1.01", codigos)  # sintética
        self.assertNotIn("1", codigos)

    def test_conta_inativa_nao_aparece(self):
        self.caixa.is_ativa = False
        self.caixa.save()
        self.como(self.viewer)
        codigos = [c["codigo"] for c in self.client.get(self.url_busca, {"q": "caixa"}).json()["contas"]]
        self.assertEqual(codigos, [])

    def test_patrimonial_exclui_resultado(self):
        self.como(self.viewer)
        todos = {c["codigo"] for c in self.client.get(self.url_busca).json()["contas"]}
        patrimoniais = {c["codigo"] for c in self.client.get(self.url_busca, {"patrimonial": "1"}).json()["contas"]}
        self.assertIn("3.01.01", todos)
        self.assertNotIn("3.01.01", patrimoniais)
        self.assertIn("2.03.01", patrimoniais)

    def test_busca_por_codigo_e_por_descricao(self):
        self.como(self.viewer)
        por_codigo = [c["codigo"] for c in self.client.get(self.url_busca, {"q": "2.03.0"}).json()["contas"]]
        self.assertEqual(por_codigo, ["2.03.01", "2.03.02", "2.03.03"])
        por_nome = [c["codigo"] for c in self.client.get(self.url_busca, {"q": "recei"}).json()["contas"]]
        self.assertEqual(por_nome, ["3.01.01"])

    def test_limite_de_resultados(self):
        pai = Conta.objects.get(codigo="3.01")
        for i in range(40):
            Conta.objects.create(codigo=f"3.01.9{i:02d}", descricao=f"Extra {i}", tipo="A", nivel=3, natureza=4, conta_superior=pai)
        self.como(self.viewer)
        self.assertEqual(len(self.client.get(self.url_busca, {"q": "extra"}).json()["contas"]), 30)

    def test_exige_acesso_a_uma_empresa(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url_busca).status_code, 302)


class ListaMovimentacoesTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.m1 = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("500"), historico="Recebimento cliente")
        self.m2 = ms.criar_movimentacao(self.fundo, self.receita, date(2025, 8, 1), D("700"))
        self.antiga = ms.criar_movimentacao(self.fundo, self.caixa, date(2024, 5, 1), D("10"))

    def pagina(self, **params):
        return self.client.get(self.url("movimentacoes_fundo"), params)

    def test_todos_os_perfis_da_empresa_veem(self):
        for u in (self.master, self.admin_emp, self.member, self.viewer, self.plataforma):
            self.como(u)
            if u is self.plataforma:
                s = self.client.session
                s["empresa_ativa_id"] = self.empresa.id
                s.save()
            self.assertEqual(self.pagina().status_code, 200, u.username)

    def test_pode_lancar_so_para_master_e_admin(self):
        for u, esperado in ((self.master, True), (self.admin_emp, True), (self.member, False), (self.viewer, False)):
            self.como(u)
            self.assertIs(self.pagina().context["pode_lancar"], esperado, u.username)

    def test_filtro_padrao_e_o_ano_da_ultima_movimentacao(self):
        self.como(self.viewer)
        r = self.pagina()
        self.assertEqual(r.context["data_ini"], date(2025, 1, 1))
        self.assertEqual({m["id"] for m in r.context["movimentacoes"]}, {self.m1.id, self.m2.id})

    def test_filtros_de_data_e_busca(self):
        self.como(self.viewer)
        r = self.pagina(data_ini="2024-01-01", data_fim="2024-12-31")
        self.assertEqual([m["id"] for m in r.context["movimentacoes"]], [self.antiga.id])
        r = self.pagina(conta="receita")
        self.assertEqual([m["id"] for m in r.context["movimentacoes"]], [self.m2.id])
        r = self.pagina(conta="cliente")  # busca no histórico
        self.assertEqual([m["id"] for m in r.context["movimentacoes"]], [self.m1.id])

    def test_nao_ve_fundo_de_outra_empresa(self):
        self.como(self.estranho)
        self.assertEqual(self.pagina().status_code, 404)

    def test_lancamento_em_periodo_finalizado_vem_nao_editavel(self):
        self.periodo(status="finalizada")
        self.como(self.master)
        linhas = {m["id"]: m for m in self.pagina(data_ini="2024-01-01", data_fim="2025-12-31").context["movimentacoes"]}
        self.assertFalse(linhas[self.m1.id]["editavel"])


class CriarEditarExcluirTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.como(self.master)

    def test_criar(self):
        r = self.client.post(self.url("movimentacao_criar"), self.form(valor="1.234,56"))
        self.assertEqual(r.status_code, 200)
        m = Movimentacao.objects.get()
        self.assertEqual((m.valor, m.data, m.conta, m.origem, m.criado_por, m.empresa), (D("1234.56"), date(2025, 3, 10), self.caixa, "manual", self.master, self.empresa))
        self.assertEqual(r.json()["item"]["valor"], "1234.56")

    def test_valor_negativo(self):
        self.client.post(self.url("movimentacao_criar"), self.form(conta_id=self.despesa.id, valor="-200,00"))
        self.assertEqual(Movimentacao.objects.get().valor, D("-200.00"))

    def test_erros_de_formulario(self):
        for campo, dados in (("data", {"data": "10/03/2025"}), ("conta", {"conta_id": ""}), ("valor", {"valor": "abc"})):
            r = self.client.post(self.url("movimentacao_criar"), self.form(**dados))
            self.assertEqual(r.status_code, 400, campo)
            self.assertIn(campo, r.json()["errors"])
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_regras_de_negocio_viram_erro_400(self):
        sintetica = Conta.objects.get(codigo="1.01")
        r = self.client.post(self.url("movimentacao_criar"), self.form(conta_id=sintetica.id))
        self.assertEqual(r.status_code, 400)
        self.assertIn("sintética", r.json()["errors"]["__all__"][0])
        self.periodo(status="finalizada")
        r = self.client.post(self.url("movimentacao_criar"), self.form())
        self.assertEqual(r.status_code, 400)
        self.assertIn("finalizado", r.json()["errors"]["__all__"][0])
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_perfis_sem_permissao(self):
        for u in (self.member, self.viewer):
            self.como(u)
            self.assertEqual(self.client.post(self.url("movimentacao_criar"), self.form()).status_code, 403, u.username)
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_admin_da_empresa_e_admin_da_plataforma_podem(self):
        self.como(self.admin_emp)
        self.assertEqual(self.client.post(self.url("movimentacao_criar"), self.form()).status_code, 200)
        self.como(self.plataforma)
        s = self.client.session
        s["empresa_ativa_id"] = self.empresa.id
        s.save()
        self.assertEqual(self.client.post(self.url("movimentacao_criar"), self.form(documento="NF 2")).status_code, 200)

    def test_nao_lanca_em_fundo_de_outra_empresa(self):
        self.como(self.estranho)
        self.assertEqual(self.client.post(self.url("movimentacao_criar"), self.form()).status_code, 404)

    def test_editar(self):
        m = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("10"))
        r = self.client.post(self.url("movimentacao_editar", m.id), self.form(valor="99,90", historico="Corrigido"))
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual((m.valor, m.historico, m.atualizado_por), (D("99.90"), "Corrigido", self.master))

    def test_nao_edita_nem_exclui_importada(self):
        lote = LoteImportacaoMovimentacao.objects.create(fundo=self.fundo, nome_arquivo="a.xlsx", sha256="x", qtd_linhas=1)
        m = Movimentacao.objects.create(fundo=self.fundo, empresa=self.empresa, conta=self.caixa, data=date(2025, 1, 5),
                                        valor=D("1"), origem="importado", lote=lote)
        self.assertEqual(self.client.post(self.url("movimentacao_editar", m.id), self.form()).status_code, 400)
        self.assertEqual(self.client.post(self.url("movimentacao_excluir", m.id)).status_code, 400)
        self.assertTrue(Movimentacao.objects.filter(pk=m.pk).exists())

    def test_nao_edita_em_periodo_finalizado(self):
        m = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("10"))
        self.periodo(status="finalizada")
        r = self.client.post(self.url("movimentacao_editar", m.id), self.form(valor="1,00"))
        self.assertEqual(r.status_code, 400)
        m.refresh_from_db()
        self.assertEqual(m.valor, D("10.00"))

    def test_excluir(self):
        m = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("10"))
        self.assertEqual(self.client.post(self.url("movimentacao_excluir", m.id)).status_code, 200)
        self.assertFalse(Movimentacao.objects.exists())

    def test_excluir_exige_permissao_e_so_aceita_post(self):
        m = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("10"))
        self.assertEqual(self.client.get(self.url("movimentacao_excluir", m.id)).status_code, 405)
        self.como(self.viewer)
        self.assertEqual(self.client.post(self.url("movimentacao_excluir", m.id)).status_code, 403)
        self.assertTrue(Movimentacao.objects.exists())


class ImportarViewTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.como(self.master)

    def enviar(self, linhas, nome="mov.xlsx", cabecalho=("DATA", "CONTA", "VALOR", "HISTÓRICO")):
        arq = SimpleUploadedFile(nome, xlsx(linhas, cabecalho))
        return self.client.post(self.url("movimentacoes_importar"), {"arquivo": arq})

    def test_pagina(self):
        r = self.client.get(self.url("movimentacoes_importar"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context["pode_lancar"])
        self.como(self.viewer)
        self.assertFalse(self.client.get(self.url("movimentacoes_importar")).context["pode_lancar"])

    def test_importa(self):
        r = self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, "x"), (datetime(2025, 3, 10), "3.01.01", 500, "y")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["qtd"], 2)
        self.assertEqual(Movimentacao.objects.filter(origem="importado", criado_por=self.master).count(), 2)

    def test_erros_por_linha_e_nada_gravado(self):
        r = self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, ""), (datetime(2025, 3, 10), "9.99", 5, "")])
        self.assertEqual(r.status_code, 400)
        j = r.json()
        self.assertFalse(j["ok"])
        self.assertEqual((j["erros"][0]["linha"], j["total"]), (3, 1))
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_sem_arquivo(self):
        self.assertEqual(self.client.post(self.url("movimentacoes_importar"), {}).status_code, 400)

    def test_arquivo_repetido(self):
        linhas = [(datetime(2025, 3, 10), "1.01.01", 500, "")]
        self.assertEqual(self.enviar(linhas).status_code, 200)
        r = self.enviar(linhas)
        self.assertEqual(r.status_code, 400)
        self.assertIn("já foi importado", r.json()["erros"][0]["mensagem"])

    def test_viewer_nao_importa(self):
        self.como(self.viewer)
        self.assertEqual(self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, "")]).status_code, 403)
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_importada_nao_aparece_como_editavel_mas_o_lote_se_desfaz(self):
        self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, "")])
        lote = LoteImportacaoMovimentacao.objects.get()
        r = self.client.post(self.url("movimentacoes_lote_desfazer", lote.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual((Movimentacao.objects.count(), LoteImportacaoMovimentacao.objects.count()), (0, 0))

    def test_desfazer_em_periodo_finalizado_e_recusado(self):
        self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, "")])
        lote = LoteImportacaoMovimentacao.objects.get()
        self.periodo(status="finalizada")
        r = self.client.post(self.url("movimentacoes_lote_desfazer", lote.id))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Movimentacao.objects.count(), 1)
        lista = self.client.get(self.url("movimentacoes_importar")).context["lotes"]
        self.assertFalse(lista[0]["pode_desfazer"])

    def test_desfazer_exige_permissao(self):
        self.enviar([(datetime(2025, 3, 10), "1.01.01", 500, "")])
        lote = LoteImportacaoMovimentacao.objects.get()
        self.como(self.member)
        self.assertEqual(self.client.post(self.url("movimentacoes_lote_desfazer", lote.id)).status_code, 403)

    def test_modelo_xlsx(self):
        self.como(self.viewer)
        r = self.client.get(self.url("movimentacoes_modelo"))
        self.assertEqual(r.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Movimentações", "Plano de contas"])
        self.assertEqual([c.value for c in wb["Movimentações"][1]], ["DATA", "CONTA", "VALOR", "HISTÓRICO", "DOCUMENTO"])
        codigos = [row[0].value for row in wb["Plano de contas"].iter_rows(min_row=2)]
        self.assertIn("1.01.01", codigos)
        self.assertNotIn("1.01", codigos)  # sintéticas ficam de fora

    def test_o_modelo_baixado_pode_ser_importado(self):
        # o arquivo de exemplo do modelo precisa ser aceito pelo importador
        self.como(self.master)
        wb = openpyxl.load_workbook(io.BytesIO(self.client.get(self.url("movimentacoes_modelo")).content))
        ws = wb["Movimentações"]
        for linha in ws.iter_rows(min_row=2, max_row=3):
            linha[1].value = {"1.01.01.01.01": "1.01.01", "3.01.01.01.01.01": "3.01.01"}[linha[1].value]
        saida = io.BytesIO()
        wb.save(saida)
        r = self.client.post(self.url("movimentacoes_importar"), {"arquivo": SimpleUploadedFile("m.xlsx", saida.getvalue())})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["qtd"], 2)


class SaldosIniciaisViewTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.como(self.master)

    def salvar(self, **extra):
        dados = {"conta_id": self.caixa.id, "data_base": "2024-12-31", "valor": "1.000,00"}
        dados.update(extra)
        return self.client.post(self.url("saldo_inicial_salvar"), dados)

    def test_pagina(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2024, 12, 31), D("1000"))
        r = self.client.get(self.url("saldos_iniciais_fundo"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["conta"] for s in r.context["saldos"]], ["1.01.01"])
        self.como(self.estranho)
        self.assertEqual(self.client.get(self.url("saldos_iniciais_fundo")).status_code, 404)

    def test_salvar_cria_e_atualiza(self):
        self.assertEqual(self.salvar().status_code, 200)
        self.assertEqual(self.salvar(valor="2.500,00").status_code, 200)
        s = SaldoInicial.objects.get()
        self.assertEqual((s.valor, s.data_base, s.criado_por), (D("2500.00"), date(2024, 12, 31), self.master))

    def test_erros(self):
        self.assertEqual(self.salvar(conta_id=self.receita.id).status_code, 400)  # resultado
        self.assertEqual(self.salvar(conta_id="").status_code, 400)
        self.assertEqual(self.salvar(data_base="31/12/2024").status_code, 400)
        self.assertEqual(self.salvar(valor="").status_code, 400)
        self.assertEqual(SaldoInicial.objects.count(), 0)

    def test_periodo_finalizado_recusa(self):
        self.periodo(status="finalizada")
        r = self.salvar(data_base="2025-01-01")
        self.assertEqual(r.status_code, 400)

    def test_excluir(self):
        s = ms.definir_saldo_inicial(self.fundo, self.caixa, date(2024, 12, 31), D("1000"))
        self.assertEqual(self.client.post(self.url("saldo_inicial_excluir", s.id)).status_code, 200)
        self.assertFalse(SaldoInicial.objects.exists())

    def test_sem_permissao(self):
        for u in (self.member, self.viewer):
            self.como(u)
            self.assertEqual(self.salvar().status_code, 403, u.username)
        self.assertEqual(SaldoInicial.objects.count(), 0)

    def test_importar_planilha_de_saldos(self):
        arq = SimpleUploadedFile("s.csv", "conta;valor\n1.01.01;1.000,00\n2.03.01;1.000,00\n".encode())
        r = self.client.post(self.url("saldos_iniciais_importar"), {"arquivo": arq, "data_base": "2024-12-31"})
        self.assertEqual((r.status_code, r.json()["qtd"]), (200, 2))
        self.assertEqual(SaldoInicial.objects.count(), 2)

    def test_importar_exige_data_base_e_reporta_erros(self):
        arq = SimpleUploadedFile("s.csv", b"conta;valor\n1.01.01;10,00\n")
        self.assertEqual(self.client.post(self.url("saldos_iniciais_importar"), {"arquivo": arq}).status_code, 400)
        ruim = SimpleUploadedFile("s2.csv", b"conta;valor\n3.01.01;10,00\n")
        r = self.client.post(self.url("saldos_iniciais_importar"), {"arquivo": ruim, "data_base": "2024-12-31"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["erros"][0]["linha"], 2)

    def test_importar_sem_permissao(self):
        self.como(self.viewer)
        arq = SimpleUploadedFile("s.csv", b"conta;valor\n1.01.01;10,00\n")
        self.assertEqual(self.client.post(self.url("saldos_iniciais_importar"), {"arquivo": arq, "data_base": "2024-12-31"}).status_code, 403)


class BalanceteCalculadoViewTests(ViewsMixin, TestCase):
    """Anterior + movimentações + ajustes = atual, para todas as contas, com a árvore consolidada."""

    def setUp(self):
        self.montar()
        self.periodo_2025 = self.periodo()
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2024, 12, 31), D("1000"))
        ms.definir_saldo_inicial(self.fundo, self.capital, date(2024, 12, 31), D("1000"))
        self.mov(self.fundo, self.caixa, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.receita, date(2025, 3, 10), "500")
        self.mov(self.fundo, self.caixa, date(2025, 6, 10), "-200")
        self.mov(self.fundo, self.despesa, date(2025, 6, 10), "-200")

    def pagina(self, periodo=None):
        return self.client.get(reverse("balancete_periodo", args=[(periodo or self.periodo_2025).id]))

    @staticmethod
    def linhas(r):
        return {l["codigo"]: l for l in r.context["linhas"]}

    def test_valores_por_conta_e_consolidados(self):
        self.como(self.viewer)
        r = self.pagina()
        self.assertEqual(r.status_code, 200)
        L = self.linhas(r)
        caixa = L["1.01.01"]
        self.assertEqual((caixa["anterior"], caixa["movimentos"], caixa["atual"]), (D("1000"), D("300"), D("1300")))
        self.assertEqual((L["1"]["anterior"], L["1"]["atual"]), (D("1000"), D("1300")))  # sintética somada
        self.assertEqual((L["3"]["movimentos"], L["3"]["atual"]), (D("300"), D("300")))
        self.assertEqual(L["3.01.01"]["anterior"], D("0"))  # resultado parte de zero no ano

    def test_formula_fecha_em_todas_as_linhas(self):
        self.como(self.viewer)
        for l in self.pagina().context["linhas"]:
            self.assertEqual(l["anterior"] + l["movimentos"] + l["ajustes"], l["atual"], l["codigo"])

    def test_conferencia_zerada_e_sem_ajustes_no_primeiro_ano(self):
        self.como(self.viewer)
        c = self.pagina().context
        self.assertEqual((c["conferencia_atual"], c["conferencia_anterior"]), (0, 0))
        self.assertFalse(c["tem_ajustes"])
        # caixa, receita e despesa; o capital só tem saldo inicial, sem movimento no período
        self.assertEqual(c["contas_com_movimento"], 3)

    def test_segundo_ano_mostra_o_ajuste_do_lucro_acumulado(self):
        periodo_2026 = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2026, 12, 31),
            data_anterior=date(2025, 12, 31), status="nao_iniciada",
        )
        self.mov(self.fundo, self.caixa, date(2026, 2, 1), "100")
        self.mov(self.fundo, self.receita, date(2026, 2, 1), "100")
        self.como(self.viewer)
        r = self.pagina(periodo_2026)
        L = self.linhas(r)
        self.assertTrue(r.context["tem_ajustes"])
        lucros = L["2.03.02"]
        self.assertEqual((lucros["anterior"], lucros["movimentos"], lucros["ajustes"], lucros["atual"]), (D("0"), D("0"), D("300"), D("300")))
        self.assertEqual((L["3"]["anterior"], L["3"]["atual"]), (D("0"), D("100")))  # resultado zerou em 1/1
        self.assertEqual((r.context["conferencia_atual"], r.context["conferencia_anterior"]), (0, 0))
        for l in r.context["linhas"]:
            self.assertEqual(l["anterior"] + l["movimentos"] + l["ajustes"], l["atual"], l["codigo"])

    def test_grupo_com_total_zero_continua_na_arvore(self):
        # receita +500 e despesa −200 já existem; mais −300 de despesa zera o grupo 3.01 (e a raiz 3)
        self.mov(self.fundo, self.despesa, date(2025, 9, 1), "-300")
        self.como(self.viewer)
        L = self.linhas(self.pagina())
        self.assertEqual((L["3.01"]["atual"], L["3"]["atual"]), (D("0"), D("0")))
        # sem os ancestrais, as filhas ficariam inalcançáveis na árvore
        self.assertEqual((L["3.01.01"]["atual"], L["3.01.02"]["atual"]), (D("500"), D("-500")))

    def test_fundo_sem_dados(self):
        PeriodoDF.objects.filter(pk=self.periodo_2025.pk).update(fundo=self.montar_fundo("Fundo Vazio", "44.444.444/0001-44"))
        self.como(self.viewer)
        r = self.pagina()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["linhas"], [])

    def test_exige_login_e_isola_empresa(self):
        self.client.logout()
        self.assertEqual(self.pagina().status_code, 302)
        self.como(self.estranho)
        self.assertEqual(self.pagina().status_code, 404)

    def test_periodo_antigo_sem_datas_e_preenchido(self):
        PeriodoDF.objects.filter(pk=self.periodo_2025.pk).update(data_referencia=None, data_anterior=None)
        self.como(self.viewer)
        self.assertEqual(self.pagina().status_code, 200)


class PontosDeEntradaTests(ViewsMixin, TestCase):
    def setUp(self):
        self.montar()
        self.como(self.viewer)

    def test_pagina_da_df_tem_os_links_por_fundo_e_o_balancete_calculado(self):
        html = self.client.get(reverse("demonstracao_financeira")).content.decode()
        self.assertIn('id="link-movimentacoes"', html)
        self.assertIn('id="link-saldos-iniciais"', html)
        self.assertIn("/balancete/", html)
        self.assertNotIn("/saldos/", html)  # o editor do balancete antigo saiu da tela

    def test_lista_de_fundos_aponta_para_as_movimentacoes(self):
        r = self.client.get(reverse("listar_fundos"))
        self.assertContains(r, reverse("movimentacoes_fundo", args=[self.fundo.id]))
