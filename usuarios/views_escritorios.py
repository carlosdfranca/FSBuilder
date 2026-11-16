# usuarios/views_escritorios.py
"""
Gestão de escritórios de contabilidade e da equipe (convites por e-mail).

- Lista, criação, edição e vínculo com empresas: só o admin da plataforma.
- Detalhe e equipe (convites, membros): admin da plataforma ou Administrador do próprio escritório.
- Aceite do convite: público (a pessoa chega pelo link do e-mail).
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from usuarios.forms import AceitarConviteForm, ConvidarEscritorioForm, EscritorioContabilForm
from usuarios.models import (
    ConviteEscritorio, Empresa, EscritorioContabil, MembroEscritorio, VinculoContabil,
)
from usuarios.permissions import is_global_admin, platform_admin_required
from usuarios.services import convite_escritorio_service as convites
from usuarios.services import escritorio_service as escritorios
from usuarios.services.convite_escritorio_service import (
    ConviteEscritorioError, ConviteEscritorioInvalidoError,
)

LIMITE_BUSCA_EMPRESAS = 20
Status = ConviteEscritorio.Status


# ------------------------------------------------------------------ helpers
def _erro_json(mensagem: str, status: int = 400, campo: str = "__all__") -> JsonResponse:
    return JsonResponse({"ok": False, "errors": {campo: [mensagem]}}, status=status)


def _erros_do_form(form) -> dict:
    return {campo: [e["message"] for e in erros] for campo, erros in form.errors.get_json_data().items()}


def _executar(fazer) -> JsonResponse:
    """Roda a ação e traduz as exceções de negócio em JSON (padrão {"ok", "errors"} do projeto)."""
    try:
        return JsonResponse({"ok": True, **fazer()})
    except PermissionDenied as exc:
        return _erro_json(str(exc) or "Você não tem permissão para esta ação.", 403)
    except ConviteEscritorioError as exc:
        return _erro_json(str(exc))
    except ValidationError as exc:
        erros = exc.message_dict if hasattr(exc, "error_dict") else {"__all__": exc.messages}
        return JsonResponse({"ok": False, "errors": erros}, status=400)


def _escritorio_gerenciavel(request, escritorio_id: int) -> EscritorioContabil:
    """Escritório que o usuário pode gerenciar (plataforma ou Administrador dele); senão 403."""
    escritorio = get_object_or_404(EscritorioContabil, id=escritorio_id)
    if not convites.pode_gerenciar_equipe(request.user, escritorio):
        raise PermissionDenied("Você não tem permissão para gerenciar este escritório.")
    return escritorio


def _link_absoluto(request, convite: ConviteEscritorio) -> str:
    return request.build_absolute_uri(convite.get_link_aceite())


def _serializar_convite(request, convite: ConviteEscritorio) -> dict:
    return {"id": convite.id, "email": convite.email, "role": convite.role, "status": convite.status,
            "link": _link_absoluto(request, convite)}


# ------------------------------------------------------------------ lista e cadastro (plataforma)
@login_required
@platform_admin_required
def escritorios_listar(request):
    lista = list(
        EscritorioContabil.objects
        .annotate(
            qtd_membros=Count("membros", filter=Q(membros__is_active=True), distinct=True),
            qtd_empresas=Count("vinculos", filter=Q(vinculos__is_active=True), distinct=True),
            qtd_convites=Count("convites", filter=Q(convites__status=Status.PENDING), distinct=True),
        )
        .order_by("nome")
    )
    return render(request, "usuarios/escritorios_lista.html", {
        "escritorios": lista,
        "kpis": {
            "ativos": sum(1 for e in lista if e.is_ativo),
            "membros": sum(e.qtd_membros for e in lista),
            "empresas": sum(e.qtd_empresas for e in lista),
            "convites": sum(e.qtd_convites for e in lista),
        },
    })


@login_required
@platform_admin_required
@require_POST
def escritorio_criar(request):
    form = EscritorioContabilForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": _erros_do_form(form)}, status=400)
    escritorio = form.save(commit=False)
    escritorio.is_ativo = True  # todo escritório novo nasce ativo
    escritorio.save()
    return JsonResponse({"ok": True, "url": reverse("escritorio_detalhe", args=[escritorio.id])})


@login_required
@platform_admin_required
@require_POST
def escritorio_editar(request, escritorio_id):
    escritorio = get_object_or_404(EscritorioContabil, id=escritorio_id)
    form = EscritorioContabilForm(request.POST, instance=escritorio)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": _erros_do_form(form)}, status=400)
    form.save()
    return JsonResponse({"ok": True})


# ------------------------------------------------------------------ detalhe (plataforma ou admin do escritório)
@login_required
def escritorio_detalhe(request, escritorio_id):
    escritorio = _escritorio_gerenciavel(request, escritorio_id)
    plataforma = is_global_admin(request.user)

    membros = []
    for m in escritorio.membros.select_related("usuario").order_by("-is_active", "usuario__first_name", "usuario__username"):
        # Espelha validar_pode_alterar_membro só para mostrar/ocultar botões; o servidor sempre revalida.
        m.pode_alterar = plataforma or (m.role != MembroEscritorio.Role.ADMIN and m.usuario_id != request.user.id)
        membros.append(m)

    convites_lista = []
    for c in escritorio.convites.select_related("convidado_por").order_by("-criado_em")[:50]:
        c.link = _link_absoluto(request, c) if c.is_valido() else ""
        # Admin do escritório não mexe em convite de Administrador (regra do serviço)
        c.pode_gerir = plataforma or c.role != MembroEscritorio.Role.ADMIN
        c.pode_reenviar_agora = c.pode_reenviar() and escritorio.is_ativo and c.pode_gerir
        convites_lista.append(c)

    vinculos = list(escritorio.vinculos.select_related("empresa", "criado_por").order_by("-is_active", "empresa__nome"))

    return render(request, "usuarios/escritorio_detalhe.html", {
        "escritorio": escritorio,
        "e_plataforma": plataforma,
        "membros": membros,
        "convites": convites_lista,
        "vinculos": vinculos,
        "roles": MembroEscritorio.Role.choices,
        "roles_convite": MembroEscritorio.Role.choices if plataforma else [
            (v, r) for v, r in MembroEscritorio.Role.choices if v != MembroEscritorio.Role.ADMIN
        ],
        "kpis": {
            "membros": sum(1 for m in membros if m.is_active),
            "empresas": sum(1 for v in vinculos if v.is_active),
            "convites": sum(1 for c in convites_lista if c.status == Status.PENDING),
        },
        "aba": request.GET.get("aba", "equipe"),
    })


# ------------------------------------------------------------------ equipe: convites
@login_required
@require_POST
def escritorio_convite_criar(request, escritorio_id):
    escritorio = get_object_or_404(EscritorioContabil, id=escritorio_id)
    form = ConvidarEscritorioForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": _erros_do_form(form)}, status=400)

    def fazer():
        convite = convites.criar_convite(
            escritorio=escritorio, email=form.cleaned_data["email"],
            role=form.cleaned_data["role"], convidado_por=request.user,
        )
        return {"item": _serializar_convite(request, convite)}
    return _executar(fazer)


def _convite_do_escritorio(escritorio_id: int, convite_id: int) -> ConviteEscritorio:
    return get_object_or_404(ConviteEscritorio, id=convite_id, escritorio_id=escritorio_id)


@login_required
@require_POST
def escritorio_convite_reenviar(request, escritorio_id, convite_id):
    _convite_do_escritorio(escritorio_id, convite_id)
    return _executar(lambda: {"item": _serializar_convite(request, convites.reenviar_convite(convite_id, request.user))})


@login_required
@require_POST
def escritorio_convite_cancelar(request, escritorio_id, convite_id):
    _convite_do_escritorio(escritorio_id, convite_id)
    return _executar(lambda: {"id": convites.cancelar_convite(convite_id, request.user).id})


# ------------------------------------------------------------------ equipe: membros
@login_required
@require_POST
def escritorio_membro_editar(request, escritorio_id, membro_id):
    get_object_or_404(MembroEscritorio, id=membro_id, escritorio_id=escritorio_id)
    role = (request.POST.get("role") or "").strip() or None
    ativo = {"1": True, "0": False}.get(request.POST.get("is_active"))

    def fazer():
        membro = escritorios.alterar_membro(membro_id, por=request.user, role=role, is_active=ativo)
        return {"role": membro.role, "is_active": membro.is_active}
    return _executar(fazer)


# ------------------------------------------------------------------ vínculos com empresas (plataforma)
@login_required
@platform_admin_required
@require_POST
def escritorio_empresa_vincular(request, escritorio_id):
    escritorio = get_object_or_404(EscritorioContabil, id=escritorio_id)
    try:
        empresa_id = int(request.POST.get("empresa_id") or 0)
    except ValueError:
        empresa_id = 0
    empresa = Empresa.objects.filter(id=empresa_id).first()
    if empresa is None:
        return _erro_json("Escolha uma empresa da lista.", campo="empresa")

    def fazer():
        vinculo = escritorios.vincular_empresa(escritorio=escritorio, empresa=empresa, por=request.user)
        return {"id": vinculo.id}
    return _executar(fazer)


@login_required
@platform_admin_required
@require_POST
def escritorio_empresa_alterar(request, escritorio_id, vinculo_id):
    get_object_or_404(VinculoContabil, id=vinculo_id, escritorio_id=escritorio_id)
    ativo = request.POST.get("ativo") == "1"

    def fazer():
        vinculo = escritorios.definir_vinculo_ativo(vinculo_id, ativo=ativo, por=request.user)
        return {"is_active": vinculo.is_active}
    return _executar(fazer)


@login_required
@platform_admin_required
@require_GET
def empresas_buscar(request):
    """Autocomplete de empresas para vincular; informa as que já têm escritório (não podem ser escolhidas)."""
    termo = (request.GET.get("q") or "").strip()
    qs = Empresa.objects.all()
    if termo:
        filtro = Q(nome__icontains=termo) | Q(cnpj__icontains=termo)
        digitos = re.sub(r"\D", "", termo)
        if digitos:
            filtro |= Q(cnpj__icontains=digitos)
        qs = qs.filter(filtro)
    empresas = list(qs.only("id", "nome", "cnpj").order_by("nome")[:LIMITE_BUSCA_EMPRESAS])
    ativos = {
        v.empresa_id: v
        for v in VinculoContabil.objects.filter(is_active=True, empresa__in=empresas).select_related("escritorio")
    }
    atual = str(request.GET.get("escritorio") or "")
    return JsonResponse({"empresas": [
        {
            "id": e.id, "nome": e.nome, "cnpj": e.cnpj or "",
            "escritorio_atual": ativos[e.id].escritorio.nome if e.id in ativos else "",
            "ja_neste": e.id in ativos and str(ativos[e.id].escritorio_id) == atual,
            "bloqueada": e.id in ativos,
        }
        for e in empresas
    ]})


# ------------------------------------------------------------------ aceite (público)
def _estado_do_aceite(request, convite) -> str:
    if request.user.is_authenticated:
        email = (request.user.email or "").strip().lower()
        return "logado_ok" if email == convite.email else "logado_outro"
    return "tem_conta" if convites.existe_conta_com_email(convite.email) else "novo"


def aceitar_convite_escritorio(request, token):
    try:
        convite = convites.validar_token(token)
    except ConviteEscritorioInvalidoError as exc:
        return render(request, "convites/convite_invalido.html", {"erro": str(exc)})

    estado = _estado_do_aceite(request, convite)
    form = AceitarConviteForm() if estado == "novo" else None
    erro = ""
    ip = request.META.get("REMOTE_ADDR")
    agente = request.META.get("HTTP_USER_AGENT", "")

    if request.method == "POST":
        try:
            if estado == "logado_ok":
                convites.aceitar_como_usuario_existente(token=token, usuario=request.user, ip_address=ip, user_agent=agente)
                messages.success(request, f"Você agora faz parte do escritório {convite.escritorio.nome}.")
                return redirect("painel_contabilidade")
            if estado == "novo":
                form = AceitarConviteForm(request.POST)
                if form.is_valid():
                    usuario, _ = convites.aceitar_como_novo_usuario(
                        token=token, username=form.cleaned_data["username"],
                        first_name=form.cleaned_data.get("first_name", ""), last_name=form.cleaned_data.get("last_name", ""),
                        password=form.cleaned_data["password1"], ip_address=ip, user_agent=agente,
                    )
                    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
                    messages.success(request, f"Bem-vindo! Você agora faz parte do escritório {convite.escritorio.nome}.")
                    return redirect("painel_contabilidade")
            else:
                erro = "Esta ação não está disponível agora. Siga as instruções da página."
        except ValidationError as exc:
            if form is not None and hasattr(exc, "error_dict"):
                for campo, msgs in exc.message_dict.items():
                    for msg in msgs:
                        form.add_error(campo if campo in form.fields else None, msg)
            else:
                erro = " ".join(exc.messages)
        except ConviteEscritorioInvalidoError as exc:
            return render(request, "convites/convite_invalido.html", {"erro": str(exc)})
        except ConviteEscritorioError as exc:
            erro = str(exc)

    return render(request, "convites/aceitar_convite_escritorio.html", {
        "convite": convite,
        "escritorio": convite.escritorio,
        "role_display": convite.get_role_display(),
        "estado": estado,
        "form": form,
        "erro": erro,
        "email_logado": (request.user.email if request.user.is_authenticated else ""),
        "login_url": f"{reverse('login')}?{urlencode({'next': request.path})}",
    })
