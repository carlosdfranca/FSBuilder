from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional
from decimal import Decimal
from datetime import date

from django.db import transaction

from df.models import MecItem


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
    pendentes: int = 0  # linhas com conta sem mapeamento, guardadas p/ o Plano de Contas


def _to_decimal(v: Optional[float]) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def calcular_data_referencia_periodo(periodo_df) -> date:
    """
    Retorna a data de referência (fechamento) da DF de um período.
    Anual: 31/12 do ano.
    Transitória/Encerramento: usa data_vencimento.
    """
    if periodo_df.tipo_periodo == 'anual':
        return date(periodo_df.ano, 12, 31)
    else:
        return periodo_df.data_vencimento


# ============================================================
# IMPORTAÇÃO DO MEC (posição de cotas por data)
# ============================================================
def import_mec(*, fundo_id: int, rows: List, periodo_df_id: int = None) -> ImportReport:
    """
    Importa linhas canônicas (MecRowDTO) para MecItem:
    - idempotente (update_or_create por fundo+data_posicao)
    - Opcionalmente vincula ao PeriodoDF (periodo_df_id)
    """
    if not rows:
        return ImportReport(imported=0, updated=0, ignored=0, errors=[])

    # Validar PeriodoDF se fornecido
    periodo_df = None
    if periodo_df_id:
        from df.models import Fundo, PeriodoDF
        fundo = Fundo.objects.get(id=fundo_id)
        periodo_df = PeriodoDF.objects.filter(id=periodo_df_id, fundo=fundo).first()
        if not periodo_df:
            raise ValueError(f"PeriodoDF {periodo_df_id} não encontrado ou não pertence ao fundo {fundo_id}")

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
            "periodo_df_id": periodo_df_id,
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