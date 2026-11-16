"""Telas de gestão de escritórios de contabilidade: lista, detalhe, equipe, vínculos e aceite público."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.models import (
    ConviteEscritorio, EscritorioContabil, MembroEscritorio, Usuario, VinculoContabil,
)
from usuarios.services import convite_escritorio_service as svc
from usuarios.test_contabilidade import user
from usuarios.test_convite_escritorio import ADMIN, EquipeMixin, MEMBRO
from usuarios.utils.acesso import empresas_acessiveis

PENDENTE = ConviteEscritorio.Status.PENDING


class TelasMixin(EquipeMixin):
    def montar_telas(self):
        self.montar_equipe()

    def como(self, u):
        self.client.force_login(u)

    def url(self, nome, *args):
        return reverse(nome, args=args)

    def post(self, rota, *args, **dados):
        return self.client.post(self.url(rota, *args), dados)

    def acessiveis(self, u):
        return set(empresas_acessiveis(u).values_list("id", flat=True))


class AcessoTelasTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()

    def status(self, nome, *args):
        return self.client.get(self.url(nome, *args)).status_code

    def test_lista_so_para_a_plataforma(self):
        for u, esperado in ((self.plat, 200), (self.adm, 403), (self.membro, 403), (self.estranho, 403)):
            self.como(u)
            self.assertEqual(self.status("escritorios_listar"), esperado, u.username)
        self.client.logout()
        self.assertEqual(self.status("escritorios_listar"), 302)

    def test_detalhe_para_plataforma_e_admin_do_proprio_escritorio(self):
        casos = ((self.plat, 200), (self.adm, 200), (self.membro, 403), (self.adm_y, 403), (self.estranho, 403))
        for u, esperado in casos:
            self.como(u)
            self.assertEqual(self.status("escritorio_detalhe", self.esc.id), esperado, u.username)
        self.client.logout()
        self.assertEqual(self.status("escritorio_detalhe", self.esc.id), 302)

    def test_detalhe_inexistente(self):
        self.como(self.plat)
        self.assertEqual(self.status("escritorio_detalhe", 999999), 404)

    def test_admin_de_escritorio_desativado_perde_o_acesso_a_tela(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        self.como(self.adm)
        self.assertEqual(self.status("escritorio_detalhe", self.esc.id), 403)

    def test_paginas_trazem_o_menu(self):
        self.como(self.plat)
        for nome, args in (("escritorios_listar", []), ("escritorio_detalhe", [self.esc.id])):
            self.assertContains(self.client.get(self.url(nome, *args)), 'class="site-nav"')


class ListaTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.como(self.plat)
        self.convidar("p1@x.com")
        self.convidar("p2@x.com")
        cancelado = self.convidar("c@x.com")
        svc.cancelar_convite(cancelado.id, self.plat)

    def test_kpis_e_contagens(self):
        r = self.client.get(self.url("escritorios_listar"))
        linhas = {e.nome: e for e in r.context["escritorios"]}
        x = linhas["Contábil X"]
        self.assertEqual((x.qtd_membros, x.qtd_empresas, x.qtd_convites), (2, 2, 2))  # contador + adm; Alfa e Beta; 2 pendentes
        y = linhas["Contábil Y"]
        self.assertEqual((y.qtd_membros, y.qtd_empresas, y.qtd_convites), (1, 0, 0))
        self.assertEqual(r.context["kpis"], {"ativos": 2, "membros": 3, "empresas": 2, "convites": 2})

    def test_contagens_ignoram_inativos(self):
        MembroEscritorio.objects.filter(usuario=self.membro).update(is_active=False)
        VinculoContabil.objects.filter(pk=self.vinc_b.pk).update(is_active=False)
        x = {e.nome: e for e in self.client.get(self.url("escritorios_listar")).context["escritorios"]}["Contábil X"]
        self.assertEqual((x.qtd_membros, x.qtd_empresas), (1, 1))

    def test_menu_do_usuario_so_mostra_escritorios_para_a_plataforma(self):
        link = self.url("escritorios_listar")
        self.assertContains(self.client.get(self.url("escritorios_listar")), link)  # menu do usuário (plataforma)
        self.como(self.contador)
        self.assertNotContains(self.client.get(self.url("painel_contabilidade")), link)


class EscritorioCrudTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.como(self.plat)

    def test_criar(self):
        r = self.post("escritorio_criar", nome="  Novo Contábil  ", cnpj="12.345.678/0001-95")
        self.assertEqual(r.status_code, 200)
        e = EscritorioContabil.objects.get(nome="Novo Contábil")
        self.assertEqual((e.cnpj, e.is_ativo), ("12.345.678/0001-95", True))
        self.assertEqual(r.json()["url"], self.url("escritorio_detalhe", e.id))

    def test_cnpj_em_branco_nao_colide(self):
        self.assertEqual(self.post("escritorio_criar", nome="A1", cnpj="").status_code, 200)
        self.assertEqual(self.post("escritorio_criar", nome="A2", cnpj="").status_code, 200)
        self.assertEqual(list(EscritorioContabil.objects.filter(nome__in=["A1", "A2"]).values_list("cnpj", flat=True)), [None, None])

    def test_validacoes(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(cnpj="11.111.111/0001-11")
        for campo, dados in (
            ("nome", {"nome": "Contábil X"}),                                  # nome repetido
            ("cnpj", {"nome": "Z", "cnpj": "11.111.111/0001-11"}),           # CNPJ repetido
            ("cnpj", {"nome": "Z", "cnpj": "123"}),                           # formato inválido
            ("nome", {"nome": ""}),
        ):
            r = self.post("escritorio_criar", **dados)
            self.assertEqual(r.status_code, 400, dados)
            self.assertIn(campo, r.json()["errors"], dados)
        self.assertFalse(EscritorioContabil.objects.filter(nome="Z").exists())

    def test_so_a_plataforma_cria_e_edita(self):
        for u in (self.adm, self.membro, self.estranho):
            self.como(u)
            self.assertEqual(self.post("escritorio_criar", nome="Invasor").status_code, 403, u.username)
            self.assertEqual(self.post("escritorio_editar", self.esc.id, nome="Invasor").status_code, 403, u.username)
        self.assertFalse(EscritorioContabil.objects.filter(nome="Invasor").exists())

    def test_editar_e_desativar(self):
        r = self.post("escritorio_editar", self.esc.id, nome="Contábil X Renomeado", cnpj="")
        self.assertEqual(r.status_code, 200)
        self.esc.refresh_from_db()
        self.assertEqual((self.esc.nome, self.esc.cnpj, self.esc.is_ativo), ("Contábil X Renomeado", None, False))  # sem o switch = inativo
        self.assertEqual(self.acessiveis(self.contador), set())  # desativar corta a equipe
        self.post("escritorio_editar", self.esc.id, nome="Contábil X Renomeado", is_ativo="on")
        self.esc.refresh_from_db()
        self.assertTrue(self.esc.is_ativo)
        self.assertEqual(self.acessiveis(self.contador), {self.a.id, self.b.id})

    def test_editar_nome_repetido(self):
        r = self.post("escritorio_editar", self.esc.id, nome="Contábil Y", is_ativo="on")
        self.assertEqual(r.status_code, 400)
        self.esc.refresh_from_db()
        self.assertEqual(self.esc.nome, "Contábil X")

    def test_so_aceita_post(self):
        self.assertEqual(self.client.get(self.url("escritorio_criar")).status_code, 405)


class ConviteViewsTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()

    def criar(self, u, escritorio=None, **dados):
        self.como(u)
        return self.post("escritorio_convite_criar", (escritorio or self.esc).id, **{"email": "novo@x.com", "role": MEMBRO, **dados})

    def test_plataforma_convida_e_recebe_o_link_completo(self):
        r = self.criar(self.plat)
        self.assertEqual(r.status_code, 200)
        c = ConviteEscritorio.objects.get()
        self.assertEqual((c.email, c.role, c.convidado_por, c.status), ("novo@x.com", MEMBRO, self.plat, PENDENTE))
        self.assertEqual(r.json()["item"]["link"], f"http://testserver/escritorios/convite/{c.token}/")

    def test_admin_do_escritorio_convida_membro_mas_nao_admin(self):
        self.assertEqual(self.criar(self.adm).status_code, 200)
        r = self.criar(self.adm, email="outro@x.com", role=ADMIN)
        self.assertEqual(r.status_code, 403)
        self.assertIn("plataforma", r.json()["errors"]["__all__"][0])
        self.assertEqual(ConviteEscritorio.objects.count(), 1)

    def test_sem_permissao(self):
        for u in (self.membro, self.adm_y, self.estranho):
            self.assertEqual(self.criar(u).status_code, 403, u.username)
        self.assertEqual(ConviteEscritorio.objects.count(), 0)

    def test_erros_de_formulario_e_de_negocio(self):
        r = self.criar(self.plat, email="sem-arroba")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.json()["errors"])
        r = self.criar(self.plat, email="contador@x.com")  # já é membro ativo
        self.assertEqual(r.status_code, 400)
        self.assertIn("já pertence", r.json()["errors"]["__all__"][0])
        self.assertEqual(self.criar(self.plat, role="DONO").status_code, 400)
        self.assertEqual(ConviteEscritorio.objects.count(), 0)

    def test_escritorio_inexistente(self):
        self.como(self.plat)
        self.assertEqual(self.post("escritorio_convite_criar", 999999, email="a@x.com", role=MEMBRO).status_code, 404)

    def test_reenviar_e_cancelar(self):
        c = self.convidar("a@x.com", por=self.adm)
        self.como(self.adm)
        r = self.post("escritorio_convite_reenviar", self.esc.id, c.id)
        self.assertEqual(r.status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.tentativas_envio, 2)
        self.assertIn(str(c.token), r.json()["item"]["link"])
        self.assertEqual(self.post("escritorio_convite_cancelar", self.esc.id, c.id).status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteEscritorio.Status.CANCELLED)
        r = self.post("escritorio_convite_cancelar", self.esc.id, c.id)
        self.assertEqual(r.status_code, 400)  # já cancelado

    def test_convite_de_outro_escritorio_e_404(self):
        c = self.convidar("a@x.com", escritorio=self.esc_y, por=self.plat)
        self.como(self.plat)
        self.assertEqual(self.post("escritorio_convite_reenviar", self.esc.id, c.id).status_code, 404)
        self.assertEqual(self.post("escritorio_convite_cancelar", self.esc.id, c.id).status_code, 404)

    def test_admin_do_escritorio_nao_mexe_em_convite_de_admin_nem_de_outro_escritorio(self):
        convite_admin = self.convidar("adm2@x.com", ADMIN, por=self.plat)
        self.como(self.adm)
        self.assertEqual(self.post("escritorio_convite_cancelar", self.esc.id, convite_admin.id).status_code, 403)
        self.assertEqual(self.post("escritorio_convite_reenviar", self.esc.id, convite_admin.id).status_code, 403)
        self.como(self.adm_y)
        c = self.convidar("a@x.com", por=self.plat)
        self.assertEqual(self.post("escritorio_convite_cancelar", self.esc.id, c.id).status_code, 403)

    def test_detalhe_mostra_link_so_dos_validos_e_respeita_o_papel(self):
        valido = self.convidar("a@x.com", por=self.plat)
        cancelado = self.convidar("b@x.com", por=self.plat)
        svc.cancelar_convite(cancelado.id, self.plat)
        convite_admin = self.convidar("adm2@x.com", ADMIN, por=self.plat)
        self.como(self.adm)
        ctx = self.client.get(self.url("escritorio_detalhe", self.esc.id)).context
        por_email = {c.email: c for c in ctx["convites"]}
        self.assertIn(str(valido.token), por_email["a@x.com"].link)
        self.assertEqual(por_email["b@x.com"].link, "")
        self.assertFalse(por_email["adm2@x.com"].pode_gerir)       # admin do escritório não gere convite de admin
        self.assertFalse(por_email["adm2@x.com"].pode_reenviar_agora)
        self.assertTrue(por_email["a@x.com"].pode_reenviar_agora)
        self.assertEqual([v for v, _ in ctx["roles_convite"]], [MEMBRO])
        self.como(self.plat)
        ctx = self.client.get(self.url("escritorio_detalhe", self.esc.id)).context
        self.assertEqual([v for v, _ in ctx["roles_convite"]], [ADMIN, MEMBRO])
        self.assertTrue({c.email: c for c in ctx["convites"]}["adm2@x.com"].pode_gerir)


class MembroViewsTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.m_membro = MembroEscritorio.objects.get(usuario=self.membro)
        self.m_adm = MembroEscritorio.objects.get(usuario=self.adm)

    def editar(self, u, membro, **dados):
        self.como(u)
        return self.post("escritorio_membro_editar", self.esc.id, membro.id, **dados)

    def test_plataforma_altera_papel_e_status(self):
        r = self.editar(self.plat, self.m_membro, role=ADMIN, is_active="0")
        self.assertEqual((r.status_code, r.json()["role"], r.json()["is_active"]), (200, ADMIN, False))
        self.m_membro.refresh_from_db()
        self.assertEqual((self.m_membro.role, self.m_membro.is_active), (ADMIN, False))
        self.assertEqual(self.acessiveis(self.membro), set())  # desativado: sem acesso às empresas

    def test_so_o_status(self):
        self.editar(self.plat, self.m_membro, is_active="0")
        self.m_membro.refresh_from_db()
        self.assertEqual((self.m_membro.role, self.m_membro.is_active), (MEMBRO, False))
        self.editar(self.plat, self.m_membro, is_active="1")
        self.m_membro.refresh_from_db()
        self.assertTrue(self.m_membro.is_active)
        self.assertEqual(self.acessiveis(self.membro), {self.a.id, self.b.id})

    def test_admin_do_escritorio_desativa_membro_comum(self):
        self.assertEqual(self.editar(self.adm, self.m_membro, is_active="0").status_code, 200)
        self.m_membro.refresh_from_db()
        self.assertFalse(self.m_membro.is_active)

    def test_admin_do_escritorio_nao_promove_nem_mexe_em_si_nem_em_outro_admin(self):
        self.assertEqual(self.editar(self.adm, self.m_membro, role=ADMIN).status_code, 403)
        self.assertEqual(self.editar(self.adm, self.m_adm, is_active="0").status_code, 403)
        outro = MembroEscritorio.objects.create(escritorio=self.esc, usuario=user("adm3"), role=ADMIN)
        self.assertEqual(self.editar(self.adm, outro, is_active="0").status_code, 403)
        self.m_membro.refresh_from_db()
        self.m_adm.refresh_from_db()
        self.assertEqual((self.m_membro.role, self.m_adm.is_active), (MEMBRO, True))

    def test_sem_permissao(self):
        for u in (self.membro, self.adm_y, self.estranho):
            self.assertEqual(self.editar(u, self.m_membro, is_active="0").status_code, 403, u.username)

    def test_papel_invalido_e_membro_de_outro_escritorio(self):
        self.assertEqual(self.editar(self.plat, self.m_membro, role="DONO").status_code, 400)
        outro = MembroEscritorio.objects.get(usuario=self.adm_y)
        self.como(self.plat)
        self.assertEqual(self.post("escritorio_membro_editar", self.esc.id, outro.id, is_active="0").status_code, 404)

    def test_detalhe_marca_quem_pode_ser_alterado(self):
        self.como(self.adm)
        membros = {m.usuario_id: m for m in self.client.get(self.url("escritorio_detalhe", self.esc.id)).context["membros"]}
        self.assertTrue(membros[self.membro.id].pode_alterar)
        self.assertFalse(membros[self.adm.id].pode_alterar)  # ele mesmo
        self.como(self.plat)
        membros = {m.usuario_id: m for m in self.client.get(self.url("escritorio_detalhe", self.esc.id)).context["membros"]}
        self.assertTrue(all(m.pode_alterar for m in membros.values()))


class VinculoViewsTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.como(self.plat)

    def test_vincular_registra_quem_criou(self):
        r = self.post("escritorio_empresa_vincular", self.esc.id, empresa_id=self.c.id)
        self.assertEqual(r.status_code, 200)
        v = VinculoContabil.objects.get(escritorio=self.esc, empresa=self.c)
        self.assertEqual((v.is_active, v.criado_por), (True, self.plat))
        self.assertEqual(self.acessiveis(self.contador), {self.a.id, self.b.id, self.c.id})

    def test_empresa_de_outro_escritorio_e_recusada_com_o_nome_dele(self):
        r = self.post("escritorio_empresa_vincular", self.esc_y.id, empresa_id=self.a.id)  # a Alfa é da Contábil X
        self.assertEqual(r.status_code, 400)
        self.assertIn("Contábil X", r.json()["errors"]["__all__"][0])
        self.assertFalse(VinculoContabil.objects.filter(escritorio=self.esc_y).exists())

    def test_ja_vinculada_a_este_escritorio(self):
        r = self.post("escritorio_empresa_vincular", self.esc.id, empresa_id=self.a.id)
        self.assertEqual(r.status_code, 400)
        self.assertIn("já está vinculada a este escritório", r.json()["errors"]["__all__"][0])

    def test_empresa_invalida(self):
        for dados in ({}, {"empresa_id": "999999"}, {"empresa_id": "abc"}):
            self.assertEqual(self.post("escritorio_empresa_vincular", self.esc.id, **dados).status_code, 400, dados)

    def test_desvincular_e_reativar(self):
        r = self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="0")
        self.assertEqual((r.status_code, r.json()["is_active"]), (200, False))
        self.assertEqual(self.acessiveis(self.contador), {self.b.id})
        r = self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="1")
        self.assertEqual((r.status_code, r.json()["is_active"]), (200, True))
        self.assertEqual(self.acessiveis(self.contador), {self.a.id, self.b.id})

    def test_reativar_falha_se_outro_escritorio_assumiu_a_empresa(self):
        self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="0")
        self.assertEqual(self.post("escritorio_empresa_vincular", self.esc_y.id, empresa_id=self.a.id).status_code, 200)
        r = self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Contábil Y", r.json()["errors"]["__all__"][0])

    def test_vincular_de_novo_reativa_o_vinculo_existente(self):
        self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="0")
        self.assertEqual(self.post("escritorio_empresa_vincular", self.esc.id, empresa_id=self.a.id).status_code, 200)
        self.assertEqual(VinculoContabil.objects.filter(escritorio=self.esc, empresa=self.a).count(), 1)
        self.vinc_a.refresh_from_db()
        self.assertTrue(self.vinc_a.is_active)

    def test_so_a_plataforma_gerencia_vinculos(self):
        for u in (self.adm, self.membro, self.estranho):
            self.como(u)
            self.assertEqual(self.post("escritorio_empresa_vincular", self.esc.id, empresa_id=self.c.id).status_code, 403, u.username)
            self.assertEqual(self.post("escritorio_empresa_alterar", self.esc.id, self.vinc_a.id, ativo="0").status_code, 403, u.username)
        self.assertFalse(VinculoContabil.objects.filter(empresa=self.c).exists())
        self.vinc_a.refresh_from_db()
        self.assertTrue(self.vinc_a.is_active)

    def test_vinculo_de_outro_escritorio_e_404(self):
        self.assertEqual(self.post("escritorio_empresa_alterar", self.esc_y.id, self.vinc_a.id, ativo="0").status_code, 404)

    def test_admin_do_escritorio_ve_as_empresas_mas_sem_botoes_de_vinculo(self):
        self.como(self.adm)
        r = self.client.get(self.url("escritorio_detalhe", self.esc.id))
        self.assertEqual({v.empresa_id for v in r.context["vinculos"]}, {self.a.id, self.b.id})
        self.assertFalse(r.context["e_plataforma"])
        self.assertNotContains(r, 'id="edVincular"')
        self.como(self.plat)
        self.assertContains(self.client.get(self.url("escritorio_detalhe", self.esc.id)), 'id="edVincular"')


class EmpresasBuscarTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.a.cnpj, self.b.cnpj = "11.111.111/0001-11", "22222222000122"
        self.a.save(), self.b.save()
        self.como(self.plat)

    def buscar(self, **params):
        return self.client.get(self.url("empresas_buscar"), params).json()["empresas"]

    def test_so_plataforma(self):
        for u in (self.adm, self.membro, self.estranho):
            self.como(u)
            self.assertEqual(self.client.get(self.url("empresas_buscar")).status_code, 403, u.username)
        self.client.logout()
        self.assertEqual(self.client.get(self.url("empresas_buscar")).status_code, 302)

    def test_busca_por_nome_e_por_cnpj_com_ou_sem_mascara(self):
        self.assertEqual([e["nome"] for e in self.buscar(q="alfa")], ["Alfa Ltda"])
        self.assertEqual([e["nome"] for e in self.buscar(q="11.111.111/0001-11")], ["Alfa Ltda"])
        self.assertEqual([e["nome"] for e in self.buscar(q="22.222.222/0001-22")], ["Beta Ltda"])  # cadastrada sem máscara
        self.assertEqual([e["nome"] for e in self.buscar(q="zzz")], [])

    def test_informa_o_escritorio_atual_e_bloqueia(self):
        r = {e["nome"]: e for e in self.buscar(escritorio=self.esc_y.id)}
        self.assertEqual((r["Alfa Ltda"]["escritorio_atual"], r["Alfa Ltda"]["bloqueada"], r["Alfa Ltda"]["ja_neste"]), ("Contábil X", True, False))
        self.assertEqual((r["Gama Ltda"]["escritorio_atual"], r["Gama Ltda"]["bloqueada"]), ("", False))
        r = {e["nome"]: e for e in self.buscar(escritorio=self.esc.id)}
        self.assertTrue(r["Alfa Ltda"]["ja_neste"])

    def test_vinculo_inativo_nao_bloqueia(self):
        VinculoContabil.objects.filter(pk=self.vinc_a.pk).update(is_active=False)
        r = {e["nome"]: e for e in self.buscar()}
        self.assertFalse(r["Alfa Ltda"]["bloqueada"])

    def test_limite(self):
        from usuarios.models import Empresa
        for i in range(30):
            Empresa.objects.create(nome=f"Lote {i:02d}")
        self.assertEqual(len(self.buscar(q="Lote")), 20)


class AceitarConviteTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()
        self.convite = self.convidar("Nova@Contabil.com", MEMBRO, por=self.plat)
        self.link = self.url("aceitar_convite_escritorio", self.convite.token)

    def dados(self, **extra):
        return {"username": "nova", "first_name": "Nova", "last_name": "Pessoa",
                "password1": "Senha-forte-123", "password2": "Senha-forte-123", **extra}

    def test_token_invalido_mostra_a_pagina_de_convite_invalido(self):
        import uuid
        r = self.client.get(self.url("aceitar_convite_escritorio", uuid.uuid4()))
        self.assertContains(r, "Convite não encontrado")

    def test_estados_invalidos(self):
        casos = {
            ConviteEscritorio.Status.CANCELLED: "cancelado",
            ConviteEscritorio.Status.ACCEPTED: "já foi aceito",
        }
        for status, trecho in casos.items():
            ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=status)
            self.assertContains(self.client.get(self.link), trecho, msg_prefix=status)
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=PENDENTE, expira_em=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.client.get(self.link), "expirou")

    def test_escritorio_desativado(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        self.assertContains(self.client.get(self.link), "desativado")

    # ---- estado 1: e-mail sem conta, sem login
    def test_novo_usuario_ve_o_formulario(self):
        r = self.client.get(self.link)
        self.assertEqual(r.context["estado"], "novo")
        self.assertContains(r, "Criar conta e aceitar")
        self.assertContains(r, "Contábil X")
        self.assertContains(r, "nova@contabil.com")  # e-mail do convite em minúsculas

    def test_novo_usuario_cria_conta_entra_e_vai_para_o_painel(self):
        r = self.client.post(self.link, self.dados())
        self.assertRedirects(r, self.url("painel_contabilidade"), fetch_redirect_response=False)
        u = Usuario.objects.get(username="nova")
        self.assertEqual(u.email, "nova@contabil.com")
        self.assertTrue(MembroEscritorio.objects.filter(escritorio=self.esc, usuario=u, is_active=True, role=MEMBRO).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), u.id)  # já entrou
        self.assertEqual(self.acessiveis(u), {self.a.id, self.b.id})
        self.assertEqual(self.client.get(self.url("painel_contabilidade")).status_code, 200)

    def test_formulario_invalido_nao_cria_nada(self):
        for extra, campo in (({"password2": "outra"}, "password2"), ({"username": ""}, "username")):
            r = self.client.post(self.link, self.dados(**extra))
            self.assertEqual(r.status_code, 200)
            self.assertIn(campo, r.context["form"].errors)
        self.assertFalse(Usuario.objects.filter(username="nova").exists())
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, PENDENTE)

    def test_usuario_ja_em_uso_vira_erro_no_campo(self):
        user("nova")
        r = self.client.post(self.link, self.dados())
        self.assertEqual(r.status_code, 200)
        self.assertIn("username", r.context["form"].errors)
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, PENDENTE)

    def test_link_nao_vale_duas_vezes(self):
        self.client.post(self.link, self.dados())
        self.client.logout()
        self.assertContains(self.client.get(self.link), "já foi aceito")
        self.assertEqual(self.client.post(self.link, self.dados(username="outra")).status_code, 200)
        self.assertFalse(Usuario.objects.filter(username="outra").exists())

    # ---- estado 2: e-mail com conta, sem login
    def test_email_com_conta_pede_login_e_nao_mostra_formulario(self):
        user("ana", email="NOVA@contabil.com")
        r = self.client.get(self.link)
        self.assertEqual(r.context["estado"], "tem_conta")
        self.assertContains(r, "Entrar para aceitar")
        self.assertNotContains(r, "Criar conta e aceitar")
        self.assertNotContains(r, "ana")  # não revela o usuário a quem só tem o link
        self.assertEqual(r.context["login_url"], f"/login/?next=%2Fescritorios%2Fconvite%2F{self.convite.token}%2F")

    def test_post_anonimo_com_conta_existente_nao_cria_segundo_login(self):
        user("ana", email="nova@contabil.com")
        r = self.client.post(self.link, self.dados())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Usuario.objects.filter(email__iexact="nova@contabil.com").count(), 1)
        self.assertFalse(MembroEscritorio.objects.filter(usuario__username="nova").exists())

    def test_fluxo_completo_login_com_retorno_ao_convite(self):
        u = user("ana", email="nova@contabil.com")
        u.set_password("Senha-forte-123")
        u.save()
        login_url = self.client.get(self.link).context["login_url"]
        r = self.client.post(reverse("login"), {"username": "ana", "password": "Senha-forte-123", "next": login_url.split("next=")[1].replace("%2F", "/")})
        self.assertRedirects(r, self.link, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.link).context["estado"], "logado_ok")

    # ---- estado 3: logado com o e-mail do convite
    def test_logado_com_o_email_do_convite_aceita(self):
        u = user("ana", email="nova@contabil.com")
        self.como(u)
        r = self.client.get(self.link)
        self.assertEqual(r.context["estado"], "logado_ok")
        self.assertContains(r, "Aceitar convite")
        r = self.client.post(self.link)
        self.assertRedirects(r, self.url("painel_contabilidade"), fetch_redirect_response=False)
        self.assertTrue(MembroEscritorio.objects.filter(escritorio=self.esc, usuario=u, is_active=True).exists())
        self.assertEqual(Usuario.objects.filter(email__iexact="nova@contabil.com").count(), 1)
        self.assertEqual(self.acessiveis(u), {self.a.id, self.b.id})

    # ---- estado 4: logado com outro e-mail
    def test_logado_com_outro_email_e_avisado_e_nada_muda(self):
        outro = user("bia", email="bia@outro.com")
        self.como(outro)
        r = self.client.get(self.link)
        self.assertEqual(r.context["estado"], "logado_outro")
        self.assertContains(r, "nova@contabil.com")
        self.assertContains(r, "bia@outro.com")
        self.assertContains(r, reverse("logout"))
        self.assertNotContains(r, "Criar conta e aceitar")
        r = self.client.post(self.link, self.dados())
        self.assertEqual(r.status_code, 200)
        self.assertFalse(MembroEscritorio.objects.filter(usuario=outro, escritorio=self.esc).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), outro.id)  # a sessão não foi trocada
        self.assertFalse(Usuario.objects.filter(username="nova").exists())

    def test_logado_sem_email_cadastrado_tambem_nao_aceita(self):
        sem_email = user("semmail")
        self.como(sem_email)
        self.assertEqual(self.client.get(self.link).context["estado"], "logado_outro")


class NavegacaoEscritoriosTests(TelasMixin, TestCase):
    def setUp(self):
        self.montar_telas()

    def test_painel_oferece_gerenciar_equipe_so_ao_admin_do_escritorio(self):
        destino = self.url("escritorio_detalhe", self.esc.id)
        self.como(self.adm)
        self.assertContains(self.client.get(self.url("painel_contabilidade")), destino)
        self.como(self.membro)
        self.assertNotContains(self.client.get(self.url("painel_contabilidade")), destino)

    def test_admin_do_escritorio_chega_a_equipe_pelo_painel(self):
        self.como(self.adm)
        r = self.client.get(self.url("painel_contabilidade"))
        self.assertEqual([e.id for e in r.context["escritorios_admin"]], [self.esc.id])
        self.assertEqual(self.client.get(self.url("escritorio_detalhe", self.esc.id)).status_code, 200)

    def test_autocomplete_generico_e_o_de_contas_continuam_presentes(self):
        self.como(self.plat)
        html = self.client.get(self.url("escritorio_detalhe", self.esc.id)).content.decode()
        self.assertIn("window.autocomplete = function", html)
        # a tela de movimentações segue com o invólucro de contas sobre o genérico
        from df.models import Fundo
        fundo = Fundo.objects.create(empresa=self.a, nome="F", cnpj="99.999.999/0001-99", tipo_fundo="FIDC")
        s = self.client.session
        s["empresa_ativa_id"] = self.a.id
        s.save()
        html = self.client.get(reverse("saldos_iniciais_fundo", args=[fundo.id])).content.decode()
        self.assertIn("window.autocomplete = function", html)
        self.assertIn("window.contaAutocomplete = function", html)


class AdminDoConviteTests(TelasMixin, TestCase):
    def test_changelist_abre_e_nao_permite_alterar(self):
        self.montar_telas()
        self.convidar("a@x.com")
        admin = user("super", is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        r = self.client.get(reverse("admin:usuarios_conviteescritorio_changelist"))
        self.assertContains(r, "a@x.com")
        c = ConviteEscritorio.objects.get()
        self.assertEqual(self.client.get(reverse("admin:usuarios_conviteescritorio_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:usuarios_conviteescritorio_change", args=[c.pk]), {"email": "x@x.com"}).status_code, 403)
