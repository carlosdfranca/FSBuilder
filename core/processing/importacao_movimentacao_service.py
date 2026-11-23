"""
Importação de movimentações e de saldos iniciais por planilha.

Regra de ouro: tudo ou nada. Qualquer linha inválida recusa o arquivo inteiro e devolve a lista
de erros por linha; nada é gravado. As regras são as mesmas do lançamento manual
(`movimentacao_service.validar_lancamento`).
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone

from core.upload.movimentacao_parser import LinhaPlanilha, PlanilhaInvalida, ler_planilha
from df.models import Conta, Fundo, LoteImportacaoMovimentacao, Movimentacao, SaldoInicial
from df.services.movimentacao_service import (
    _checar_fechamento, data_fechamento, validar_conta_lancavel, validar_lancamento,
)
from df.services.periodo_service import ano_do_saldo_inicial, atualizar_periodos_do_intervalo, garantir_periodos_anuais

LIMITE_ERROS = 200
TAMANHO_LOTE_BD = 1000


class ImportacaoInvalida(Exception):
    """Arquivo recusado. `erros` = [{"linha": n | None, "mensagem": ...}] (no máximo LIMITE_ERROS)."""

    def __init__(self, erros: list[dict], total: int | None = None):
        super().__init__(f"{total or len(erros)} erro(s)")
        self.erros = erros[:LIMITE_ERROS]
        self.total = total or len(erros)


def _recusar(mensagem: str):
    raise ImportacaoInvalida([{"linha": None, "mensagem": mensagem}])


def _ler(nome: str, conteudo: bytes, **kw) -> tuple[list[LinhaPlanilha], list[dict]]:
    try:
        return ler_planilha(nome, conteudo, **kw)
    except PlanilhaInvalida as exc:
        _recusar(exc.mensagem)


def _contas_por_codigo(linhas: list[LinhaPlanilha]) -> dict[str, Conta]:
    return {c.codigo: c for c in Conta.objects.filter(codigo__in={l.conta for l in linhas})}


def _valor_da_linha(linha: LinhaPlanilha, conta: Conta) -> Decimal:
    """Valor com sinal. Débito/crédito: Ativo = D − C; Passivo, PL e Resultado = C − D."""
    if linha.valor is not None:
        return linha.valor
    if conta.natureza == Conta.Natureza.ATIVO:
        return linha.debito - linha.credito
    return linha.credito - linha.debito


# ---------------------------------------------------------------- movimentações
def importar_movimentacoes(
    fundo: Fundo, nome_arquivo: str, conteudo: bytes, usuario=None, periodos_criados: list | None = None
) -> LoteImportacaoMovimentacao:
    sha = hashlib.sha256(conteudo).hexdigest()
    repetido = LoteImportacaoMovimentacao.objects.filter(fundo=fundo, sha256=sha).first()
    if repetido:
        _recusar(
            f"Este arquivo já foi importado em {timezone.localtime(repetido.importado_em):%d/%m/%Y %H:%M} "
            f"({repetido.qtd_linhas} linhas). Desfaça o lote anterior se quiser importá-lo de novo."
        )

    linhas, erros = _ler(nome_arquivo, conteudo)
    contas = _contas_por_codigo(linhas)
    fechamento = data_fechamento(fundo)
    bases = dict(SaldoInicial.objects.filter(fundo=fundo).values_list("conta_id", "data_base"))

    prontas: list[tuple[LinhaPlanilha, Conta, Decimal]] = []
    for linha in linhas:
        conta = contas.get(linha.conta)
        if conta is None:
            erros.append({"linha": linha.linha, "mensagem": f"conta {linha.conta} não existe no plano de contas"})
            continue
        valor = _valor_da_linha(linha, conta)
        try:
            validar_lancamento(conta, linha.data, valor, fechamento, bases.get(conta.id))
        except ValidationError as exc:
            erros.append({"linha": linha.linha, "mensagem": exc.messages[0]})
            continue
        prontas.append((linha, conta, valor))

    if erros:
        erros.sort(key=lambda e: e["linha"] or 0)
        raise ImportacaoInvalida(erros, total=len(erros))
    if not prontas:
        _recusar("A planilha não tem nenhuma movimentação.")

    with transaction.atomic():
        lote = LoteImportacaoMovimentacao.objects.create(
            fundo=fundo,
            nome_arquivo=nome_arquivo[:255],
            sha256=sha,
            qtd_linhas=len(prontas),
            valor_total=sum((v for _, _, v in prontas), Decimal("0")),
            importado_por=usuario,
        )
        Movimentacao.objects.bulk_create(
            (
                Movimentacao(
                    fundo=fundo, empresa_id=fundo.empresa_id, conta=conta, data=linha.data, valor=valor,
                    historico=linha.historico[:255], documento=linha.documento[:60],
                    origem=Movimentacao.Origem.IMPORTADO, lote=lote, criado_por=usuario, atualizado_por=usuario,
                )
                for linha, conta, valor in prontas
            ),
            batch_size=TAMANHO_LOTE_BD,
        )
        datas = [linha.data for linha, _, _ in prontas]
        criados = garantir_periodos_anuais(fundo, {d.year for d in datas})
        if periodos_criados is not None:
            periodos_criados.extend(criados)
        atualizar_periodos_do_intervalo(fundo, min(datas), max(datas))
    return lote


def desfazer_lote(lote: LoteImportacaoMovimentacao) -> int:
    """Remove o lote e suas movimentações; recusa se alguma estiver em período já finalizado."""
    fundo = lote.fundo
    limites = lote.movimentacoes.aggregate(menor=Min("data"), maior=Max("data"))
    if limites["menor"] is not None:
        _checar_fechamento(data_fechamento(fundo), limites["menor"])
    quantidade = lote.movimentacoes.count()
    with transaction.atomic():
        lote.delete()
        if limites["menor"] is not None:
            atualizar_periodos_do_intervalo(fundo, limites["menor"], limites["maior"])
    return quantidade


# ---------------------------------------------------------------- saldos iniciais
def importar_saldos_iniciais(
    fundo: Fundo, data_base: date, nome_arquivo: str, conteudo: bytes, usuario=None,
    periodos_criados: list | None = None,
) -> int:
    """
    Planilha com CONTA e VALOR (ou DÉBITO/CRÉDITO). Substitui o saldo inicial das contas citadas;
    as não citadas ficam como estão. Devolve a quantidade de contas gravadas.
    """
    linhas, erros = _ler(nome_arquivo, conteudo, exigir_data=False)
    contas = _contas_por_codigo(linhas)
    fechamento = data_fechamento(fundo)
    try:
        _checar_fechamento(fechamento, data_base)
    except ValidationError as exc:
        _recusar(exc.messages[0])

    vistas: dict[str, int] = {}
    prontas: list[tuple[Conta, Decimal]] = []
    for linha in linhas:
        conta = contas.get(linha.conta)
        if conta is None:
            erros.append({"linha": linha.linha, "mensagem": f"conta {linha.conta} não existe no plano de contas"})
            continue
        if conta.is_resultado:
            erros.append({"linha": linha.linha, "mensagem": f"a conta {conta.codigo} é de resultado e não tem saldo inicial"})
            continue
        try:
            validar_conta_lancavel(conta, data_base)
        except ValidationError as exc:
            erros.append({"linha": linha.linha, "mensagem": exc.messages[0]})
            continue
        if conta.codigo in vistas:
            erros.append({"linha": linha.linha, "mensagem": f"conta {conta.codigo} repetida (já na linha {vistas[conta.codigo]})"})
            continue
        vistas[conta.codigo] = linha.linha
        prontas.append((conta, _valor_da_linha(linha, conta)))

    # Trocar a data-base de um saldo que já está em período finalizado mudaria uma DF emitida.
    existentes = {si.conta_id: si for si in SaldoInicial.objects.filter(fundo=fundo, conta__in=[c for c, _ in prontas])}
    for conta, _ in prontas:
        atual = existentes.get(conta.id)
        if atual is None:
            continue
        try:
            _checar_fechamento(fechamento, atual.data_base)
        except ValidationError as exc:
            erros.append({"linha": vistas[conta.codigo], "mensagem": f"conta {conta.codigo}: {exc.messages[0]}"})

    if erros:
        erros.sort(key=lambda e: e["linha"] or 0)
        raise ImportacaoInvalida(erros, total=len(erros))
    if not prontas:
        _recusar("A planilha não tem nenhum saldo.")

    with transaction.atomic():
        for conta, valor in prontas:
            existente = existentes.get(conta.id)
            if existente:
                existente.data_base, existente.valor, existente.atualizado_por = data_base, valor, usuario
                existente.save()
            else:
                SaldoInicial.objects.create(
                    fundo=fundo, conta=conta, data_base=data_base, valor=valor,
                    criado_por=usuario, atualizado_por=usuario,
                )
        criados = garantir_periodos_anuais(fundo, [ano_do_saldo_inicial(data_base)])
        if periodos_criados is not None:
            periodos_criados.extend(criados)
        atualizar_periodos_do_intervalo(fundo, data_base, data_base)
    return len(prontas)
