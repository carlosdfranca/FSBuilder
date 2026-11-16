"""Criação, edição e exclusão de movimentações e saldos iniciais, com as validações de negócio."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from df.models import Conta, Fundo, Movimentacao, PeriodoDF, SaldoInicial
from df.services.periodo_service import atualizar_periodos_da_data


def data_fechamento(fundo: Fundo) -> date | None:
    """Data de referência do último período finalizado do fundo (None se não há)."""
    return PeriodoDF.objects.filter(
        fundo=fundo, status="finalizada", data_referencia__isnull=False
    ).aggregate(d=Max("data_referencia"))["d"]


def _checar_fechamento(fechamento: date | None, data: date) -> None:
    if fechamento is not None and data <= fechamento:
        raise ValidationError(
            f"A data {data:%d/%m/%Y} está dentro de um período já finalizado "
            f"(até {fechamento:%d/%m/%Y}). Reabra o período para alterar."
        )


def validar_data_aberta(fundo: Fundo, data: date) -> None:
    """DF emitida não pode mudar sozinha: bloqueia datas dentro de período finalizado."""
    _checar_fechamento(data_fechamento(fundo), data)


def validar_conta_lancavel(conta: Conta, data: date) -> None:
    if not conta.is_analitica:
        raise ValidationError(f"A conta {conta.codigo} é sintética e não recebe lançamento.")
    if not conta.is_ativa:
        raise ValidationError(f"A conta {conta.codigo} está inativa.")
    if conta.vigencia_inicio and data < conta.vigencia_inicio:
        raise ValidationError(f"A conta {conta.codigo} só vale a partir de {conta.vigencia_inicio:%d/%m/%Y}.")
    if conta.vigencia_fim and data > conta.vigencia_fim:
        raise ValidationError(f"A conta {conta.codigo} deixou de valer em {conta.vigencia_fim:%d/%m/%Y}.")


def validar_lancamento(
    conta: Conta, data: date, valor: Decimal, fechamento: date | None, data_base_saldo: date | None
) -> None:
    """
    Regras de uma movimentação, sem acessar o banco: serve ao lançamento manual e à importação
    em lote (que carrega `fechamento` e as datas-base uma única vez).
    """
    validar_conta_lancavel(conta, data)
    _checar_fechamento(fechamento, data)
    if valor == 0:
        raise ValidationError("O valor da movimentação não pode ser zero.")
    if data_base_saldo is not None and data <= data_base_saldo:
        raise ValidationError(
            f"A conta {conta.codigo} tem saldo inicial em {data_base_saldo:%d/%m/%Y}; "
            f"só aceita movimentações posteriores a essa data."
        )


def _validar_movimentacao(fundo: Fundo, conta: Conta, data: date, valor: Decimal) -> None:
    base = SaldoInicial.objects.filter(fundo=fundo, conta=conta).values_list("data_base", flat=True).first()
    validar_lancamento(conta, data, valor, data_fechamento(fundo), base)


@transaction.atomic
def criar_movimentacao(
    fundo: Fundo,
    conta: Conta,
    data: date,
    valor: Decimal,
    usuario=None,
    *,
    historico: str = "",
    documento: str = "",
    origem: str = Movimentacao.Origem.MANUAL,
    lote=None,
) -> Movimentacao:
    _validar_movimentacao(fundo, conta, data, valor)
    mov = Movimentacao.objects.create(
        fundo=fundo,
        empresa_id=fundo.empresa_id,
        conta=conta,
        data=data,
        valor=valor,
        historico=historico[:255],
        documento=documento[:60],
        origem=origem,
        lote=lote,
        criado_por=usuario,
        atualizado_por=usuario,
    )
    atualizar_periodos_da_data(fundo, data)
    return mov


@transaction.atomic
def editar_movimentacao(
    mov: Movimentacao, usuario=None, *, conta: Conta | None = None, data: date | None = None,
    valor: Decimal | None = None, historico: str | None = None, documento: str | None = None,
) -> Movimentacao:
    nova_conta = conta or mov.conta
    nova_data = data or mov.data
    novo_valor = mov.valor if valor is None else valor
    validar_data_aberta(mov.fundo, mov.data)  # o lançamento original também precisa estar em período aberto
    _validar_movimentacao(mov.fundo, nova_conta, nova_data, novo_valor)

    data_antiga = mov.data
    mov.conta, mov.data, mov.valor = nova_conta, nova_data, novo_valor
    if historico is not None:
        mov.historico = historico[:255]
    if documento is not None:
        mov.documento = documento[:60]
    mov.atualizado_por = usuario
    mov.save()
    for d in {data_antiga, nova_data}:
        atualizar_periodos_da_data(mov.fundo, d)
    return mov


@transaction.atomic
def excluir_movimentacao(mov: Movimentacao) -> None:
    validar_data_aberta(mov.fundo, mov.data)
    fundo, data = mov.fundo, mov.data
    mov.delete()
    atualizar_periodos_da_data(fundo, data)


@transaction.atomic
def definir_saldo_inicial(
    fundo: Fundo, conta: Conta, data_base: date, valor: Decimal, usuario=None
) -> SaldoInicial:
    if conta.is_resultado:
        raise ValidationError("Conta de resultado não tem saldo inicial: ela zera a cada 31/12.")
    validar_conta_lancavel(conta, data_base)
    validar_data_aberta(fundo, data_base)

    existente = SaldoInicial.objects.filter(fundo=fundo, conta=conta).first()
    if existente:
        validar_data_aberta(fundo, existente.data_base)
        data_antiga = existente.data_base
        existente.data_base, existente.valor, existente.atualizado_por = data_base, valor, usuario
        existente.save()
        for d in {data_antiga, data_base}:
            atualizar_periodos_da_data(fundo, d)
        return existente
    saldo = SaldoInicial.objects.create(
        fundo=fundo, conta=conta, data_base=data_base, valor=valor, criado_por=usuario, atualizado_por=usuario
    )
    atualizar_periodos_da_data(fundo, data_base)
    return saldo


@transaction.atomic
def excluir_saldo_inicial(saldo: SaldoInicial) -> None:
    validar_data_aberta(saldo.fundo, saldo.data_base)
    fundo, data = saldo.fundo, saldo.data_base
    saldo.delete()
    atualizar_periodos_da_data(fundo, data)
