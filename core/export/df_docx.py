import os
from io import BytesIO

from django.conf import settings
from docxtpl import DocxTemplate


def _fmt(value):
    """Formata inteiro como string numérica con separador de milhar brasileiro."""
    if value is None:
        return "-"
    try:
        v = int(value)
        if v == 0:
            return "-"
        return f"{v:,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def _fmt_float(value, decimals=2):
    """Formata float com casas decimais."""
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def _perc_from(val, base):
    try:
        return round((float(val or 0) / float(base or 0)) * 100, 2) if base else 0.0
    except Exception:
        return 0.0


def _data_str(data, default="—"):
    if not data:
        return default
    return data.strftime("%d/%m/%Y")


_MESES_PT = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _data_extenso(data, default="—"):
    """Retorna a data por extenso, ex: '31 de dezembro de 2024'."""
    if not data:
        return default
    return f"{data.day} de {_MESES_PT[data.month - 1]} de {data.year}"


# ================================================================
# Construtores de listas de linhas
# ================================================================

def _build_ativo_rows(dpf_tabela, pl_atual, pl_anterior):
    rows = []
    ativo = dpf_tabela["ATIVO"]
    for grupo_label, bloco in ativo.items():
        if grupo_label.startswith("TOTAL_"):
            continue
        soma_at = bloco.get("SOMA", 0) or 0
        soma_an = bloco.get("SOMA_ANTERIOR", 0) or 0
        p_at = bloco.get("PERC_ATUAL", _perc_from(soma_at, pl_atual))
        p_an = bloco.get("PERC_ANTERIOR", _perc_from(soma_an, pl_anterior))
        rows.append({
            "descricao": grupo_label,
            "atual": _fmt(soma_at),
            "anterior": _fmt(soma_an),
            "perc_atual": _fmt_float(p_at),
            "perc_anterior": _fmt_float(p_an),
            "tipo": "grupo",
        })
        for sub, valores in bloco.items():
            if sub in ("SOMA", "SOMA_ANTERIOR") or not isinstance(valores, dict):
                continue
            v_at = valores.get("ATUAL", 0) or 0
            v_an = valores.get("ANTERIOR", 0) or 0
            p_at = valores.get("PERC_ATUAL", _perc_from(v_at, pl_atual))
            p_an = valores.get("PERC_ANTERIOR", _perc_from(v_an, pl_anterior))
            rows.append({
                "descricao": sub,
                "atual": _fmt(v_at),
                "anterior": _fmt(v_an),
                "perc_atual": _fmt_float(p_at),
                "perc_anterior": _fmt_float(p_an),
                "tipo": "item",
            })
    return rows


def _build_passivo_rows(dpf_tabela, pl_atual, pl_anterior):
    rows = []
    passivo = dpf_tabela["PASSIVO"]
    for grupo_label, bloco in passivo.items():
        if grupo_label.startswith("TOTAL_"):
            continue
        soma_at = bloco.get("SOMA", 0) or 0
        soma_an = bloco.get("SOMA_ANTERIOR", 0) or 0
        p_at = bloco.get("PERC_ATUAL", _perc_from(soma_at, pl_atual))
        p_an = bloco.get("PERC_ANTERIOR", _perc_from(soma_an, pl_anterior))
        rows.append({
            "descricao": grupo_label,
            "atual": _fmt(soma_at),
            "anterior": _fmt(soma_an),
            "perc_atual": _fmt_float(p_at),
            "perc_anterior": _fmt_float(p_an),
            "tipo": "grupo",
        })
        for sub, valores in bloco.items():
            if sub in ("SOMA", "SOMA_ANTERIOR") or not isinstance(valores, dict):
                continue
            v_at = valores.get("ATUAL", 0) or 0
            v_an = valores.get("ANTERIOR", 0) or 0
            p_at = valores.get("PERC_ATUAL", _perc_from(v_at, pl_atual))
            p_an = valores.get("PERC_ANTERIOR", _perc_from(v_an, pl_anterior))
            rows.append({
                "descricao": sub,
                "atual": _fmt(v_at),
                "anterior": _fmt(v_an),
                "perc_atual": _fmt_float(p_at),
                "perc_anterior": _fmt_float(p_an),
                "tipo": "item",
            })
    return rows


def _build_dre_rows(dre_tabela):
    rows = []
    for grupo, dados in dre_tabela.items():
        rows.append({
            "descricao": grupo,
            "atual": _fmt(dados.get("SOMA", 0)),
            "anterior": _fmt(dados.get("SOMA_ANTERIOR", 0)),
            "tipo": "grupo",
        })
        for item, valores in dados.items():
            if item in ("SOMA", "SOMA_ANTERIOR") or not isinstance(valores, dict):
                continue
            rows.append({
                "descricao": item,
                "atual": _fmt(valores.get("ATUAL", 0)),
                "anterior": _fmt(valores.get("ANTERIOR", 0)),
                "tipo": "item",
            })
    return rows


# ================================================================
# Função principal
# ================================================================

def build_docx_context(
    fundo,
    data_atual,
    data_anterior,
    dre_tabela,
    dpf_tabela,
    dados_dmpl,
    dfc_tabela,
    resultado_exercicio,
    resultado_exercicio_anterior,
    pl_ajustado_atual,
    pl_ajustado_anterior,
    total_pl_passivo_atual,
    total_pl_passivo_anterior,
    variacao_atual,
    variacao_ant,
):
    ativo = dpf_tabela["ATIVO"]
    passivo = dpf_tabela["PASSIVO"]

    tot_ativo_at = (ativo["TOTAL_ATIVO"]["ATUAL"] or 0)
    tot_ativo_an = (ativo["TOTAL_ATIVO"]["ANTERIOR"] or 0)
    tot_passivo_at = (passivo["TOTAL_PASSIVO"]["ATUAL"] or 0)
    tot_passivo_an = (passivo["TOTAL_PASSIVO"]["ANTERIOR"] or 0)

    bloco_op = dfc_tabela.get("fluxo_operacionais", {})
    bloco_fin = dfc_tabela.get("fluxo_financiamento", {})
    ajustes = bloco_op.get("ajustes", {})

    def _op(key):
        return bloco_op.get(key, {})

    def _fin(key):
        return bloco_fin.get(key, {})

    def _adj(key):
        return ajustes.get(key, {})

    def _v(block, field):
        return _fmt(block.get(field))

    context = {
        # ── Cabeçalho ──────────────────────────────────────────
        "fundo_nome": str(fundo.nome).upper(),
        "fundo_cnpj": fundo.cnpj or "",
        "empresa_nome": fundo.empresa.nome or "",
        "empresa_cnpj": fundo.empresa.cnpj or "",
        "data_atual": _data_str(data_atual),
        "data_anterior": _data_str(data_anterior),
        "data_atual_extenso": _data_extenso(data_atual),
        "data_anterior_extenso": _data_extenso(data_anterior),

        # ── DPF — loops ────────────────────────────────────────
        "ativo_rows": _build_ativo_rows(dpf_tabela, pl_ajustado_atual, pl_ajustado_anterior),
        "passivo_rows": _build_passivo_rows(dpf_tabela, pl_ajustado_atual, pl_ajustado_anterior),

        # ── DPF — totais e PL ──────────────────────────────────
        "dpf_total_ativo_atual": _fmt(tot_ativo_at),
        "dpf_total_ativo_anterior": _fmt(tot_ativo_an),
        "dpf_total_ativo_perc_atual": _fmt_float(_perc_from(tot_ativo_at, pl_ajustado_atual)),
        "dpf_total_ativo_perc_anterior": _fmt_float(_perc_from(tot_ativo_an, pl_ajustado_anterior)),
        "dpf_total_passivo_atual": _fmt(tot_passivo_at),
        "dpf_total_passivo_anterior": _fmt(tot_passivo_an),
        "dpf_total_passivo_perc_atual": _fmt_float(_perc_from(tot_passivo_at, pl_ajustado_atual)),
        "dpf_total_passivo_perc_anterior": _fmt_float(_perc_from(tot_passivo_an, pl_ajustado_anterior)),
        "pl_ajustado_atual": _fmt(pl_ajustado_atual),
        "pl_ajustado_anterior": _fmt(pl_ajustado_anterior),
        "total_pl_passivo_atual": _fmt(total_pl_passivo_atual),
        "total_pl_passivo_anterior": _fmt(total_pl_passivo_anterior),

        # ── DRE — loop e resultado ─────────────────────────────
        "dre_rows": _build_dre_rows(dre_tabela),
        "resultado_exercicio": _fmt(resultado_exercicio),
        "resultado_exercicio_anterior": _fmt(resultado_exercicio_anterior),

        # ── DMPL ───────────────────────────────────────────────
        "dmpl_pl_inicio_atual": _fmt(dados_dmpl.get("valor_primeiro")),
        "dmpl_pl_inicio_anterior": _fmt(dados_dmpl.get("valor_primeiro_ant")),
        "dmpl_cotas_inicio_qtd": _fmt_float(dados_dmpl.get("qtd_cotas_inicio"), decimals=0),
        "dmpl_cotas_inicio_valor": _fmt_float(dados_dmpl.get("cota_inicio")),
        "dmpl_cotas_inicio_ant_qtd": _fmt_float(dados_dmpl.get("qtd_cotas_inicio_ant"), decimals=0),
        "dmpl_cotas_inicio_ant_valor": _fmt_float(dados_dmpl.get("cota_inicio_ant")),
        "dmpl_emissao_qtd": _fmt_float(dados_dmpl.get("aplicacoes_qtd"), decimals=0),
        "dmpl_emissao_valor": _fmt(dados_dmpl.get("aplicacoes_valor")),
        "dmpl_resgate_qtd": _fmt_float(dados_dmpl.get("resgates_qtd"), decimals=0),
        "dmpl_resgate_valor": _fmt(dados_dmpl.get("resgates_valor")),
        "dmpl_pl_antes_resultado": _fmt(dados_dmpl.get("pl_antes_resultado_periodo")),
        "dmpl_cotas_fim_qtd": _fmt_float(dados_dmpl.get("qtd_cotas_fim"), decimals=0),
        "dmpl_cotas_fim_valor": _fmt_float(dados_dmpl.get("cota_fim")),
        "dmpl_valor_ultimo": _fmt(dados_dmpl.get("valor_ultimo")),

        # ── DFC — bloco operacional ────────────────────────────
        "dfc_op_titulo": bloco_op.get("titulo", ""),
        "dfc_resultado_liq_titulo": _op("resultado_liquido").get("titulo", ""),
        "dfc_resultado_liq_atual": _v(_op("resultado_liquido"), "ATUAL"),
        "dfc_resultado_liq_anterior": _v(_op("resultado_liquido"), "ANTERIOR"),
        "dfc_ajustes_titulo": ajustes.get("titulo", ""),
        "dfc_rendimento_dc_titulo": _adj("rendimento_dc").get("titulo", ""),
        "dfc_rendimento_dc_atual": _v(_adj("rendimento_dc"), "ATUAL"),
        "dfc_rendimento_dc_anterior": _v(_adj("rendimento_dc"), "ANTERIOR"),
        "dfc_provisao_perdas_titulo": _adj("provisao_perdas").get("titulo", ""),
        "dfc_provisao_perdas_atual": _v(_adj("provisao_perdas"), "ATUAL"),
        "dfc_provisao_perdas_anterior": _v(_adj("provisao_perdas"), "ANTERIOR"),
        "dfc_taxa_adm_titulo": _adj("taxa_adm").get("titulo", ""),
        "dfc_taxa_adm_atual": _v(_adj("taxa_adm"), "ATUAL"),
        "dfc_taxa_adm_anterior": _v(_adj("taxa_adm"), "ANTERIOR"),
        "dfc_taxa_gestao_titulo": _adj("taxa_gestao").get("titulo", ""),
        "dfc_taxa_gestao_atual": _v(_adj("taxa_gestao"), "ATUAL"),
        "dfc_taxa_gestao_anterior": _v(_adj("taxa_gestao"), "ANTERIOR"),
        "dfc_resultado_ajustado_titulo": _adj("resultado_ajustado").get("titulo", ""),
        "dfc_resultado_ajustado_atual": _v(_adj("resultado_ajustado"), "ATUAL"),
        "dfc_resultado_ajustado_anterior": _v(_adj("resultado_ajustado"), "ANTERIOR"),
        "dfc_aumento_dc_titulo": _op("aumento_dc").get("titulo", ""),
        "dfc_aumento_dc_atual": _v(_op("aumento_dc"), "ATUAL"),
        "dfc_aumento_dc_anterior": _v(_op("aumento_dc"), "ANTERIOR"),
        "dfc_aumento_receber_titulo": _op("aumento_receber").get("titulo", ""),
        "dfc_aumento_receber_atual": _v(_op("aumento_receber"), "ATUAL"),
        "dfc_aumento_receber_anterior": _v(_op("aumento_receber"), "ANTERIOR"),
        "dfc_reducao_pagar_titulo": _op("reducao_pagar").get("titulo", ""),
        "dfc_reducao_pagar_atual": _v(_op("reducao_pagar"), "ATUAL"),
        "dfc_reducao_pagar_anterior": _v(_op("reducao_pagar"), "ANTERIOR"),
        "dfc_caixa_operacional_titulo": _op("caixa_operacional").get("titulo", ""),
        "dfc_caixa_operacional_atual": _v(_op("caixa_operacional"), "ATUAL"),
        "dfc_caixa_operacional_anterior": _v(_op("caixa_operacional"), "ANTERIOR"),

        # ── DFC — bloco financiamento ──────────────────────────
        "dfc_fin_titulo": bloco_fin.get("titulo", ""),
        "dfc_emissao_titulo": _fin("emissao").get("titulo", ""),
        "dfc_emissao_atual": _v(_fin("emissao"), "ATUAL"),
        "dfc_emissao_anterior": _v(_fin("emissao"), "ANTERIOR"),
        "dfc_resgate_titulo": _fin("resgate").get("titulo", ""),
        "dfc_resgate_atual": _v(_fin("resgate"), "ATUAL"),
        "dfc_resgate_anterior": _v(_fin("resgate"), "ANTERIOR"),
        "dfc_caixa_financiamento_titulo": _fin("caixa_financiamento").get("titulo", ""),
        "dfc_caixa_financiamento_atual": _v(_fin("caixa_financiamento"), "ATUAL"),
        "dfc_caixa_financiamento_anterior": _v(_fin("caixa_financiamento"), "ANTERIOR"),

        # ── DFC — variação e saldos ────────────────────────────
        "dfc_variacao_caixa_titulo": dfc_tabela.get("variacao_caixa", {}).get("titulo", ""),
        "dfc_variacao_caixa_atual": _fmt(variacao_atual),
        "dfc_variacao_caixa_anterior": _fmt(variacao_ant),
        "dfc_caixa_inicio_titulo": dfc_tabela.get("caixa_inicio", {}).get("titulo", ""),
        "dfc_caixa_inicio_atual": _v(dfc_tabela.get("caixa_inicio", {}), "ATUAL"),
        "dfc_caixa_inicio_anterior": _v(dfc_tabela.get("caixa_inicio", {}), "ANTERIOR"),
        "dfc_caixa_final_titulo": dfc_tabela.get("caixa_final", {}).get("titulo", ""),
        "dfc_caixa_final_atual": _v(dfc_tabela.get("caixa_final", {}), "ATUAL"),
        "dfc_caixa_final_anterior": _v(dfc_tabela.get("caixa_final", {}), "ANTERIOR"),
    }

    return context


def gerar_docx(context):
    """
    Carrega o template modelo_df.docx, renderiza com o contexto e retorna
    um BytesIO pronto para ser enviado como HttpResponse.
    """
    template_path = os.path.join(settings.MEDIA_ROOT, "doc", "modelo_df.docx")
    tpl = DocxTemplate(template_path)
    tpl.render(context)
    buffer = BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer
