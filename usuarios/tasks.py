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
    from usuarios.models import Convite
    
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
    
    return {
        'total_expirados': total,
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
