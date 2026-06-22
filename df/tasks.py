from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)

THRESHOLDS = [90, 60, 30, 15, 1]


@shared_task
def verificar_vencimentos_df():
    from df.models import PeriodoDF
    from usuarios.models import Membership

    hoje = date.today()

    for dias in THRESHOLDS:
        flag = f'notificado_{dias}'
        limite = hoje + timedelta(days=dias)

        periodos = (
            PeriodoDF.objects
            .filter(
                status__in=['nao_iniciada', 'em_andamento'],
                data_vencimento__lte=limite,
                data_vencimento__gte=hoje,
                **{flag: False},
            )
            .select_related('fundo', 'empresa')
        )

        for periodo in periodos:
            memberships = (
                Membership.objects
                .filter(
                    empresa=periodo.empresa,
                    role__in=[
                        Membership.Role.MASTER,
                        Membership.Role.ADMIN,
                        Membership.Role.MEMBER,
                    ],
                    is_active=True,
                )
                .select_related('usuario')
                .exclude(usuario__email='')
            )

            sincrono = getattr(settings, 'DF_NOTIFICACAO_SINCRONO', False)
            for m in memberships:
                if sincrono:
                    enviar_notificacao_vencimento_df(m.usuario.id, periodo.id, dias)
                else:
                    enviar_notificacao_vencimento_df.delay(m.usuario.id, periodo.id, dias)

            setattr(periodo, flag, True)
            periodo.save(update_fields=[flag, 'atualizado_em'])

            logger.info(
                f'Notificações de {dias}d enfileiradas: '
                f'período {periodo.id} ({periodo.fundo.nome} {periodo.ano}), '
                f'{memberships.count()} usuário(s)'
            )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def enviar_notificacao_vencimento_df(self, usuario_id: int, periodo_id: int, dias_restantes: int):
    from df.models import PeriodoDF
    from usuarios.models import Usuario
    from usuarios.email_service import send_email, EmailBackendError

    try:
        usuario = Usuario.objects.get(id=usuario_id)
        periodo = PeriodoDF.objects.select_related('fundo', 'empresa').get(id=periodo_id)
    except (PeriodoDF.DoesNotExist, Exception.__class__) as e:
        logger.error(f'Objeto não encontrado para notificação df: {e}')
        return {'success': False, 'reason': 'nao_encontrado'}

    if periodo.status in ('finalizada', 'vencida'):
        return {'success': False, 'reason': 'status_invalido', 'status': periodo.status}

    if not usuario.email:
        return {'success': False, 'reason': 'sem_email'}

    base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
    context = {
        'usuario': usuario,
        'periodo': periodo,
        'fundo': periodo.fundo,
        'empresa': periodo.empresa,
        'dias_restantes': dias_restantes,
        'data_vencimento': periodo.data_vencimento,
        'base_url': base_url,
    }

    html_content = render_to_string('emails/df_vencimento.html', context)
    text_content = render_to_string('emails/df_vencimento.txt', context)

    if dias_restantes == 1:
        subject = f'[URGENTE] DF vence amanhã — {periodo.fundo.nome} {periodo.ano}'
    elif dias_restantes <= 15:
        subject = f'Vencimento em {dias_restantes} dias — {periodo.fundo.nome} {periodo.ano}'
    else:
        subject = f'Lembrete: DF vence em {dias_restantes} dias — {periodo.fundo.nome} {periodo.ano}'

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@fsbuilder.com')

    send_email(
        to_email=usuario.email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        from_email=from_email,
    )

    logger.info(
        f'Notificação DF enviada: período {periodo_id} | '
        f'{dias_restantes}d | usuário {usuario.email}'
    )

    return {
        'success': True,
        'periodo_id': periodo_id,
        'usuario_email': usuario.email,
        'dias_restantes': dias_restantes,
    }
