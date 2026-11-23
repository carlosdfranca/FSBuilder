"""Reúne as demonstrações de um período (DPF, DRE, DMPL e DFC) a partir do plano de contas único."""
from __future__ import annotations

from core.processing.demonstrativo_service import NIVEL_PADRAO, gerar_dpf_arvore, gerar_dre_arvore
from core.processing.dfc_arvore_service import gerar_dfc_arvore
from core.processing.dmpl_service import gerar_dados_dmpl


def gerar_dfs(periodo, nivel: int = NIVEL_PADRAO) -> dict:
    """
    Tudo que a tela, o Excel e o Word precisam para um PeriodoDF.
    Valores em R$ mil. `zerar_anterior` vale quando o período não tem data anterior (início do fundo).
    """
    fundo_id = periodo.fundo_id
    data_atual, data_anterior = periodo.data_referencia, periodo.data_anterior
    zerar_anterior = data_anterior is None
    base = dict(fundo_id=fundo_id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior)

    dpf = gerar_dpf_arvore(nivel_max=nivel, **base)
    dre = gerar_dre_arvore(nivel_max=nivel, **base)
    dfc = gerar_dfc_arvore(**base)
    dmpl = gerar_dados_dmpl(fundo_id, data_atual, data_anterior, zerar_anterior=zerar_anterior)

    totais = dpf["totais"]
    return {
        "data_atual": data_atual,
        "data_anterior": data_anterior,
        "zerar_anterior": zerar_anterior,
        "nivel": nivel,
        "dpf": dpf,
        "dre": dre,
        "dfc": dfc,
        "dados_dmpl": dmpl,
        "resultado_exercicio": dre["resultado_atual"],
        "resultado_exercicio_anterior": dre["resultado_anterior"],
        "pl_ajustado_atual": totais["pl"]["atual"],
        "pl_ajustado_anterior": totais["pl"]["anterior"],
        "total_pl_passivo_atual": totais["passivo_pl"]["atual"],
        "total_pl_passivo_anterior": totais["passivo_pl"]["anterior"],
        "variacao_atual": dfc["variacao_atual"],
        "variacao_ant": dfc["variacao_anterior"],
    }
