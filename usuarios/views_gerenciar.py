# usuarios/views_gerenciar.py
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from usuarios.models import Usuario, Empresa, Membership


# ---------- helpers globais ----------
def _is_global(user):
    return bool(getattr(user, "has_global_scope", None) and user.has_global_scope())

def _is_global_admin(user):
    return bool(user.is_superuser or getattr(user, "global_role", "") == Usuario.GlobalRole.PLATFORM_ADMIN)

def _get_empresa_escopo(request):
    """
    Empresa atual: empresa_ativa (middleware) ou primeira empresa do membership (não-global, fallback).
    """
    emp = getattr(request, "empresa_ativa", None)
    if emp:
        return emp
    memb = Membership.objects.filter(usuario=request.user, is_active=True).select_related("empresa").first()
    return memb.empresa if memb else None

def _role_do_usuario_na_empresa(user, empresa):
    memb = Membership.objects.filter(usuario=user, empresa=empresa, is_active=True).only("role").first()
    return memb.role if memb else None


# ---------- decorators ----------
def _company_can_view(view):
    """
    Pode VER a página de gestão:
    - Qualquer usuário global (admin ou viewer), com empresa no escopo
    - Ou MASTER/ADMIN da empresa
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        empresa = _get_empresa_escopo(request)
        if not empresa:
            messages.error(request, "Selecione uma empresa na barra superior para gerenciar usuários.")
            return redirect("demonstracao_financeira")
        if _is_global(request.user):
            return view(request, empresa, *args, **kwargs)
        role = _role_do_usuario_na_empresa(request.user, empresa)
        if role in {Membership.Role.MASTER, Membership.Role.ADMIN}:
            return view(request, empresa, *args, **kwargs)
        return HttpResponseForbidden("Você não tem permissão para visualizar os usuários desta empresa.")
    return _wrapped

def _company_can_manage(view):
    """
    Pode GERENCIAR (CRUD):
    - Global ADMIN
    - MASTER/ADMIN da empresa
    (Global VIEWER NÃO pode)
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        empresa = _get_empresa_escopo(request)
        if not empresa:
            messages.error(request, "Selecione uma empresa na barra superior para gerenciar usuários.")
            return redirect("demonstracao_financeira")
        if _is_global_admin(request.user):
            return view(request, empresa, *args, **kwargs)
        role = _role_do_usuario_na_empresa(request.user, empresa)
        if role in {Membership.Role.MASTER, Membership.Role.ADMIN}:
            return view(request, empresa, *args, **kwargs)
        return HttpResponseForbidden("Você não tem permissão para gerenciar usuários desta empresa.")
    return _wrapped


# ---------- regras de atribuição ----------
def _pode_atribuir_role(request_user, empresa, target_role):
    """
    ADMIN não pode atribuir MASTER.
    MASTER pode tudo.
    Global ADMIN pode tudo.
    Global VIEWER não pode.
    """
    if _is_global_admin(request_user):
        return True
    if _is_global(request_user):
        return False  # global viewer
    role = _role_do_usuario_na_empresa(request_user, empresa)
    if role == Membership.Role.MASTER:
        return True
    if role == Membership.Role.ADMIN:
        return target_role != Membership.Role.MASTER
    return False

def _pode_alterar_ou_excluir(request_user, empresa, alvo_membership):
    """
    ADMIN não pode alterar/excluir ADMIN/MASTER; MASTER pode tudo; Global ADMIN pode tudo;
    Global VIEWER não pode.
    """
    if _is_global_admin(request_user):
        return True
    if _is_global(request_user):
        return False  # global viewer
    my_role = _role_do_usuario_na_empresa(request_user, empresa)
    if my_role == Membership.Role.MASTER:
        return True
    if my_role == Membership.Role.ADMIN:
        return alvo_membership.role not in {Membership.Role.ADMIN, Membership.Role.MASTER}
    return False


# ---------- views ----------
@login_required
@_company_can_view
def gerenciar_usuarios(request, empresa: Empresa):
    memberships = (
        Membership.objects.filter(empresa=empresa, is_active=True)
        .select_related("usuario")
        .order_by("usuario__first_name", "usuario__username")
    )

    # Flags de UI
    can_manage = _is_global_admin(request.user) or (
        _role_do_usuario_na_empresa(request.user, empresa) in {Membership.Role.MASTER, Membership.Role.ADMIN}
    )
    can_assign_master = _is_global_admin(request.user) or (
        _role_do_usuario_na_empresa(request.user, empresa) == Membership.Role.MASTER
    )

    return render(request, "usuarios/gerenciar.html", {
        "empresa": empresa,
        "memberships": memberships,
        "create_form": None,  # formulario será simples no template
        "update_form_dummy": None,
        "can_manage": can_manage,
        "can_assign_master": can_assign_master,
    })


@login_required
@_company_can_manage
@transaction.atomic
# DEPRECATED: Método antigo removido - Use o sistema de convites
# def empresa_usuario_adicionar(request, empresa: Empresa):
#     """DEPRECATED: Use o sistema de convites por email (convidar_usuario)"""
#     pass


@login_required
@_company_can_manage
@transaction.atomic
def empresa_usuario_editar(request, empresa: Empresa, membership_id: int):
    """
    Edita apenas o ROLE (papel) do usuário na empresa.
    Dados pessoais (nome, email, senha) devem ser alterados pelo próprio usuário em 'Editar Perfil'.
    
    IMPORTANTE: Usuários MASTER não podem ter o papel alterado (apenas BackOffice).
    """
    memb = get_object_or_404(
        Membership.objects.select_related("usuario", "empresa"),
        id=membership_id, empresa=empresa
    )
    if request.method != "POST":
        return redirect("gerenciar_usuarios")

    # BLOQUEIA EDIÇÃO DE MASTER - apenas BackOffice pode alterar
    if memb.role == Membership.Role.MASTER:
        messages.error(
            request, 
            "Usuários MASTER não podem ter o papel alterado pela interface. "
            "Entre em contato com o BackOffice."
        )
        return redirect("gerenciar_usuarios")

    if not _pode_alterar_ou_excluir(request.user, empresa, memb):
        messages.error(request, "Você não tem permissão para editar este usuário.")
        return redirect("gerenciar_usuarios")

    new_role = request.POST.get("role")
    
    if not new_role:
        messages.error(request, "Papel não informado.")
        return redirect("gerenciar_usuarios")
    
    # BLOQUEIA ATRIBUIÇÃO DE MASTER - apenas BackOffice pode criar
    if new_role == Membership.Role.MASTER:
        messages.error(
            request,
            "O papel MASTER é criado apenas pelo BackOffice. Entre em contato com o suporte."
        )
        return redirect("gerenciar_usuarios")

    if not _pode_atribuir_role(request.user, empresa, new_role):
        messages.error(request, "Você não tem permissão para atribuir este papel.")
        return redirect("gerenciar_usuarios")

    memb.role = new_role
    memb.save()

    messages.success(request, f"Papel do usuário alterado para {memb.get_role_display()} com sucesso.")
    return redirect("gerenciar_usuarios")


@login_required
@_company_can_manage
@transaction.atomic
def empresa_usuario_excluir(request, empresa: Empresa, membership_id: int):
    memb = get_object_or_404(
        Membership.objects.select_related("usuario", "empresa"),
        id=membership_id, empresa=empresa
    )

    if request.method != "POST":
        return redirect("gerenciar_usuarios")

    if not _pode_alterar_ou_excluir(request.user, empresa, memb):
        return HttpResponseForbidden("Você não tem permissão para excluir este usuário.")

    # BLOQUEIA REMOÇÃO DE MASTER - apenas BackOffice pode remover
    if memb.role == Membership.Role.MASTER:
        messages.error(
            request, 
            "Usuários MASTER não podem ser removidos pela interface. "
            "Entre em contato com o BackOffice."
        )
        return redirect("gerenciar_usuarios")

    memb.is_active = False
    memb.save()
    messages.success(request, "Vínculo removido com sucesso.")
    return redirect("gerenciar_usuarios")
