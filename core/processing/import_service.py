from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional
from decimal import Decimal
from datetime import date

from django.db import transaction

from df.models import BalanceteItem, MapeamentoContas, MecItem


@dataclass(frozen=True)
class ImportErrorItem:
    row_index: int
    reason: str
    raw: dict


@dataclass(frozen=True)
class ImportReport:
    imported: int
    updated: int
    ignored: int
    errors: List[ImportErrorItem]


def _to_decimal(v: Optional[float]) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


# ============================================================
# BALANCETE (com data_referencia + só saldo_atual)
# ============================================================
@transaction.atomic
def import_balancete(*, fundo_id: int, data_referencia: date, rows: List) -> ImportReport:
    """
    Importa linhas canônicas (BalanceteRowDTO) para BalanceteItem:
    - grava apenas o saldo atual
    - usa data_referencia (não mais 'ano')
    - ignora completamente saldo_anterior
    - idempotente (update_or_create)
    """
    if not rows:
        return ImportReport(imported=0, updated=0, ignored=0, errors=[])

    # Obter fundo para acessar empresa (tenant)
    from df.models import Fundo
    fundo = Fundo.objects.select_related("empresa").get(id=fundo_id)

    # Cache de contas conhecidas para esta empresa
    contas = {r.conta for r in rows if getattr(r, "conta", None)}
    mapa_by_conta: Dict[str, MapeamentoContas] = {
        m.conta: m for m in MapeamentoContas.objects.filter(
            empresa=fundo.empresa,
            conta__in=list(contas)
        )
    }

    imported = updated = ignored = 0
    errors: List[ImportErrorItem] = []

    for idx, r in enumerate(rows):
        conta = r.conta
        if not conta:
            ignored += 1
            continue

        conta_map = mapa_by_conta.get(conta)
        if conta_map is None:
            ignored += 1
            continue

        if r.saldo_atual is None:
            ignored += 1
            continue

        try:
            defaults = {
                "saldo_final": _to_decimal(r.saldo_atual),
                "data_referencia": data_referencia,
            }
            _, created = BalanceteItem.objects.update_or_create(
                fundo_id=fundo_id,
                conta_corrente_id=conta_map.id,
                data_referencia=data_referencia,
                defaults=defaults,
            )
            if created:
                imported += 1
            else:
                updated += 1

        except Exception as e:
            errors.append(ImportErrorItem(idx, str(e), raw=r.raw))

    return ImportReport(imported=imported, updated=updated, ignored=ignored, errors=errors)


@transaction.atomic
def import_mec(*, fundo_id: int, rows: List) -> ImportReport:
    """
    Importa linhas canônicas (MecRowDTO) para MecItem:
    - idempotente (update_or_create por fundo+data_posicao)
    """
    if not rows:
        return ImportReport(imported=0, updated=0, ignored=0, errors=[])

    imported = updated = ignored = 0
    errors: List[ImportErrorItem] = []

    for idx, r in enumerate(rows):
        if not getattr(r, "data_posicao", None):
            ignored += 1
            continue

        defaults = {
            "aplicacao": _to_decimal(r.aplicacao),
            "resgate": _to_decimal(r.resgate),
            "estorno": _to_decimal(r.estorno),
            "pl": _to_decimal(r.pl),
            "qtd_cotas": _to_decimal(r.qtd_cotas),
            "cota": _to_decimal(r.cota),
        }
        _, created = MecItem.objects.update_or_create(
            fundo_id=fundo_id,
            data_posicao=r.data_posicao,
            defaults=defaults,
        )
        if created:
            imported += 1
        else:
            updated += 1

    return ImportReport(imported=imported, updated=updated, ignored=ignored, errors=errors)