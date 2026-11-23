"""
Celery tasks para gerenciamento de convites.

Tasks assíncronas:
- enviar_email_convite_async: Envia email de convite (retry automático)
- expirar_convites_antigos: Task periódica para marcar convites expirados
"""

from celery import shared_task
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from django.utils.html import strip_tags
import logging

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 minutos
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True
)
def enviar_email_convite_async(self, convite_id: int):
    """
    Envia email de convite de forma assíncrona.
    
    Retry automático:
    - 3 tentativas máximas
    - Intervalo inicial de 5 minutos
    - Backoff exponencial com jitter
    
    Args:
        convite_id: ID do convite a enviar email
    
    Returns:
        dict: Resultado do envio (sucesso, tentativas, etc)
    """
    from usuarios.models import Convite
    from usuarios.email_service import send_email, EmailBackendError
    
    try:
        convite = Convite.objects.select_related(
            'empresa', 'convidado_por'
        ).get(id=convite_id)
        
        # Valida se convite está pendente
        if convite.status != Convite.Status.PENDING:
            logger.info(
                f"Convite {convite_id} não está pendente (status: {convite.status}). "
                f"Email não enviado."
            )
            return {
                'success': False,
                'reason': 'status_invalido',
                'status': convite.status
            }
        
        # Valida se não expirou
        if timezone.now() > convite.expira_em:
            logger.info(f"Convite {convite_id} já expirou. Email não enviado.")
            convite.marcar_expirado()
            return {
                'success': False,
                'reason': 'expirado'
            }
        
        # Prepara contexto para o template
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        link_aceite = convite.get_link_aceite(base_url)
        
        # Determina nome e papel do convidante
        convidante_nome = "Administrador"
        convidante_role = "Administrador"
        
        if convite.convidado_por:
            convidante_nome = convite.convidado_por.get_full_name() or convite.convidado_por.username
            
            # Tenta pegar o role do Membership na empresa
            membership = convite.convidado_por.memberships.filter(
                empresa=convite.empresa, 
                is_active=True
            ).first()
            
            if membership:
                convidante_role = membership.get_role_display()
            elif hasattr(convite.convidado_por, 'global_role') and convite.convidado_por.global_role:
                # Se não tem membership, mas tem global_role (PLATFORM_ADMIN)
                convidante_role = "Administrador Global"
        
        context = {
            'convite': convite,
            'empresa': convite.empresa,
            'convidante': convite.convidado_por,
            'convidante_nome': convidante_nome,
            'convidante_role': convidante_role,
            'role_display': convite.get_role_display(),
            'link_aceite': link_aceite,
            'base_url': base_url,
            'dias_validade': getattr(settings, 'CONVITE_EXPIRACAO_DIAS', 7),
        }
        
        # Renderiza templates
        html_message = render_to_string('emails/convite.html', context)
        text_message = render_to_string('emails/convite.txt', context)
        
        # Configura email
        subject = f"Convite para {convite.empresa.nome} - FSBuilder"
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@fsbuilder.com')
        recipient_email = convite.email
        
        # Envia email usando o novo serviço
        send_email(
            to_email=recipient_email,
            subject=subject,
            html_content=html_message,
            text_content=text_message,
            from_email=from_email
        )
        
        logger.info(
            f"Email de convite enviado com sucesso: Convite {convite_id} | "
            f"Email: {recipient_email} | Tentativa: {self.request.retries + 1}"
        )
        
        return {
            'success': True,
            'convite_id': convite_id,
            'email': recipient_email,
            'tentativas': self.request.retries + 1
        }
        
    except Convite.DoesNotExist:
        logger.error(f"Convite {convite_id} não encontrado")
        return {
            'success': False,
            'reason': 'convite_nao_existe'
        }
    except EmailBackendError as e:
        logger.error(f"Erro no backend de email: {e}")
        # Re-raise para trigger o retry automático
        raise
    except Exception as exc:
        logger.error(
            f"Erro ao enviar email de convite {convite_id}: {str(exc)} | "
            f"Tentativa {self.request.retries + 1}/{self.max_retries}"
        )
        # Re-raise para trigger o retry automático
        raise


def enviar_email_convite_sync(convite_id: int):
    """
    Versão síncrona do envio de email (para desenvolvimento/testes).
    
    Args:
        convite_id: ID do convite
    
    Returns:
        dict: Resultado do envio
    """
    from usuarios.models import Convite
    from usuarios.email_service import send_email, EmailBackendError
    from django.template.loader import render_to_string
    
    try:
        convite = Convite.objects.select_related(
            'empresa', 'convidado_por'
        ).get(id=convite_id)
        
        # Valida se convite está pendente
        if convite.status != Convite.Status.PENDING:
            logger.info(
                f"Convite {convite_id} não está pendente (status: {convite.status}). "
                f"Email não enviado."
            )
            return {
                'success': False,
                'reason': 'status_invalido',
                'status': convite.status
            }
        
        # Valida se não expirou
        if timezone.now() > convite.expira_em:
            logger.info(f"Convite {convite_id} já expirou. Email não enviado.")
            convite.marcar_expirado()
            return {
                'success': False,
                'reason': 'expirado'
            }
        
        # Prepara contexto para o template
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        link_aceite = convite.get_link_aceite(base_url)
        
        # Determina nome e papel do convidante
        convidante_nome = "Administrador"
        convidante_role = "Administrador"
        
        if convite.convidado_por:
            convidante_nome = convite.convidado_por.get_full_name() or convite.convidado_por.username
            
            # Tenta pegar o role do Membership na empresa
            membership = convite.convidado_por.memberships.filter(
                empresa=convite.empresa, 
                is_active=True
            ).first()
            
            if membership:
                convidante_role = membership.get_role_display()
            elif hasattr(convite.convidado_por, 'global_role') and convite.convidado_por.global_role:
                convidante_role = "Administrador Global"
        
        context = {
            'convite': convite,
            'empresa': convite.empresa,
            'convidante': convite.convidado_por,
            'convidante_nome': convidante_nome,
            'convidante_role': convidante_role,
            'role_display': convite.get_role_display(),
            'link_aceite': link_aceite,
            'expira_em': convite.expira_em,
            'BASE_URL': base_url
        }
        
        # Renderiza templates HTML e texto
        html_content = render_to_string('emails/convite.html', context)
        text_content = render_to_string('emails/convite.txt', context)
        
        # Configura email
        subject = f"Convite para {convite.empresa.nome} - FSBuilder"
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = convite.email
        
        # Envia email usando o novo serviço
        send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            from_email=from_email
        )
        
        logger.info(f"Email de convite enviado com sucesso (SYNC): Convite {convite_id}")
        
        return {
            'success': True,
            'convite_id': convite_id,
            'email': to_email,
            'tentativas': 1
        }
        
    except Convite.DoesNotExist:
        logger.error(f"Convite {convite_id} não encontrado")
        return {
            'success': False,
            'reason': 'convite_nao_existe'
        }
    except EmailBackendError as e:
        logger.exception(f"Erro no backend de email: {e}")
        return {
            'success': False,
            'reason': 'erro_envio',
            'error': str(e)
        }
    except Exception as e:
        logger.exception(f"Erro ao enviar email (SYNC): {e}")
        return {
            'success': False,
            'reason': 'erro_envio',
            'error': str(e)
        }


@shared_task
def expirar_convites_antigos():
    """
    Task periódica que marca convites pendentes expirados.
    
    Deve ser executada diariamente via Celery Beat.
    
    Exemplo de configuração no settings.py:
    ```python
    from celery.schedules import crontab
    
    CELERY_BEAT_SCHEDULE = {
        'expirar-convites': {
            'task': 'usuarios.tasks.expirar_convites_antigos',
            'schedule': crontab(hour=3, minute=0),  # Todo dia às 3am
        },
    }
    ```
    
    Returns:
        dict: Estatísticas da execução
    """
    from usuarios.models import Convite, ConviteEscritorio
    
    agora = timezone.now()
    
    # Busca convites pendentes expirados
    convites_expirados = Convite.objects.filter(
        status=Convite.Status.PENDING,
        expira_em__lt=agora
    )
    
    total = convites_expirados.count()
    
    if total > 0:
        # Atualiza status em batch
        convites_expirados.update(status=Convite.Status.EXPIRED)
        
        logger.info(f"Marcados {total} convites como expirados.")

    # Convites de escritório de contabilidade
    total_escritorio = ConviteEscritorio.objects.filter(
        status=ConviteEscritorio.Status.PENDING, expira_em__lt=agora
    ).update(status=ConviteEscritorio.Status.EXPIRED)
    if total_escritorio:
        logger.info(f"Marcados {total_escritorio} convites de escritório como expirados.")
    
    return {
        'total_expirados': total,
        'total_expirados_escritorio': total_escritorio,
        'executado_em': agora.isoformat()
    }


@shared_task
def enviar_notificacao_convite_aceito(convite_id: int):
    """
    Envia notificação para o convidante quando um convite é aceito.
    
    (Opcional - implementação futura)
    
    Args:
        convite_id: ID do convite aceito
    """
    from usuarios.models import Convite
    
    try:
        convite = Convite.objects.select_related(
            'empresa', 'convidado_por', 'usuario_criado'
        ).get(id=convite_id)
        
        if not convite.convidado_por or not convite.convidado_por.email:
            logger.info(f"Convite {convite_id}: convidante sem email. Notificação não enviada.")
            return {'success': False, 'reason': 'sem_email'}
        
        # Contexto
        context = {
            'convite': convite,
            'usuario_novo': convite.usuario_criado,
            'empresa': convite.empresa,
        }
        
        # Renderiza
        subject = f"Convite aceito - {convite.usuario_criado.get_full_name()} entrou em {convite.empresa.nome}"
        html_message = render_to_string('emails/convite_aceito.html', context)
        text_message = strip_tags(html_message)
        
        # Envia
        send_mail(
            subject=subject,
            message=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[convite.convidado_por.email],
            html_message=html_message,
            fail_silently=True  # Não falhar se não enviar notificação
        )
        
        logger.info(f"Notificação de aceite enviada para {convite.convidado_por.email}")
        
        return {'success': True}
        
    except Convite.DoesNotExist:
        logger.error(f"Convite {convite_id} não encontrado para notificação.")
        return {'success': False, 'reason': 'convite_nao_encontrado'}
    except Exception as exc:
        logger.error(f"Erro ao enviar notificação de aceite: {str(exc)}")
        return {'success': False, 'reason': str(exc)}


# ===== Redefinição de Senha =====

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 minutos
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True
)
def enviar_email_reset_senha_async(self, reset_token_id: int):
    """
    Envia email de redefinição de senha de forma assíncrona.

    Retry automático:
    - 3 tentativas máximas
    - Intervalo inicial de 5 minutos
    - Backoff exponencial com jitter

    Args:
        reset_token_id: ID do PasswordResetToken

    Returns:
        dict: Resultado do envio (sucesso, tentativas, etc)
    """
    from usuarios.models import PasswordResetToken
    from usuarios.email_service import send_email, EmailBackendError

    try:
        reset_token = PasswordResetToken.objects.select_related('usuario').get(id=reset_token_id)

        # Valida se token ainda está pendente
        if reset_token.status != PasswordResetToken.Status.PENDING:
            logger.info(
                f"Token de redefinição {reset_token_id} não está pendente "
                f"(status: {reset_token.status}). Email não enviado."
            )
            return {
                'success': False,
                'reason': 'status_invalido',
                'status': reset_token.status
            }

        # Valida se não expirou
        if timezone.now() > reset_token.expira_em:
            logger.info(f"Token de redefinição {reset_token_id} já expirou. Email não enviado.")
            return {
                'success': False,
                'reason': 'expirado'
            }

        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        link_reset = reset_token.get_link(base_url)
        horas_validade = getattr(settings, 'PASSWORD_RESET_EXPIRACAO_HORAS', 24)

        context = {
            'usuario': reset_token.usuario,
            'link_reset': link_reset,
            'base_url': base_url,
            'horas_validade': horas_validade,
            'expira_em': reset_token.expira_em,
        }

        html_message = render_to_string('emails/reset_senha.html', context)
        text_message = render_to_string('emails/reset_senha.txt', context)

        subject = "Redefinição de senha - FSBuilder"
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@fsbuilder.com')
        recipient_email = reset_token.usuario.email

        send_email(
            to_email=recipient_email,
            subject=subject,
            html_content=html_message,
            text_content=text_message,
            from_email=from_email
        )

        logger.info(
            f"Email de redefinição de senha enviado com sucesso: Token {reset_token_id} | "
            f"Usuário: {reset_token.usuario.username} | Tentativa: {self.request.retries + 1}"
        )

        return {
            'success': True,
            'reset_token_id': reset_token_id,
            'email': recipient_email,
            'tentativas': self.request.retries + 1
        }

    except PasswordResetToken.DoesNotExist:
        logger.error(f"Token de redefinição {reset_token_id} não encontrado")
        return {
            'success': False,
            'reason': 'token_nao_existe'
        }
    except EmailBackendError as e:
        logger.error(f"Erro no backend de email: {e}")
        raise
    except Exception as exc:
        logger.error(
            f"Erro ao enviar email de redefinição de senha {reset_token_id}: {str(exc)} | "
            f"Tentativa {self.request.retries + 1}/{self.max_retries}"
        )
        raise


def enviar_email_reset_senha_sync(reset_token_id: int):
    """
    Versão síncrona do envio de email de redefinição de senha (para desenvolvimento/testes).

    Args:
        reset_token_id: ID do PasswordResetToken

    Returns:
        dict: Resultado do envio
    """
    from usuarios.models import PasswordResetToken
    from usuarios.email_service import send_email, EmailBackendError

    try:
        reset_token = PasswordResetToken.objects.select_related('usuario').get(id=reset_token_id)

        if reset_token.status != PasswordResetToken.Status.PENDING:
            logger.info(
                f"Token de redefinição {reset_token_id} não está pendente "
                f"(status: {reset_token.status}). Email não enviado."
            )
            return {
                'success': False,
                'reason': 'status_invalido',
                'status': reset_token.status
            }

        if timezone.now() > reset_token.expira_em:
            logger.info(f"Token de redefinição {reset_token_id} já expirou. Email não enviado.")
            return {
                'success': False,
                'reason': 'expirado'
            }

        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        link_reset = reset_token.get_link(base_url)
        horas_validade = getattr(settings, 'PASSWORD_RESET_EXPIRACAO_HORAS', 24)

        context = {
            'usuario': reset_token.usuario,
            'link_reset': link_reset,
            'base_url': base_url,
            'horas_validade': horas_validade,
            'expira_em': reset_token.expira_em,
        }

        html_content = render_to_string('emails/reset_senha.html', context)
        text_content = render_to_string('emails/reset_senha.txt', context)

        subject = "Redefinição de senha - FSBuilder"
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = reset_token.usuario.email

        send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            from_email=from_email
        )

        logger.info(f"Email de redefinição de senha enviado com sucesso (SYNC): Token {reset_token_id}")

        return {
            'success': True,
            'reset_token_id': reset_token_id,
            'email': to_email,
            'tentativas': 1
        }

    except PasswordResetToken.DoesNotExist:
        logger.error(f"Token de redefinição {reset_token_id} não encontrado")
        return {
            'success': False,
            'reason': 'token_nao_existe'
        }
    except EmailBackendError as e:
        logger.exception(f"Erro no backend de email: {e}")
        return {
            'success': False,
            'reason': 'erro_envio',
            'error': str(e)
        }
    except Exception as e:
        logger.exception(f"Erro ao enviar email de redefinição de senha (SYNC): {e}")
        return {
            'success': False,
            'reason': 'erro_envio',
            'error': str(e)
        }


# =====================================================================
# Convites de escritório de contabilidade
# =====================================================================
def _enviar_email_convite_escritorio(convite) -> str:
    """Monta e envia o e-mail. Um só caminho para a versão assíncrona e a síncrona (mesmo contexto)."""
    from usuarios.email_service import send_email
    from usuarios.models import MembroEscritorio

    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')

    convidante = convite.convidado_por
    convidante_nome = "Administrador da plataforma"
    convidante_role = "Administrador da plataforma"
    if convidante:
        convidante_nome = convidante.get_full_name() or convidante.username
        membro = MembroEscritorio.objects.filter(
            escritorio=convite.escritorio, usuario=convidante, is_active=True
        ).first()
        if membro:
            convidante_role = membro.get_role_display()

    context = {
        'convite': convite,
        'escritorio': convite.escritorio,
        'convidante_nome': convidante_nome,
        'convidante_role': convidante_role,
        'role_display': convite.get_role_display(),
        'link_aceite': convite.get_link_aceite(base_url),
        'base_url': base_url,
        'dias_validade': getattr(settings, 'CONVITE_EXPIRACAO_DIAS', 7),
    }
    send_email(
        to_email=convite.email,
        subject=f"Convite para o escritório {convite.escritorio.nome} - FSBuilder",
        html_content=render_to_string('emails/convite_escritorio.html', context),
        text_content=render_to_string('emails/convite_escritorio.txt', context),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@fsbuilder.com'),
    )
    type(convite).objects.filter(pk=convite.pk).update(ultimo_envio_em=timezone.now())
    return convite.email


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def enviar_email_convite_escritorio_async(self, convite_id: int):
    """Envia o e-mail do convite de escritório (retry automático, como o convite de empresa)."""
    from usuarios.models import ConviteEscritorio

    try:
        convite = ConviteEscritorio.objects.select_related('escritorio', 'convidado_por').get(id=convite_id)
    except ConviteEscritorio.DoesNotExist:
        logger.error(f"Convite de escritório {convite_id} não encontrado")
        return {'success': False, 'reason': 'convite_nao_existe'}

    if convite.status != ConviteEscritorio.Status.PENDING:
        logger.info(f"Convite de escritório {convite_id} não está pendente ({convite.status}). E-mail não enviado.")
        return {'success': False, 'reason': 'status_invalido', 'status': convite.status}
    if timezone.now() > convite.expira_em:
        convite.marcar_expirado()
        return {'success': False, 'reason': 'expirado'}

    try:
        email = _enviar_email_convite_escritorio(convite)
    except Exception as exc:
        logger.error(
            f"Erro ao enviar convite de escritório {convite_id}: {exc} | "
            f"Tentativa {self.request.retries + 1}/{self.max_retries}"
        )
        raise  # dispara o retry automático
    logger.info(f"Convite de escritório {convite_id} enviado para {email} (tentativa {self.request.retries + 1})")
    return {'success': True, 'convite_id': convite_id, 'email': email, 'tentativas': self.request.retries + 1}


def enviar_email_convite_escritorio_sync(convite_id: int) -> dict:
    """Versão síncrona (dev/testes, CONVITE_ENVIO_SINCRONO): nunca propaga erro, só registra e devolve."""
    from usuarios.models import ConviteEscritorio

    try:
        convite = ConviteEscritorio.objects.select_related('escritorio', 'convidado_por').get(id=convite_id)
        if convite.status != ConviteEscritorio.Status.PENDING:
            return {'success': False, 'reason': 'status_invalido', 'status': convite.status}
        email = _enviar_email_convite_escritorio(convite)
        return {'success': True, 'convite_id': convite_id, 'email': email}
    except Exception as exc:
        logger.error(f"Erro ao enviar (síncrono) convite de escritório {convite_id}: {exc}")
        return {'success': False, 'reason': 'erro', 'erro': str(exc)}
