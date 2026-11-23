"""Seletor de contas: filtros por natureza e grupo, relevância, atalhos 'mais usadas' e catálogo."""
from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.tests.test_views_movimentacoes import usuario
from core.views_movimentacoes import _titulo, catalogo_do_plano
from df.models import Conta, Fundo
from df.services import movimentacao_service as ms
from usuarios.models import Empresa, Membership


def criar(codigo, descricao, tipo, nivel, natureza, pai=None, **extra):
    return Conta.objects.create(
        codigo=codigo, descricao=descricao, tipo=tipo, nivel=nivel, natureza=natureza,
        conta_superior=Conta.objects.get(codigo=pai) if pai else None, **extra,
    )


class PlanoDeTesteMixin:
    """Árvore pequena, com a mesma forma do plano real (grupos de nível 2 e 3, analíticas abaixo)."""

    def montar_plano_e_fundo(self):
        S, A = "S", "A"
        criar("1", "ATIVO", S, 1, 1)
        criar("1.01", "ATIVO CIRCULANTE", S, 2, 1, "1")
        criar("1.01.01", "DISPONIBILIDADES", S, 3, 1, "1.01")
        self.depositos = criar("1.01.01.00", "Depósitos em caixa econômica", A, 4, 1, "1.01.01")
        self.caixa = criar("1.01.01.01", "Caixa", A, 4, 1, "1.01.01")
        self.bancos = criar("1.01.01.02", "Bancos conta movimento", A, 4, 1, "1.01.01")
        criar("1.01.02", "CRÉDITOS", S, 3, 1, "1.01")
        self.clientes = criar("1.01.02.01", "Clientes", A, 4, 1, "1.01.02")
        criar("1.02", "ATIVO NÃO CIRCULANTE", S, 2, 1, "1")
        criar("1.02.01", "INVESTIMENTOS", S, 3, 1, "1.02")
        self.participacoes = criar("1.02.01.01", "Participações", A, 4, 1, "1.02.01")
        criar("1.02.09", "GRUPO SEM CONTAS", S, 3, 1, "1.02")  # sintético vazio: não entra no catálogo
        criar("2", "PASSIVO", S, 1, 2)
        criar("2.01", "PASSIVO CIRCULANTE", S, 2, 2, "2")
        criar("2.01.01", "FORNECEDORES", S, 3, 2, "2.01")
        self.fornecedores = criar("2.01.01.01", "Fornecedores nacionais", A, 4, 2, "2.01.01")
        criar("2.03", "PATRIMÔNIO LÍQUIDO", S, 2, 3, "2")
        criar("2.03.01", "CAPITAL SOCIAL", S, 3, 3, "2.03")
        self.capital = criar("2.03.01.01", "Capital subscrito", A, 4, 3, "2.03.01")
        criar("3", "RESULTADO", S, 1, 4)
        criar("3.01", "RESULTADO ANTES DO IRPJ E DA CSLL", S, 2, 4, "3")
        criar("3.01.01", "RECEITA LÍQUIDA", S, 3, 4, "3.01")
        self.vendas = criar("3.01.01.01", "Receita de vendas", A, 4, 4, "3.01.01")
        self.servicos = criar("3.01.01.02", "Receita de serviços", A, 4, 4, "3.01.01")

        self.empresa = Empresa.objects.create(nome="Empresa A")
        self.fundo = Fundo.objects.create(empresa=self.empresa, nome="Fundo A", cnpj="11.111.111/0001-11", tipo_fundo="FIDC")
        self.viewer = usuario("viewer_sel", self.empresa, Membership.Role.VIEWER)
        outra = Empresa.objects.create(nome="Empresa B")
        self.fundo_b = Fundo.objects.create(empresa=outra, nome="Fundo B", cnpj="22.222.222/0001-22", tipo_fundo="FIDC")
        self.estranho = usuario("estranho_sel", outra, Membership.Role.VIEWER)
        self.client.force_login(self.viewer)

    def buscar(self, **params):
        r = self.client.get(reverse("contas_buscar"), params)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def codigos(self, **params):
        return [c["codigo"] for c in self.buscar(**params)["contas"]]

    def lancar(self, conta, vezes, fundo=None, dia=1):
        for i in range(vezes):
            ms.criar_movimentacao(fundo or self.fundo, conta, date(2025, 3, dia + i), D("10"))


class FiltrosDaBuscaTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()

    def test_sem_filtro_traz_todas_as_analiticas_em_ordem_de_codigo(self):
        resposta = self.buscar()
        self.assertEqual([c["codigo"] for c in resposta["contas"]], sorted(c["codigo"] for c in resposta["contas"]))
        self.assertEqual(resposta["total"], 9)
        self.assertEqual(resposta["limite"], 50)

    def test_por_natureza(self):
        self.assertEqual(set(self.codigos(natureza=1)), {"1.01.01.00", "1.01.01.01", "1.01.01.02", "1.01.02.01", "1.02.01.01"})
        self.assertEqual(self.codigos(natureza=2), ["2.01.01.01"])
        self.assertEqual(self.codigos(natureza=3), ["2.03.01.01"])
        self.assertEqual(self.codigos(natureza=4), ["3.01.01.01", "3.01.01.02"])

    def test_por_grupo(self):
        self.assertEqual(self.codigos(grupo="1.01.01"), ["1.01.01.00", "1.01.01.01", "1.01.01.02"])
        self.assertEqual(self.codigos(grupo="1.01.02"), ["1.01.02.01"])
        self.assertEqual(len(self.codigos(grupo="1.01")), 4)  # prefixo mais amplo (nível 2) também vale

    def test_grupo_nao_confunde_prefixos_parecidos(self):
        criar("1.01.011", "FALSO GRUPO", "S", 3, 1, "1.01")
        criar("1.01.011.01", "Conta de outro grupo", "A", 4, 1, "1.01.011")
        self.assertNotIn("1.01.011.01", self.codigos(grupo="1.01.01"))  # "1.01.01" + "." nunca casa "1.01.011"

    def test_natureza_com_grupo_e_com_texto(self):
        self.assertEqual(self.codigos(natureza=1, grupo="1.01.01", q="banco"), ["1.01.01.02"])
        self.assertEqual(self.codigos(natureza=2, grupo="1.01.01"), [])  # grupo de outra natureza
        self.assertEqual(self.codigos(natureza=4, q="servi"), ["3.01.01.02"])

    def test_filtros_invalidos_sao_ignorados(self):
        todas = self.codigos()
        for natureza in ("abc", "9", "0", "-1", ""):
            self.assertEqual(self.codigos(natureza=natureza), todas, natureza)
        for grupo in ("abc", "1..2", "1.01.%", "1.01'--", " ", "1.01.01.x"):
            self.assertEqual(self.codigos(grupo=grupo), todas, grupo)

    def test_patrimonial_exclui_resultado_mesmo_pedindo_a_natureza(self):
        self.assertNotIn("3.01.01.01", self.codigos(patrimonial=1))
        r = self.buscar(patrimonial=1, natureza=4)
        self.assertEqual((r["contas"], r["total"]), ([], 0))

    def test_inativa_e_sintetica_nunca_aparecem(self):
        Conta.objects.filter(pk=self.caixa.pk).update(is_ativa=False)
        codigos = self.codigos()
        self.assertNotIn("1.01.01.01", codigos)
        self.assertNotIn("1.01.01", codigos)  # sintética

    def test_limite_e_total(self):
        for i in range(60):
            criar(f"2.01.01.{i + 10:02d}", f"Extra {i}", "A", 4, 2, "2.01.01")
        r = self.buscar(q="extra")
        self.assertEqual((len(r["contas"]), r["total"], r["limite"]), (50, 60, 50))

    def test_texto_ignora_acento_e_caixa(self):
        self.assertEqual(self.codigos(q="CREDITOS"), [])  # só grupos têm esse nome; analíticas não
        self.assertEqual(self.codigos(q="participacoes"), ["1.02.01.01"])
        self.assertEqual(self.codigos(q="PARTICIPAÇÕES"), ["1.02.01.01"])

    def test_texto_muito_longo_e_cortado_sem_erro(self):
        self.assertEqual(self.codigos(q="x" * 500), [])


class RelevanciaTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()

    def test_descricao_que_comeca_com_o_termo_vem_antes_da_que_apenas_contem(self):
        # "Depósitos em caixa econômica" tem código MENOR, mas só contém "caixa"; "Caixa" começa com ele
        self.assertEqual(self.codigos(q="caixa"), ["1.01.01.01", "1.01.01.00"])

    def test_codigo_exato_vem_primeiro(self):
        self.assertEqual(self.codigos(q="1.01.01.01")[0], "1.01.01.01")

    def test_codigo_que_comeca_com_o_termo_vem_antes_de_quem_so_contem(self):
        criar("1.01.01.10", "Aplicação 1.01.01.0x", "A", 4, 1, "1.01.01")  # descrição contém o código
        resultado = self.codigos(q="1.01.01.0")
        self.assertEqual(resultado[:3], ["1.01.01.00", "1.01.01.01", "1.01.01.02"])
        self.assertEqual(resultado[-1], "1.01.01.10")


class CaminhoDasContasTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()

    def test_caminho_grupo_e_natureza(self):
        conta = next(c for c in self.buscar(q="Caixa")["contas"] if c["codigo"] == "1.01.01.01")
        self.assertEqual(conta["grupo_codigo"], "1.01.01")
        self.assertEqual(conta["caminho"], ["Ativo circulante", "Disponibilidades"])
        self.assertEqual((conta["natureza_codigo"], conta["natureza"]), (1, "Ativo"))

    def test_nomes_de_grupo_em_maiusculas_sao_suavizados_mantendo_siglas(self):
        conta = self.buscar(natureza=4)["contas"][0]
        self.assertEqual(conta["caminho"], ["Resultado antes do IRPJ e da CSLL", "Receita líquida"])
        pl = self.buscar(natureza=3)["contas"][0]
        self.assertEqual(pl["caminho"], ["Patrimônio líquido", "Capital social"])

    def test_acentos_preservados_no_titulo(self):
        self.assertEqual(self.buscar(grupo="1.01.02")["contas"][0]["caminho"][1], "Créditos")


class TituloTests(SimpleTestCase):
    def test_suaviza_so_o_que_esta_em_maiusculas(self):
        self.assertEqual(_titulo("ATIVO CIRCULANTE"), "Ativo circulante")
        self.assertEqual(_titulo("Caixa e bancos"), "Caixa e bancos")  # já misto: não mexe
        self.assertEqual(_titulo("PROVISÃO PARA CSLL E IRPJ"), "Provisão para CSLL e IRPJ")
        self.assertEqual(_titulo("PATRIMÔNIO LÍQUIDO"), "Patrimônio líquido")
        self.assertEqual(_titulo(""), "")


class MaisUsadasTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()
        self.lancar(self.caixa, 3)
        self.lancar(self.clientes, 2, dia=10)
        self.lancar(self.vendas, 1, dia=20)

    def mais_usadas(self, **params):
        return self.buscar(fundo=self.fundo.id, recentes=1, **params).get("mais_usadas")

    def test_ordenadas_por_uso_com_a_contagem(self):
        r = self.mais_usadas()
        self.assertEqual([(c["codigo"], c["usos"]) for c in r], [("1.01.01.01", 3), ("1.01.02.01", 2), ("3.01.01.01", 1)])
        self.assertEqual(r[0]["caminho"], ["Ativo circulante", "Disponibilidades"])

    def test_empate_vence_o_lancamento_mais_recente(self):
        self.lancar(self.servicos, 1, dia=28)  # 1 uso, mais recente que "vendas"
        self.assertEqual([c["codigo"] for c in self.mais_usadas()][-2:], ["3.01.01.02", "3.01.01.01"])

    def test_respeita_patrimonial(self):
        self.assertNotIn("3.01.01.01", [c["codigo"] for c in self.mais_usadas(patrimonial=1)])

    def test_so_aparece_sem_texto_e_sem_filtros(self):
        for extra in ({"q": "caixa"}, {"natureza": 1}, {"grupo": "1.01.01"}):
            self.assertNotIn("mais_usadas", self.buscar(fundo=self.fundo.id, recentes=1, **extra), extra)
        self.assertNotIn("mais_usadas", self.buscar(fundo=self.fundo.id))  # sem pedir

    def test_conta_inativa_nao_aparece(self):
        Conta.objects.filter(pk=self.caixa.pk).update(is_ativa=False)
        self.assertNotIn("1.01.01.01", [c["codigo"] for c in self.mais_usadas()])

    def test_limita_a_seis(self):
        for i in range(8):
            conta = criar(f"2.01.01.{i + 20:02d}", f"Outra {i}", "A", 4, 2, "2.01.01")
            self.lancar(conta, 1, dia=i + 1)
        self.assertEqual(len(self.mais_usadas()), 6)

    def test_fundo_de_outra_empresa_nao_vaza(self):
        self.lancar(self.caixa, 5, fundo=self.fundo_b)
        self.client.force_login(self.estranho)  # empresa B só enxerga o fundo B
        r = self.buscar(fundo=self.fundo.id, recentes=1)
        self.assertEqual(r["mais_usadas"], [])
        self.assertEqual([c["usos"] for c in self.buscar(fundo=self.fundo_b.id, recentes=1)["mais_usadas"]], [5])

    def test_fundo_invalido_ou_ausente(self):
        for fundo in ("abc", "999999", ""):
            self.assertEqual(self.buscar(fundo=fundo, recentes=1)["mais_usadas"], [], fundo)
        self.assertEqual(self.buscar(recentes=1)["mais_usadas"], [])

    def test_fundo_sem_lancamentos(self):
        outro = Fundo.objects.create(empresa=self.empresa, nome="Fundo vazio", cnpj="33.333.333/0001-33", tipo_fundo="FIDC")
        self.assertEqual(self.buscar(fundo=outro.id, recentes=1)["mais_usadas"], [])


class CatalogoTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()

    def test_naturezas_com_contagem(self):
        naturezas = {n["codigo"]: n for n in catalogo_do_plano()["naturezas"]}
        self.assertEqual({k: v["qtd"] for k, v in naturezas.items()}, {1: 5, 2: 1, 3: 1, 4: 2})
        self.assertEqual({k: v["curto"] for k, v in naturezas.items()}, {1: "Ativo", 2: "Passivo", 3: "PL", 4: "Resultado"})
        self.assertEqual(naturezas[3]["label"], "Patrimônio Líquido")

    def test_grupos_com_pai_natureza_e_contagem(self):
        grupos = {g["codigo"]: g for g in catalogo_do_plano()["grupos"]}
        self.assertEqual(set(grupos), {"1.01.01", "1.01.02", "1.02.01", "2.01.01", "2.03.01", "3.01.01"})  # sem o grupo vazio
        g = grupos["1.01.01"]
        self.assertEqual((g["descricao"], g["pai_codigo"], g["pai_descricao"], g["natureza"], g["qtd"]),
                         ("Disponibilidades", "1.01", "Ativo circulante", 1, 3))
        self.assertEqual(grupos["2.03.01"]["natureza"], 3)  # PL é natureza 3 mesmo sob o Passivo

    def test_grupos_em_ordem_de_codigo(self):
        codigos = [g["codigo"] for g in catalogo_do_plano()["grupos"]]
        self.assertEqual(codigos, sorted(codigos))

    def test_patrimonial_nao_tem_resultado(self):
        cat = catalogo_do_plano(patrimonial=True)
        self.assertEqual({n["codigo"] for n in cat["naturezas"]}, {1, 2, 3})
        self.assertNotIn("3.01.01", [g["codigo"] for g in cat["grupos"]])

    def test_ignora_contas_inativas(self):
        Conta.objects.filter(pk__in=[self.caixa.pk, self.bancos.pk, self.depositos.pk]).update(is_ativa=False)
        grupos = [g["codigo"] for g in catalogo_do_plano()["grupos"]]
        self.assertNotIn("1.01.01", grupos)  # o grupo ficou sem contas lançáveis


class PaginasComSeletorTests(PlanoDeTesteMixin, TestCase):
    def setUp(self):
        self.montar_plano_e_fundo()
        self.master = usuario("master_sel", self.empresa, Membership.Role.MASTER)
        self.client.force_login(self.master)
        self.mov = ms.criar_movimentacao(self.fundo, self.caixa, date(2025, 3, 10), D("10"))

    def test_lista_recebe_catalogo_completo_e_conta_detalhada(self):
        r = self.client.get(reverse("movimentacoes_fundo", args=[self.fundo.id]))
        self.assertEqual({n["codigo"] for n in r.context["catalogo_contas"]["naturezas"]}, {1, 2, 3, 4})
        detalhe = r.context["movimentacoes"][0]["conta_detalhe"]  # cartão da conta ao editar
        self.assertEqual((detalhe["codigo"], detalhe["natureza_codigo"], detalhe["caminho"]),
                         ("1.01.01.01", 1, ["Ativo circulante", "Disponibilidades"]))
        self.assertContains(r, 'id="ca-catalogo"')

    def test_saldos_iniciais_recebe_catalogo_sem_resultado(self):
        ms.definir_saldo_inicial(self.fundo, self.caixa, date(2024, 12, 31), D("5"))
        r = self.client.get(reverse("saldos_iniciais_fundo", args=[self.fundo.id]))
        self.assertEqual({n["codigo"] for n in r.context["catalogo_contas"]["naturezas"]}, {1, 2, 3})
        self.assertEqual(r.context["saldos"][0]["conta_detalhe"]["caminho"], ["Ativo circulante", "Disponibilidades"])
        self.assertContains(r, 'id="ca-catalogo"')

    def test_respostas_json_de_criar_e_editar_tambem_trazem_a_conta_detalhada(self):
        r = self.client.post(reverse("movimentacao_criar", args=[self.fundo.id]), {
            "data": "2025-04-01", "conta_id": self.vendas.id, "valor": "100,00"})
        self.assertEqual(r.json()["item"]["conta_detalhe"]["natureza_codigo"], 4)
