from typing import Iterable

from django.db import transaction
from django.db.models import Count, Max

from df.models import BalanceteItem, BalanceteLinhaPendente


def _descricao_da_linha(row):
    """Melhor esforço: procura uma coluna de descrição/nome na linha original da planilha."""
    raw = getattr(row, "raw", None) or {}
    for chave, valor in raw.items():
        nome = str(chave).strip().lower()
        if nome in {"descricao", "descrição", "nome", "nome da conta", "historico", "histórico"}:
            texto = str(valor or "").strip()
            if texto and texto.lower() != "nan":
                return texto[:255]
    return None


@transaction.atomic
def registrar_pendentes(*, fundo, data_referencia, periodo_df_id, rows: Iterable, decimal_fn) -> int:
    """
    Guarda as linhas cuja conta não tem mapeamento na empresa do fundo.
    Idempotente por (fundo, data_referencia, conta). Retorna quantas linhas foram gravadas.
    """
    total = 0
    for row in rows:
        BalanceteLinhaPendente.objects.update_or_create(
            fundo=fundo,
            data_referencia=data_referencia,
            conta=row.conta,
            defaults={
                "empresa_id": fundo.empresa_id,
                "periodo_df_id": periodo_df_id,
                "descricao": _descricao_da_linha(row),
                "saldo_final": decimal_fn(row.saldo_atual),
            },
        )
        total += 1
    return total


def limpar_pendentes_mapeadas(*, fundo, data_referencia, contas: Iterable[str]) -> int:
    """Remove pendentes de contas que, num reimport, passaram a ter mapeamento."""
    contas = list(contas)
    if not contas:
        return 0
    apagadas, _ = BalanceteLinhaPendente.objects.filter(
        fundo=fundo, data_referencia=data_referencia, conta__in=contas
    ).delete()
    return apagadas


@transaction.atomic
def materializar_pendentes(mapeamento) -> dict:
    """
    Converte em BalanceteItem todas as linhas pendentes da empresa com a conta recém-mapeada.
    Usa a mesma chave de import_balancete (fundo, conta, data_referencia). Retorna
    {"linhas": n, "fundos": m} para a mensagem de feedback.
    """
    pendentes = list(
        BalanceteLinhaPendente.objects.filter(
            empresa_id=mapeamento.empresa_id, conta=mapeamento.conta
        )
    )
    for p in pendentes:
        BalanceteItem.objects.update_or_create(
            fundo_id=p.fundo_id,
            conta_corrente_id=mapeamento.id,
            data_referencia=p.data_referencia,
            defaults={
                "saldo_final": p.saldo_final,
                "periodo_df_id": p.periodo_df_id,
            },
        )
    BalanceteLinhaPendente.objects.filter(pk__in=[p.pk for p in pendentes]).delete()
    return {"linhas": len(pendentes), "fundos": len({p.fundo_id for p in pendentes})}


def contas_pendentes_agrupadas(empresa):
    """Uma linha por conta pendente, com quantos fundos/linhas ela afeta."""
    return list(
        BalanceteLinhaPendente.objects.filter(empresa=empresa)
        .values("conta")
        .annotate(
            qtd_fundos=Count("fundo", distinct=True),
            qtd_linhas=Count("id"),
            descricao=Max("descricao"),
            ultima=Max("data_referencia"),
        )
        .order_by("conta")
    )


def total_contas_pendentes(empresa) -> int:
    return (
        BalanceteLinhaPendente.objects.filter(empresa=empresa)
        .values("conta").distinct().count()
    )


def descartar_pendentes(empresa, conta: str) -> int:
    apagadas, _ = BalanceteLinhaPendente.objects.filter(empresa=empresa, conta=conta).delete()
    return apagadas
