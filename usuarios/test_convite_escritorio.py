"""Convite por e-mail para a equipe de um escritório de contabilidade."""
import uuid
from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from usuarios.models import ConviteEscritorio, EscritorioContabil, MembroEscritorio, Usuario
from usuarios.services import convite_escritorio_service as svc
from usuarios.services.convite_escritorio_service import (
    ConviteEscritorioError, ConviteEscritorioInvalidoError, JaEhMembroError, LimiteReenvioExcedidoError,
)
from usuarios.tasks import (
    enviar_email_convite_escritorio_async, enviar_email_convite_escritorio_sync, expirar_convites_antigos,
)
from usuarios.test_contabilidade import ContabilidadeMixin, user

ADMIN, MEMBRO = MembroEscritorio.Role.ADMIN, MembroEscritorio.Role.MEMBRO
PENDENTE, ACEITO, CANCELADO, EXPIRADO = (
    ConviteEscritorio.Status.PENDING, ConviteEscritorio.Status.ACCEPTED,
    ConviteEscritorio.Status.CANCELLED, ConviteEscritorio.Status.EXPIRED,
)


class EquipeMixin(ContabilidadeMixin):
    """Escritório 'Contábil X' com um admin e um membro comum (`contador`); 'Contábil Y' à parte."""

    def montar_equipe(self):
        self.montar()
        self.plat = user("plat", global_role=Usuario.GlobalRole.PLATFORM_ADMIN)
        self.adm = user("adm_esc", email="adm@x.com")
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.adm, role=ADMIN)
        self.membro = self.contador  # MEMBRO
        self.esc_y = EscritorioContabil.objects.create(nome="Contábil Y")
        self.adm_y = user("adm_y", email="adm@y.com")
        MembroEscritorio.objects.create(escritorio=self.esc_y, usuario=self.adm_y, role=ADMIN)
        self.estranho = user("estranho", email="estranho@z.com")

    def convidar(self, email="novo@x.com", role=MEMBRO, por=None, escritorio=None):
        return svc.criar_convite(escritorio=escritorio or self.esc, email=email, role=role, convidado_por=por or self.plat)


class ModeloTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def test_expiracao_padrao_e_link(self):
        c = self.convidar()
        self.assertAlmostEqual((c.expira_em - timezone.now()).days, 6, delta=1)
        self.assertEqual(c.get_link_aceite(), f"/escritorios/convite/{c.token}/")
        self.assertEqual(c.get_link_aceite("https://app.exemplo.com/"), f"https://app.exemplo.com/escritorios/convite/{c.token}/")
        self.assertTrue(c.is_valido())

    def test_is_valido_falha_por_status_expiracao_e_escritorio(self):
        c = self.convidar()
        ConviteEscritorio.objects.filter(pk=c.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
        c.refresh_from_db()
        self.assertFalse(c.is_valido())
        c.marcar_expirado()
        self.assertEqual(c.status, EXPIRADO)
        c2 = self.convidar("outro@x.com")
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        c2 = ConviteEscritorio.objects.get(pk=c2.pk)
        self.assertFalse(c2.is_valido())

    def test_limite_de_reenvios(self):
        c = self.convidar()
        self.assertTrue(c.pode_reenviar())
        c.tentativas_envio = 3
        self.assertFalse(c.pode_reenviar())
        c.tentativas_envio, c.status = 1, ACEITO
        self.assertFalse(c.pode_reenviar())


class PermissaoTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def test_quem_pode_gerenciar_a_equipe(self):
        esperado = {self.plat: True, self.adm: True, self.membro: False, self.adm_y: False, self.estranho: False}
        for u, ok in esperado.items():
            self.assertIs(svc.pode_gerenciar_equipe(u, self.esc), ok, u.username)

    def test_admin_de_escritorio_desativado_perde_o_poder(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        self.assertFalse(svc.pode_gerenciar_equipe(self.adm, self.esc))
        self.assertTrue(svc.pode_gerenciar_equipe(self.plat, self.esc))  # a plataforma continua

    def test_admin_de_escritorio_inativo_na_equipe_nao_gerencia(self):
        MembroEscritorio.objects.filter(usuario=self.adm).update(is_active=False)
        self.assertFalse(svc.pode_gerenciar_equipe(self.adm, self.esc))

    def test_alterar_membro(self):
        membro = MembroEscritorio.objects.get(usuario=self.membro)
        admin_membro = MembroEscritorio.objects.get(usuario=self.adm)
        svc.validar_pode_alterar_membro(self.plat, admin_membro, MEMBRO)  # plataforma mexe em qualquer um
        svc.validar_pode_alterar_membro(self.adm, membro, MEMBRO)         # admin mexe em membro comum
        with self.assertRaises(PermissionDenied):
            svc.validar_pode_alterar_membro(self.adm, membro, ADMIN)      # mas não promove
        with self.assertRaises(PermissionDenied):
            svc.validar_pode_alterar_membro(self.adm, admin_membro)       # nem em si mesmo
        outro_admin = user("outro_admin")
        outro = MembroEscritorio.objects.create(escritorio=self.esc, usuario=outro_admin, role=ADMIN)
        with self.assertRaises(PermissionDenied):
            svc.validar_pode_alterar_membro(self.adm, outro)              # nem em outro admin
        with self.assertRaises(PermissionDenied):
            svc.validar_pode_alterar_membro(self.membro, membro)          # membro comum não gerencia
        with self.assertRaises(PermissionDenied):
            svc.validar_pode_alterar_membro(self.adm_y, membro)           # nem admin de outro escritório


class CriarConviteTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def test_plataforma_convida_membro_e_admin(self):
        self.assertEqual(self.convidar("a@x.com", MEMBRO).role, MEMBRO)
        self.assertEqual(self.convidar("b@x.com", ADMIN).role, ADMIN)

    def test_admin_do_escritorio_convida_so_membro_comum(self):
        self.assertEqual(self.convidar("a@x.com", MEMBRO, por=self.adm).convidado_por, self.adm)
        with self.assertRaises(PermissionDenied):
            self.convidar("b@x.com", ADMIN, por=self.adm)

    def test_sem_permissao(self):
        for u in (self.membro, self.adm_y, self.estranho):
            with self.assertRaises(PermissionDenied, msg=u.username):
                self.convidar("a@x.com", MEMBRO, por=u)
        self.assertFalse(ConviteEscritorio.objects.exists())

    def test_normaliza_e_valida_o_email(self):
        self.assertEqual(self.convidar("  Maria@Contabil.COM ").email, "maria@contabil.com")
        for ruim in ("", "sem-arroba", "a@"):
            with self.assertRaises(ConviteEscritorioError):
                self.convidar(ruim)

    def test_papel_invalido(self):
        with self.assertRaises(ConviteEscritorioError):
            self.convidar("a@x.com", "DONO")

    def test_quem_ja_e_membro_ativo_nao_recebe_convite(self):
        with self.assertRaises(JaEhMembroError):
            self.convidar("CONTADOR@x.com")  # e-mail do `contador`, comparado sem diferenciar maiúsculas
        MembroEscritorio.objects.filter(usuario=self.contador).update(is_active=False)
        self.convidar("contador@x.com")  # inativo pode ser convidado de novo

    def test_novo_convite_cancela_o_pendente_anterior_do_mesmo_email(self):
        primeiro = self.convidar("a@x.com")
        segundo = self.convidar("A@x.com")
        primeiro.refresh_from_db()
        self.assertEqual((primeiro.status, segundo.status), (CANCELADO, PENDENTE))
        self.assertIsNotNone(primeiro.cancelado_em)
        self.assertEqual(ConviteEscritorio.objects.filter(escritorio=self.esc, email="a@x.com", status=PENDENTE).count(), 1)

    def test_convite_de_outro_escritorio_ou_email_nao_e_cancelado(self):
        mesmo_email_outro_esc = self.convidar("a@x.com", escritorio=self.esc_y)
        outro_email = self.convidar("b@x.com")
        self.convidar("a@x.com")
        mesmo_email_outro_esc.refresh_from_db()
        outro_email.refresh_from_db()
        self.assertEqual((mesmo_email_outro_esc.status, outro_email.status), (PENDENTE, PENDENTE))

    def test_escritorio_desativado_recusa(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        with self.assertRaises(ConviteEscritorioError):
            self.convidar("a@x.com")


class EnvioDoEmailTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    @override_settings(CONVITE_ENVIO_SINCRONO=False)
    def test_assincrono_so_enfileira_depois_do_commit(self):
        with mock.patch.object(enviar_email_convite_escritorio_async, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                convite = self.convidar("a@x.com")
                delay.assert_not_called()  # ainda dentro da transação
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            delay.assert_called_once_with(convite.id)

    @override_settings(CONVITE_ENVIO_SINCRONO=True)
    def test_sincrono_envia_depois_do_commit(self):
        with mock.patch("usuarios.email_service.send_email") as enviar:
            with self.captureOnCommitCallbacks(execute=True):
                convite = self.convidar("a@x.com")
        enviar.assert_called_once()
        self.assertEqual(enviar.call_args.kwargs["to_email"], "a@x.com")
        convite.refresh_from_db()
        self.assertIsNotNone(convite.ultimo_envio_em)

    @override_settings(CONVITE_ENVIO_SINCRONO=False)
    def test_convite_que_falha_na_validacao_nao_agenda_envio(self):
        with mock.patch.object(enviar_email_convite_escritorio_async, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(PermissionDenied):
                    self.convidar("a@x.com", ADMIN, por=self.adm)
            delay.assert_not_called()

    @override_settings(BASE_URL="https://app.exemplo.com")
    def test_conteudo_do_email(self):
        convite = self.convidar("a@x.com", ADMIN, por=self.plat)
        with mock.patch("usuarios.email_service.send_email") as enviar:
            resultado = enviar_email_convite_escritorio_sync(convite.id)
        self.assertTrue(resultado["success"])
        kw = enviar.call_args.kwargs
        self.assertEqual(kw["to_email"], "a@x.com")
        self.assertIn("Contábil X", kw["subject"])
        link = f"https://app.exemplo.com/escritorios/convite/{convite.token}/"
        for corpo in (kw["html_content"], kw["text_content"]):
            self.assertIn(link, corpo)
            self.assertIn("Administrador do escritório", corpo)
            self.assertIn("Contábil X", corpo)
        self.assertIn("Administrador da plataforma", kw["text_content"])  # quem convidou

    def test_convidante_membro_aparece_com_o_papel_no_escritorio(self):
        convite = self.convidar("a@x.com", MEMBRO, por=self.adm)
        with mock.patch("usuarios.email_service.send_email") as enviar:
            enviar_email_convite_escritorio_sync(convite.id)
        self.assertIn("adm_esc (Administrador do escritório)", enviar.call_args.kwargs["text_content"])

    def test_sincrono_nao_propaga_erro_de_envio(self):
        convite = self.convidar("a@x.com")
        with mock.patch("usuarios.email_service.send_email", side_effect=RuntimeError("smtp fora")):
            resultado = enviar_email_convite_escritorio_sync(convite.id)
        self.assertEqual((resultado["success"], resultado["reason"]), (False, "erro"))

    def test_nao_envia_convite_que_nao_esta_pendente(self):
        convite = self.convidar("a@x.com")
        svc.cancelar_convite(convite.id, self.plat)
        with mock.patch("usuarios.email_service.send_email") as enviar:
            self.assertFalse(enviar_email_convite_escritorio_sync(convite.id)["success"])
            self.assertEqual(enviar_email_convite_escritorio_async.apply(args=[convite.id]).get()["reason"], "status_invalido")
        enviar.assert_not_called()

    def test_tarefa_assincrona_envia_e_trata_convite_inexistente_ou_expirado(self):
        convite = self.convidar("a@x.com")
        with mock.patch("usuarios.email_service.send_email") as enviar:
            r = enviar_email_convite_escritorio_async.apply(args=[convite.id]).get()
            self.assertTrue(r["success"])
            self.assertEqual(enviar.call_count, 1)
            self.assertEqual(enviar_email_convite_escritorio_async.apply(args=[999999]).get()["reason"], "convite_nao_existe")
            ConviteEscritorio.objects.filter(pk=convite.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
            self.assertEqual(enviar_email_convite_escritorio_async.apply(args=[convite.id]).get()["reason"], "expirado")
            self.assertEqual(enviar.call_count, 1)


class ReenviarECancelarTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()
        self.convite = self.convidar("a@x.com")

    def test_reenviar_troca_o_token_e_conta_a_tentativa(self):
        antigo = self.convite.token
        novo = svc.reenviar_convite(self.convite.id, self.plat)
        self.assertNotEqual(novo.token, antigo)
        self.assertEqual((novo.tentativas_envio, novo.status), (2, PENDENTE))
        with self.assertRaises(ConviteEscritorioInvalidoError):
            svc.validar_token(antigo)  # o link antigo morreu
        self.assertEqual(svc.validar_token(novo.token).id, novo.id)

    def test_reenviar_convite_expirado_reabre(self):
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=EXPIRADO, expira_em=timezone.now() - timedelta(days=1))
        novo = svc.reenviar_convite(self.convite.id, self.adm)
        self.assertEqual(novo.status, PENDENTE)
        self.assertGreater(novo.expira_em, timezone.now())

    @override_settings(CONVITE_MAX_REENVIOS=3)
    def test_limite_de_reenvios(self):
        svc.reenviar_convite(self.convite.id, self.plat)
        svc.reenviar_convite(self.convite.id, self.plat)
        with self.assertRaises(LimiteReenvioExcedidoError):
            svc.reenviar_convite(self.convite.id, self.plat)

    def test_nao_reenvia_aceito_nem_cancelado_nem_de_escritorio_desativado(self):
        svc.cancelar_convite(self.convite.id, self.plat)
        with self.assertRaises(ConviteEscritorioError):
            svc.reenviar_convite(self.convite.id, self.plat)
        aceito = self.convidar("b@x.com")
        ConviteEscritorio.objects.filter(pk=aceito.pk).update(status=ACEITO)
        with self.assertRaises(ConviteEscritorioError):
            svc.reenviar_convite(aceito.id, self.plat)
        pendente = self.convidar("c@x.com")
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        with self.assertRaises(ConviteEscritorioError):
            svc.reenviar_convite(pendente.id, self.plat)

    def test_permissoes_de_reenvio_e_cancelamento(self):
        for u in (self.membro, self.adm_y, self.estranho):
            with self.assertRaises(PermissionDenied, msg=u.username):
                svc.reenviar_convite(self.convite.id, u)
            with self.assertRaises(PermissionDenied, msg=u.username):
                svc.cancelar_convite(self.convite.id, u)

    def test_admin_do_escritorio_nao_mexe_em_convite_de_admin(self):
        convite_admin = self.convidar("adm2@x.com", ADMIN, por=self.plat)
        with self.assertRaises(PermissionDenied):
            svc.reenviar_convite(convite_admin.id, self.adm)
        with self.assertRaises(PermissionDenied):
            svc.cancelar_convite(convite_admin.id, self.adm)
        svc.cancelar_convite(convite_admin.id, self.plat)

    def test_cancelar(self):
        c = svc.cancelar_convite(self.convite.id, self.adm)
        self.assertEqual(c.status, CANCELADO)
        self.assertIsNotNone(c.cancelado_em)
        with self.assertRaises(ConviteEscritorioError):
            svc.cancelar_convite(self.convite.id, self.adm)  # de novo
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=ACEITO)
        with self.assertRaises(ConviteEscritorioError):
            svc.cancelar_convite(self.convite.id, self.adm)


class ValidarTokenTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()
        self.convite = self.convidar("a@x.com")

    def test_valido(self):
        self.assertEqual(svc.validar_token(self.convite.token).id, self.convite.id)

    def test_inexistente(self):
        with self.assertRaises(ConviteEscritorioInvalidoError):
            svc.validar_token(uuid.uuid4())
        with self.assertRaises(ConviteEscritorioInvalidoError):
            svc.validar_token("isto-nao-e-uuid")

    def test_estados_invalidos(self):
        casos = {ACEITO: "aceito", CANCELADO: "cancelado", EXPIRADO: "expirou"}
        for status, trecho in casos.items():
            ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=status)
            with self.assertRaises(ConviteEscritorioInvalidoError, msg=status) as ctx:
                svc.validar_token(self.convite.token)
            self.assertIn(trecho, str(ctx.exception))

    def test_vencido_vira_expirado(self):
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(expira_em=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ConviteEscritorioInvalidoError):
            svc.validar_token(self.convite.token)
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, EXPIRADO)

    def test_escritorio_desativado(self):
        EscritorioContabil.objects.filter(pk=self.esc.pk).update(is_ativo=False)
        with self.assertRaises(ConviteEscritorioInvalidoError) as ctx:
            svc.validar_token(self.convite.token)
        self.assertIn("desativado", str(ctx.exception))


class AceitarNovoUsuarioTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()
        self.convite = self.convidar("Nova.Pessoa@X.com", ADMIN)

    def aceitar(self, **extra):
        dados = dict(token=self.convite.token, username="nova", first_name="Nova", last_name="Pessoa",
                     password="Senha-forte-123", ip_address="10.0.0.5", user_agent="UA/1.0")
        dados.update(extra)
        return svc.aceitar_como_novo_usuario(**dados)

    def test_cria_usuario_e_membro(self):
        usuario, membro = self.aceitar()
        self.assertEqual((usuario.username, usuario.email, usuario.first_name), ("nova", "nova.pessoa@x.com", "Nova"))
        self.assertTrue(usuario.is_active)
        self.assertTrue(usuario.check_password("Senha-forte-123"))
        self.assertEqual((membro.escritorio, membro.role, membro.is_active), (self.esc, ADMIN, True))
        self.assertEqual(set(self.ids(usuario)), {self.a.id, self.b.id})  # já alcança as empresas vinculadas

    def test_registra_o_aceite(self):
        usuario, _ = self.aceitar()
        self.convite.refresh_from_db()
        self.assertEqual((self.convite.status, self.convite.usuario_aceito, self.convite.ip_address, self.convite.user_agent),
                         (ACEITO, usuario, "10.0.0.5", "UA/1.0"))
        self.assertIsNotNone(self.convite.aceito_em)

    def test_nao_aceita_duas_vezes(self):
        self.aceitar()
        with self.assertRaises(ConviteEscritorioInvalidoError):
            self.aceitar(username="outra")
        self.assertEqual(Usuario.objects.filter(email__iexact="nova.pessoa@x.com").count(), 1)

    def test_usuario_ja_em_uso_nao_consome_o_convite(self):
        user("NOVA")  # mesmo nome, outra caixa
        with self.assertRaises(ValidationError) as ctx:
            self.aceitar()
        self.assertIn("username", ctx.exception.message_dict)
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, PENDENTE)
        self.assertFalse(Usuario.objects.filter(email__iexact="nova.pessoa@x.com").exists())
        self.aceitar(username="nova2")  # com outro nome funciona

    def test_email_que_ja_tem_conta_nao_cria_segundo_login(self):
        user("antiga", email="NOVA.PESSOA@x.com")
        with self.assertRaises(ConviteEscritorioError) as ctx:
            self.aceitar()
        self.assertIn("Entre nela", str(ctx.exception))
        self.assertEqual(Usuario.objects.filter(email__iexact="nova.pessoa@x.com").count(), 1)
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, PENDENTE)

    def test_conta_inativa_com_o_mesmo_email_nao_bloqueia(self):
        user("antiga", email="nova.pessoa@x.com", is_active=False)
        usuario, _ = self.aceitar()
        self.assertTrue(usuario.is_active)

    def test_convite_invalido_nao_cria_nada(self):
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(status=CANCELADO)
        with self.assertRaises(ConviteEscritorioInvalidoError):
            self.aceitar()
        self.assertFalse(Usuario.objects.filter(username="nova").exists())


class AceitarUsuarioExistenteTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()
        self.conta = user("ana", email="ana@contabil.com")
        self.convite = self.convidar("Ana@Contabil.com", MEMBRO)

    def aceitar(self, usuario=None, **extra):
        return svc.aceitar_como_usuario_existente(
            token=self.convite.token, usuario=usuario or self.conta, ip_address="10.0.0.9", user_agent="UA", **extra
        )

    def test_soma_o_acesso_na_mesma_conta(self):
        usuario, membro = self.aceitar()
        self.assertEqual((usuario.id, membro.escritorio, membro.role, membro.is_active), (self.conta.id, self.esc, MEMBRO, True))
        self.assertEqual(Usuario.objects.filter(email__iexact="ana@contabil.com").count(), 1)
        self.assertEqual(self.ids(self.conta), {self.a.id, self.b.id})
        self.convite.refresh_from_db()
        self.assertEqual((self.convite.status, self.convite.usuario_aceito), (ACEITO, self.conta))

    def test_exige_o_email_do_convite(self):
        outra = user("bia", email="bia@outro.com")
        with self.assertRaises(ConviteEscritorioError) as ctx:
            self.aceitar(outra)
        self.assertIn("ana@contabil.com", str(ctx.exception))
        self.assertFalse(MembroEscritorio.objects.filter(usuario=outra).exists())
        self.convite.refresh_from_db()
        self.assertEqual(self.convite.status, PENDENTE)

    def test_exige_usuario_logado_e_ativo(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertRaises(ConviteEscritorioError):
            self.aceitar(AnonymousUser())
        Usuario.objects.filter(pk=self.conta.pk).update(is_active=False)
        self.conta.refresh_from_db()
        with self.assertRaises(ConviteEscritorioError):
            self.aceitar()

    def test_membro_inativo_e_reativado_com_o_papel_do_convite(self):
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.conta, role=ADMIN, is_active=False)
        _, membro = self.aceitar()
        self.assertEqual((membro.is_active, membro.role), (True, MEMBRO))
        self.assertEqual(MembroEscritorio.objects.filter(escritorio=self.esc, usuario=self.conta).count(), 1)

    def test_admin_ativo_nao_e_rebaixado(self):
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.conta, role=ADMIN, is_active=True)
        _, membro = self.aceitar()
        self.assertEqual(membro.role, ADMIN)

    def test_membro_ativo_e_promovido_por_convite_de_admin(self):
        MembroEscritorio.objects.create(escritorio=self.esc, usuario=self.conta, role=MEMBRO, is_active=True)
        ConviteEscritorio.objects.filter(pk=self.convite.pk).update(role=ADMIN)
        _, membro = self.aceitar()
        self.assertEqual(membro.role, ADMIN)

    def test_nao_aceita_duas_vezes(self):
        self.aceitar()
        with self.assertRaises(ConviteEscritorioInvalidoError):
            self.aceitar()

    def test_existe_conta_com_email(self):
        self.assertTrue(svc.existe_conta_com_email("ANA@contabil.com"))
        self.assertFalse(svc.existe_conta_com_email("ninguem@x.com"))
        Usuario.objects.filter(pk=self.conta.pk).update(is_active=False)
        self.assertFalse(svc.existe_conta_com_email("ana@contabil.com"))


class ExpiracaoTests(EquipeMixin, TestCase):
    def setUp(self):
        self.montar_equipe()

    def test_tarefa_periodica_expira_convites_de_escritorio(self):
        vencido = ConviteEscritorio.objects.create(
            escritorio=self.esc, email="v@x.com", expira_em=timezone.now() - timedelta(days=1))
        vigente = self.convidar("ok@x.com")
        aceito = ConviteEscritorio.objects.create(
            escritorio=self.esc, email="ac@x.com", status=ACEITO, expira_em=timezone.now() - timedelta(days=1))
        resultado = expirar_convites_antigos()
        self.assertEqual(resultado["total_expirados_escritorio"], 1)
        for c in (vencido, vigente, aceito):
            c.refresh_from_db()
        self.assertEqual((vencido.status, vigente.status, aceito.status), (EXPIRADO, PENDENTE, ACEITO))
