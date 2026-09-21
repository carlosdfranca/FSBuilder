# usuarios/permissions.py
from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from django.http import HttpResponseForbidden
from django.urls import reverse

from usuarios.models import Usuario, Membership

# -------- helpers globais --------
def is_global(user):
    return bool(getattr(user, "has_global_scope", None) and user.has_global_scope())

def is_global_admin(user):
    return bool(user.is_superuser or getattr(user, "global_role", "") == Usuario.GlobalRole.PLATFORM_ADMIN)

def get_empresa_escopo(request):
    """
    Empresa atual: empresa_ativa (middleware) ou primeira empresa do membership (para não-globais, fallback).
    Globais dependem de empresa ativa → None aqui força seleção.
    """
    emp = getattr(request, "empresa_ativa", None)
    if emp:
        return emp
    if not is_global(request.user):
        memb = (
            Membership.objects
            .filter(usuario=request.user, is_active=True)
            .select_related("empresa")
            .first()
        )
        return memb.empresa if memb else None
    return None

def role_na_empresa(user, empresa):
    if not (user and empresa):
        return None
    memb = Membership.objects.filter(usuario=user, empresa=empresa, is_active=True).only("role").first()
    return memb.role if memb else None

def _redirect_to_select(request, msg=None):
    """Redireciona de forma segura para a página de seleção, sem criar loop nem spam de mensagens."""
    select_url = reverse("selecionar_empresa")
    if request.path != select_url:
        if msg:
            messages.warning(request, msg)
        return redirect("selecionar_empresa")
    # Já estamos na página de seleção → não redirecionar de novo
    return None

# ---------- Decorators genéricos (dados) ----------
def company_can_view_data(view):
    """
    Pode VER dados da empresa:
    - Qualquer global (Admin/Viewer) com empresa ativa selecionada
    - Qualquer usuário com membership ativo na empresa (qualquer role)
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        empresa = get_empresa_escopo(request)
        if not empresa:
            maybe_redirect = _redirect_to_select(request)
            return maybe_redirect or view(request, *args, **kwargs)  # se já está na seleção, deixa a view seguir (mas ela não deve usar este decorator)
        if is_global(request.user):
            return view(request, *args, **kwargs)
        if role_na_empresa(request.user, empresa) is not None:
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("Você não tem permissão para visualizar os dados desta empresa.")
    return _wrapped

def company_can_manage_data(view):
    """
    Pode GERENCIAR dados (CRUD genérico):
    - Global ADMIN
    - MASTER/ADMIN da empresa
    (Global VIEWER NÃO pode)
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        empresa = get_empresa_escopo(request)
        if not empresa:
            maybe_redirect = _redirect_to_select(request, "Selecione uma empresa na barra superior para gerenciar os dados.")
            return maybe_redirect or view(request, *args, **kwargs)
        if is_global_admin(request.user):
            return view(request, *args, **kwargs)
        user_role = role_na_empresa(request.user, empresa)
        if user_role in {Membership.Role.MASTER, Membership.Role.ADMIN}:
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("Você não tem permissão para gerenciar os dados desta empresa.")
    return _wrapped

# ---------- Decorator específico: Fundos ----------
def company_can_manage_fundos(view):
    """
    Pode GERENCIAR Fundos (CRUD):
    - Global ADMIN
    - MASTER/ADMIN/MEMBER da empresa
    (Global VIEWER NÃO pode)
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        empresa = get_empresa_escopo(request)
        if not empresa:
            maybe_redirect = _redirect_to_select(request, "Selecione uma empresa na barra superior para gerenciar fundos.")
            return maybe_redirect or view(request, *args, **kwargs)
        if is_global_admin(request.user):
            return view(request, *args, **kwargs)
        user_role = role_na_empresa(request.user, empresa)
        if user_role in {Membership.Role.MASTER, Membership.Role.ADMIN, Membership.Role.MEMBER}:
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("Você não tem permissão para gerenciar fundos desta empresa.")
    return _wrapped


def platform_admin_required(view):
    """
    Acesso restrito à plataforma (Global Admin) — não é escopado por empresa.
    Usado no painel administrativo de documentos: visão de operador sobre
    todos os clientes, não uma tela de autogestão de uma empresa específica.
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if not is_global_admin(request.user):
            return HttpResponseForbidden("Acesso restrito a administradores da plataforma.")
        return view(request, *args, **kwargs)
    return _wrapped


def pode_baixar_documentos(user, empresa) -> bool:
    """
    Quem pode baixar o CONTEÚDO de um documento anexado à DF (tem PII de
    terceiros — kits cadastrais, extratos bancários de cotistas):
    - Global ADMIN
    - MASTER/ADMIN/MEMBER da empresa
    (VIEWER da empresa e Global VIEWER NÃO podem — veem que o documento existe,
    mas não baixam o arquivo. Minimização, LGPD Art. 6º, III.)

    É uma função, não um decorator: a view de download precisa resolver o
    documento primeiro (mesmo quando o acesso será negado) para registrar a
    tentativa em LogAcessoDocumento — um decorator que barra antes da view
    rodar esconderia essa tentativa do log de auditoria.
    """
    if not (user and user.is_authenticated and empresa):
        return False
    if is_global_admin(user):
        return True
    return role_na_empresa(user, empresa) in {Membership.Role.MASTER, Membership.Role.ADMIN, Membership.Role.MEMBER}


def company_can_download_data(view):
    """
    Pode FAZER DOWNLOAD de dados da empresa:
    - Global (admin/viewer) COM empresa ativa
    - Usuário com membership ativo na empresa
    ❌ Nunca redireciona
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Usuário não autenticado.")

        empresa = get_empresa_escopo(request)

        if not empresa:
            return HttpResponseForbidden(
                "Nenhuma empresa selecionada para download."
            )

        if is_global(request.user):
            return view(request, *args, **kwargs)

        if role_na_empresa(request.user, empresa) is not None:
            return view(request, *args, **kwargs)

        return HttpResponseForbidden(
            "Você não tem permissão para baixar os dados desta empresa."
        )

    return _wrapped