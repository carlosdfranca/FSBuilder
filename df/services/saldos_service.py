from collections import OrderedDict
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from df.models import BalanceteItem, BalanceteLinhaPendente, MapeamentoContas, PeriodoDF

COLUNA_ATUAL = "atual"
COLUNA_ANTERIOR = "anterior"
COLUNAS = (COLUNA_ATUAL, COLUNA_ANTERIOR)

TIPOS_GRUPO = {1: "ativo", 2: "passivo", 3: "pl", 4: "resultado"}


def datas_do_periodo(periodo):
    """(data_atual, data_anterior) do período. Prefere o que já está salvo; senão calcula."""
    from core.processing.import_service import (
        calcular_data_referencia_periodo,
        calcular_data_referencia_periodo_anterior,
    )
    atual = periodo.data_referencia or calcular_data_referencia_periodo(periodo)
    anterior = periodo.data_anterior or calcular_data_referencia_periodo_anterior(periodo)
    return atual, anterior


def periodo_dono_da_data(fundo, data) -> Optional[PeriodoDF]:
    """Período do fundo cuja data_referencia é `data`; usado para a FK das linhas de saldo anterior."""
    if not data:
        return None
    return PeriodoDF.objects.filter(fundo=fundo, data_referencia=data).order_by("-ano").first()


def listar_saldos(periodo):
    """
    Junta, por conta, os saldos da data atual e da anterior do período.
    Retorna (linhas, totais). `totais` soma o saldo atual por tipo de GrupoGrande.
    """
    data_atual, data_anterior = datas_do_periodo(periodo)
    datas = [d for d in (data_atual, data_anterior) if d]

    itens = (
        BalanceteItem.objects
        .filter(fundo=periodo.fundo, data_referencia__in=datas)
        .select_related("conta_corrente__grupo_pequeno__grupao", "atualizado_por")
    )

    por_conta = OrderedDict()
    for item in itens:
        m = item.conta_corrente
        if m is None:
            continue
        linha = por_conta.setdefault(m.id, {
            "mapeamento": m,
            "conta": m.conta,
            "descricao": m.descricao or "",
            "grupo": m.grupo_pequeno.nome if m.grupo_pequeno else "",
            "atual": None,
            "anterior": None,
        })
        if item.data_referencia == data_atual:
            linha["atual"] = item
        elif item.data_referencia == data_anterior:
            linha["anterior"] = item

    linhas = sorted(por_conta.values(), key=lambda l: l["conta"])

    totais = {nome: Decimal("0") for nome in TIPOS_GRUPO.values()}
    for l in linhas:
        item = l["atual"]
        gp = l["mapeamento"].grupo_pequeno
        if item is None or item.saldo_final is None or gp is None:
            continue
        nome = TIPOS_GRUPO.get(gp.grupao.tipo)
        if nome:
            totais[nome] += item.saldo_final
    totais["diferenca"] = totais["ativo"] - (totais["passivo"] + totais["pl"])
    totais["contas"] = len(linhas)
    return linhas, totais


def _resolver_data(periodo, coluna):
    data_atual, data_anterior = datas_do_periodo(periodo)
    if coluna == COLUNA_ATUAL:
        return data_atual
    if coluna == COLUNA_ANTERIOR:
        if not data_anterior:
            raise ValidationError(
                "Este tipo de período não tem saldo anterior calculável. "
                "Use a importação para definir a base de comparação."
            )
        return data_anterior
    raise ValidationError("Coluna inválida.")


@transaction.atomic
def salvar_saldo(*, periodo, mapeamento, coluna, valor, usuario=None) -> BalanceteItem:
    """Cria ou atualiza um saldo manualmente. Levanta ValidationError em entrada inválida."""
    from df.services.mapeamento_service import limpar_pendentes_mapeadas
    from df.services.periodo_service import atualizar_status_automatico

    if mapeamento.empresa_id != periodo.fundo.empresa_id:
        raise ValidationError("Conta não pertence à empresa do fundo.")
    if mapeamento.grupo_pequeno_id is None:
        raise ValidationError("A conta precisa estar classificada no Plano de Contas.")
    if valor is None:
        raise ValidationError("Informe o valor do saldo.")

    data = _resolver_data(periodo, coluna)
    fundo = periodo.fundo

    existente = BalanceteItem.objects.filter(
        fundo=fundo, conta_corrente=mapeamento, data_referencia=data
    ).first()

    if coluna == COLUNA_ATUAL:
        periodo_fk = periodo.id
    else:
        dono = periodo_dono_da_data(fundo, data)
        periodo_fk = dono.id if dono else (existente.periodo_df_id if existente else None)

    item, _ = BalanceteItem.objects.update_or_create(
        fundo=fundo,
        conta_corrente=mapeamento,
        data_referencia=data,
        defaults={
            "saldo_final": valor,
            "periodo_df_id": periodo_fk,
            "origem": BalanceteItem.ORIGEM_MANUAL,
            "atualizado_por": usuario,
        },
    )

    campos = []
    if coluna == COLUNA_ATUAL and not periodo.data_referencia:
        periodo.data_referencia = data
        campos.append("data_referencia")
    if coluna == COLUNA_ANTERIOR and not periodo.data_anterior:
        periodo.data_anterior = data
        campos.append("data_anterior")
    if campos:
        periodo.save(update_fields=campos + ["atualizado_em"])

    limpar_pendentes_mapeadas(fundo=fundo, data_referencia=data, contas=[mapeamento.conta])
    atualizar_status_automatico(periodo)
    return item


@transaction.atomic
def excluir_saldo(*, periodo, item_id) -> None:
    """Apaga um saldo do fundo do período, desde que seja de uma das duas datas da DF."""
    from df.services.periodo_service import atualizar_status_automatico

    datas = [d for d in datas_do_periodo(periodo) if d]
    deleted, _ = BalanceteItem.objects.filter(
        id=item_id, fundo=periodo.fundo, data_referencia__in=datas
    ).delete()
    if not deleted:
        raise BalanceteItem.DoesNotExist
    atualizar_status_automatico(periodo)


def contar_manuais(fundo, data) -> int:
    return BalanceteItem.objects.filter(
        fundo=fundo, data_referencia=data, origem=BalanceteItem.ORIGEM_MANUAL
    ).count()


def substituir_saldos(fundo, data) -> int:
    """Apaga saldos e pendentes de uma data (modo 'Substituir tudo' da importação)."""
    apagados, _ = BalanceteItem.objects.filter(fundo=fundo, data_referencia=data).delete()
    BalanceteLinhaPendente.objects.filter(fundo=fundo, data_referencia=data).delete()
    return apagados
