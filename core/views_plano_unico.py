"""Plano de contas único (global). Leitura para todos; edição só para admin da plataforma."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from df.models import Conta
from usuarios.permissions import is_global_admin, platform_admin_required


def _serializar(c: Conta) -> dict:
    return {
        "id": c.id,
        "codigo": c.codigo,
        "descricao": c.descricao,
        "tipo": c.tipo,
        "nivel": c.nivel,
        "natureza": c.natureza,
        "natureza_label": c.get_natureza_display(),
        "papel": c.papel or "",
        "papel_label": c.get_papel_display() if c.papel else "",
        "is_ativa": c.is_ativa,
        "orientacoes": c.orientacoes,
    }


@login_required
def plano_contas_unico(request):
    contas = list(Conta.objects.order_by("codigo"))
    kpis = {
        "total": len(contas),
        "analiticas": sum(1 for c in contas if c.is_analitica),
        "sinteticas": sum(1 for c in contas if not c.is_analitica),
        "inativas": sum(1 for c in contas if not c.is_ativa),
    }
    return render(request, "plano_contas/unico.html", {
        "contas": contas,
        "kpis": kpis,
        "can_edit": is_global_admin(request.user),
        "naturezas": Conta.Natureza.choices,
        "papeis": Conta.Papel.choices,
    })


@login_required
@platform_admin_required
@require_POST
def editar_conta(request, conta_id):
    conta = get_object_or_404(Conta, id=conta_id)

    descricao = (request.POST.get("descricao") or "").strip()
    if not descricao:
        return JsonResponse({"ok": False, "errors": {"descricao": ["Informe a descrição."]}}, status=400)
    conta.descricao = descricao[:255]
    conta.is_ativa = request.POST.get("is_ativa") in ("1", "true", "on")

    papel = (request.POST.get("papel") or "").strip() or None
    if papel and papel not in Conta.Papel.values:
        return JsonResponse({"ok": False, "errors": {"papel": ["Papel inválido."]}}, status=400)
    conta.papel = papel

    try:
        conta.clean()
    except ValidationError as exc:
        return JsonResponse({"ok": False, "errors": {"__all__": exc.messages}}, status=400)
    conta.save()
    return JsonResponse({"ok": True, "item": _serializar(conta)})


@login_required
@platform_admin_required
@require_POST
def criar_conta(request):
    superior = get_object_or_404(Conta, id=request.POST.get("conta_superior"))
    sufixo = (request.POST.get("sufixo") or "").strip()
    descricao = (request.POST.get("descricao") or "").strip()

    erros = {}
    if not sufixo.isdigit():
        erros["sufixo"] = ["Informe o sufixo numérico do código."]
    if not descricao:
        erros["descricao"] = ["Informe a descrição."]
    if erros:
        return JsonResponse({"ok": False, "errors": erros}, status=400)

    codigo = f"{superior.codigo}.{sufixo.zfill(2)}"
    if Conta.objects.filter(codigo=codigo).exists():
        return JsonResponse({"ok": False, "errors": {"sufixo": [f"A conta {codigo} já existe."]}}, status=400)

    conta = Conta(
        codigo=codigo,
        descricao=descricao[:255],
        tipo=Conta.Tipo.ANALITICA,
        conta_superior=superior,
        nivel=superior.nivel + 1,
        natureza=superior.natureza,
        origem_tabela="MANUAL",
    )
    try:
        conta.clean()
    except ValidationError as exc:
        return JsonResponse({"ok": False, "errors": {"__all__": exc.messages}}, status=400)
    conta.save()
    return JsonResponse({"ok": True, "item": _serializar(conta)})
