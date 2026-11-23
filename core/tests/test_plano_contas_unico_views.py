"""Tela do plano de contas único: leitura para qualquer usuário logado, edição só para admin da plataforma."""
from django.test import TestCase
from django.urls import reverse

from df.models import Conta
from usuarios.models import Empresa, Membership, Usuario


class PlanoUnicoViewsTests(TestCase):

    def setUp(self):
        self.ativo = Conta.objects.create(codigo="1", descricao="ATIVO", tipo="S", nivel=1, natureza=1)
        self.circ = Conta.objects.create(
            codigo="1.01", descricao="CIRCULANTE", tipo="S", nivel=2, natureza=1, conta_superior=self.ativo
        )
        self.caixa = Conta.objects.create(
            codigo="1.01.01", descricao="Caixa", tipo="A", nivel=3, natureza=1, conta_superior=self.circ
        )
        self.pl = Conta.objects.create(codigo="2", descricao="PASSIVO", tipo="S", nivel=1, natureza=2)
        self.pl_s = Conta.objects.create(
            codigo="2.03", descricao="PL", tipo="S", nivel=2, natureza=3, conta_superior=self.pl
        )
        self.lucros = Conta.objects.create(
            codigo="2.03.01", descricao="Lucros", tipo="A", nivel=3, natureza=3, conta_superior=self.pl_s
        )

        self.empresa = Empresa.objects.create(nome="Empresa A")
        self.membro = Usuario.objects.create_user(username="membro_pu", password="x")
        Membership.objects.create(
            usuario=self.membro, empresa=self.empresa, role=Membership.Role.MASTER, is_active=True
        )
        self.admin = Usuario.objects.create_user(
            username="admin_pu", password="x", global_role=Usuario.GlobalRole.PLATFORM_ADMIN
        )

    def test_exige_login(self):
        self.assertEqual(self.client.get(reverse("plano_contas_unico")).status_code, 302)

    def test_usuario_comum_le_mas_nao_edita(self):
        self.client.force_login(self.membro)
        r = self.client.get(reverse("plano_contas_unico"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["can_edit"])
        self.assertEqual(r.context["kpis"]["total"], 6)
        self.assertEqual(r.context["kpis"]["analiticas"], 2)

        r = self.client.post(reverse("editar_conta", args=[self.caixa.id]), {"descricao": "x"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(
            reverse("criar_conta"), {"conta_superior": self.circ.id, "sufixo": "9", "descricao": "x"}
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Conta.objects.count(), 6)

    def test_admin_edita_descricao_e_inativa(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("editar_conta", args=[self.caixa.id]), {"descricao": "Caixa geral"})
        self.assertEqual(r.status_code, 200)
        self.caixa.refresh_from_db()
        self.assertEqual(self.caixa.descricao, "Caixa geral")
        self.assertFalse(self.caixa.is_ativa)  # sem is_ativa no POST = inativa

    def test_admin_define_papel_unico(self):
        self.client.force_login(self.admin)
        url = reverse("editar_conta", args=[self.lucros.id])
        r = self.client.post(url, {"descricao": "Lucros", "is_ativa": "1", "papel": "LUCROS_ACUMULADOS"})
        self.assertEqual(r.status_code, 200)

        outra = Conta.objects.create(
            codigo="2.03.02", descricao="Outra", tipo="A", nivel=3, natureza=3, conta_superior=self.pl_s
        )
        r = self.client.post(
            reverse("editar_conta", args=[outra.id]),
            {"descricao": "Outra", "is_ativa": "1", "papel": "LUCROS_ACUMULADOS"},
        )
        self.assertEqual(r.status_code, 400)
        outra.refresh_from_db()
        self.assertIsNone(outra.papel)

    def test_papel_em_conta_de_ativo_e_recusado(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("editar_conta", args=[self.caixa.id]),
            {"descricao": "Caixa", "is_ativa": "1", "papel": "LUCROS_ACUMULADOS"},
        )
        self.assertEqual(r.status_code, 400)

    def test_admin_cria_conta_analitica_filha(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("criar_conta"), {"conta_superior": self.circ.id, "sufixo": "5", "descricao": "Bancos"}
        )
        self.assertEqual(r.status_code, 200)
        nova = Conta.objects.get(codigo="1.01.05")
        self.assertEqual((nova.nivel, nova.natureza, nova.tipo), (3, 1, "A"))
        self.assertEqual(nova.conta_superior, self.circ)

    def test_criar_recusa_codigo_repetido_e_pai_analitico(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("criar_conta"), {"conta_superior": self.circ.id, "sufixo": "01", "descricao": "Dup"}
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            reverse("criar_conta"), {"conta_superior": self.caixa.id, "sufixo": "1", "descricao": "Filha"}
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Conta.objects.count(), 6)
