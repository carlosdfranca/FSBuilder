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


def calcular_data_referencia_periodo(periodo_df) -> date:
    """
    Retorna a data de fechamento do balancete para um período.
    Trimestral: último dia do mês de fechamento do trimestre (mar/jun/set/dez).
    Anual: 31/12 do ano.
    Transitória/Encerramento: usa data_vencimento.
    """
    import calendar
    if periodo_df.tipo_periodo == 'trimestral':
        mes_fim = periodo_df.trimestre * 3
        ultimo_dia = calendar.monthrange(periodo_df.ano, mes_fim)[1]
        return date(periodo_df.ano, mes_fim, ultimo_dia)
    elif periodo_df.tipo_periodo == 'anual':
        return date(periodo_df.ano, 12, 31)
    else:
        return periodo_df.data_vencimento


def calcular_data_referencia_periodo_anterior(periodo_df):
    """
    Retorna a data de fechamento do período ANTERIOR para usar como saldo_anterior.
    Retorna None para tipos manuais (transitória/encerramento).
    """
    import calendar
    if periodo_df.tipo_periodo == 'trimestral':
        if periodo_df.trimestre == 1:
            return date(periodo_df.ano - 1, 12, 31)
        mes_fim = (periodo_df.trimestre - 1) * 3
        ultimo_dia = calendar.monthrange(periodo_df.ano, mes_fim)[1]
        return date(periodo_df.ano, mes_fim, ultimo_dia)
    elif periodo_df.tipo_periodo == 'anual':
        return date(periodo_df.ano - 1, 12, 31)
    return None


# ============================================================
# BALANCETE (com data_referencia + só saldo_atual)
# ============================================================
@transaction.atomic
def import_balancete(*, fundo_id: int, data_referencia: date, rows: List, periodo_df_id: int = None) -> ImportReport:
    """
    Importa linhas canônicas (BalanceteRowDTO) para BalanceteItem:
    - grava apenas o saldo atual
    - usa data_referencia (não mais 'ano')
    - ignora completamente saldo_anterior
    - idempotente (update_or_create)
    - Opcionalmente vincula ao PeriodoDF (periodo_df_id)
    """
    if not rows:
        return ImportReport(imported=0, updated=0, ignored=0, errors=[])

    # Obter fundo para acessar empresa (tenant)
    from df.models import Fundo, PeriodoDF
    fundo = Fundo.objects.select_related("empresa").get(id=fundo_id)

    # Validar PeriodoDF se fornecido
    periodo_df = None
    if periodo_df_id:
        periodo_df = PeriodoDF.objects.filter(id=periodo_df_id, fundo=fundo).first()
        if not periodo_df:
            raise ValueError(f"PeriodoDF {periodo_df_id} não encontrado ou não pertence ao fundo {fundo_id}")

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
                "periodo_df_id": periodo_df_id,
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

    # Atualizar data_referencia do período se fornecido e não definida
    if periodo_df and not periodo_df.data_referencia:
        periodo_df.data_referencia = data_referencia
        periodo_df.save()

    # Atualizar status do período se fornecido
    if periodo_df:
        from df.services.periodo_service import atualizar_status_automatico
        atualizar_status_automatico(periodo_df)

    return ImportReport(imported=imported, updated=updated, ignored=ignored, errors=errors)


@transaction.atomic
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

    # Atualizar status do período se fornecido
    if periodo_df:
        from df.services.periodo_service import atualizar_status_automatico
        atualizar_status_automatico(periodo_df)

    return ImportReport(imported=imported, updated=updated, ignored=ignored, errors=errors)