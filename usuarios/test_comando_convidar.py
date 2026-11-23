"""Comando `convidar_escritorio`: cria o convite e entrega o link pelo terminal."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from usuarios.models import ConviteEscritorio, EscritorioContabil, MembroEscritorio, Usuario
from usuarios.services import convite_escritorio_service as svc
from usuarios.test_contabilidade import user
from usuarios.test_convite_escritorio import ADMIN, EquipeMixin, MEMBRO

CANCELADO = ConviteEscritorio.Status.CANCELLED


@override_settings(BASE_URL="http://localhost:8000")
class ComandoConvidarTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def rodar(self, *args, **kw):
        saida = StringIO()
        call_command("convidar_escritorio", *args, stdout=saida, **kw)
        return saida.getvalue()

    def test_cria_o_convite_e_imprime_o_link_sem_enviar_email(self):
        with mock.patch.object(svc, "_agendar_envio") as agendar:
            texto = self.rodar("Maria@Contabil.com", escritorio="contábil x")  # nome sem diferenciar maiúsculas
        agendar.assert_not_called()
        c = ConviteEscritorio.objects.get()
        self.assertEqual((c.email, c.role, c.escritorio, c.convidado_por), ("maria@contabil.com", MEMBRO, self.esc, self.plat))
        self.assertIn(f"http://localhost:8000/escritorios/convite/{c.token}/", texto)
        self.assertIn("não enviado", texto)
        self.assertIn("Contábil X", texto)

    def test_enviar_agenda_o_email(self):
        with mock.patch.object(svc, "_agendar_envio") as agendar:
            texto = self.rodar("a@x.com", escritorio="Contábil X", enviar=True)
        agendar.assert_called_once_with(ConviteEscritorio.objects.get().id)
        self.assertIn("enviado", texto)
        self.assertNotIn("não enviado", texto)

    def test_papel_admin(self):
        self.rodar("a@x.com", escritorio="Contábil X", papel="ADMIN")
        self.assertEqual(ConviteEscritorio.objects.get().role, ADMIN)

    def test_usa_o_admin_da_plataforma_ou_o_informado(self):
        self.rodar("a@x.com", escritorio="Contábil X")
        self.assertEqual(ConviteEscritorio.objects.get().convidado_por, self.plat)
        outro = user("outro_admin", global_role=Usuario.GlobalRole.PLATFORM_ADMIN)
        self.rodar("b@x.com", escritorio="Contábil X", por="outro_admin")
        self.assertEqual(ConviteEscritorio.objects.get(email="b@x.com").convidado_por, outro)

    def test_superusuario_serve_como_quem_convida(self):
        Usuario.objects.filter(pk=self.plat.pk).update(global_role=Usuario.GlobalRole.NONE)
        root = user("root", is_superuser=True)
        self.rodar("a@x.com", escritorio="Contábil X")
        self.assertEqual(ConviteEscritorio.objects.get().convidado_por, root)

    def test_sem_nenhum_admin_pede_o_parametro_por(self):
        Usuario.objects.filter(pk=self.plat.pk).update(global_role=Usuario.GlobalRole.NONE)
        with self.assertRaisesMessage(CommandError, "--por"):
            self.rodar("a@x.com", escritorio="Contábil X")

    def test_por_inexistente_ou_sem_permissao(self):
        with self.assertRaisesMessage(CommandError, "não encontrado"):
            self.rodar("a@x.com", escritorio="Contábil X", por="ninguem")
        with self.assertRaises(CommandError):
            self.rodar("a@x.com", escritorio="Contábil X", por="estranho")  # existe, mas não gerencia o escritório
        self.assertFalse(ConviteEscritorio.objects.exists())

    def test_escritorio_inexistente_lista_os_existentes(self):
        with self.assertRaises(CommandError) as ctx:
            self.rodar("a@x.com", escritorio="Não Existe")
        self.assertIn("Contábil X", str(ctx.exception))
        self.assertIn("--criar-escritorio", str(ctx.exception))
        self.assertFalse(EscritorioContabil.objects.filter(nome="Não Existe").exists())

    def test_criar_escritorio_na_hora(self):
        texto = self.rodar("a@x.com", escritorio="Escritório Novo", criar_escritorio=True, cnpj="12.345.678/0001-95")
        e = EscritorioContabil.objects.get(nome="Escritório Novo")
        self.assertEqual((e.cnpj, e.is_ativo), ("12.345.678/0001-95", True))
        self.assertIn("criado", texto)
        self.assertEqual(ConviteEscritorio.objects.get().escritorio, e)

    def test_email_invalido_ou_de_quem_ja_e_membro(self):
        with self.assertRaises(CommandError):
            self.rodar("sem-arroba", escritorio="Contábil X")
        with self.assertRaisesMessage(CommandError, "já pertence"):
            self.rodar("contador@x.com", escritorio="Contábil X")
        self.assertFalse(ConviteEscritorio.objects.exists())

    def test_novo_convite_cancela_o_pendente_do_mesmo_email(self):
        self.rodar("a@x.com", escritorio="Contábil X")
        self.rodar("a@x.com", escritorio="Contábil X")
        self.assertEqual(ConviteEscritorio.objects.filter(status=CANCELADO).count(), 1)
        self.assertEqual(ConviteEscritorio.objects.filter(status=ConviteEscritorio.Status.PENDING).count(), 1)

    def test_avisa_quando_o_email_ja_tem_conta(self):
        user("ana", email="ana@x.com")
        self.assertIn("já tem conta", self.rodar("ana@x.com", escritorio="Contábil X"))
        self.assertNotIn("já tem conta", self.rodar("nova@x.com", escritorio="Contábil X"))

    def test_o_link_impresso_funciona_no_aceite(self):
        texto = self.rodar("nova@x.com", escritorio="Contábil X")
        caminho = texto.split("http://localhost:8000")[1].split()[0]
        r = self.client.get(caminho)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["estado"], "novo")

    def test_base_url_configurada_aparece_no_link(self):
        with override_settings(BASE_URL="https://app.exemplo.com/"):
            texto = self.rodar("a@x.com", escritorio="Contábil X")
        self.assertIn("https://app.exemplo.com/escritorios/convite/", texto)


class CriarConviteSemEmailTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def test_enviar_email_falso_nao_agenda_envio(self):
        with mock.patch.object(svc, "_agendar_envio") as agendar:
            svc.criar_convite(escritorio=self.esc, email="a@x.com", role=MEMBRO, convidado_por=self.plat, enviar_email=False)
        agendar.assert_not_called()

    def test_padrao_continua_enviando(self):
        with mock.patch.object(svc, "_agendar_envio") as agendar:
            c = svc.criar_convite(escritorio=self.esc, email="a@x.com", role=MEMBRO, convidado_por=self.plat)
        agendar.assert_called_once_with(c.id)
