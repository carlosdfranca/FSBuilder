"""
Service layer para gerenciamento de convites de usuários.

Este módulo contém a lógica de negócio para:
- Criar convites
- Validar tokens
- Aceitar convites
- Reenviar convites
- Cancelar convites

Separação de responsabilidades:
- Services: Lógica de negócio e orquestração
- Models: Estrutura de dados e validações básicas
- Views: Interface HTTP e user feedback
- Tasks: Operações assíncronas (envio de email)
"""

from django.db import transaction
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
from typing import Dict, Optional, Any
import logging

from usuarios.models import Convite, Empresa, Membership

Usuario = get_user_model()
logger = logging.getLogger(__name__)


# ===== Exceções Customizadas =====

class ConviteError(Exception):
    """Exceção base para erros relacionados a convites."""
    pass


class ConviteInvalidoError(ConviteError):
    """Token inválido, expirado ou já utilizado."""
    pass


class ConviteJaExisteError(ConviteError):
    """Já existe um convite pendente para este email."""
    pass


class LimiteReenvioExcedidoError(ConviteError):
    """Limite de reenvios do convite foi excedido."""
    pass


# ===== Validações =====

def _validar_permissao_convidar(usuario: Usuario, empresa: Empresa) -> bool:
    """
    Valida se o usuário tem permissão para convidar outros usuários na empresa.
    
    Args:
        usuario: Usuário que está tentando criar o convite
        empresa: Empresa para qual o convite será criado
    
    Returns:
        bool: True se tem permissão
    
    Raises:
        PermissionDenied: Se não tem permissão
    """
    # Global admins podem convidar para qualquer empresa
    if usuario.is_platform_admin():
        return True
    
    # Verifica se tem membership ativo na empresa
    try:
        membership = Membership.objects.get(
            empresa=empresa,
            usuario=usuario,
            is_active=True
        )
        
        # Apenas MASTER e ADMIN podem convidar
        if membership.role in {Membership.Role.MASTER, Membership.Role.ADMIN}:
            return True
        
        raise PermissionDenied(_("Apenas MASTER ou ADMIN podem convidar usuários."))
        
    except Membership.DoesNotExist:
        raise PermissionDenied(_("Você não tem acesso a esta empresa."))


def _validar_pode_atribuir_role(convidante: Usuario, empresa: Empresa, role: str) -> bool:
    """
    Valida se o convidante pode atribuir a role especificada.
    
    Regras:
    - Global ADMIN: pode atribuir qualquer role
    - MASTER: pode atribuir qualquer role
    - ADMIN: pode atribuir apenas MEMBER e VIEWER (não pode criar MASTER ou ADMIN)
    
    Args:
        convidante: Usuário que está criando o convite
        empresa: Empresa do convite
        role: Role que será atribuída
    
    Returns:
        bool: True se pode atribuir
    
    Raises:
        PermissionDenied: Se não pode atribuir a role
    """
    # Global admin pode tudo
    if convidante.is_platform_admin():
        return True
    
    # Pega o role do convidante na empresa
    try:
        membership = Membership.objects.get(
            empresa=empresa,
            usuario=convidante,
            is_active=True
        )
        
        # MASTER pode atribuir qualquer role
        if membership.role == Membership.Role.MASTER:
            return True
        
        # ADMIN não pode criar MASTER ou ADMIN
        if membership.role == Membership.Role.ADMIN:
            if role in {Membership.Role.MASTER, Membership.Role.ADMIN}:
                raise PermissionDenied(
                    _("Admin não pode convidar usuários com role MASTER ou ADMIN.")
                )
            return True
        
        raise PermissionDenied(_("Você não tem permissão para convidar usuários."))
        
    except Membership.DoesNotExist:
        raise PermissionDenied(_("Você não tem acesso a esta empresa."))


def _validar_email_disponivel(email: str, empresa: Empresa) -> bool:
    """
    Valida se o email está disponível para convite.
    
    Regras:
    - Não pode ter membership ativo na empresa
    - Não pode ter convite pendente na empresa
    
    Args:
        email: Email a ser validado
        empresa: Empresa do convite
    
    Returns:
        bool: True se disponível
    
    Raises:
        ValidationError: Se email não está disponível
    """
    # Verifica se já existe usuário com esse email vinculado à empresa
    usuario_existente = Usuario.objects.filter(email=email).first()
    if usuario_existente:
        membership_ativa = Membership.objects.filter(
            empresa=empresa,
            usuario=usuario_existente,
            is_active=True
        ).exists()
        
        if membership_ativa:
            raise ValidationError(
                _("Este email já pertence a um usuário ativo nesta empresa.")
            )
    
    # Verifica se já existe convite pendente
    # Nota: O constraint do DB já previne isso, mas validamos antes
    convite_pendente = Convite.objects.filter(
        empresa=empresa,
        email=email,
        status=Convite.Status.PENDING
    ).exists()
    
    if convite_pendente:
        raise ConviteJaExisteError(
            _("Já existe um convite pendente para este email nesta empresa.")
        )
    
    return True


# ===== Funções Principais =====

@transaction.atomic
def criar_convite(
    *,
    empresa: Empresa,
    email: str,
    role: str,
    convidado_por: Usuario,
    cancelar_pendentes: bool = True
) -> Convite:
    """
    Cria um novo convite para um usuário se juntar à empresa.
    
    Args:
        empresa: Empresa para qual o usuário está sendo convidado
        email: Email do usuário a ser convidado
        role: Role que o usuário terá (MASTER, ADMIN, MEMBER, VIEWER)
        convidado_por: Usuário que está criando o convite
        cancelar_pendentes: Se True, cancela convites pendentes anteriores
    
    Returns:
        Convite: Objeto Convite criado
    
    Raises:
        PermissionDenied: Se não tem permissão para convidar
        ValidationError: Se email não está disponível ou dados inválidos
        ConviteJaExisteError: Se já existe convite pendente e cancelar_pendentes=False
    """
    # Normaliza email
    email = email.lower().strip()
    
    # Validações de permissão
    _validar_permissao_convidar(convidado_por, empresa)
    _validar_pode_atribuir_role(convidado_por, empresa, role)
    
    # Valida se empresa está ativa
    if not empresa.is_ativo:
        raise ValidationError(_("Não é possível criar convites para empresas inativas."))
    
    # Cancela convites pendentes anteriores (se solicitado)
    if cancelar_pendentes:
        Convite.objects.filter(
            empresa=empresa,
            email=email,
            status=Convite.Status.PENDING
        ).update(
            status=Convite.Status.CANCELLED,
            cancelado_em=timezone.now()
        )
    else:
        # Se não pode cancelar, valida se email está disponível
        _validar_email_disponivel(email, empresa)
    
    # Cria o convite
    dias_expiracao = getattr(settings, 'CONVITE_EXPIRACAO_DIAS', 7)
    convite = Convite.objects.create(
        empresa=empresa,
        email=email,
        role=role,
        convidado_por=convidado_por,
        expira_em=timezone.now() + timedelta(days=dias_expiracao),
        ultimo_envio_em=timezone.now()
    )
    
    logger.info(
        f"Convite criado: {convite.id} | Email: {email} | Empresa: {empresa.nome} | "
        f"Role: {role} | Por: {convidado_por.username}"
    )
    
    # Enfileira envio de email (importado aqui para evitar circular import)
    envio_sincrono = getattr(settings, 'CONVITE_ENVIO_SINCRONO', False)
    if envio_sincrono:
        # Modo síncrono (dev/tests)
        from usuarios.tasks import enviar_email_convite_sync
        enviar_email_convite_sync(convite.id)
    else:
        # Modo assíncrono (produção)
        from usuarios.tasks import enviar_email_convite_async
        enviar_email_convite_async.delay(convite.id)
    
    return convite


def validar_token(token: str) -> Convite:
    """
    Valida um token de convite e retorna o convite se válido.
    
    Args:
        token: Token UUID do convite
    
    Returns:
        Convite: Objeto Convite se válido
    
    Raises:
        ConviteInvalidoError: Se token inválido, expirado ou já utilizado
    """
    try:
        convite = Convite.objects.select_related('empresa').get(token=token)
    except Convite.DoesNotExist:
        logger.warning(f"Tentativa de acesso com token inválido: {token}")
        raise ConviteInvalidoError(_("Convite não encontrado ou inválido."))
    
    # Verifica se já foi aceito
    if convite.status == Convite.Status.ACCEPTED:
        raise ConviteInvalidoError(_("Este convite já foi utilizado."))
    
    # Verifica se foi cancelado
    if convite.status == Convite.Status.CANCELLED:
        raise ConviteInvalidoError(_("Este convite foi cancelado."))
    
    # Verifica expiração
    if timezone.now() > convite.expira_em:
        # Marca como expirado se ainda estava pendente
        if convite.status == Convite.Status.PENDING:
            convite.marcar_expirado()
        raise ConviteInvalidoError(
            _("Este convite expirou. Solicite um novo convite ao administrador.")
        )
    
    # Verifica se empresa está ativa
    if not convite.empresa.is_ativo:
        raise ConviteInvalidoError(
            _("A empresa para este convite está inativa. Contate o administrador.")
        )
    
    # Convite válido
    return convite


@transaction.atomic
def aceitar_convite(
    *,
    token: str,
    username: str,
    first_name: str,
    last_name: str,
    password: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
) -> Usuario:
    """
    Aceita um convite, criando o usuário e vinculando à empresa.
    
    Args:
        token: Token UUID do convite
        username: Nome de usuário desejado
        first_name: Primeiro nome
        last_name: Sobrenome
        password: Senha do usuário
        ip_address: IP do usuário (para audit)
        user_agent: User agent do browser (para audit)
    
    Returns:
        Usuario: Usuário criado
    
    Raises:
        ConviteInvalidoError: Se convite inválido
        ValidationError: Se dados do usuário inválidos
    """
    # Valida o token
    convite = validar_token(token)
    
    # Revalida status (proteção contra race condition)
    if convite.status != Convite.Status.PENDING:
        raise ConviteInvalidoError(_("Este convite não está mais disponível."))
    
    # Valida unicidade do username
    if Usuario.objects.filter(username=username).exists():
        raise ValidationError({"username": _("Este nome de usuário já está em uso.")})
    
    # Cria o usuário
    usuario = Usuario.objects.create(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=convite.email,
        is_active=True
    )
    usuario.set_password(password)
    usuario.save(update_fields=['password'])
    
    logger.info(
        f"Usuário criado via convite: {usuario.username} | Email: {usuario.email} | "
        f"Convite: {convite.id}"
    )
    
    # Cria o membership
    membership = Membership.objects.create(
        empresa=convite.empresa,
        usuario=usuario,
        role=convite.role,
        is_active=True
    )
    
    logger.info(
        f"Membership criado: {membership.id} | Usuário: {usuario.username} | "
        f"Empresa: {convite.empresa.nome} | Role: {convite.role}"
    )
    
    # Atualiza o convite
    convite.status = Convite.Status.ACCEPTED
    convite.aceito_em = timezone.now()
    convite.usuario_criado = usuario
    convite.ip_address = ip_address
    convite.user_agent = user_agent[:500] if user_agent else ""  # Limita tamanho
    convite.save(update_fields=[
        'status', 'aceito_em', 'usuario_criado', 'ip_address', 'user_agent'
    ])
    
    logger.info(f"Convite aceito: {convite.id} | Usuário: {usuario.username}")
    
    return usuario


@transaction.atomic
def reenviar_convite(convite_id: int, reenviado_por: Usuario) -> Convite:
    """
    Reenvia um convite, gerando novo token e resetando expiração.
    
    Args:
        convite_id: ID do convite a ser reenviado
        reenviado_por: Usuário que está reenviando
    
    Returns:
        Convite: Convite atualizado
    
    Raises:
        Convite.DoesNotExist: Se convite não existe
        PermissionDenied: Se não tem permissão
        LimiteReenvioExcedidoError: Se excedeu limite de reenvios
    """
    convite = Convite.objects.select_related('empresa').get(id=convite_id)
    
    # Valida permissão
    _validar_permissao_convidar(reenviado_por, convite.empresa)
    
    # Valida se pode reenviar
    if not convite.pode_reenviar():
        max_reenvios = getattr(settings, 'CONVITE_MAX_REENVIOS', 3)
        raise LimiteReenvioExcedidoError(
            _(f"Limite de {max_reenvios} reenvios excedido para este convite.")
        )
    
    # Valida status
    if convite.status == Convite.Status.ACCEPTED:
        raise ValidationError(_("Este convite já foi aceito."))
    
    if convite.status == Convite.Status.CANCELLED:
        raise ValidationError(_("Este convite foi cancelado."))
    
    # Atualiza convite
    import uuid
    convite.token = uuid.uuid4()
    dias_expiracao = getattr(settings, 'CONVITE_EXPIRACAO_DIAS', 7)
    convite.expira_em = timezone.now() + timedelta(days=dias_expiracao)
    convite.status = Convite.Status.PENDING  # Se estava expirado, volta para pendente
    convite.tentativas_envio += 1
    convite.ultimo_envio_em = timezone.now()
    convite.save(update_fields=[
        'token', 'expira_em', 'status', 'tentativas_envio', 'ultimo_envio_em'
    ])
    
    logger.info(
        f"Convite reenviado: {convite.id} | Email: {convite.email} | "
        f"Tentativa: {convite.tentativas_envio} | Por: {reenviado_por.username}"
    )
    
    # Enfileira envio de email
    envio_sincrono = getattr(settings, 'CONVITE_ENVIO_SINCRONO', False)
    if envio_sincrono:
        from usuarios.tasks import enviar_email_convite_sync
        enviar_email_convite_sync(convite.id)
    else:
        from usuarios.tasks import enviar_email_convite_async
        enviar_email_convite_async.delay(convite.id)
    
    return convite


@transaction.atomic
def cancelar_convite(convite_id: int, cancelado_por: Usuario) -> Convite:
    """
    Cancela um convite pendente.
    
    Args:
        convite_id: ID do convite a ser cancelado
        cancelado_por: Usuário que está cancelando
    
    Returns:
        Convite: Convite cancelado
    
    Raises:
        Convite.DoesNotExist: Se convite não existe
        PermissionDenied: Se não tem permissão
        ValidationError: Se convite não pode ser cancelado
    """
    convite = Convite.objects.select_related('empresa').get(id=convite_id)
    
    # Valida permissão
    _validar_permissao_convidar(cancelado_por, convite.empresa)
    
    # Valida status
    if convite.status == Convite.Status.ACCEPTED:
        raise ValidationError(_("Não é possível cancelar um convite já aceito."))
    
    if convite.status == Convite.Status.CANCELLED:
        raise ValidationError(_("Este convite já está cancelado."))
    
    # Cancela convite
    convite.status = Convite.Status.CANCELLED
    convite.cancelado_em = timezone.now()
    convite.save(update_fields=['status', 'cancelado_em'])
    
    logger.info(
        f"Convite cancelado: {convite.id} | Email: {convite.email} | "
        f"Por: {cancelado_por.username}"
    )
    
    return convite
