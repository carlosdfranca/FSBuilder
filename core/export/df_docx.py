import os
import re
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


def _paragrafos(texto):
    """Quebra texto livre em parágrafos (blocos separados por linha em branco)."""
    if not texto or not texto.strip():
        return []
    blocos = re.split(r"\n\s*\n", texto.replace("\r\n", "\n").strip())
    return [" ".join(l.strip() for l in b.split("\n")) for b in blocos if b.strip()]


# ================================================================
# Construtores de listas de linhas (a partir da árvore do plano)
# ================================================================

_TIPO_DOCX = {"grupo": "grupo", "total": "total", "conta": "item", "virtual": "item"}


def _rows(linhas, com_perc=False):
    """Linhas da árvore no formato de loop do template: textos já formatados."""
    rows = []
    for l in linhas:
        row = {
            "descricao": l["descricao"],
            "atual": _fmt(l["atual"]) if l["atual"] is not None else "",
            "anterior": _fmt(l["anterior"]) if l["anterior"] is not None else "",
            "tipo": _TIPO_DOCX.get(l["tipo_linha"], "item"),
            "nivel": l["nivel"],
        }
        if com_perc:
            row["perc_atual"] = _fmt_float(l.get("perc_atual"))
            row["perc_anterior"] = _fmt_float(l.get("perc_anterior"))
        rows.append(row)
    return rows


# ================================================================
# Função principal
# ================================================================

def build_docx_context(fundo, dfs):
    """
    Contexto do template modelo_df.docx. `dfs` é o resultado de core.processing.df_service.gerar_dfs.
    Valores já estão em R$ mil e arredondados.
    """
    data_atual, data_anterior = dfs["data_atual"], dfs["data_anterior"]
    dpf, dre, dfc, dados_dmpl = dfs["dpf"], dfs["dre"], dfs["dfc"], dfs["dados_dmpl"]
    totais = dpf["totais"]

    def total(chave, campo):
        return totais[chave][campo]

    return {
        # ── Cabeçalho ──────────────────────────────────────────
        "fundo_nome": str(fundo.nome).upper(),
        "fundo_cnpj": fundo.cnpj or "",
        "empresa_nome": fundo.empresa.nome or "",
        "empresa_cnpj": fundo.empresa.cnpj or "",
        "data_atual": _data_str(data_atual),
        "data_anterior": _data_str(data_anterior),
        "data_atual_extenso": _data_extenso(data_atual),
        "data_anterior_extenso": _data_extenso(data_anterior),

        # ── Contexto operacional (texto da empresa) ────────────
        "contexto_operacional_paragrafos": (
            _paragrafos(fundo.empresa.contexto_operacional)
            or ["[Contexto operacional não preenchido]"]
        ),

        # ── DPF — loops (ativo, passivo e PL, com recuo por nível) ─
        "ativo_rows": _rows(dpf["ativo"], com_perc=True),
        "passivo_rows": _rows(dpf["passivo"], com_perc=True),
        "pl_rows": _rows(dpf["pl"], com_perc=True),

        # ── DPF — totais ───────────────────────────────────────
        "dpf_total_ativo_atual": _fmt(total("ativo", "atual")),
        "dpf_total_ativo_anterior": _fmt(total("ativo", "anterior")),
        "dpf_total_ativo_perc_atual": _fmt_float(total("ativo", "perc_atual")),
        "dpf_total_ativo_perc_anterior": _fmt_float(total("ativo", "perc_anterior")),
        "dpf_total_passivo_atual": _fmt(total("passivo", "atual")),
        "dpf_total_passivo_anterior": _fmt(total("passivo", "anterior")),
        "dpf_total_passivo_perc_atual": _fmt_float(total("passivo", "perc_atual")),
        "dpf_total_passivo_perc_anterior": _fmt_float(total("passivo", "perc_anterior")),
        "pl_ajustado_atual": _fmt(dfs["pl_ajustado_atual"]),
        "pl_ajustado_anterior": _fmt(dfs["pl_ajustado_anterior"]),
        "total_pl_passivo_atual": _fmt(dfs["total_pl_passivo_atual"]),
        "total_pl_passivo_anterior": _fmt(dfs["total_pl_passivo_anterior"]),

        # ── DRE — loop e resultado ─────────────────────────────
        "dre_rows": _rows(dre["linhas"]),
        "resultado_exercicio": _fmt(dfs["resultado_exercicio"]),
        "resultado_exercicio_anterior": _fmt(dfs["resultado_exercicio_anterior"]),

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

        # ── DFC — loop único (seções, linhas e totais) + saldos de caixa ──
        "dfc_rows": _rows(dfc["linhas"]),
        "dfc_variacao_caixa_atual": _fmt(dfc["variacao_atual"]),
        "dfc_variacao_caixa_anterior": _fmt(dfc["variacao_anterior"]),
        "dfc_caixa_inicio_atual": _fmt(dfc["caixa_inicio_atual"]),
        "dfc_caixa_inicio_anterior": _fmt(dfc["caixa_inicio_anterior"]),
        "dfc_caixa_final_atual": _fmt(dfc["caixa_final_atual"]),
        "dfc_caixa_final_anterior": _fmt(dfc["caixa_final_anterior"]),
    }


def gerar_docx(context):
    """
    Carrega o template modelo_df.docx, renderiza com o contexto e retorna
    um BytesIO pronto para ser enviado como HttpResponse.
    """
    template_path = os.path.join(settings.MEDIA_ROOT, "doc", "modelo_df.docx")
    tpl = DocxTemplate(template_path)
    tpl.render(context, autoescape=True)
    buffer = BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer
