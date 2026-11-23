"""
Motor de saldos: nenhum saldo é armazenado, tudo sai de SaldoInicial + Movimentacao.

Regras
- Patrimoniais (naturezas 1–3): saldo = SaldoInicial (se data_base <= data) + movimentações
  posteriores à data_base da conta (ou todas, se a conta não tem saldo inicial).
- Resultado (natureza 4): só conta o ano da data consultada (zera em 1/1).
- Resultado acumulado: a soma das movimentações de resultado de anos anteriores vai, sem
  gravar nada, para a conta de Lucros (se >= 0) ou de Prejuízos (se < 0) acumulados.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, OuterRef, Q, Subquery, Sum

from df.models import Conta, Movimentacao, SaldoInicial

ZERO = Decimal("0")
PATRIMONIAIS = (Conta.Natureza.ATIVO, Conta.Natureza.PASSIVO, Conta.Natureza.PATRIMONIO_LIQUIDO)


def inicio_do_ano(d: date) -> date:
    return date(d.year, 1, 1)


def _conta_do_papel(papel: str) -> Conta:
    conta = Conta.objects.filter(papel=papel).first()
    if conta is None:
        raise ImproperlyConfigured(
            f"Nenhuma conta do plano tem o papel {papel}; defina-a na tela do Plano de Contas."
        )
    return conta


def _soma_por_conta(qs) -> dict[int, Decimal]:
    return {r["conta_id"]: r["total"] for r in qs.values("conta_id").annotate(total=Sum("valor"))}


def _movimentos_patrimoniais(fundo_id: int, ini_exclusivo: date | None, fim: date) -> dict[int, Decimal]:
    """
    Σ movimentações patrimoniais com ini_exclusivo < data <= fim, ignorando as que caem
    em ou antes da data_base do saldo inicial da própria conta (já estão no saldo inicial).
    """
    base = SaldoInicial.objects.filter(
        fundo_id=OuterRef("fundo_id"), conta_id=OuterRef("conta_id")
    ).values("data_base")[:1]
    qs = (
        Movimentacao.objects.filter(fundo_id=fundo_id, data__lte=fim, conta__natureza__in=PATRIMONIAIS)
        .annotate(base=Subquery(base))
        .filter(Q(base__isnull=True) | Q(data__gt=F("base")))
    )
    if ini_exclusivo is not None:
        qs = qs.filter(data__gt=ini_exclusivo)
    return _soma_por_conta(qs)


def _movimentos_resultado(
    fundo_id: int, ini: date, fim: date, *, ini_inclusivo: bool = True
) -> dict[int, Decimal]:
    qs = Movimentacao.objects.filter(
        fundo_id=fundo_id, data__lte=fim, conta__natureza=Conta.Natureza.RESULTADO
    )
    return _soma_por_conta(qs.filter(data__gte=ini) if ini_inclusivo else qs.filter(data__gt=ini))


def _acumulado_anterior(fundo_id: int, data: date) -> Decimal:
    """Resultado de todos os exercícios anteriores ao ano de `data`."""
    total = Movimentacao.objects.filter(
        fundo_id=fundo_id,
        data__lt=inicio_do_ano(data),
        conta__natureza=Conta.Natureza.RESULTADO,
    ).aggregate(t=Sum("valor"))["t"]
    return total or ZERO


def saldos_analiticas(fundo_id: int, data: date) -> dict[int, Decimal]:
    """Saldo de cada conta analítica do fundo em `data` → {conta_id: valor} (sem zeros)."""
    saldo: dict[int, Decimal] = defaultdict(lambda: ZERO)

    for si in SaldoInicial.objects.filter(fundo_id=fundo_id, data_base__lte=data):
        saldo[si.conta_id] += si.valor
    for conta_id, total in _movimentos_patrimoniais(fundo_id, None, data).items():
        saldo[conta_id] += total

    for conta_id, total in _movimentos_resultado(fundo_id, inicio_do_ano(data), data).items():
        saldo[conta_id] += total

    acumulado = _acumulado_anterior(fundo_id, data)
    if acumulado != ZERO:
        papel = Conta.Papel.LUCROS_ACUMULADOS if acumulado > ZERO else Conta.Papel.PREJUIZOS_ACUMULADOS
        saldo[_conta_do_papel(papel).id] += acumulado

    return {cid: v for cid, v in saldo.items() if v != ZERO}


def consolidar_arvore(saldos: dict[int, Decimal]) -> dict[int, Decimal]:
    """
    Soma cada saldo analítico em todos os seus ancestrais.
    No plano referencial o PL (2.03) é filho de Passivo (2): o total de `2` inclui o PL.
    """
    pai = dict(Conta.objects.values_list("id", "conta_superior_id"))
    total: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for conta_id, valor in saldos.items():
        atual = conta_id
        while atual is not None:
            total[atual] += valor
            atual = pai.get(atual)
    return {cid: v for cid, v in total.items() if v != ZERO}


def saldos_por_codigo(fundo_id: int, data: date) -> dict[str, Decimal]:
    """Saldos de todas as contas (sintéticas inclusive) indexados pelo código."""
    consolidado = consolidar_arvore(saldos_analiticas(fundo_id, data))
    codigos = dict(Conta.objects.filter(id__in=consolidado).values_list("id", "codigo"))
    return {codigos[cid]: v for cid, v in consolidado.items()}


def balancete_calculado(fundo_id: int, data_ini: date, data_fim: date) -> list[dict]:
    """
    Por conta analítica com algum valor: anterior + movimentos + ajustes = atual.

    - `anterior`: saldo em `data_ini`. Contas de resultado partem de zero quando o período
      cruza 1/1 (o resultado do ano anterior já foi para o acumulado).
    - `movimentos`: Σ dos lançamentos reais no período (resultado: só dentro do ano de `data_fim`).
    - `ajustes`: o que muda o saldo sem ser lançamento do período: transferência virtual do
      resultado de anos anteriores para Lucros/Prejuízos acumulados (período que cruza 1/1) e
      saldo inicial cuja data_base cai dentro do período.
    """
    anterior = saldos_analiticas(fundo_id, data_ini)
    atual = saldos_analiticas(fundo_id, data_fim)
    cruza_ano = data_fim.year != data_ini.year

    natureza = dict(Conta.objects.filter(id__in=set(anterior) | set(atual)).values_list("id", "natureza"))
    # O resultado do ano anterior sai do resultado e vai para o acumulado (em `ajustes`).
    if cruza_ano:
        anterior = {
            cid: (ZERO if natureza[cid] == Conta.Natureza.RESULTADO else v) for cid, v in anterior.items()
        }

    mov_pat = _movimentos_patrimoniais(fundo_id, data_ini, data_fim)
    if cruza_ano:
        mov_res = _movimentos_resultado(fundo_id, inicio_do_ano(data_fim), data_fim)
    else:
        mov_res = _movimentos_resultado(fundo_id, data_ini, data_fim, ini_inclusivo=False)

    linhas = []
    for cid in sorted(set(anterior) | set(atual) | set(mov_pat) | set(mov_res)):
        ant = anterior.get(cid, ZERO)
        mov = mov_pat.get(cid, ZERO) + mov_res.get(cid, ZERO)
        fim = atual.get(cid, ZERO)
        if ant == mov == fim == ZERO:
            continue
        linhas.append(
            {"conta_id": cid, "anterior": ant, "movimentos": mov, "ajustes": fim - ant - mov, "atual": fim}
        )
    return linhas


def conferencia(fundo_id: int, data: date) -> Decimal:
    """
    Ativo − (Passivo + Resultado do exercício). Deve ser 0.
    Aqui a conta `2` já inclui o PL (2.03) e a `3` é o resultado do exercício.
    """
    saldos = saldos_por_codigo(fundo_id, data)
    return saldos.get("1", ZERO) - (saldos.get("2", ZERO) + saldos.get("3", ZERO))
