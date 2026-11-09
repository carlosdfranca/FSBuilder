"""Testes do Plano de Contas (CRUD de MapeamentoContas por empresa) e das linhas pendentes do balancete:
isolamento entre empresas, permissões por role, proteção de contas em uso e materialização de pendentes."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.processing.import_service import import_balancete
from core.upload.balancete_parser import BalanceteRowDTO
from df.models import (
    BalanceteItem, BalanceteLinhaPendente, Fundo, GrupoGrande, GrupoPequeno, MapeamentoContas,
)
from usuarios.models import Empresa, Membership, Usuario


def _usuario(username, empresa, role):
    u = Usuario.objects.create_user(username=username, password="x")
    Membership.objects.create(usuario=u, empresa=empresa, role=role, is_active=True)
    return u


def _linha(conta, saldo, **raw):
    return BalanceteRowDTO(conta=conta, saldo_atual=saldo, saldo_anterior=None, raw={"CONTA": conta, **raw})


class PlanoContasBase(TestCase):

    def setUp(self):
        self.empresa_a = Empresa.objects.create(nome="Empresa A")
        self.empresa_b = Empresa.objects.create(nome="Empresa B")
        self.grupao = GrupoGrande.objects.create(nome="Disponibilidades", tipo=1, ordem=1)
        self.grupinho = GrupoPequeno.objects.create(nome="Bancos", grupao=self.grupao)
        self.fundo_a = Fundo.objects.create(
            empresa=self.empresa_a, nome="Fundo A", cnpj="11.111.111/0001-11", tipo_fundo="FIDC",
        )
        self.master = _usuario("master_pc", self.empresa_a, Membership.Role.MASTER)
        self.admin = _usuario("admin_pc", self.empresa_a, Membership.Role.ADMIN)
        self.member = _usuario("member_pc", self.empresa_a, Membership.Role.MEMBER)
        self.viewer = _usuario("viewer_pc", self.empresa_a, Membership.Role.VIEWER)

    def _criar(self, client, conta="1.1.01", **extra):
        dados = {"conta": conta, "descricao": "Caixa", "grupo_pequeno": self.grupinho.id}
        dados.update(extra)
        return client.post(reverse("criar_mapeamento"), dados)


class PlanoContasPermissaoTests(PlanoContasBase):

    def test_todos_os_roles_da_empresa_veem_a_pagina(self):
        for u in (self.master, self.admin, self.member, self.viewer):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("plano_de_contas")).status_code, 200, u.username)

    def test_member_e_viewer_nao_gravam(self):
        m = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01", grupo_pequeno=self.grupinho)
        for u in (self.member, self.viewer):
            self.client.force_login(u)
            self.assertEqual(self._criar(self.client, "9.9.99").status_code, 403, u.username)
            self.assertEqual(
                self.client.post(reverse("editar_mapeamento", args=[m.id]), {"conta": "x"}).status_code, 403
            )
            self.assertEqual(self.client.post(reverse("excluir_mapeamento", args=[m.id])).status_code, 403)
        self.assertEqual(MapeamentoContas.objects.count(), 1)

    def test_master_e_admin_gravam(self):
        for i, u in enumerate((self.master, self.admin)):
            self.client.force_login(u)
            r = self._criar(self.client, f"1.1.0{i}")
            self.assertEqual(r.status_code, 200, u.username)
            self.assertTrue(r.json()["ok"])

    def test_get_nas_apis_de_escrita_e_proibido(self):
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(reverse("criar_mapeamento")).status_code, 405)

    def test_anonimo_vai_para_login(self):
        r = self.client.get(reverse("plano_de_contas"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)


class PlanoContasIsolamentoTests(PlanoContasBase):

    def setUp(self):
        super().setUp()
        self.map_b = MapeamentoContas.objects.create(empresa=self.empresa_b, conta="1.1.01", grupo_pequeno=self.grupinho)
        self.master_b = _usuario("master_b_pc", self.empresa_b, Membership.Role.MASTER)

    def test_pagina_lista_so_contas_da_propria_empresa(self):
        MapeamentoContas.objects.create(empresa=self.empresa_a, conta="2.2.02", grupo_pequeno=self.grupinho)
        self.client.force_login(self.master)
        contas = [l["conta"] for l in self.client.get(reverse("plano_de_contas")).context["linhas"]]
        self.assertEqual(contas, ["2.2.02"])

    def test_nao_edita_nem_exclui_mapeamento_de_outra_empresa(self):
        self.client.force_login(self.master)
        r = self.client.post(reverse("editar_mapeamento", args=[self.map_b.id]), {"conta": "1.1.01", "descricao": "hack"})
        self.assertEqual(r.status_code, 404)
        r = self.client.post(reverse("excluir_mapeamento", args=[self.map_b.id]))
        self.assertEqual(r.status_code, 404)
        self.map_b.refresh_from_db()
        self.assertIsNone(self.map_b.descricao)

    def test_empresa_do_post_e_ignorada_na_criacao(self):
        self.client.force_login(self.master)
        r = self._criar(self.client, "5.5.05", empresa=self.empresa_b.id)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(MapeamentoContas.objects.filter(empresa=self.empresa_a, conta="5.5.05").exists())
        self.assertFalse(MapeamentoContas.objects.filter(empresa=self.empresa_b, conta="5.5.05").exists())

    def test_mesma_conta_pode_existir_em_empresas_diferentes(self):
        self.client.force_login(self.master)
        self.assertEqual(self._criar(self.client, "1.1.01").status_code, 200)  # B já tem 1.1.01

    def test_descartar_pendente_so_afeta_a_propria_empresa(self):
        fundo_b = Fundo.objects.create(empresa=self.empresa_b, nome="Fundo B", cnpj="22.222.222/0001-22", tipo_fundo="FIDC")
        BalanceteLinhaPendente.objects.create(
            empresa=self.empresa_b, fundo=fundo_b, data_referencia=date(2025, 12, 31), conta="7.7.07",
        )
        self.client.force_login(self.master)
        r = self.client.post(reverse("descartar_pendente"), {"conta": "7.7.07"})
        self.assertEqual(r.json()["removidas"], 0)
        self.assertEqual(BalanceteLinhaPendente.objects.count(), 1)


class PlanoContasCrudTests(PlanoContasBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.master)

    def test_criar_duplicado_retorna_erro_de_form(self):
        self.assertEqual(self._criar(self.client, "1.1.01").status_code, 200)
        r = self._criar(self.client, "1.1.01")
        self.assertEqual(r.status_code, 400)
        self.assertIn("conta", r.json()["errors"])
        self.assertEqual(MapeamentoContas.objects.filter(empresa=self.empresa_a).count(), 1)

    def test_criar_sem_conta_retorna_erro(self):
        r = self._criar(self.client, "   ")
        self.assertEqual(r.status_code, 400)
        self.assertIn("conta", r.json()["errors"])

    def test_criar_sem_grupinho_e_permitido(self):
        r = self.client.post(reverse("criar_mapeamento"), {"conta": "3.3.03", "descricao": "", "grupo_pequeno": ""})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(MapeamentoContas.objects.get(conta="3.3.03").grupo_pequeno)

    def test_editar_conta_sem_uso_altera_codigo(self):
        m = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01", grupo_pequeno=self.grupinho)
        r = self.client.post(reverse("editar_mapeamento", args=[m.id]), {"conta": "1.1.99", "descricao": "Novo", "grupo_pequeno": ""})
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual((m.conta, m.descricao, m.grupo_pequeno_id), ("1.1.99", "Novo", None))

    def test_editar_conta_em_uso_trava_o_codigo_mas_muda_o_grupinho(self):
        outro = GrupoPequeno.objects.create(nome="Aplicações", grupao=self.grupao)
        m = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01", grupo_pequeno=self.grupinho)
        BalanceteItem.objects.create(
            fundo=self.fundo_a, conta_corrente=m, data_referencia=date(2025, 12, 31), saldo_final=Decimal("10"),
        )
        r = self.client.post(
            reverse("editar_mapeamento", args=[m.id]),
            {"conta": "TROCADA", "descricao": "x", "grupo_pequeno": outro.id},
        )
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.conta, "1.1.01")
        self.assertEqual(m.grupo_pequeno_id, outro.id)
        self.assertTrue(r.json()["aviso"])

    def test_excluir_conta_sem_uso(self):
        m = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01")
        self.assertTrue(self.client.post(reverse("excluir_mapeamento", args=[m.id])).json()["ok"])
        self.assertFalse(MapeamentoContas.objects.filter(pk=m.pk).exists())

    def test_excluir_conta_em_uso_retorna_409(self):
        m = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01", grupo_pequeno=self.grupinho)
        BalanceteItem.objects.create(
            fundo=self.fundo_a, conta_corrente=m, data_referencia=date(2025, 12, 31), saldo_final=Decimal("10"),
        )
        r = self.client.post(reverse("excluir_mapeamento", args=[m.id]))
        self.assertEqual(r.status_code, 409)
        self.assertTrue(MapeamentoContas.objects.filter(pk=m.pk).exists())


class PendentesBalanceteTests(PlanoContasBase):

    DATA = date(2025, 12, 31)

    def _importar(self, linhas):
        return import_balancete(fundo_id=self.fundo_a.id, data_referencia=self.DATA, rows=linhas)

    def test_import_com_conta_nao_mapeada_gera_pendente(self):
        report = self._importar([_linha("1.1.01", 100.0, Descricao="Caixa")])
        self.assertEqual(report.pendentes, 1)
        self.assertEqual(report.imported, 0)
        self.assertEqual(BalanceteItem.objects.count(), 0)
        p = BalanceteLinhaPendente.objects.get()
        self.assertEqual((p.empresa_id, p.conta, p.saldo_final), (self.empresa_a.id, "1.1.01", Decimal("100.0")))

    def test_import_e_idempotente_para_pendentes(self):
        self._importar([_linha("1.1.01", 100.0)])
        self._importar([_linha("1.1.01", 250.0)])
        self.assertEqual(BalanceteLinhaPendente.objects.count(), 1)
        self.assertEqual(BalanceteLinhaPendente.objects.get().saldo_final, Decimal("250.0"))

    def test_linha_sem_saldo_nao_vira_pendente(self):
        self.assertEqual(self._importar([_linha("1.1.01", None)]).pendentes, 0)

    def test_mapear_conta_materializa_pendente(self):
        self._importar([_linha("1.1.01", 100.0)])
        self.client.force_login(self.master)
        r = self._criar(self.client, "1.1.01")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["aplicados"], {"linhas": 1, "fundos": 1})
        item = BalanceteItem.objects.get()
        self.assertEqual((item.fundo_id, item.saldo_final, item.data_referencia), (self.fundo_a.id, Decimal("100.00"), self.DATA))
        self.assertFalse(BalanceteLinhaPendente.objects.exists())

    def test_reimport_apos_mapear_nao_recria_pendente(self):
        self._importar([_linha("1.1.01", 100.0)])
        self.client.force_login(self.master)
        self._criar(self.client, "1.1.01")
        report = self._importar([_linha("1.1.01", 300.0)])
        self.assertEqual((report.pendentes, report.updated), (0, 1))
        self.assertFalse(BalanceteLinhaPendente.objects.exists())
        self.assertEqual(BalanceteItem.objects.get().saldo_final, Decimal("300.0"))

    def test_reimport_limpa_pendente_de_conta_mapeada_por_fora(self):
        """Mapeada pelo admin (sem passar pela view): o reimport deve limpar a pendência velha."""
        self._importar([_linha("1.1.01", 100.0)])
        MapeamentoContas.objects.create(empresa=self.empresa_a, conta="1.1.01", grupo_pequeno=self.grupinho)
        self._importar([_linha("1.1.01", 100.0)])
        self.assertFalse(BalanceteLinhaPendente.objects.exists())

    def test_pagina_expoe_pendentes_agrupadas_e_kpi(self):
        self._importar([_linha("1.1.01", 100.0), _linha("2.2.02", 5.0)])
        self.client.force_login(self.master)
        ctx = self.client.get(reverse("plano_de_contas")).context
        self.assertEqual(ctx["kpis"]["pendentes"], 2)
        self.assertEqual([p["conta"] for p in ctx["pendentes"]], ["1.1.01", "2.2.02"])

    def test_descartar_pendente(self):
        self._importar([_linha("1.1.01", 100.0)])
        self.client.force_login(self.master)
        r = self.client.post(reverse("descartar_pendente"), {"conta": "1.1.01"})
        self.assertEqual((r.json()["removidas"], r.json()["pendentes_total"]), (1, 0))
