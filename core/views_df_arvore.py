"""Protótipo: DPF e DRE geradas pela árvore do plano de contas único (a partir das movimentações)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from core.processing.demonstrativo_service import (
    NIVEL_MAX, NIVEL_MIN, NIVEL_PADRAO, gerar_dpf_arvore, gerar_dre_arvore,
)
from df.models import Fundo, PeriodoDF
from usuarios.permissions import company_can_view_data
from usuarios.utils.company_scope import query_por_empresa_ativa


def _nivel(request) -> int:
    try:
        nivel = int(request.GET.get("nivel", NIVEL_PADRAO))
    except (TypeError, ValueError):
        return NIVEL_PADRAO
    return max(NIVEL_MIN, min(NIVEL_MAX, nivel))


@login_required
@company_can_view_data
def df_resultado_arvore(request, periodo_df_id):
    fundo_qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    periodo = get_object_or_404(PeriodoDF.objects.select_related("fundo"), id=periodo_df_id, fundo__in=fundo_qs)
    fundo = periodo.fundo

    if not periodo.data_referencia:
        messages.error(request, f"O período '{periodo.nome_exibicao}' não possui data de referência.")
        return redirect("demonstracao_financeira")

    nivel = _nivel(request)
    zerar_anterior = periodo.data_anterior is None
    args = dict(
        fundo_id=fundo.id,
        data_atual=periodo.data_referencia,
        data_anterior=periodo.data_anterior,
        nivel_max=nivel,
        zerar_anterior=zerar_anterior,
    )
    return render(request, "df_arvore.html", {
        "fundo": fundo,
        "periodo": periodo,
        "data_atual": periodo.data_referencia,
        "data_anterior": periodo.data_anterior,
        "nivel": nivel,
        "niveis": range(NIVEL_MIN, NIVEL_MAX + 1),
        "dpf": gerar_dpf_arvore(**args),
        "dre": gerar_dre_arvore(**args),
    })
