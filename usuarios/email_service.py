"""
Serviço de envio de emails com suporte a múltiplos backends.

Backends disponíveis:
- Microsoft Graph API (OAuth2) - Recomendado para Microsoft 365
- SMTP tradicional - Fallback/desenvolvimento

Uso:
    from usuarios.email_service import send_email
    
    send_email(
        to_email='usuario@exemplo.com',
        subject='Assunto',
        html_content='<p>Conteúdo HTML</p>',
        text_content='Conteúdo texto',
        from_email='noreply@empresa.com'
    )
"""

import logging
import msal
import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class EmailBackendError(Exception):
    """Exceção base para erros de envio de email."""
    pass


class MSGraphEmailBackend:
    """Backend de email usando Microsoft Graph API com OAuth2."""
    
    def __init__(self):
        """Inicializa o backend com credenciais do Azure."""
        self.tenant_id = getattr(settings, 'AZURE_TENANT_ID', None)
        self.client_id = getattr(settings, 'AZURE_CLIENT_ID', None)
        self.client_secret = getattr(settings, 'AZURE_CLIENT_SECRET', None)
        self.from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@example.com')
        
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise EmailBackendError(
                "Credenciais Azure não configuradas. "
                "Defina AZURE_TENANT_ID, AZURE_CLIENT_ID e AZURE_CLIENT_SECRET."
            )
    
    def acquire_token(self) -> str:
        """
        Adquire token de acesso OAuth2 do Azure AD.
        
        Returns:
            str: Access token para autenticação na Graph API
            
        Raises:
            EmailBackendError: Se falhar ao obter token
        """
        try:
            authority = f"https://login.microsoftonline.com/{self.tenant_id}"
            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=authority,
                client_credential=self.client_secret,
            )
            
            # Solicita token para Microsoft Graph API
            result = app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
            
            if "access_token" in result:
                logger.debug("Token OAuth2 adquirido com sucesso")
                return result["access_token"]
            else:
                error = result.get("error", "Unknown error")
                error_desc = result.get("error_description", "No description")
                logger.error(f"Erro ao adquirir token: {error} - {error_desc}")
                raise EmailBackendError(f"Falha na autenticação OAuth2: {error}")
                
        except Exception as e:
            logger.exception(f"Erro ao adquirir token OAuth2: {e}")
            raise EmailBackendError(f"Erro ao conectar com Azure AD: {str(e)}")
    
    def build_message_payload(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> Dict:
        """
        Constrói payload da mensagem no formato esperado pela Graph API.
        
        Args:
            to_email: Email do destinatário
            subject: Assunto do email
            html_content: Conteúdo HTML do email
            text_content: Conteúdo em texto puro (opcional)
            
        Returns:
            Dict com estrutura de mensagem para Graph API
        """
        # Usa HTML se disponível, senão usa texto
        content_type = "HTML" if html_content else "Text"
        content = html_content or text_content or ""
        
        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": content_type,
                    "content": content
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": to_email
                        }
                    }
                ]
            },
            "saveToSentItems": "true"
        }
        
        return message
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        from_email: Optional[str] = None
    ) -> bool:
        """
        Envia email via Microsoft Graph API.
        
        Args:
            to_email: Email do destinatário
            subject: Assunto do email
            html_content: Conteúdo HTML
            text_content: Conteúdo texto (fallback)
            from_email: Email remetente (usa DEFAULT_FROM_EMAIL se não fornecido)
            
        Returns:
            bool: True se enviado com sucesso
            
        Raises:
            EmailBackendError: Se falhar ao enviar
        """
        try:
            # Adquire token OAuth2
            access_token = self.acquire_token()
            
            # Constrói payload da mensagem
            message_payload = self.build_message_payload(
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
            # Email remetente (do campo "From")
            sender_email = from_email or self.from_email
            
            # Endpoint da Graph API para enviar email
            # Usa o email do usuário autenticado (service account)
            url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail"
            
            # Headers com token de autenticação
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Envia request
            response = requests.post(
                url,
                headers=headers,
                json=message_payload,
                timeout=30
            )
            
            # Microsoft Graph retorna 202 Accepted para emails enviados
            if response.status_code == 202:
                logger.info(
                    f"Email enviado com sucesso via Microsoft Graph API: "
                    f"{to_email} | Assunto: {subject}"
                )
                return True
            else:
                # Log detalhado do erro
                error_detail = response.text
                logger.error(
                    f"Erro ao enviar email via Graph API: "
                    f"Status {response.status_code} | {error_detail}"
                )
                raise EmailBackendError(
                    f"Graph API retornou erro {response.status_code}: {error_detail}"
                )
                
        except requests.exceptions.RequestException as e:
            logger.exception(f"Erro de conexão com Graph API: {e}")
            raise EmailBackendError(f"Erro de rede ao enviar email: {str(e)}")
        except Exception as e:
            logger.exception(f"Erro inesperado ao enviar email via Graph API: {e}")
            raise EmailBackendError(f"Erro ao enviar email: {str(e)}")


class SMTPEmailBackend:
    """Backend de email usando SMTP tradicional (fallback)."""
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
        from_email: Optional[str] = None
    ) -> bool:
        """
        Envia email via SMTP (Django EmailMultiAlternatives).
        
        Args:
            to_email: Email do destinatário
            subject: Assunto
            html_content: Conteúdo HTML
            text_content: Conteúdo texto
            from_email: Email remetente
            
        Returns:
            bool: True se enviado com sucesso
            
        Raises:
            EmailBackendError: Se falhar ao enviar
        """
        try:
            sender = from_email or settings.DEFAULT_FROM_EMAIL
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content or html_content,
                from_email=sender,
                to=[to_email]
            )
            
            if html_content:
                email.attach_alternative(html_content, "text/html")
            
            email.send(fail_silently=False)
            
            logger.info(f"Email enviado com sucesso via SMTP: {to_email}")
            return True
            
        except Exception as e:
            logger.exception(f"Erro ao enviar email via SMTP: {e}")
            raise EmailBackendError(f"Erro SMTP: {str(e)}")


def get_email_backend():
    """
    Factory function para obter o backend de email configurado.
    
    Returns:
        MSGraphEmailBackend ou SMTPEmailBackend baseado em EMAIL_SEND_METHOD
    """
    method = getattr(settings, 'EMAIL_SEND_METHOD', 'smtp').lower()
    
    if method == 'graph':
        return MSGraphEmailBackend()
    elif method == 'smtp':
        return SMTPEmailBackend()
    else:
        logger.warning(
            f"EMAIL_SEND_METHOD '{method}' inválido. Usando SMTP como fallback."
        )
        return SMTPEmailBackend()


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    from_email: Optional[str] = None
) -> bool:
    """
    Função principal de envio de email.
    Usa o backend configurado em settings.EMAIL_SEND_METHOD.
    
    Args:
        to_email: Email do destinatário
        subject: Assunto do email
        html_content: Conteúdo HTML
        text_content: Conteúdo texto (fallback)
        from_email: Email remetente (opcional)
        
    Returns:
        bool: True se enviado com sucesso
        
    Raises:
        EmailBackendError: Se falhar ao enviar
        
    Example:
        >>> send_email(
        ...     to_email='usuario@exemplo.com',
        ...     subject='Bem-vindo!',
        ...     html_content='<h1>Olá!</h1>',
        ...     text_content='Olá!'
        ... )
        True
    """
    backend = get_email_backend()
    return backend.send_email(
        to_email=to_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        from_email=from_email
    )
