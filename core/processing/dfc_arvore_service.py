"""
DFC (método indireto) a partir do plano de contas: cada linha cadastrada em LinhaDFC mede a
variação de saldo das suas contas entre o início e o fim do período.

    efeito no caixa = sinal × (saldo no fim − saldo no início)

Caixa e equivalentes = conta 1.01.01. Como as linhas particionam o plano fora do caixa, a soma
das atividades é igual à variação de 1.01.01; se não for (conta nova sem linha), aparece uma
linha de conciliação em vez de a diferença ficar escondida.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from core.processing.demonstrativo_service import _mil
from df.models import LinhaDFC
from df.services.saldo_calculo_service import saldos_por_codigo

ZERO = Decimal("0")
CODIGO_CAIXA = "1.01.01"
CODIGO_RESULTADO = "3"  # a linha do resultado líquido aparece sempre, mesmo zerada
TITULOS_SECAO = {
    LinhaDFC.Secao.OPERACIONAL: "Fluxo de caixa das atividades operacionais",
    LinhaDFC.Secao.INVESTIMENTO: "Fluxo de caixa das atividades de investimento",
    LinhaDFC.Secao.FINANCIAMENTO: "Fluxo de caixa das atividades de financiamento",
}
TITULOS_TOTAL = {
    LinhaDFC.Secao.OPERACIONAL: "Caixa líquido das atividades operacionais",
    LinhaDFC.Secao.INVESTIMENTO: "Caixa líquido das atividades de investimento",
    LinhaDFC.Secao.FINANCIAMENTO: "Caixa líquido das atividades de financiamento",
}


def _um_ano_antes(d: date) -> date:
    ultimo_dia = calendar.monthrange(d.year - 1, d.month)[1]
    return date(d.year - 1, d.month, min(d.day, ultimo_dia))


def _soma(saldos: dict[str, Decimal], prefixos: list[str]) -> Decimal:
    # Os saldos já vêm consolidados na árvore: o valor de um código inclui toda a subárvore.
    return sum((saldos.get(p, ZERO) for p in prefixos), ZERO)


def _fluxo(linhas, fim: dict, inicio: dict) -> dict[int, Decimal]:
    return {l.id: l.sinal * (_soma(fim, l.lista_prefixos) - _soma(inicio, l.lista_prefixos)) for l in linhas}


def gerar_dfc_arvore(
    fundo_id: int,
    data_atual: date,
    data_anterior: date | None,
    dividir_por_mil: bool = True,
    zerar_anterior: bool = False,
) -> dict:
    """
    Duas colunas, como as demais demonstrações:
    - atual: fluxo entre `data_anterior` e `data_atual`;
    - anterior: fluxo do período imediatamente anterior (um ano antes de cada ponta).
    Sem `data_anterior` (início do fundo) a coluna atual parte do zero e a anterior fica vazia.
    """
    linhas = list(LinhaDFC.objects.filter(is_ativa=True))

    fim_atual = saldos_por_codigo(fundo_id, data_atual)
    ini_atual = saldos_por_codigo(fundo_id, data_anterior) if data_anterior else {}
    tem_anterior = bool(data_anterior) and not zerar_anterior
    if tem_anterior:
        fim_ant, ini_ant = ini_atual, saldos_por_codigo(fundo_id, _um_ano_antes(data_anterior))
    else:
        fim_ant = ini_ant = {}

    fluxo_a = _fluxo(linhas, fim_atual, ini_atual)
    fluxo_p = _fluxo(linhas, fim_ant, ini_ant)

    def linha(descricao, atual, anterior, tipo, nivel):
        return {
            "descricao": descricao, "tipo_linha": tipo, "nivel": nivel,
            "atual": _mil(atual, dividir_por_mil), "anterior": _mil(anterior, dividir_por_mil),
        }

    rows: list[dict] = []
    total_a = total_p = ZERO
    for secao in LinhaDFC.Secao:
        da_secao = [l for l in linhas if l.secao == secao]
        visiveis = [
            l for l in da_secao
            if fluxo_a[l.id] != ZERO or fluxo_p[l.id] != ZERO or CODIGO_RESULTADO in l.lista_prefixos
        ]
        sec_a = sum((fluxo_a[l.id] for l in da_secao), ZERO)
        sec_p = sum((fluxo_p[l.id] for l in da_secao), ZERO)
        total_a += sec_a
        total_p += sec_p
        if not visiveis and sec_a == ZERO and sec_p == ZERO and secao != LinhaDFC.Secao.OPERACIONAL:
            continue
        rows.append({"descricao": TITULOS_SECAO[secao], "tipo_linha": "grupo", "nivel": 1,
                     "atual": None, "anterior": None})
        rows += [linha(l.descricao, fluxo_a[l.id], fluxo_p[l.id], "conta", 2) for l in visiveis]
        rows.append(linha(TITULOS_TOTAL[secao], sec_a, sec_p, "total", 1))

    caixa_fim_a, caixa_ini_a = fim_atual.get(CODIGO_CAIXA, ZERO), ini_atual.get(CODIGO_CAIXA, ZERO)
    caixa_fim_p, caixa_ini_p = fim_ant.get(CODIGO_CAIXA, ZERO), ini_ant.get(CODIGO_CAIXA, ZERO)

    # Se alguma conta ficou sem linha, a soma das atividades não bate com o caixa: mostra a diferença.
    conciliacao_a = (caixa_fim_a - caixa_ini_a) - total_a
    conciliacao_p = (caixa_fim_p - caixa_ini_p) - total_p
    variacao_a, variacao_p = total_a + conciliacao_a, total_p + conciliacao_p
    if conciliacao_a != ZERO or conciliacao_p != ZERO:
        rows.append(linha("Contas sem linha na DFC (conciliação com o caixa)", conciliacao_a, conciliacao_p, "conta", 2))

    rows.append(linha("Variação no caixa e equivalentes de caixa", variacao_a, variacao_p, "total", 1))
    rows.append(linha("Caixa e equivalentes de caixa no início do período", caixa_ini_a, caixa_ini_p, "conta", 1))
    rows.append(linha("Caixa e equivalentes de caixa no final do período", caixa_fim_a, caixa_fim_p, "total", 1))

    return {
        "linhas": rows,
        "variacao_atual": _mil(variacao_a, dividir_por_mil),
        "variacao_anterior": _mil(variacao_p, dividir_por_mil),
        "tem_conciliacao": conciliacao_a != ZERO or conciliacao_p != ZERO,
    }
