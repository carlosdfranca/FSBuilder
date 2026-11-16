"""Regressão: toda página interna precisa do menu (o base.html não o inclui) e o login respeita o `next`."""
import pathlib
import re
from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from df.models import Fundo, PeriodoDF
from usuarios.models import Empresa, Membership, Usuario

# Páginas públicas ou de autenticação: não têm (nem devem ter) o menu.
SEM_MENU = {
    "login.html",
    "senha/esqueci_senha.html",
    "senha/esqueci_senha_enviado.html",
    "senha/link_invalido.html",
    "senha/redefinir_senha.html",
    "convites/aceitar_convite_escritorio.html",  # página pública: a pessoa ainda nem tem conta
}


class MenuPresenteTests(TestCase):
    def test_todo_template_interno_que_estende_a_base_inclui_o_navbar(self):
        raiz = pathlib.Path(settings.BASE_DIR) / "core" / "templates"
        faltando = []
        for caminho in sorted(raiz.rglob("*.html")):
            relativo = caminho.relative_to(raiz).as_posix()
            texto = caminho.read_text(encoding="utf-8")
            estende_base = re.search(r"{%\s*extends\s+['\"]base/base\.html['\"]", texto)
            if estende_base and "block conteudo" in texto and "navbar.html" not in texto and relativo not in SEM_MENU:
                faltando.append(relativo)
        self.assertEqual(faltando, [], f"Templates internos sem o menu: {faltando}")

    def test_as_telas_novas_renderizam_o_menu(self):
        empresa = Empresa.objects.create(nome="Empresa A")
        fundo = Fundo.objects.create(empresa=empresa, nome="Fundo A", cnpj="11.111.111/0001-11", tipo_fundo="FIDC")
        periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=empresa, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2099, 3, 31), data_referencia=date(2025, 12, 31),
            data_anterior=date(2024, 12, 31), status="nao_iniciada",
        )
        user = Usuario.objects.create_user(username="menu_user")
        Membership.objects.create(usuario=user, empresa=empresa, role=Membership.Role.MASTER, is_active=True)
        self.client.force_login(user)
        urls = [
            reverse("plano_contas_unico"),
            reverse("movimentacoes_fundo", args=[fundo.id]),
            reverse("movimentacoes_importar", args=[fundo.id]),
            reverse("saldos_iniciais_fundo", args=[fundo.id]),
            reverse("balancete_periodo", args=[periodo.id]),
        ]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, 'class="site-nav"', msg_prefix=url)


class LoginNextTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(username="logador", password="Senha-forte-123")

    def entrar(self, **extra):
        return self.client.post(reverse("login"), {"username": "logador", "password": "Senha-forte-123", **extra})

    def test_formulario_repassa_o_next(self):
        r = self.client.get(reverse("login"), {"next": "/fundos/"})
        self.assertContains(r, '<input type="hidden" name="next" value="/fundos/">', html=False)

    def test_sem_next_nao_ha_campo_escondido(self):
        self.assertNotContains(self.client.get(reverse("login")), 'name="next"')

    def test_depois_de_entrar_vai_para_o_destino(self):
        r = self.entrar(next="/fundos/")
        self.assertRedirects(r, "/fundos/", fetch_redirect_response=False)

    def test_login_required_leva_ao_destino_original(self):
        r = self.client.get(reverse("listar_fundos"))
        self.assertEqual(r.status_code, 302)
        destino = r["Location"]
        self.assertIn("next=", destino)
        pagina = self.client.get(destino)
        self.assertContains(pagina, 'name="next"')

    def test_destino_externo_e_ignorado(self):
        r = self.entrar(next="https://evil.example/roubo")
        self.assertRedirects(r, settings.LOGIN_REDIRECT_URL, fetch_redirect_response=False)

    def test_sem_next_vai_para_a_pagina_inicial(self):
        self.assertRedirects(self.entrar(), settings.LOGIN_REDIRECT_URL, fetch_redirect_response=False)
