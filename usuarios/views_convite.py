"""
Views para gerenciamento de convites de usuários.

Views incluídas:
- convidar_usuario: Criar novo convite (MASTER/ADMIN)
- listar_convites: Listar convites da empresa (MASTER/ADMIN)
- reenviar_convite: Reenviar email de convite (MASTER/ADMIN)
- cancelar_convite: Cancelar convite pendente (MASTER/ADMIN)
- aceitar_convite: Aceitar convite e criar conta (público)
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse, Http404
from django.views.decorators.http import require_http_methods
from django.conf import settings

from usuarios.models import Convite, Empresa, Membership
from usuarios.forms import ConvidarUsuarioForm, AceitarConviteForm
from usuarios.services import convite_service
from usuarios.services.convite_service import (
    ConviteInvalidoError,
    ConviteJaExisteError,
    LimiteReenvioExcedidoError
)

import logging

logger = logging.getLogger(__name__)


def _get_empresa_escopo(request):
    """Helper para pegar empresa ativa do request."""
    return getattr(request, 'empresa_ativa', None)


# ===== Criar Convite =====

@login_required
@require_http_methods(["GET", "POST"])
def convidar_usuario(request):
    """
    View para criar um novo convite.
    
    Acesso: MASTER, ADMIN ou Global ADMIN
    """
    empresa = _get_empresa_escopo(request)
    
    if not empresa:
        messages.error(request, "Selecione uma empresa primeiro.")
        return redirect('selecionar_empresa')
    
    if request.method == "POST":
        form = ConvidarUsuarioForm(request.POST)
        
        if form.is_valid():
            email = form.cleaned_data['email']
            role = form.cleaned_data['role']
            
            try:
                convite = convite_service.criar_convite(
                    empresa=empresa,
                    email=email,
                    role=role,
                    convidado_por=request.user,
                    cancelar_pendentes=True
                )
                
                messages.success(
                    request,
                    f"Convite enviado com sucesso para {email}! "
                    f"O usuário receberá um email com instruções."
                )
                
                # Se requisição AJAX, retorna JSON
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'success': True,
                        'message': f'Convite enviado para {email}',
                        'convite_id': convite.id
                    })
                
                return redirect('listar_convites')
                
            except PermissionDenied as e:
                messages.error(request, str(e))
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': str(e)}, status=403)
                
            except ValidationError as e:
                messages.error(request, str(e))
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': str(e)}, status=400)
                
            except ConviteJaExisteError as e:
                messages.warning(request, str(e))
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': str(e)}, status=400)
                
            except Exception as e:
                logger.exception(f"Erro ao criar convite: {e}")
                messages.error(request, "Erro ao criar convite. Tente novamente.")
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': 'Erro interno'}, status=500)
        
        else:
            # Form inválido
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'errors': form.errors
                }, status=400)
    
    else:
        form = ConvidarUsuarioForm()
    
    return render(request, 'usuarios/convidar_usuario.html', {
        'form': form,
        'empresa': empresa
    })


# ===== Listar Convites =====

@login_required
def listar_convites(request):
    """
    View para listar convites da empresa.
    
    Acesso: MASTER, ADMIN ou Global ADMIN/VIEWER
    
    - Se houver empresa_ativa (selecionada no navbar): filtra por essa empresa
    - Caso contrário: usuários normais não veem nada (precisam selecionar), 
      e globais veem todas
    """
    empresa = _get_empresa_escopo(request)
    
    # Verifica se usuário é global
    is_global = hasattr(request.user, 'has_global_scope') and request.user.has_global_scope()
    
    # Filtra por status (opcional)
    status_filter = request.GET.get('status', 'todos')
    
    # Se houver empresa selecionada, filtra por ela (mesmo para globais)
    if empresa:
        convites = Convite.objects.filter(empresa=empresa).select_related(
            'convidado_por', 'usuario_criado'
        ).order_by('-criado_em')
        
        # Stats da empresa específica
        stats = {
            'total': Convite.objects.filter(empresa=empresa).count(),
            'pendentes': Convite.objects.filter(empresa=empresa, status=Convite.Status.PENDING).count(),
            'aceitos': Convite.objects.filter(empresa=empresa, status=Convite.Status.ACCEPTED).count(),
            'expirados': Convite.objects.filter(empresa=empresa, status=Convite.Status.EXPIRED).count(),
            'cancelados': Convite.objects.filter(empresa=empresa, status=Convite.Status.CANCELLED).count(),
        }
        empresa_nome = empresa.nome
        
    # Se não houver empresa selecionada
    elif is_global:
        # Global sem empresa selecionada: mostra todas
        convites = Convite.objects.all().select_related(
            'empresa', 'convidado_por', 'usuario_criado'
        ).order_by('-criado_em')
        
        # Stats de todas as empresas
        stats = {
            'total': Convite.objects.count(),
            'pendentes': Convite.objects.filter(status=Convite.Status.PENDING).count(),
            'aceitos': Convite.objects.filter(status=Convite.Status.ACCEPTED).count(),
            'expirados': Convite.objects.filter(status=Convite.Status.EXPIRED).count(),
            'cancelados': Convite.objects.filter(status=Convite.Status.CANCELLED).count(),
        }
        empresa_nome = "Todas as Empresas"
        
    else:
        # Usuário normal sem empresa: não pode acessar
        messages.error(request, "Selecione uma empresa primeiro.")
        return redirect('selecionar_empresa')
    
    if status_filter != 'todos':
        convites = convites.filter(status=status_filter.upper())
    
    return render(request, 'usuarios/listar_convites.html', {
        'convites': convites,
        'empresa': empresa,
        'empresa_nome': empresa_nome,
        'is_global': is_global,
        'stats': stats,
        'status_filter': status_filter
    })


# ===== Reenviar Convite =====

@login_required
@require_http_methods(["POST"])
def reenviar_convite(request, convite_id):
    """
    Reenvia um convite, gerando novo token e email.
    
    Acesso: MASTER, ADMIN ou Global ADMIN
    """
    try:
        convite = convite_service.reenviar_convite(convite_id, request.user)
        
        messages.success(
            request,
            f"Convite reenviado para {convite.email}. "
            f"(Tentativa {convite.tentativas_envio} de {settings.CONVITE_MAX_REENVIOS})"
        )
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f'Convite reenviado para {convite.email}',
                'tentativas': convite.tentativas_envio
            })
        
        return redirect('listar_convites')
        
    except Convite.DoesNotExist:
        messages.error(request, "Convite não encontrado.")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Não encontrado'}, status=404)
        return redirect('listar_convites')
        
    except PermissionDenied as e:
        messages.error(request, str(e))
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=403)
        return redirect('listar_convites')
        
    except LimiteReenvioExcedidoError as e:
        messages.error(request, str(e))
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        return redirect('listar_convites')
        
    except Exception as e:
        logger.exception(f"Erro ao reenviar convite {convite_id}: {e}")
        messages.error(request, "Erro ao reenviar convite.")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Erro interno'}, status=500)
        return redirect('listar_convites')


# ===== Cancelar Convite =====

@login_required
@require_http_methods(["POST"])
def cancelar_convite(request, convite_id):
    """
    Cancela um convite pendente.
    
    Acesso: MASTER, ADMIN ou Global ADMIN
    """
    try:
        convite = convite_service.cancelar_convite(convite_id, request.user)
        
        messages.success(request, f"Convite para {convite.email} cancelado com sucesso.")
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f'Convite para {convite.email} cancelado'
            })
        
        return redirect('listar_convites')
        
    except Convite.DoesNotExist:
        messages.error(request, "Convite não encontrado.")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Não encontrado'}, status=404)
        return redirect('listar_convites')
        
    except PermissionDenied as e:
        messages.error(request, str(e))
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=403)
        return redirect('listar_convites')
        
    except ValidationError as e:
        messages.error(request, str(e))
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        return redirect('listar_convites')
        
    except Exception as e:
        logger.exception(f"Erro ao cancelar convite {convite_id}: {e}")
        messages.error(request, "Erro ao cancelar convite.")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Erro interno'}, status=500)
        return redirect('listar_convites')


# ===== Aceitar Convite (Público) =====

def aceitar_convite(request, token):
    """
    View pública para aceitar um convite e criar conta.
    
    GET: Exibe form de signup
    POST: Processa signup e cria usuário
    """
    # Valida token
    try:
        convite = convite_service.validar_token(str(token))
    except ConviteInvalidoError as e:
        return render(request, 'convites/convite_invalido.html', {
            'erro': str(e)
        })
    
    if request.method == "POST":
        form = AceitarConviteForm(request.POST)
        
        if form.is_valid():
            try:
                # Pega IP e User Agent para audit
                ip_address = request.META.get('REMOTE_ADDR')
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                
                # Aceita convite e cria usuário
                usuario = convite_service.aceitar_convite(
                    token=str(token),
                    username=form.cleaned_data['username'],
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    password=form.cleaned_data['password1'],
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                
                # Auto-login
                login(request, usuario, backend='django.contrib.auth.backends.ModelBackend')
                
                # Define empresa ativa na sessão
                request.session['empresa_ativa_id'] = str(convite.empresa.id)
                
                messages.success(
                    request,
                    f"Bem-vindo ao FSBuilder, {usuario.get_full_name() or usuario.username}! "
                    f"Sua conta foi criada com sucesso."
                )
                
                # Redirect para dashboard
                return redirect('demonstracao_financeira')
                
            except ConviteInvalidoError as e:
                messages.error(request, str(e))
                return redirect('login')
                
            except ValidationError as e:
                # Erros de validação do username, etc
                for field, errors in e.message_dict.items():
                    for error in errors:
                        form.add_error(field, error)
                        
            except Exception as e:
                logger.exception(f"Erro ao aceitar convite {token}: {e}")
                messages.error(request, "Erro ao criar conta. Tente novamente.")
    
    else:
        # GET: Exibe form pré-preenchido
        form = AceitarConviteForm()
    
    return render(request, 'convites/aceitar_convite.html', {
        'form': form,
        'convite': convite,
        'empresa': convite.empresa,
        'role_display': convite.get_role_display()
    })
