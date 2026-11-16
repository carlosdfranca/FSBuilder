"""Perfil Contabilidade: escritório com vários usuários, vinculado a empresas."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from df.models import Conta, Fundo, Movimentacao, PeriodoDF
from df.tasks import _destinatarios_vencimento
from usuarios.models import (
    Empresa, EscritorioContabil, MembroEscritorio, Membership, Usuario, VinculoContabil,
)
from usuarios.permissions import ROLE_CONTABILIDADE, get_empresa_escopo, pode_lancar_movimentacoes, role_na_empresa
from usuarios.utils.acesso import empresas_acessiveis, escritorios_do_usuario, tem_acesso_contabil
from usuarios.utils.company_scope import query_por_empresa_ativa


def user(nome, **kw):
    return Usuario.objects.create_user(username=nome, **kw)  # sem senha: force_login não precisa


class ContabilidadeMixin:
    def montar(self):
        self.a = Empresa.objects.create(nome="Alfa Ltda")
        self.b = Empresa.objects.create(nome="Beta Ltda")
        self.c = Empresa.objects.create(nome="Gama Ltda")
        self.esc = EscritorioContabil.objects.create(nome="Contábil X")
        self.contador = user("contador", email="contador@x.com")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.contador)
        self.vinc_a = VinculoContabil.objects.create(escritorio=self.esc, empresa=self.a)
        self.vinc_b = VinculoContabil.objects.create(escritorio=self.esc, empresa=self.b)
        self.fundo_a = self.fundo(self.a, "Fundo A", "11.111.111/0001-11")
        self.fundo_b = self.fundo(self.b, "Fundo B", "22.222.222/0001-22")
        self.fundo_c = self.fundo(self.c, "Fundo C", "33.333.333/0001-33")

    @staticmethod
    def fundo(empresa, nome, cnpj):
        return Fundo.objects.create(empresa=empresa, nome=nome, cnpj=cnpj, tipo_fundo="FIDC")

    def ids(self, u):
        return set(empresas_acessiveis(u).values_list("id", flat=True))


class ModelosTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_uma_empresa_nao_tem_dois_escritorios_ativos(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        with self.assertRaises(ValidationError) as ctx:
            VinculoContabil.objects.create(escritorio=outro, empresa=self.a)
        self.assertIn("Contábil X", str(ctx.exception))
        self.assertEqual(VinculoContabil.objects.filter(empresa=self.a).count(), 1)

    def test_depois_de_desativar_outro_escritorio_pode_assumir(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        self.vinc_a.is_active = False
        self.vinc_a.save()
        VinculoContabil.objects.create(escritorio=outro, empresa=self.a)
        self.assertEqual(VinculoContabil.objects.filter(empresa=self.a, is_active=True).count(), 1)

    def test_reativar_com_outro_ativo_e_recusado(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        self.vinc_a.is_active = False
        self.vinc_a.save()
        VinculoContabil.objects.create(escritorio=outro, empresa=self.a)
        self.vinc_a.is_active = True
        with self.assertRaises(ValidationError):
            self.vinc_a.save()

    def test_vinculos_inativos_podem_se_repetir(self):
        e2, e3 = (EscritorioContabil.objects.create(nome=n) for n in ("Y", "Z"))
        VinculoContabil.objects.create(escritorio=e2, empresa=self.c, is_active=False)
        VinculoContabil.objects.create(escritorio=e3, empresa=self.c, is_active=False)
        self.assertEqual(VinculoContabil.objects.filter(empresa=self.c).count(), 2)

    def test_mesmo_usuario_nao_entra_duas_vezes_no_escritorio(self):
        with self.assertRaises(IntegrityError):
            MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.contador)

    def test_mesma_empresa_nao_duplica_no_mesmo_escritorio(self):
        with self.assertRaises((IntegrityError, ValidationError)):
            VinculoContabil.objects.create(escritorio=self.esc, empresa=self.a)


class EmpresasAcessiveisTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_contador_alcanca_so_as_empresas_vinculadas(self):
        self.assertEqual(self.ids(self.contador), {self.a.id, self.b.id})

    def test_vinculo_direto_soma_com_o_do_escritorio_sem_duplicar(self):
        Membership.objects.create(usuario=self.contador, empresa=self.a, role="VIEWER")
        Membership.objects.create(usuario=self.contador, empresa=self.c, role="MEMBER")
        qs = empresas_acessiveis(self.contador)
        self.assertEqual(sorted(qs.values_list("id", flat=True)), sorted([self.a.id, self.b.id, self.c.id]))
        self.assertEqual(qs.count(), 3)

    def test_vinculo_inativo_tira_o_acesso(self):
        self.vinc_b.is_active = False
        self.vinc_b.save()
        self.assertEqual(self.ids(self.contador), {self.a.id})

    def test_membro_inativo_perde_o_acesso(self):
        MembroEscritorio.objects.filter(usuario=self.contador).update(is_active=False)
        self.assertEqual(self.ids(self.contador), set())
        self.assertFalse(escritorios_do_usuario(self.contador).exists())

    def test_escritorio_desativado_suspende_toda_a_equipe(self):
        colega = user("colega")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=colega)
        self.assertEqual(self.ids(colega), {self.a.id, self.b.id})
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        self.assertEqual((self.ids(self.contador), self.ids(colega)), (set(), set()))

    def test_equipe_toda_enxerga_as_mesmas_empresas(self):
        colega = user("colega")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=colega)
        self.assertEqual(self.ids(colega), self.ids(self.contador))

    def test_usuario_de_fora_nao_alcanca_nada(self):
        self.assertEqual(self.ids(user("solto")), set())

    def test_globais_veem_todas(self):
        plat = user("plat", global_role=Usuario.GlobalRole.PLATFORM_VIEWER)
        self.assertEqual(self.ids(plat), {self.a.id, self.b.id, self.c.id})

    def test_dois_escritorios(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        MembroEscritorio.objects.create(escritorio=outro, usuario=self.contador)
        VinculoContabil.objects.create(escritorio=outro, empresa=self.c)
        self.assertEqual(self.ids(self.contador), {self.a.id, self.b.id, self.c.id})
        self.assertEqual(escritorios_do_usuario(self.contador).count(), 2)

    def test_tem_acesso_contabil(self):
        self.assertTrue(tem_acesso_contabil(self.contador, self.a))
        self.assertFalse(tem_acesso_contabil(self.contador, self.c))
        self.assertFalse(tem_acesso_contabil(user("solto"), self.a))


class PapelEPermissoesTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_role_na_empresa(self):
        self.assertEqual(role_na_empresa(self.contador, self.a), ROLE_CONTABILIDADE)
        self.assertIsNone(role_na_empresa(self.contador, self.c))
        Membership.objects.create(usuario=self.contador, empresa=self.a, role="VIEWER")
        self.assertEqual(role_na_empresa(self.contador, self.a), "VIEWER")  # o vínculo direto manda

    def test_pode_lancar(self):
        self.assertTrue(pode_lancar_movimentacoes(self.contador, self.a))
        self.assertFalse(pode_lancar_movimentacoes(self.contador, self.c))
        # mesmo com um vínculo direto mais restrito, o escritório continua podendo lançar
        Membership.objects.create(usuario=self.contador, empresa=self.a, role="VIEWER")
        self.assertTrue(pode_lancar_movimentacoes(self.contador, self.a))
        viewer = user("viewer")
        Membership.objects.create(usuario=viewer, empresa=self.a, role="VIEWER")
        self.assertFalse(pode_lancar_movimentacoes(viewer, self.a))

    def test_escopo_padrao_e_a_primeira_empresa_por_nome(self):
        req = RequestFactory().get("/")
        req.user = self.contador
        self.assertEqual(get_empresa_escopo(req), self.a)  # Alfa vem antes de Beta

    def pagina_do_fundo(self, nome, fundo):
        return self.client.get(reverse(nome, args=[fundo.id]))

    def test_contador_ve_e_lanca_nas_empresas_vinculadas(self):
        self.client.force_login(self.contador)
        self.assertEqual(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_a).status_code, 200)
        self.assertTrue(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_a).context["pode_lancar"])
        self.assertEqual(self.pagina_do_fundo("saldos_iniciais_fundo", self.fundo_a).status_code, 200)

    def test_contador_nao_ve_empresa_sem_vinculo(self):
        self.client.force_login(self.contador)
        self.assertEqual(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_c).status_code, 404)

    def test_contador_lanca_movimentacao(self):
        Conta.objects.create(codigo="1", descricao="ATIVO", tipo="S", nivel=1, natureza=1)
        caixa = Conta.objects.create(
            codigo="1.01", descricao="Caixa", tipo="A", nivel=2, natureza=1, conta_superior=Conta.objects.get(codigo="1")
        )
        self.client.force_login(self.contador)
        r = self.client.post(
            reverse("movimentacao_criar", args=[self.fundo_a.id]),
            {"data": "2025-03-10", "conta_id": caixa.id, "valor": "10,00"},
        )
        self.assertEqual(r.status_code, 200)
        m = Movimentacao.objects.get()
        self.assertEqual((m.empresa, m.criado_por), (self.a, self.contador))

    def test_contador_nao_lanca_em_empresa_sem_vinculo(self):
        self.client.force_login(self.contador)
        r = self.client.post(reverse("movimentacao_criar", args=[self.fundo_c.id]), {})
        self.assertIn(r.status_code, (403, 404))
        self.assertFalse(Movimentacao.objects.exists())

    def test_contador_nao_gerencia_fundos_periodos_dados_nem_usuarios(self):
        self.client.force_login(self.contador)
        r = self.client.post(reverse("criar_periodo_manual", args=[self.fundo_a.id]), {})
        self.assertEqual(r.status_code, 403)  # company_can_manage_fundos
        periodo = PeriodoDF.objects.create(
            fundo=self.fundo_a, empresa=self.a, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), status="nao_iniciada",
        )
        r = self.client.post(reverse("salvar_contexto_operacional", args=[periodo.id]), {"contexto_operacional": "x"})
        self.assertEqual(r.status_code, 403)  # company_can_manage_data
        r = self.client.get(reverse("gerenciar_usuarios"))
        self.assertNotEqual(r.status_code, 200)

    def test_desvincular_a_empresa_tira_o_acesso_na_hora(self):
        self.client.force_login(self.contador)
        self.client.post(reverse("trocar_empresa_ativa"), {"empresa_id": self.b.id})
        self.assertEqual(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_b).status_code, 200)
        VinculoContabil.objects.filter(pk=self.vinc_b.pk).update(is_active=False)
        self.assertEqual(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_b).status_code, 404)
        self.assertEqual(self.pagina_do_fundo("movimentacoes_fundo", self.fundo_a).status_code, 200)


class MiddlewareTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()
        self.client.force_login(self.contador)

    def ativa(self):
        return self.client.get(reverse("painel_contabilidade")).wsgi_request.empresa_ativa

    def sessao(self, empresa_id):
        s = self.client.session
        s["empresa_ativa_id"] = empresa_id
        s.save()

    def test_sem_sessao_usa_a_primeira_acessivel_e_grava(self):
        self.assertEqual(self.ativa(), self.a)
        self.assertEqual(self.client.session["empresa_ativa_id"], self.a.id)

    def test_respeita_a_empresa_valida_da_sessao(self):
        self.sessao(self.b.id)
        self.assertEqual(self.ativa(), self.b)

    def test_empresa_sem_acesso_na_sessao_e_descartada(self):
        self.sessao(self.c.id)  # id adulterado ou acesso revogado
        self.assertEqual(self.ativa(), self.a)
        self.assertEqual(self.client.session["empresa_ativa_id"], self.a.id)

    def test_empresa_inexistente_na_sessao(self):
        self.sessao(999999)
        self.assertEqual(self.ativa(), self.a)

    def test_acesso_revogado_derruba_a_empresa_ativa(self):
        self.sessao(self.b.id)
        VinculoContabil.objects.filter(pk=self.vinc_b.pk).update(is_active=False)
        self.assertEqual(self.ativa(), self.a)

    def test_sem_nenhum_acesso_fica_sem_empresa(self):
        MembroEscritorio.objects.filter(usuario=self.contador).update(is_active=False)
        self.sessao(self.a.id)
        self.assertIsNone(self.ativa())
        self.assertNotIn("empresa_ativa_id", self.client.session)

    def test_global_mantem_a_empresa_escolhida(self):
        plat = user("plat", global_role=Usuario.GlobalRole.PLATFORM_ADMIN)
        self.client.force_login(plat)
        self.sessao(self.c.id)
        self.assertEqual(self.ativa(), self.c)

    def test_global_sem_escolha_nao_ganha_empresa(self):
        plat = user("plat", global_role=Usuario.GlobalRole.PLATFORM_ADMIN)
        self.client.force_login(plat)
        self.assertIsNone(self.ativa())


class TrocarEmpresaTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()
        self.client.force_login(self.contador)
        self.url = reverse("trocar_empresa_ativa")

    def test_troca_para_empresa_vinculada(self):
        r = self.client.post(self.url, {"empresa_id": self.b.id})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session["empresa_ativa_id"], self.b.id)

    def test_nao_troca_para_empresa_sem_vinculo(self):
        self.client.post(self.url, {"empresa_id": self.a.id})
        r = self.client.post(self.url, {"empresa_id": self.c.id})
        self.assertEqual(self.client.session["empresa_ativa_id"], self.a.id)
        self.assertIn("não tem acesso", " ".join(m.message for m in get_messages(r.wsgi_request)))

    def test_next_do_mesmo_host_e_respeitado(self):
        r = self.client.post(self.url, {"empresa_id": self.b.id, "next": reverse("controle_emissoes")})
        self.assertEqual(r["Location"], reverse("controle_emissoes"))

    def test_next_externo_e_ignorado(self):
        r = self.client.post(self.url, {"empresa_id": self.b.id, "next": "https://evil.example/x"}, HTTP_REFERER="/volta/")
        self.assertEqual(r["Location"], "/volta/")

    def test_selecao_lista_so_o_que_alcanca(self):
        r = self.client.get(reverse("selecionar_empresa"))
        self.assertEqual({e.id for e in r.context["empresas"]}, {self.a.id, self.b.id})
        r = self.client.post(reverse("selecionar_empresa"), {"empresa_id": self.c.id})
        self.assertEqual(r.status_code, 302)
        self.assertNotEqual(self.client.session.get("empresa_ativa_id"), str(self.c.id))


class EscopoDeConsultaTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def consulta(self, empresa_ativa):
        req = RequestFactory().get("/")
        req.user = self.contador
        req.empresa_ativa = empresa_ativa
        return set(query_por_empresa_ativa(Fundo.objects.all(), req, "empresa").values_list("nome", flat=True))

    def test_com_empresa_ativa_filtra_so_por_ela(self):
        self.assertEqual(self.consulta(self.a), {"Fundo A"})
        self.assertEqual(self.consulta(self.b), {"Fundo B"})

    def test_empresa_ativa_sem_acesso_nao_vaza(self):
        self.assertEqual(self.consulta(self.c), set())

    def test_sem_empresa_ativa_vale_tudo_que_alcanca(self):
        self.assertEqual(self.consulta(None), {"Fundo A", "Fundo B"})

    def test_membership_multiplo_tambem_filtra_pela_ativa(self):
        multi = user("multi")
        Membership.objects.create(usuario=multi, empresa=self.a, role="MEMBER")
        Membership.objects.create(usuario=multi, empresa=self.c, role="MEMBER")
        req = RequestFactory().get("/")
        req.user, req.empresa_ativa = multi, self.c
        self.assertEqual(set(query_por_empresa_ativa(Fundo.objects.all(), req).values_list("nome", flat=True)), {"Fundo C"})


class NavegacaoTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def test_contador_com_duas_empresas_ve_o_seletor_e_o_painel(self):
        self.client.force_login(self.contador)
        r = self.client.get(reverse("controle_emissoes"))
        ctx = r.context
        self.assertTrue(ctx["pode_trocar_empresa"])
        self.assertTrue(ctx["user_is_contabilidade"])
        self.assertContains(r, 'name="empresa_id"')
        self.assertContains(r, reverse("painel_contabilidade"))
        self.assertEqual([e.id for e in ctx["empresas_disponiveis"]], [self.a.id, self.b.id])

    def test_usuario_comum_com_uma_empresa_nao_ve_seletor_nem_painel(self):
        u = user("comum")
        Membership.objects.create(usuario=u, empresa=self.a, role="VIEWER")
        self.client.force_login(u)
        r = self.client.get(reverse("controle_emissoes"))
        self.assertFalse(r.context["pode_trocar_empresa"])
        self.assertFalse(r.context["user_is_contabilidade"])
        self.assertNotContains(r, reverse("painel_contabilidade"))

    def test_usuario_com_duas_empresas_diretas_agora_pode_trocar(self):
        u = user("duas")
        Membership.objects.create(usuario=u, empresa=self.a, role="MEMBER")
        Membership.objects.create(usuario=u, empresa=self.c, role="MEMBER")
        self.client.force_login(u)
        self.assertTrue(self.client.get(reverse("controle_emissoes")).context["pode_trocar_empresa"])

    def test_contabilidade_nao_ve_gestao_de_usuarios(self):
        self.client.force_login(self.contador)
        ctx = self.client.get(reverse("controle_emissoes")).context
        self.assertFalse(ctx["user_can_view_company_users"])
        self.assertFalse(ctx["user_can_manage_company_users"])


class PainelTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()
        hoje = date.today()
        PeriodoDF.objects.create(fundo=self.fundo_a, empresa=self.a, tipo_periodo="anual", ano=2024,
                                 data_vencimento=hoje - timedelta(days=10), status="vencida")
        PeriodoDF.objects.create(fundo=self.fundo_a, empresa=self.a, tipo_periodo="anual", ano=2025,
                                 data_vencimento=hoje + timedelta(days=20), status="em_andamento")
        PeriodoDF.objects.create(fundo=self.fundo_a, empresa=self.a, tipo_periodo="anual", ano=2023,
                                 data_vencimento=hoje - timedelta(days=400), status="finalizada")
        PeriodoDF.objects.create(fundo=self.fundo_c, empresa=self.c, tipo_periodo="anual", ano=2025,
                                 data_vencimento=hoje, status="vencida")
        self.client.force_login(self.contador)

    def test_mostra_so_as_empresas_vinculadas_com_os_numeros(self):
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertEqual(r.status_code, 200)
        linhas = {l["empresa"].nome: l for l in r.context["linhas"]}
        self.assertEqual(set(linhas), {"Alfa Ltda", "Beta Ltda"})  # a Gama não aparece
        a = linhas["Alfa Ltda"]["empresa"]
        self.assertEqual((a.qtd_fundos, a.periodos_abertos, a.periodos_vencidos), (1, 2, 1))
        self.assertEqual(a.proximo_vencimento, date.today() - timedelta(days=10))
        self.assertTrue(linhas["Alfa Ltda"]["atrasado"])
        b = linhas["Beta Ltda"]["empresa"]
        self.assertEqual((b.qtd_fundos, b.periodos_abertos, b.proximo_vencimento), (1, 0, None))
        self.assertEqual((r.context["total_abertos"], r.context["total_vencidos"]), (2, 1))

    def test_tem_o_formulario_de_abrir_empresa(self):
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertContains(r, reverse("trocar_empresa_ativa"))
        self.assertContains(r, f'value="{self.a.id}"')
        self.assertNotContains(r, f'name="empresa_id" value="{self.c.id}"')

    def test_quem_nao_e_de_escritorio_volta_para_a_df(self):
        self.client.force_login(user("solto"))
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertRedirects(r, reverse("demonstracao_financeira"), fetch_redirect_response=False)

    def test_escritorio_desativado_nao_abre_o_painel(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        self.assertEqual(self.client.get(reverse("painel_contabilidade")).status_code, 302)

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("painel_contabilidade")).status_code, 302)

    def test_vinculo_inativo_some_do_painel(self):
        VinculoContabil.objects.filter(pk=self.vinc_b.pk).update(is_active=False)
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertEqual([l["empresa"].nome for l in r.context["linhas"]], ["Alfa Ltda"])

    def test_dois_escritorios_mostram_a_coluna_de_escritorio(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        MembroEscritorio.objects.create(escritorio=outro, usuario=self.contador)
        VinculoContabil.objects.create(escritorio=outro, empresa=self.c)
        r = self.client.get(reverse("painel_contabilidade"))
        self.assertTrue(r.context["mais_de_um_escritorio"])
        por_empresa = {l["empresa"].nome: l["escritorio"] for l in r.context["linhas"]}
        self.assertEqual(por_empresa, {"Alfa Ltda": "Contábil X", "Beta Ltda": "Contábil X", "Gama Ltda": "Contábil Y"})


class NotificacaoDeVencimentoTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()

    def destinatarios(self, empresa=None):
        memb = Membership.objects.filter(empresa=empresa or self.a, role__in=["MASTER", "ADMIN", "MEMBER"], is_active=True)
        return _destinatarios_vencimento(empresa or self.a, memb)

    def test_inclui_a_equipe_do_escritorio_vinculado(self):
        colega = user("colega", email="colega@x.com")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=colega)
        dono = user("dono", email="dono@a.com")
        Membership.objects.create(usuario=dono, empresa=self.a, role="MASTER")
        self.assertEqual(self.destinatarios(), sorted([self.contador.id, colega.id, dono.id]))

    def test_sem_repeticao_quando_esta_nos_dois_lugares(self):
        Membership.objects.create(usuario=self.contador, empresa=self.a, role="MEMBER")
        self.assertEqual(self.destinatarios(), [self.contador.id])

    def test_ignora_sem_email_inativo_e_vinculo_desativado(self):
        sem_email = user("semmail")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=sem_email)
        inativo_na_equipe = user("inativo_eq", email="i@x.com")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=inativo_na_equipe, is_active=False)
        conta_inativa = user("conta_inativa", email="ci@x.com", is_active=False)
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=conta_inativa)
        self.assertEqual(self.destinatarios(), [self.contador.id])
        VinculoContabil.objects.filter(pk=self.vinc_a.pk).update(is_active=False)
        self.assertEqual(self.destinatarios(), [])

    def test_empresa_sem_escritorio_so_avisa_os_membros(self):
        dono = user("dono_c", email="c@c.com")
        Membership.objects.create(usuario=dono, empresa=self.c, role="ADMIN")
        self.assertEqual(self.destinatarios(self.c), [dono.id])

    def test_viewer_da_empresa_continua_sem_receber(self):
        v = user("viewer_a", email="v@a.com")
        Membership.objects.create(usuario=v, empresa=self.a, role="VIEWER")
        self.assertNotIn(v.id, self.destinatarios())


class AdminTests(ContabilidadeMixin, TestCase):
    def setUp(self):
        self.montar()
        self.admin = user("super", is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)

    def test_paginas_do_admin_abrem(self):
        for nome, args in (
            ("admin:usuarios_escritoriocontabil_changelist", []),
            ("admin:usuarios_escritoriocontabil_add", []),
            ("admin:usuarios_escritoriocontabil_change", [self.esc.pk]),
            ("admin:usuarios_empresa_change", [self.a.pk]),
        ):
            self.assertEqual(self.client.get(reverse(nome, args=args)).status_code, 200, nome)

    def test_lista_mostra_membros_e_empresas(self):
        r = self.client.get(reverse("admin:usuarios_escritoriocontabil_changelist"))
        self.assertContains(r, "Contábil X")

    def test_admin_preenche_criado_por_e_recusa_segundo_escritorio(self):
        outro = EscritorioContabil.objects.create(nome="Contábil Y")
        dados = {
            "nome": "Contábil Y", "cnpj": "", "is_ativo": "on",
            "membros-TOTAL_FORMS": "0", "membros-INITIAL_FORMS": "0", "membros-MIN_NUM_FORMS": "0", "membros-MAX_NUM_FORMS": "1000",
            "vinculos-TOTAL_FORMS": "1", "vinculos-INITIAL_FORMS": "0", "vinculos-MIN_NUM_FORMS": "0", "vinculos-MAX_NUM_FORMS": "1000",
            "vinculos-0-empresa": str(self.a.id), "vinculos-0-is_active": "on", "vinculos-0-escritorio": str(outro.id),
        }
        r = self.client.post(reverse("admin:usuarios_escritoriocontabil_change", args=[outro.pk]), dados)
        self.assertEqual(r.status_code, 200)  # voltou ao formulário com erro: a Alfa já tem escritório ativo
        self.assertFalse(VinculoContabil.objects.filter(escritorio=outro).exists())
        # empresa livre: grava e registra quem criou
        dados["vinculos-0-empresa"] = str(self.c.id)
        r = self.client.post(reverse("admin:usuarios_escritoriocontabil_change", args=[outro.pk]), dados)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(VinculoContabil.objects.get(escritorio=outro).criado_por, self.admin)
