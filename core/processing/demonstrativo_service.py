"""
Demonstrativos (DPF e DRE) gerados pela árvore do plano de contas único.

Cada linha tem um formato único, usado pela tela e (nas próximas etapas) pelos exportadores:
    {codigo, descricao, nivel, tipo_linha, atual, anterior, perc_atual, perc_anterior}
`tipo_linha`: "grupo" (tem linhas abaixo visíveis), "conta" (folha da visão) ou "total"/"virtual".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from df.models import Conta
from df.services.saldo_calculo_service import saldos_por_codigo

ZERO = Decimal("0")
NIVEL_PADRAO = 4
NIVEL_MIN, NIVEL_MAX = 2, 7
CODIGO_RESULTADO = "3"
CODIGO_PL = "2.03"
RAIZES_PASSIVO = ("2.01", "2.02")


def _no_ramo(codigo: str, raiz: str) -> bool:
    return codigo == raiz or codigo.startswith(raiz + ".")


def _mil(valor: Decimal, dividir_por_mil: bool) -> Decimal:
    return valor / Decimal(1000) if dividir_por_mil else valor


def _perc(valor: Decimal, base: Decimal | None) -> Decimal | None:
    if not base:
        return None
    return valor / base * Decimal(100)


def _saldos(fundo_id: int, data: date | None, zerar: bool) -> dict[str, Decimal]:
    if zerar or data is None:
        return {}
    return saldos_por_codigo(fundo_id, data)


def montar_demonstrativo(
    atual: dict[str, Decimal],
    anterior: dict[str, Decimal],
    raizes: tuple[str, ...] | list[str],
    nivel_max: int = NIVEL_PADRAO,
    dividir_por_mil: bool = True,
    ocultar_zeradas: bool = True,
    ajustes: dict[str, tuple[Decimal, Decimal]] | None = None,
) -> list[dict]:
    """
    Percorre a árvore a partir de `raizes` e devolve as linhas até `nivel_max`.
    Os níveis abaixo do corte já estão somados nas contas sintéticas visíveis.

    `ajustes` soma {codigo: (delta_atual, delta_anterior)} em linhas e ancestrais (ex.: resultado
    do exercício dentro do PL).
    """
    ajustes = ajustes or {}

    def valor(codigo: str, base: dict[str, Decimal], idx: int) -> Decimal:
        v = base.get(codigo, ZERO)
        for cod_aj, deltas in ajustes.items():
            if _no_ramo(cod_aj, codigo):
                v += deltas[idx]
        return v

    contas = list(
        Conta.objects.filter(nivel__lte=nivel_max).order_by("codigo").values("codigo", "descricao", "nivel", "tipo")
    )
    linhas: list[dict] = []
    for raiz in raizes:
        for c in contas:
            if not _no_ramo(c["codigo"], raiz):
                continue
            a = valor(c["codigo"], atual, 0)
            b = valor(c["codigo"], anterior, 1)
            if ocultar_zeradas and a == ZERO and b == ZERO:
                continue
            linhas.append(
                {
                    "codigo": c["codigo"],
                    "descricao": c["descricao"],
                    "nivel": c["nivel"],
                    "tipo_linha": "conta",
                    "atual": _mil(a, dividir_por_mil),
                    "anterior": _mil(b, dividir_por_mil),
                }
            )

    # "grupo" = a linha seguinte está num nível mais fundo
    for i, linha in enumerate(linhas[:-1]):
        if linhas[i + 1]["nivel"] > linha["nivel"]:
            linha["tipo_linha"] = "grupo"
    return linhas


def _total(codigo: str, descricao: str, atual: Decimal, anterior: Decimal, nivel: int = 1) -> dict:
    return {
        "codigo": codigo,
        "descricao": descricao,
        "nivel": nivel,
        "tipo_linha": "total",
        "atual": atual,
        "anterior": anterior,
    }


def _com_percentuais(linhas: list[dict], base_atual: Decimal | None, base_anterior: Decimal | None) -> list[dict]:
    for linha in linhas:
        linha["perc_atual"] = _perc(linha["atual"], base_atual)
        linha["perc_anterior"] = _perc(linha["anterior"], base_anterior)
    return linhas


def gerar_dpf_arvore(
    fundo_id: int,
    data_atual: date,
    data_anterior: date | None,
    nivel_max: int = NIVEL_PADRAO,
    dividir_por_mil: bool = True,
    zerar_anterior: bool = False,
) -> dict:
    """
    DPF = Ativo | Passivo (2.01 + 2.02) | PL (2.03 + resultado do exercício).
    No plano referencial o PL é filho de Passivo (`2`); aqui ele aparece separado.
    """
    atual = _saldos(fundo_id, data_atual, False)
    anterior = _saldos(fundo_id, data_anterior, zerar_anterior)
    res_atual, res_ant = atual.get(CODIGO_RESULTADO, ZERO), anterior.get(CODIGO_RESULTADO, ZERO)

    def soma(base, *codigos):
        return sum((base.get(c, ZERO) for c in codigos), ZERO)

    ativo = montar_demonstrativo(atual, anterior, ["1"], nivel_max, dividir_por_mil)
    passivo = montar_demonstrativo(atual, anterior, RAIZES_PASSIVO, nivel_max, dividir_por_mil)
    pl = montar_demonstrativo(
        atual, anterior, [CODIGO_PL], nivel_max, dividir_por_mil,
        ajustes={CODIGO_PL: (res_atual, res_ant)},
    )
    pl.append(
        {
            "codigo": CODIGO_RESULTADO,
            "descricao": "Resultado do exercício",
            "nivel": 3,
            "tipo_linha": "virtual",
            "atual": _mil(res_atual, dividir_por_mil),
            "anterior": _mil(res_ant, dividir_por_mil),
        }
    )

    tot_ativo = (soma(atual, "1"), soma(anterior, "1"))
    tot_passivo = (soma(atual, *RAIZES_PASSIVO), soma(anterior, *RAIZES_PASSIVO))
    tot_pl = (soma(atual, CODIGO_PL) + res_atual, soma(anterior, CODIGO_PL) + res_ant)

    for lista, base in ((ativo, tot_ativo), (passivo, tot_ativo), (pl, tot_ativo)):
        _com_percentuais(lista, _mil(base[0], dividir_por_mil), _mil(base[1], dividir_por_mil))

    totais = {
        "ativo": _total("1", "Total do Ativo", *(_mil(v, dividir_por_mil) for v in tot_ativo)),
        "passivo": _total("2", "Total do Passivo", *(_mil(v, dividir_por_mil) for v in tot_passivo)),
        "pl": _total(CODIGO_PL, "Total do Patrimônio Líquido", *(_mil(v, dividir_por_mil) for v in tot_pl)),
    }
    totais["passivo_pl"] = _total(
        "2+3",
        "Total do Passivo e do PL",
        totais["passivo"]["atual"] + totais["pl"]["atual"],
        totais["passivo"]["anterior"] + totais["pl"]["anterior"],
    )
    for t in totais.values():
        t["perc_atual"] = _perc(t["atual"], totais["ativo"]["atual"])
        t["perc_anterior"] = _perc(t["anterior"], totais["ativo"]["anterior"])

    return {
        "ativo": ativo,
        "passivo": passivo,
        "pl": pl,
        "totais": totais,
        "diferenca_atual": totais["ativo"]["atual"] - totais["passivo_pl"]["atual"],
        "diferenca_anterior": totais["ativo"]["anterior"] - totais["passivo_pl"]["anterior"],
    }


def gerar_dre_arvore(
    fundo_id: int,
    data_atual: date,
    data_anterior: date | None,
    nivel_max: int = NIVEL_PADRAO,
    dividir_por_mil: bool = True,
    zerar_anterior: bool = False,
) -> dict:
    """DRE = árvore da conta `3`. O resultado do exercício é o saldo consolidado de `3`."""
    atual = _saldos(fundo_id, data_atual, False)
    anterior = _saldos(fundo_id, data_anterior, zerar_anterior)
    linhas = montar_demonstrativo(atual, anterior, [CODIGO_RESULTADO], nivel_max, dividir_por_mil)
    res_atual, res_ant = atual.get(CODIGO_RESULTADO, ZERO), anterior.get(CODIGO_RESULTADO, ZERO)
    return {
        "linhas": linhas,
        "resultado_atual": _mil(res_atual, dividir_por_mil),
        "resultado_anterior": _mil(res_ant, dividir_por_mil),
    }
