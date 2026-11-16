"""Movimentações, importação por planilha e saldos iniciais de um fundo. FBVs, padrão AJAX do projeto."""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from openpyxl.styles import Font

from core.processing.importacao_movimentacao_service import (
    ImportacaoInvalida, desfazer_lote, importar_movimentacoes, importar_saldos_iniciais,
)
from core.upload.movimentacao_parser import MAX_LINHAS, parse_valor
from df.models import Conta, Fundo, LoteImportacaoMovimentacao, Movimentacao, PeriodoDF, SaldoInicial
from df.services import movimentacao_service as ms
from df.services.periodo_service import garantir_datas_periodo
from df.services.saldo_calculo_service import balancete_calculado, conferencia, consolidar_arvore
from usuarios.permissions import (
    company_can_manage_movimentacoes, company_can_view_data, get_empresa_escopo, pode_lancar_movimentacoes,
)
from usuarios.utils.company_scope import query_por_empresa_ativa

LIMITE_LINHAS_TELA = 5000
LIMITE_BUSCA_CONTAS = 30


def _fundo(request, fundo_id) -> Fundo:
    qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    return get_object_or_404(qs, id=fundo_id)


def _pode_lancar(request) -> bool:
    return pode_lancar_movimentacoes(request.user, get_empresa_escopo(request))


def _erros(exc: ValidationError, campo: str = "__all__") -> JsonResponse:
    return JsonResponse({"ok": False, "errors": {campo: exc.messages}}, status=400)


def _erro_campo(campo: str, mensagem: str) -> JsonResponse:
    return JsonResponse({"ok": False, "errors": {campo: [mensagem]}}, status=400)


def _data(texto) -> date | None:
    try:
        return datetime.strptime((texto or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _valor(texto) -> Decimal | None:
    try:
        return parse_valor(texto)
    except ValueError:
        return None


def _conta(request) -> Conta | None:
    return Conta.objects.filter(id=request.POST.get("conta_id") or 0).first()


def _moeda(v) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ busca de contas
@login_required
@company_can_view_data
@require_GET
def contas_buscar(request):
    """Autocomplete: contas analíticas ativas por código ou descrição. `patrimonial=1` exclui resultado."""
    termo = (request.GET.get("q") or "").strip()
    qs = Conta.objects.filter(tipo=Conta.Tipo.ANALITICA, is_ativa=True)
    if request.GET.get("patrimonial") == "1":
        qs = qs.exclude(natureza=Conta.Natureza.RESULTADO)
    if termo:
        qs = qs.filter(Q(codigo__startswith=termo) | Q(codigo__icontains=termo) | Q(descricao__icontains=termo))
    return JsonResponse({"contas": [
        {"id": c.id, "codigo": c.codigo, "descricao": c.descricao, "natureza": c.get_natureza_display()}
        for c in qs.order_by("codigo")[:LIMITE_BUSCA_CONTAS]
    ]})


# ------------------------------------------------------------------ movimentações
def _serializar(m: Movimentacao, fechamento: date | None) -> dict:
    return {
        "id": m.id, "data": m.data.isoformat(), "conta_id": m.conta_id, "conta": m.conta.codigo,
        "conta_descricao": m.conta.descricao, "valor": str(m.valor), "historico": m.historico,
        "documento": m.documento, "origem": m.origem, "negativo": m.valor < 0,
        "editavel": not (fechamento and m.data <= fechamento),
    }


@login_required
@company_can_view_data
def movimentacoes_fundo(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    ultima = Movimentacao.objects.filter(fundo=fundo).order_by("-data").values_list("data", flat=True).first()
    ano_padrao = (ultima or date.today()).year

    data_ini = _data(request.GET.get("data_ini")) or date(ano_padrao, 1, 1)
    data_fim = _data(request.GET.get("data_fim")) or date(ano_padrao, 12, 31)
    busca = (request.GET.get("conta") or "").strip()

    qs = Movimentacao.objects.filter(fundo=fundo, data__gte=data_ini, data__lte=data_fim).select_related("conta")
    if busca:
        qs = qs.filter(Q(conta__codigo__startswith=busca) | Q(conta__descricao__icontains=busca) | Q(historico__icontains=busca))
    totais = qs.aggregate(qtd=Count("id"), soma=Sum("valor"))
    fechamento = ms.data_fechamento(fundo)

    return render(request, "movimentacoes/lista.html", {
        "fundo": fundo,
        "movimentacoes": [_serializar(m, fechamento) for m in qs.order_by("-data", "-id")[:LIMITE_LINHAS_TELA]],
        "truncado": (totais["qtd"] or 0) > LIMITE_LINHAS_TELA,
        "qtd": totais["qtd"] or 0,
        "soma": totais["soma"] or Decimal("0"),
        "data_ini": data_ini, "data_fim": data_fim, "busca": busca,
        "fechamento": fechamento,
        "pode_lancar": _pode_lancar(request),
        "total_geral": Movimentacao.objects.filter(fundo=fundo).count(),
    })


def _dados_movimentacao(request):
    """Lê e valida o formulário; devolve (dados, None) ou (None, JsonResponse de erro)."""
    data, valor, conta = _data(request.POST.get("data")), _valor(request.POST.get("valor")), _conta(request)
    if data is None:
        return None, _erro_campo("data", "Informe uma data válida.")
    if conta is None:
        return None, _erro_campo("conta", "Escolha uma conta da lista.")
    if valor is None:
        return None, _erro_campo("valor", "Informe o valor (ex.: 1.234,56 ou -500,00).")
    return {
        "data": data, "conta": conta, "valor": valor,
        "historico": (request.POST.get("historico") or "").strip(),
        "documento": (request.POST.get("documento") or "").strip(),
    }, None


@login_required
@company_can_manage_movimentacoes
@require_POST
def movimentacao_criar(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    dados, erro = _dados_movimentacao(request)
    if erro:
        return erro
    try:
        mov = ms.criar_movimentacao(
            fundo, dados["conta"], dados["data"], dados["valor"], request.user,
            historico=dados["historico"], documento=dados["documento"],
        )
    except ValidationError as exc:
        return _erros(exc)
    return JsonResponse({"ok": True, "item": _serializar(mov, ms.data_fechamento(fundo))})


@login_required
@company_can_manage_movimentacoes
@require_POST
def movimentacao_editar(request, fundo_id, mov_id):
    fundo = _fundo(request, fundo_id)
    mov = get_object_or_404(Movimentacao.objects.select_related("conta"), id=mov_id, fundo=fundo)
    if mov.lote_id:
        return JsonResponse({"ok": False, "errors": {"__all__": [
            "Movimentação importada: desfaça o lote para corrigi-la ou lance um ajuste."]}}, status=400)
    dados, erro = _dados_movimentacao(request)
    if erro:
        return erro
    try:
        mov = ms.editar_movimentacao(
            mov, request.user, conta=dados["conta"], data=dados["data"], valor=dados["valor"],
            historico=dados["historico"], documento=dados["documento"],
        )
    except ValidationError as exc:
        return _erros(exc)
    return JsonResponse({"ok": True, "item": _serializar(mov, ms.data_fechamento(fundo))})


@login_required
@company_can_manage_movimentacoes
@require_POST
def movimentacao_excluir(request, fundo_id, mov_id):
    fundo = _fundo(request, fundo_id)
    mov = get_object_or_404(Movimentacao, id=mov_id, fundo=fundo)
    if mov.lote_id:
        return JsonResponse({"ok": False, "errors": {"__all__": [
            "Movimentação importada: desfaça o lote para removê-la."]}}, status=400)
    try:
        ms.excluir_movimentacao(mov)
    except ValidationError as exc:
        return _erros(exc)
    return JsonResponse({"ok": True})


# ------------------------------------------------------------------ importação
@login_required
@company_can_view_data
def movimentacoes_importar(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    if request.method == "POST":
        return _importar_post(request, fundo)
    fechamento = ms.data_fechamento(fundo)
    lotes = []
    for lote in LoteImportacaoMovimentacao.objects.filter(fundo=fundo).select_related("importado_por")[:50]:
        menor = lote.movimentacoes.order_by("data").values_list("data", flat=True).first()
        lotes.append({"lote": lote, "pode_desfazer": not (fechamento and menor and menor <= fechamento)})
    return render(request, "movimentacoes/importar.html", {
        "fundo": fundo, "lotes": lotes, "pode_lancar": _pode_lancar(request), "max_linhas": MAX_LINHAS,
    })


def _resposta_importacao(fazer):
    try:
        return JsonResponse({"ok": True, **fazer()})
    except ImportacaoInvalida as exc:
        return JsonResponse({"ok": False, "erros": exc.erros, "total": exc.total}, status=400)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "erros": [{"linha": None, "mensagem": m} for m in exc.messages],
                             "total": len(exc.messages)}, status=400)


def _importar_post(request, fundo):
    if not _pode_lancar(request):
        return JsonResponse({"ok": False, "erros": [{"linha": None, "mensagem": "Sem permissão para importar."}], "total": 1}, status=403)
    arquivo = request.FILES.get("arquivo")
    if not arquivo:
        return JsonResponse({"ok": False, "erros": [{"linha": None, "mensagem": "Selecione um arquivo."}], "total": 1}, status=400)

    def fazer():
        lote = importar_movimentacoes(fundo, arquivo.name, arquivo.read(), request.user)
        return {"qtd": lote.qtd_linhas, "mensagem": f"{lote.qtd_linhas} movimentação(ões) importada(s)."}
    return _resposta_importacao(fazer)


@login_required
@company_can_manage_movimentacoes
@require_POST
def movimentacoes_lote_desfazer(request, fundo_id, lote_id):
    fundo = _fundo(request, fundo_id)
    lote = get_object_or_404(LoteImportacaoMovimentacao, id=lote_id, fundo=fundo)
    return _resposta_importacao(lambda: {"mensagem": f"{desfazer_lote(lote)} movimentação(ões) removida(s)."})


@login_required
@company_can_view_data
@require_GET
def movimentacoes_modelo(request, fundo_id):
    """Planilha modelo: aba de exemplo + aba com as contas lançáveis do plano."""
    _fundo(request, fundo_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Movimentações"
    ws.append(["DATA", "CONTA", "VALOR", "HISTÓRICO", "DOCUMENTO"])
    ws.append([date(2025, 1, 31), "1.01.01.01.01", 1000.00, "Exemplo: recebimento", "NF 123"])
    ws.append([date(2025, 1, 31), "3.01.01.01.01.01", 1000.00, "Exemplo: receita", "NF 123"])
    for celula in ws[1]:
        celula.font = Font(bold=True)
    for linha in range(2, 400):
        ws.cell(row=linha, column=2).number_format = "@"  # código como texto: o Excel não vira 1.10 em 1,1
        ws.cell(row=linha, column=1).number_format = "dd/mm/yyyy"
    for coluna, largura in zip("ABCDE", (14, 22, 16, 40, 16)):
        ws.column_dimensions[coluna].width = largura

    plano = wb.create_sheet("Plano de contas")
    plano.append(["CÓDIGO", "DESCRIÇÃO", "NATUREZA"])
    for celula in plano[1]:
        celula.font = Font(bold=True)
    for c in Conta.objects.filter(tipo=Conta.Tipo.ANALITICA, is_ativa=True).order_by("codigo"):
        plano.append([c.codigo, c.descricao, c.get_natureza_display()])
    plano.column_dimensions["A"].width = 22
    plano.column_dimensions["B"].width = 80
    plano.column_dimensions["C"].width = 22

    saida = io.BytesIO()
    wb.save(saida)
    resposta = HttpResponse(
        saida.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resposta["Content-Disposition"] = 'attachment; filename="modelo_movimentacoes.xlsx"'
    return resposta


# ------------------------------------------------------------------ saldos iniciais
def _serializar_saldo(s: SaldoInicial, fechamento: date | None) -> dict:
    return {
        "id": s.id, "conta_id": s.conta_id, "conta": s.conta.codigo, "conta_descricao": s.conta.descricao,
        "natureza": s.conta.get_natureza_display(), "data_base": s.data_base.isoformat(), "valor": str(s.valor),
        "negativo": s.valor < 0, "editavel": not (fechamento and s.data_base <= fechamento),
    }


@login_required
@company_can_view_data
def saldos_iniciais_fundo(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    fechamento = ms.data_fechamento(fundo)
    saldos = SaldoInicial.objects.filter(fundo=fundo).select_related("conta").order_by("conta__codigo")
    return render(request, "movimentacoes/saldos_iniciais.html", {
        "fundo": fundo,
        "saldos": [_serializar_saldo(s, fechamento) for s in saldos],
        "fechamento": fechamento,
        "pode_lancar": _pode_lancar(request),
    })


@login_required
@company_can_manage_movimentacoes
@require_POST
def saldo_inicial_salvar(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    data_base, valor, conta = _data(request.POST.get("data_base")), _valor(request.POST.get("valor")), _conta(request)
    if conta is None:
        return _erro_campo("conta", "Escolha uma conta da lista.")
    if data_base is None:
        return _erro_campo("data_base", "Informe a data-base.")
    if valor is None:
        return _erro_campo("valor", "Informe o valor (ex.: 1.234,56 ou -500,00).")
    try:
        saldo = ms.definir_saldo_inicial(fundo, conta, data_base, valor, request.user)
    except ValidationError as exc:
        return _erros(exc)
    saldo.conta = conta
    return JsonResponse({"ok": True, "item": _serializar_saldo(saldo, ms.data_fechamento(fundo))})


@login_required
@company_can_manage_movimentacoes
@require_POST
def saldo_inicial_excluir(request, fundo_id, saldo_id):
    fundo = _fundo(request, fundo_id)
    saldo = get_object_or_404(SaldoInicial, id=saldo_id, fundo=fundo)
    try:
        ms.excluir_saldo_inicial(saldo)
    except ValidationError as exc:
        return _erros(exc)
    return JsonResponse({"ok": True})


@login_required
@company_can_manage_movimentacoes
@require_POST
def saldos_iniciais_importar(request, fundo_id):
    fundo = _fundo(request, fundo_id)
    arquivo, data_base = request.FILES.get("arquivo"), _data(request.POST.get("data_base"))
    if data_base is None:
        return JsonResponse({"ok": False, "erros": [{"linha": None, "mensagem": "Informe a data-base dos saldos."}], "total": 1}, status=400)
    if not arquivo:
        return JsonResponse({"ok": False, "erros": [{"linha": None, "mensagem": "Selecione um arquivo."}], "total": 1}, status=400)

    def fazer():
        qtd = importar_saldos_iniciais(fundo, data_base, arquivo.name, arquivo.read(), request.user)
        return {"qtd": qtd, "mensagem": f"{qtd} saldo(s) inicial(is) gravado(s)."}
    return _resposta_importacao(fazer)


# ------------------------------------------------------------------ balancete calculado
COLUNAS_BALANCETE = ("anterior", "movimentos", "ajustes", "atual")


@login_required
@company_can_view_data
def balancete_periodo(request, periodo_id):
    """
    Balancete do período calculado das movimentações: saldo anterior + movimentações + ajustes = saldo
    atual, para todas as contas (sintéticas somadas na árvore). Nada disso é armazenado.
    """
    fundos = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    periodo = get_object_or_404(PeriodoDF.objects.select_related("fundo__empresa"), id=periodo_id, fundo__in=fundos)
    garantir_datas_periodo(periodo)
    fundo = periodo.fundo

    analiticas = balancete_calculado(fundo.id, periodo.data_anterior, periodo.data_referencia)
    consolidado = {
        col: consolidar_arvore({l["conta_id"]: l[col] for l in analiticas}) for col in COLUNAS_BALANCETE
    }

    ids = {cid for col in consolidado.values() for cid in col}
    codigos = set(Conta.objects.filter(id__in=ids).values_list("codigo", flat=True))
    # Inclui os ancestrais mesmo quando o total deles dá zero (+100 e −100 nas filhas): sem eles a árvore quebra.
    todos = set(codigos)
    for codigo in codigos:
        partes = codigo.split(".")
        todos.update(".".join(partes[:i]) for i in range(1, len(partes)))
    contas = list(Conta.objects.filter(codigo__in=todos).order_by("codigo"))

    zero = Decimal("0")
    linhas = [
        {
            "codigo": c.codigo, "descricao": c.descricao, "nivel": c.nivel, "tipo": c.tipo,
            "natureza": c.get_natureza_display(),
            **{col: consolidado[col].get(c.id, zero) for col in COLUNAS_BALANCETE},
        }
        for c in contas
    ]

    return render(request, "movimentacoes/balancete.html", {
        "fundo": fundo,
        "periodo": periodo,
        "linhas": linhas,
        "data_atual": periodo.data_referencia,
        "data_anterior": periodo.data_anterior,
        "tem_ajustes": any(l["ajustes"] for l in analiticas),
        "contas_com_movimento": sum(1 for l in analiticas if l["movimentos"]),
        "conferencia_atual": conferencia(fundo.id, periodo.data_referencia),
        "conferencia_anterior": conferencia(fundo.id, periodo.data_anterior),
    })
