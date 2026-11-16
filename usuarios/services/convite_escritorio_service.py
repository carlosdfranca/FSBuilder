"""
Convites para a equipe de um escritório de contabilidade.

Quem pode gerenciar a equipe: admin da plataforma, ou membro ativo com papel ADMIN do próprio
escritório (ativo). Só o admin da plataforma atribui (ou mexe em) o papel ADMIN.

Aceite, dois caminhos:
- e-mail sem conta  → cria o Usuário (usuário, nome e senha escolhidos) e o MembroEscritorio;
- e-mail com conta  → a pessoa entra nessa conta e o acesso ao escritório é somado a ela
                      (nunca cria um segundo login para o mesmo e-mail).
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from usuarios.models import ConviteEscritorio, EscritorioContabil, MembroEscritorio, Usuario
from usuarios.permissions import is_global_admin

logger = logging.getLogger(__name__)

Status = ConviteEscritorio.Status


class ConviteEscritorioError(Exception):
    """Regra de negócio violada (mensagem pronta para o usuário)."""


class ConviteEscritorioInvalidoError(ConviteEscritorioError):
    """Token inexistente, expirado, cancelado, já aceito ou escritório desativado."""


class JaEhMembroError(ConviteEscritorioError):
    pass


class LimiteReenvioExcedidoError(ConviteEscritorioError):
    pass


# ----------------------------------------------------------------------- permissões
def papel_no_escritorio(user, escritorio) -> str | None:
    """Papel do usuário num escritório ativo (None se não é membro ativo)."""
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    return (
        MembroEscritorio.objects
        .filter(escritorio=escritorio, escritorio__is_ativo=True, usuario=user, is_active=True)
        .values_list("role", flat=True)
        .first()
    )


def pode_gerenciar_equipe(user, escritorio) -> bool:
    if is_global_admin(user):
        return True
    return papel_no_escritorio(user, escritorio) == MembroEscritorio.Role.ADMIN


def _validar_pode_gerenciar(user, escritorio) -> None:
    if not pode_gerenciar_equipe(user, escritorio):
        raise PermissionDenied("Você não tem permissão para gerenciar a equipe deste escritório.")


def _validar_pode_atribuir_role(user, role: str) -> None:
    if role not in MembroEscritorio.Role.values:
        raise ConviteEscritorioError("Papel inválido.")
    if role == MembroEscritorio.Role.ADMIN and not is_global_admin(user):
        raise PermissionDenied("Só o administrador da plataforma atribui o papel de Administrador do escritório.")


def validar_pode_alterar_membro(user, membro: MembroEscritorio, novo_role: str | None = None) -> None:
    """Admin do escritório só mexe em membros comuns, e nunca em si mesmo; o da plataforma, em todos."""
    _validar_pode_gerenciar(user, membro.escritorio)
    if is_global_admin(user):
        if novo_role is not None:
            _validar_pode_atribuir_role(user, novo_role)
        return
    if membro.usuario_id == user.id:
        raise PermissionDenied("Você não pode alterar o seu próprio acesso.")
    if membro.role == MembroEscritorio.Role.ADMIN:
        raise PermissionDenied("Só o administrador da plataforma altera um Administrador do escritório.")
    if novo_role is not None:
        _validar_pode_atribuir_role(user, novo_role)


# ----------------------------------------------------------------------- envio
def _agendar_envio(convite_id: int) -> None:
    """Só dispara depois do commit: o worker nunca roda antes de o convite existir no banco."""
    from usuarios.tasks import enviar_email_convite_escritorio_async, enviar_email_convite_escritorio_sync

    if getattr(settings, "CONVITE_ENVIO_SINCRONO", False):
        transaction.on_commit(lambda: enviar_email_convite_escritorio_sync(convite_id))
    else:
        transaction.on_commit(lambda: enviar_email_convite_escritorio_async.delay(convite_id))


# ----------------------------------------------------------------------- criar / reenviar / cancelar
@transaction.atomic
def criar_convite(*, escritorio: EscritorioContabil, email: str, role: str, convidado_por: Usuario) -> ConviteEscritorio:
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ConviteEscritorioError("Informe um e-mail válido.") from None

    _validar_pode_gerenciar(convidado_por, escritorio)
    _validar_pode_atribuir_role(convidado_por, role)

    # Serializa os convites do mesmo escritório: a unicidade do pendente fica aqui (MySQL ignora a condicional).
    escritorio = EscritorioContabil.objects.select_for_update().get(pk=escritorio.pk)
    if not escritorio.is_ativo:
        raise ConviteEscritorioError("Este escritório está desativado.")

    if MembroEscritorio.objects.filter(escritorio=escritorio, is_active=True, usuario__email__iexact=email).exists():
        raise JaEhMembroError("Este e-mail já pertence a um membro ativo do escritório.")

    ConviteEscritorio.objects.filter(
        escritorio=escritorio, email__iexact=email, status=Status.PENDING
    ).update(status=Status.CANCELLED, cancelado_em=timezone.now())

    convite = ConviteEscritorio.objects.create(
        escritorio=escritorio, email=email, role=role, convidado_por=convidado_por,
    )
    _agendar_envio(convite.id)
    return convite


@transaction.atomic
def reenviar_convite(convite_id: int, reenviado_por: Usuario) -> ConviteEscritorio:
    convite = ConviteEscritorio.objects.select_for_update().select_related("escritorio").get(id=convite_id)
    _validar_pode_gerenciar(reenviado_por, convite.escritorio)
    _validar_pode_atribuir_role(reenviado_por, convite.role)
    if convite.status in (Status.ACCEPTED, Status.CANCELLED):
        raise ConviteEscritorioError("Este convite já foi aceito ou cancelado.")
    if not convite.escritorio.is_ativo:
        raise ConviteEscritorioError("Este escritório está desativado.")
    if not convite.pode_reenviar():
        raise LimiteReenvioExcedidoError(
            f"Limite de reenvios atingido ({getattr(settings, 'CONVITE_MAX_REENVIOS', 3)}). Crie um novo convite."
        )

    convite.token = uuid.uuid4()  # o link antigo deixa de valer
    convite.expira_em = timezone.now() + timedelta(days=getattr(settings, "CONVITE_EXPIRACAO_DIAS", 7))
    convite.status = Status.PENDING
    convite.tentativas_envio += 1
    convite.save(update_fields=["token", "expira_em", "status", "tentativas_envio"])
    _agendar_envio(convite.id)
    return convite


@transaction.atomic
def cancelar_convite(convite_id: int, cancelado_por: Usuario) -> ConviteEscritorio:
    convite = ConviteEscritorio.objects.select_for_update().select_related("escritorio").get(id=convite_id)
    _validar_pode_gerenciar(cancelado_por, convite.escritorio)
    _validar_pode_atribuir_role(cancelado_por, convite.role)
    if convite.status == Status.ACCEPTED:
        raise ConviteEscritorioError("Este convite já foi aceito.")
    if convite.status == Status.CANCELLED:
        raise ConviteEscritorioError("Este convite já foi cancelado.")
    convite.status = Status.CANCELLED
    convite.cancelado_em = timezone.now()
    convite.save(update_fields=["status", "cancelado_em"])
    return convite


# ----------------------------------------------------------------------- validar / aceitar
def _checar_valido(convite: ConviteEscritorio) -> None:
    if convite.status == Status.ACCEPTED:
        raise ConviteEscritorioInvalidoError("Este convite já foi aceito.")
    if convite.status == Status.CANCELLED:
        raise ConviteEscritorioInvalidoError("Este convite foi cancelado.")
    if convite.status == Status.PENDING and timezone.now() > convite.expira_em:
        convite.marcar_expirado()
    if convite.status == Status.EXPIRED:
        raise ConviteEscritorioInvalidoError("Este convite expirou. Peça um novo ao administrador.")
    if not convite.escritorio.is_ativo:
        raise ConviteEscritorioInvalidoError("O escritório deste convite está desativado.")


def validar_token(token) -> ConviteEscritorio:
    try:
        convite = ConviteEscritorio.objects.select_related("escritorio").get(token=token)
    except (ConviteEscritorio.DoesNotExist, ValidationError, ValueError):
        logger.warning("Convite de escritório inexistente (token %s…)", str(token)[:8])
        raise ConviteEscritorioInvalidoError("Convite não encontrado.") from None
    _checar_valido(convite)
    return convite


def existe_conta_com_email(email: str) -> bool:
    return Usuario.objects.filter(email__iexact=(email or "").strip(), is_active=True).exists()


def _bloquear_convite(token) -> ConviteEscritorio:
    """Relê o convite com lock (impede duas aceitações simultâneas) e revalida."""
    try:
        convite = ConviteEscritorio.objects.select_for_update().select_related("escritorio").get(token=token)
    except (ConviteEscritorio.DoesNotExist, ValidationError, ValueError):
        raise ConviteEscritorioInvalidoError("Convite não encontrado.") from None
    _checar_valido(convite)
    return convite


def _marcar_aceito(convite: ConviteEscritorio, usuario: Usuario, ip_address, user_agent) -> None:
    convite.status = Status.ACCEPTED
    convite.aceito_em = timezone.now()
    convite.usuario_aceito = usuario
    convite.ip_address = ip_address
    convite.user_agent = (user_agent or "")[:500]
    convite.save(update_fields=["status", "aceito_em", "usuario_aceito", "ip_address", "user_agent"])


@transaction.atomic
def aceitar_como_novo_usuario(
    *, token, username: str, first_name: str, last_name: str, password: str, ip_address=None, user_agent: str = ""
) -> tuple[Usuario, MembroEscritorio]:
    convite = _bloquear_convite(token)
    if existe_conta_com_email(convite.email):
        raise ConviteEscritorioError("Já existe uma conta com este e-mail. Entre nela para aceitar o convite.")
    if Usuario.objects.filter(username__iexact=username).exists():
        raise ValidationError({"username": ["Este nome de usuário já está em uso."]})

    usuario = Usuario(username=username, first_name=first_name, last_name=last_name, email=convite.email, is_active=True)
    usuario.set_password(password)
    usuario.save()
    membro = MembroEscritorio.objects.create(
        escritorio=convite.escritorio, usuario=usuario, role=convite.role, is_active=True
    )
    _marcar_aceito(convite, usuario, ip_address, user_agent)
    return usuario, membro


@transaction.atomic
def aceitar_como_usuario_existente(
    *, token, usuario: Usuario, ip_address=None, user_agent: str = ""
) -> tuple[Usuario, MembroEscritorio]:
    convite = _bloquear_convite(token)
    if not (getattr(usuario, "is_authenticated", False) and usuario.is_active):
        raise ConviteEscritorioError("Entre na sua conta para aceitar o convite.")
    if (usuario.email or "").strip().lower() != convite.email:
        raise ConviteEscritorioError(f"Este convite é para {convite.email}. Entre com a conta desse e-mail.")

    membro, criado = MembroEscritorio.objects.get_or_create(
        escritorio=convite.escritorio, usuario=usuario,
        defaults={"role": convite.role, "is_active": True},
    )
    if not criado:
        # Já era membro: reativa e nunca rebaixa um Administrador ativo
        if not (membro.is_active and membro.role == MembroEscritorio.Role.ADMIN):
            membro.role = convite.role
        membro.is_active = True
        membro.save(update_fields=["role", "is_active", "atualizado_em"])
    _marcar_aceito(convite, usuario, ip_address, user_agent)
    return usuario, membro
