from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from collections import defaultdict
from datetime import date
import json
import logging

from df.models import (
    Fundo, BalanceteItem, HistoricoEmissaoDF, PeriodoDF, Gestora,
    ChecklistItemPeriodo, DocumentoDF, LogAcessoDocumento,
)
from df.validators import validar_documento_upload
from df.services.documento_service import (
    sugerir_contem_dados_pessoais,
    registrar_acesso,
    resolver_documento_escopo,
    uso_documentos_bytes,
    resposta_download,
    estatisticas_por_empresa,
    calcular_retencao_ate,
    serie_crescimento_mensal,
)
from usuarios.models import Empresa, Membership
from usuarios.utils.company_scope import query_por_empresa_ativa
from usuarios.permissions import (
    company_can_view_data,
    company_can_manage_fundos,
    get_empresa_escopo,
    role_na_empresa,
    is_global_admin,
    company_can_download_data,
    pode_baixar_documentos,
    platform_admin_required,
)

from .forms import FundoForm, GestoraForm, EditarPerfilForm, EmpresaDocumentosConfigForm
from df.forms import PeriodoDFManualForm

logger = logging.getLogger(__name__)

# Camadas novas (core)
from core.export.df_excel import criar_aba_dpf, criar_aba_dre, criar_aba_dmpl, criar_aba_dfc
from core.export.df_docx import build_docx_context, gerar_docx
from core.processing.import_service import import_balancete, import_mec
from core.processing.dre_service import gerar_dados_dre
from core.processing.dpf_service import gerar_dados_dpf
from core.processing.dmpl_service import gerar_dados_dmpl
from core.processing.dfc_service import gerar_tabela_dfc
from core.processing.status_service import calcular_status_periodo
from core.upload.balancete_parser import parse_excel, BalanceteSchemaError
from core.upload.mec_parser import parse_excel_mec, MecSchemaError

import os
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime, date


# --------- helpers de escopo/flags para UI ---------
def _empresas_do_usuario(user):
    has_global = getattr(user, "has_global_scope", None)
    if has_global and user.has_global_scope():
        return Empresa.objects.all()
    empresa_ids = Membership.objects.filter(usuario=user, is_active=True).values_list("empresa_id", flat=True)
    return Empresa.objects.filter(id__in=list(empresa_ids))

def _can_manage_fundos(request):
    """
    Habilita botões na UI de Fundos conforme a mesma regra do decorator company_can_manage_fundos.
    """
    empresa = get_empresa_escopo(request)
    if not empresa:
        return False
    if is_global_admin(request.user):
        return True
    user_role = role_na_empresa(request.user, empresa)
    return user_role in {Membership.Role.MASTER, Membership.Role.ADMIN, Membership.Role.MEMBER}


def _pode_anexar_documentos(request):
    """
    Habilita a UI de upload de documentos: mesma regra de _can_manage_fundos,
    mais o gate de DPA (empresa.documentos_habilitados) — sem contrato de
    operador assinado, o recurso nem aparece.
    """
    empresa = get_empresa_escopo(request)
    if not empresa or not empresa.documentos_habilitados:
        return False
    return _can_manage_fundos(request)


# ===============================
# PÁGINA: Demonstração Financeira
# ===============================
@login_required
@company_can_view_data
def demonstracao_financeira(request):
    empresa_ativa = get_empresa_escopo(request)
    fundos_qs = query_por_empresa_ativa(
        Fundo.objects.select_related("empresa", "gestora"),
        request,
        "empresa",
    ).order_by("nome")
    fundos = list(fundos_qs)
    gestoras = list(Gestora.objects.filter(empresa=empresa_ativa).order_by('nome')) if empresa_ativa else []

    from core.processing.import_service import calcular_data_referencia_periodo
    fundos_periodos = {}
    fundos_prazos = {}
    hoje = date.today()

    for fundo in fundos:
        periodos = PeriodoDF.objects.filter(fundo=fundo).order_by('ano', 'tipo_periodo')
        periodos_data = []
        for p in periodos:
            data_ref_calculada = calcular_data_referencia_periodo(p)
            cl_total = p.checklist_items.count()
            cl_recebidos = p.checklist_items.filter(recebido=True).count()
            periodos_data.append({
                'id': p.id,
                'nome': p.nome_exibicao,
                'tipo': p.tipo_periodo,
                'ano': p.ano,
                'status': p.status,
                'tem_balancete': p.balancete_items.exists(),
                'data_referencia': p.data_referencia.isoformat() if p.data_referencia else None,
                'data_referencia_calculada': data_ref_calculada.isoformat() if data_ref_calculada else None,
                'data_vencimento': p.data_vencimento.isoformat() if p.data_vencimento else None,
                'checklist_summary': {'total': cl_total, 'recebidos': cl_recebidos},
            })
        fundos_periodos[str(fundo.id)] = periodos_data

        proximo_periodo = (
            PeriodoDF.objects.filter(fundo=fundo, data_vencimento__gte=hoje)
            .exclude(status="finalizada")
            .order_by("data_vencimento")
            .first()
        )
        if proximo_periodo:
            dias_restantes = (proximo_periodo.data_vencimento - hoje).days
            if dias_restantes > 30:
                status_prazo = "ok"
            elif dias_restantes >= 15:
                status_prazo = "aviso"
            elif dias_restantes >= 0:
                status_prazo = "urgente"
            else:
                status_prazo = "vencida"
                dias_restantes = abs(dias_restantes)

            fundos_prazos[str(fundo.id)] = {
                "data_vencimento": proximo_periodo.data_vencimento.isoformat(),
                "dias_restantes": dias_restantes,
                "status": status_prazo,
                "periodo_nome": proximo_periodo.nome_exibicao,
            }

    return render(request, "demonstracao_financeira.html", {
        "fundos": fundos,
        "gestoras": gestoras,
        "fundos_periodos": fundos_periodos,
        "fundos_prazos": fundos_prazos,
        "can_enviar_balancete": _can_manage_fundos(request),
    })


# ===============================
# PÁGINA: Controle de Emissões
# ===============================
@login_required
@company_can_view_data
def controle_emissoes(request):
    """
    Dashboard de vencimentos e status de todos os períodos de DF de todos os fundos.
    """
    empresa = get_empresa_escopo(request)
    hoje = date.today()

    # Sincroniza status vencida em lote: períodos sem dados e sem emissão que já passaram
    # do prazo ainda podem estar como nao_iniciada se nunca houve interação com eles.
    PeriodoDF.objects.filter(
        empresa=empresa,
        status='nao_iniciada',
        data_vencimento__lt=hoje,
    ).update(status='vencida')
    # Reverte caso data_vencimento tenha sido corrigida para o futuro
    PeriodoDF.objects.filter(
        empresa=empresa,
        status='vencida',
        data_vencimento__gte=hoje,
    ).update(status='nao_iniciada')

    status_filtro = request.GET.get('status', '')
    ano_filtro = request.GET.get('ano', '')
    fundo_filtro = request.GET.get('fundo', '')
    gestora_filtro = request.GET.get('gestora', '')
    tipo_fundo_filtro = request.GET.get('tipo_fundo', '')

    qs = (
        PeriodoDF.objects
        .filter(empresa=empresa)
        .select_related('fundo', 'fundo__gestora')
        .order_by('data_vencimento', 'fundo__nome')
    )
    if ano_filtro:
        try:
            qs = qs.filter(ano=int(ano_filtro))
        except ValueError:
            ano_filtro = ''
    if fundo_filtro:
        try:
            qs = qs.filter(fundo_id=int(fundo_filtro))
        except ValueError:
            fundo_filtro = ''
    if gestora_filtro:
        try:
            qs = qs.filter(fundo__gestora_id=int(gestora_filtro))
        except ValueError:
            gestora_filtro = ''
    if tipo_fundo_filtro:
        tipos_validos = {c[0] for c in Fundo.TIPO_FUNDO_CHOICES}
        if tipo_fundo_filtro in tipos_validos:
            qs = qs.filter(fundo__tipo_fundo=tipo_fundo_filtro)
        else:
            tipo_fundo_filtro = ''

    todos = list(qs)

    metricas = {
        'total': len(todos),
        'finalizadas': sum(1 for p in todos if p.status == 'finalizada'),
        'em_andamento': sum(1 for p in todos if p.status == 'em_andamento'),
        'nao_iniciadas': sum(1 for p in todos if p.status == 'nao_iniciada'),
        'vencidas': sum(1 for p in todos if p.status == 'vencida'),
        'quase_vencendo': sum(
            1 for p in todos
            if p.status not in ('finalizada', 'vencida')
            and p.data_vencimento
            and 0 <= (p.data_vencimento - hoje).days <= 30
        ),
    }

    if status_filtro == 'quase_vencendo':
        periodos_base = [
            p for p in todos
            if p.status not in ('finalizada', 'vencida')
            and p.data_vencimento
            and 0 <= (p.data_vencimento - hoje).days <= 30
        ]
    elif status_filtro:
        periodos_base = [p for p in todos if p.status == status_filtro]
    else:
        periodos_base = todos

    periodos = []
    for p in periodos_base:
        dias = (p.data_vencimento - hoje).days if p.data_vencimento else None
        if p.status == 'finalizada':
            urgencia = 'finalizada'
        elif p.status == 'vencida':
            urgencia = 'vencida'
        elif dias is not None and dias <= 7:
            urgencia = 'urgente'
        elif dias is not None and dias <= 30:
            urgencia = 'aviso'
        else:
            urgencia = 'ok'
        periodos.append({
            'periodo': p,
            'dias_ate_vencimento': dias,
            'dias_atraso': abs(dias) if dias is not None and dias < 0 else 0,
            'urgencia': urgencia,
        })

    anos_disponiveis = list(
        PeriodoDF.objects.filter(empresa=empresa)
        .values_list('ano', flat=True)
        .distinct()
        .order_by('-ano')
    )

    fundos_lista = list(
        Fundo.objects.filter(empresa=empresa).order_by('nome').values('id', 'nome')
    )
    gestoras_lista = list(
        Gestora.objects.filter(empresa=empresa).order_by('nome').values('id', 'nome')
    )

    # String com params não-status para montar URLs dos KPI cards
    _filtros_parts = []
    if ano_filtro:
        _filtros_parts.append(f'ano={ano_filtro}')
    if fundo_filtro:
        _filtros_parts.append(f'fundo={fundo_filtro}')
    if gestora_filtro:
        _filtros_parts.append(f'gestora={gestora_filtro}')
    if tipo_fundo_filtro:
        _filtros_parts.append(f'tipo_fundo={tipo_fundo_filtro}')
    filtros_base = '&'.join(_filtros_parts)

    # Breakdown por ano para o gráfico de barras (usa todos, sem filtro de status)
    _todos_sem_filtro = todos  # já é todos os do ano filtrado (ou todos os anos)
    ano_breakdown = {}
    for p in _todos_sem_filtro:
        yr = str(p.ano)
        if yr not in ano_breakdown:
            ano_breakdown[yr] = {'finalizada': 0, 'em_andamento': 0, 'vencida': 0, 'nao_iniciada': 0}
        key = p.status if p.status in ano_breakdown[yr] else 'nao_iniciada'
        ano_breakdown[yr][key] += 1

    anos_sorted = sorted(ano_breakdown.keys())
    dashboard_data = {
        'status_labels': ['Finalizadas', 'Em Andamento', 'Vencidas', 'Não Iniciadas'],
        'status_values': [
            metricas['finalizadas'], metricas['em_andamento'],
            metricas['vencidas'], metricas['nao_iniciadas'],
        ],
        'status_colors': ['#10b981', '#3b82f6', '#ef4444', '#6b7585'],
        'anos': anos_sorted,
        'por_ano': {
            'finalizadas':   [ano_breakdown[a]['finalizada']   for a in anos_sorted],
            'em_andamento':  [ano_breakdown[a]['em_andamento'] for a in anos_sorted],
            'vencidas':      [ano_breakdown[a]['vencida']      for a in anos_sorted],
            'nao_iniciadas': [ano_breakdown[a]['nao_iniciada'] for a in anos_sorted],
        },
    }

    return render(request, "controle_emissoes.html", {
        "periodos": periodos,
        "metricas": metricas,
        "status_filtro": status_filtro,
        "ano_filtro": ano_filtro,
        "fundo_filtro": fundo_filtro,
        "gestora_filtro": gestora_filtro,
        "tipo_fundo_filtro": tipo_fundo_filtro,
        "anos_disponiveis": anos_disponiveis,
        "fundos_lista": fundos_lista,
        "gestoras_lista": gestoras_lista,
        "tipos_fundo": Fundo.TIPO_FUNDO_CHOICES,
        "filtros_base": filtros_base,
        "can_manage_fundos": _can_manage_fundos(request),
        "hoje": hoje,
        "dashboard_data": json.dumps(dashboard_data),
    })


# ===============================
# IMPORTAR BALANCETE (orientado a período)
# ===============================
@login_required
@company_can_manage_fundos
def importar_balancete_view(request):
    if request.method != "POST":
        return redirect("demonstracao_financeira")

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    def _err(msg):
        if is_ajax:
            return JsonResponse({"ok": False, "message": msg}, status=400)
        messages.error(request, msg)
        return redirect("demonstracao_financeira")

    fundo_id = request.POST.get("fundo_id")
    periodo_df_id = request.POST.get("periodo_df_id")
    saldo_anterior_mode = request.POST.get("saldo_anterior_mode", "zerado")
    periodo_anterior_id = request.POST.get("periodo_anterior_id")
    arquivo_balancete = request.FILES.get("arquivo_balancete")
    arquivo_saldo_anterior = request.FILES.get("arquivo_saldo_anterior")

    fundo_qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(fundo_qs, id=fundo_id)

    if not periodo_df_id:
        return _err("Selecione o período da DF.")

    periodo = get_object_or_404(PeriodoDF, id=periodo_df_id, fundo=fundo)

    from core.processing.import_service import (
        calcular_data_referencia_periodo,
        calcular_data_referencia_periodo_anterior,
    )
    data_referencia = calcular_data_referencia_periodo(periodo)

    if not arquivo_balancete:
        return _err("Selecione o arquivo do balancete (Saldo Final).")

    try:
        rows = parse_excel(arquivo_balancete)
        report = import_balancete(
            fundo_id=fundo.id,
            data_referencia=data_referencia,
            rows=rows,
            periodo_df_id=periodo.id,
        )
    except BalanceteSchemaError as e:
        return _err(f"Planilha inválida: faltam colunas {', '.join(e.missing_columns)}")
    except Exception as e:
        return _err(f"Erro ao importar balancete: {e}")

    periodo.data_referencia = data_referencia

    if saldo_anterior_mode == "zerado":
        periodo.data_anterior = None
        periodo.save()
    elif saldo_anterior_mode == "clone":
        if not periodo_anterior_id:
            return _err("Selecione o período de origem para o clone.")
        periodo_origem = get_object_or_404(PeriodoDF, id=periodo_anterior_id, fundo=fundo)
        if not periodo_origem.data_referencia:
            return _err(f"O período '{periodo_origem.nome_exibicao}' ainda não possui balancete importado.")
        periodo.data_anterior = periodo_origem.data_referencia
        periodo.save()
    elif saldo_anterior_mode == "spreadsheet":
        if not arquivo_saldo_anterior:
            return _err("Selecione o arquivo do saldo anterior.")
        data_anterior = calcular_data_referencia_periodo_anterior(periodo)
        if not data_anterior:
            return _err("Não foi possível determinar a data do saldo anterior para este tipo de período.")
        try:
            rows_ant = parse_excel(arquivo_saldo_anterior)
            import_balancete(fundo_id=fundo.id, data_referencia=data_anterior, rows=rows_ant)
        except BalanceteSchemaError as e:
            return _err(f"Planilha do saldo anterior inválida: faltam colunas {', '.join(e.missing_columns)}")
        except Exception as e:
            return _err(f"Erro ao importar saldo anterior: {e}")
        periodo.data_anterior = data_anterior
        periodo.save()
    else:
        periodo.save()

    if report.errors:
        msg = f"Balancete importado com avisos. {report.imported} inseridos, {report.updated} atualizados, {report.ignored} ignorados."
        if is_ajax:
            return JsonResponse({"ok": True, "warning": True, "message": msg})
        messages.warning(request, msg)
    else:
        msg = f"Balancete importado com sucesso para '{periodo.nome_exibicao}': {report.imported} inseridos, {report.updated} atualizados."
        if is_ajax:
            return JsonResponse({"ok": True, "message": msg})
        messages.success(request, msg)

    return redirect("demonstracao_financeira")


# ===============================
# IMPORTAR MEC (sem alterações)
# ===============================
@login_required
@company_can_manage_fundos
def importar_mec_view(request):
    if request.method != "POST":
        return redirect("demonstracao_financeira")

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    def _err(msg):
        if is_ajax:
            return JsonResponse({"ok": False, "message": msg}, status=400)
        messages.error(request, msg)
        return redirect("demonstracao_financeira")

    fundo_id = request.POST.get("fundo_id")
    arquivo_mec = request.FILES.get("arquivo_mec")

    fundo_qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(fundo_qs, id=fundo_id)

    if not arquivo_mec:
        return _err("Selecione o arquivo do MEC.")

    try:
        rows_mec = parse_excel_mec(arquivo_mec)
        report = import_mec(fundo_id=fundo.id, rows=rows_mec)
    except MecSchemaError as e:
        return _err(f"Planilha do MEC inválida: faltam colunas {', '.join(e.missing_columns)}")
    except Exception as e:
        return _err(f"Erro ao importar MEC: {e}")

    if report.errors:
        msg = f"MEC importado com erros. {report.imported} inseridos, {report.updated} atualizados, {report.ignored} ignorados."
        if is_ajax:
            return JsonResponse({"ok": True, "warning": True, "message": msg})
        messages.warning(request, msg)
    else:
        msg = f"MEC importado com sucesso. {report.imported} inseridos, {report.updated} atualizados, {report.ignored} ignorados."
        if is_ajax:
            return JsonResponse({"ok": True, "message": msg})
        messages.success(request, msg)

    return redirect("demonstracao_financeira")


# ===============================
# API: períodos de um fundo (JSON)
# ===============================
@login_required
@company_can_view_data
def api_periodos_fundo(request, fundo_id):
    fundo_qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    fundo = get_object_or_404(fundo_qs, id=fundo_id)

    from core.processing.import_service import calcular_data_referencia_periodo
    periodos = PeriodoDF.objects.filter(fundo=fundo).order_by("ano", "tipo_periodo")
    periodos_data = []
    for p in periodos:
        data_ref_calc = calcular_data_referencia_periodo(p)
        cl_total = p.checklist_items.count()
        cl_recebidos = p.checklist_items.filter(recebido=True).count()
        periodos_data.append({
            "id": p.id,
            "nome": p.nome_exibicao,
            "tipo": p.tipo_periodo,
            "ano": p.ano,
            "status": p.status,
            "tem_balancete": p.balancete_items.exists(),
            "data_referencia": p.data_referencia.isoformat() if p.data_referencia else None,
            "data_referencia_calculada": data_ref_calc.isoformat() if data_ref_calc else None,
            "data_vencimento": p.data_vencimento.isoformat() if p.data_vencimento else None,
            "checklist_summary": {"total": cl_total, "recebidos": cl_recebidos},
        })
    return JsonResponse({"periodos": periodos_data})


# ===============================
# DF RESULTADO / Exportações (sem mudanças)
# ===============================
def _pct(v, base):
    try:
        v = float(v or 0)
        b = float(base or 0)
        return round((v / b) * 100, 2) if b != 0 else 0.0
    except Exception:
        return 0.0


def annotate_percents(dpf: dict, pl_atual_val: float, pl_ant_val: float) -> dict:
    """
    Adiciona PERC_ATUAL/PERC_ANTERIOR em:
      - cada TOTAL_* (com base em ATUAL/ANTERIOR)
      - cada grupo (com base em SOMA/SOMA_ANTERIOR)
      - cada subgrupo (com base em ATUAL/ANTERIOR)
    Sempre usando como base o PL ajustado (pl_atual_val / pl_ant_val).
    """
    for sec_name, sec in dpf.items():  # ATIVO, PASSIVO, PL
        if not isinstance(sec, dict):
            continue

        for grupo_label, bloco in sec.items():
            if not isinstance(bloco, dict):
                continue

            # Totais da seção (ex.: TOTAL_ATIVO, TOTAL_PASSIVO, TOTAL_PL)
            if grupo_label.startswith("TOTAL_"):
                atual_tot = bloco.get("ATUAL", 0)
                ant_tot = bloco.get("ANTERIOR", 0)
                bloco["PERC_ATUAL"] = _pct(atual_tot, pl_atual_val)
                bloco["PERC_ANTERIOR"] = _pct(ant_tot, pl_ant_val)
                continue

            # Grupos normais
            soma_atual = bloco.get("SOMA", 0)
            soma_ant = bloco.get("SOMA_ANTERIOR", 0)
            bloco["PERC_ATUAL"] = _pct(soma_atual, pl_atual_val)
            bloco["PERC_ANTERIOR"] = _pct(soma_ant, pl_ant_val)

            # Subgrupos
            for sub_label, valores in bloco.items():
                if sub_label in ("SOMA", "SOMA_ANTERIOR"):
                    continue
                if isinstance(valores, dict) and ("ATUAL" in valores or "ANTERIOR" in valores):
                    atual_v = valores.get("ATUAL", 0)
                    ant_v = valores.get("ANTERIOR", 0)
                    valores["PERC_ATUAL"] = _pct(atual_v, pl_atual_val)
                    valores["PERC_ANTERIOR"] = _pct(ant_v, pl_ant_val)

    return dpf


@login_required
@company_can_view_data
def df_resultado(request, periodo_df_id):
    fundo_qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    periodo = get_object_or_404(
        PeriodoDF.objects.select_related("fundo"),
        id=periodo_df_id,
        fundo__in=fundo_qs,
    )
    fundo = periodo.fundo

    if not periodo.data_referencia:
        messages.error(request, f"O período '{periodo.nome_exibicao}' ainda não possui balancete importado.")
        return redirect("demonstracao_financeira")

    data_atual_date = periodo.data_referencia
    data_anterior_date = periodo.data_anterior
    zerar_anterior = data_anterior_date is None

    dre_tabela, resultado_exercicio, resultado_exercicio_anterior = gerar_dados_dre(
        fundo_id=fundo.id,
        data_atual=data_atual_date,
        data_anterior=data_anterior_date,
        zerar_anterior=zerar_anterior,
    )
    dpf_tabela, metricas_dpf = gerar_dados_dpf(
        fundo_id=fundo.id,
        data_atual=data_atual_date,
        data_anterior=data_anterior_date,
        zerar_anterior=zerar_anterior,
    )
    dados_dmpl = gerar_dados_dmpl(
        fundo_id=fundo.id,
        data_atual=data_atual_date,
        data_anterior=data_anterior_date,
        zerar_anterior=zerar_anterior,
    )
    dfc_tabela, variacao_caixa_atual, variacao_caixa_ant = gerar_tabela_dfc(
        fundo_id=fundo.id,
        data_atual=data_atual_date,
        data_anterior=data_anterior_date,
        zerar_anterior=zerar_anterior,
    )

    pl_atual = (dpf_tabela["PL"]["TOTAL_PL"]["ATUAL"] or 0) + (resultado_exercicio or 0)
    pl_anterior = (dpf_tabela["PL"]["TOTAL_PL"]["ANTERIOR"] or 0) + (resultado_exercicio_anterior or 0)
    total_pl_passivo_atual = pl_atual + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ATUAL"] or 0)
    total_pl_passivo_anterior = pl_anterior + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ANTERIOR"] or 0)

    dpf_tabela = annotate_percents(dpf_tabela, pl_atual, pl_anterior)

    context = {
        "fundo": fundo,
        "periodo": periodo,
        "data_atual": data_atual_date,
        "data_anterior": data_anterior_date,
        "zerar_anterior": zerar_anterior,
        "dre_tabela": dre_tabela,
        "dpf_tabela": dpf_tabela,
        "dados_dmpl": dados_dmpl,
        "dfc_tabela": dfc_tabela,
        "resultado_exercicio": resultado_exercicio,
        "resultado_exercicio_anterior": 0 if zerar_anterior else resultado_exercicio_anterior,
        "pl_ajustado_atual": pl_atual,
        "pl_ajustado_anterior": pl_anterior,
        "total_pl_passivo_atual": total_pl_passivo_atual,
        "total_pl_passivo_anterior": total_pl_passivo_anterior,
        "variacao_atual": variacao_caixa_atual,
        "variacao_ant": 0 if zerar_anterior else variacao_caixa_ant,
    }

    return render(request, "df_resultado.html", context)

    


@login_required
@company_can_download_data
def exportar_dfs_excel(request, periodo_df_id):
    fundo_qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    periodo = get_object_or_404(
        PeriodoDF.objects.select_related("fundo"),
        id=periodo_df_id,
        fundo__in=fundo_qs,
    )
    fundo = periodo.fundo

    if not periodo.data_referencia:
        messages.error(request, f"O período '{periodo.nome_exibicao}' ainda não possui balancete importado.")
        return redirect("demonstracao_financeira")

    data_atual = periodo.data_referencia
    data_anterior = periodo.data_anterior
    zerar_anterior = data_anterior is None

    dre_tabela, resultado_exercicio, resultado_exercicio_anterior = gerar_dados_dre(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )
    dpf_tabela, _metricas_dpf = gerar_dados_dpf(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )

    pl_atual = (dpf_tabela["PL"]["TOTAL_PL"]["ATUAL"] or 0) + (resultado_exercicio or 0)
    pl_anterior = (dpf_tabela["PL"]["TOTAL_PL"]["ANTERIOR"] or 0) + (resultado_exercicio_anterior or 0)
    total_pl_passivo_atual = pl_atual + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ATUAL"] or 0)
    total_pl_passivo_anterior = pl_anterior + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ANTERIOR"] or 0)

    dpf_tabela = annotate_percents(dpf_tabela, pl_atual, pl_anterior)

    dados_dmpl = gerar_dados_dmpl(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )
    dfc_tabela, variacao_atual, variacao_ant = gerar_tabela_dfc(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )

    wb = Workbook()
    criar_aba_dpf(wb, fundo, data_atual, data_anterior, dpf_tabela, pl_atual, pl_anterior, total_pl_passivo_atual, total_pl_passivo_anterior)
    criar_aba_dre(wb, fundo, data_atual, data_anterior, dre_tabela, resultado_exercicio, resultado_exercicio_anterior)
    criar_aba_dmpl(wb, fundo, data_atual, data_anterior, dados_dmpl, resultado_exercicio, resultado_exercicio_anterior, pl_atual, pl_anterior)
    criar_aba_dfc(wb, fundo, data_atual, data_anterior, dfc_tabela, variacao_atual, variacao_ant)

    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    nome_curto = "_".join(str(fundo.nome).replace("-", "").split())
    if data_anterior:
        response["Content-Disposition"] = f"attachment; filename=DFs_{data_atual.strftime('%Y%m%d')}_{data_anterior.strftime('%Y%m%d')}_{nome_curto}.xlsx"
    else:
        response["Content-Disposition"] = f"attachment; filename=DFs_{data_atual.strftime('%Y%m%d')}_{nome_curto}.xlsx"

    wb.save(response)

    HistoricoEmissaoDF.objects.create(
        fundo=fundo,
        empresa=fundo.empresa,
        usuario=request.user if request.user.is_authenticated else None,
        data_referencia_df=data_atual,
        data_anterior_df=data_anterior,
        tipo_exportacao='excel',
        periodo_df=periodo,
    )

    return response


@login_required
@company_can_download_data
def exportar_dfs_docx(request, periodo_df_id):
    fundo_qs = query_por_empresa_ativa(Fundo.objects.select_related("empresa"), request, "empresa")
    periodo = get_object_or_404(
        PeriodoDF.objects.select_related("fundo"),
        id=periodo_df_id,
        fundo__in=fundo_qs,
    )
    fundo = periodo.fundo

    if not periodo.data_referencia:
        messages.error(request, f"O período '{periodo.nome_exibicao}' ainda não possui balancete importado.")
        return redirect("demonstracao_financeira")

    data_atual = periodo.data_referencia
    data_anterior = periodo.data_anterior
    zerar_anterior = data_anterior is None

    dre_tabela, resultado_exercicio, resultado_exercicio_anterior = gerar_dados_dre(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )
    dpf_tabela, _metricas_dpf = gerar_dados_dpf(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )

    pl_atual = (dpf_tabela["PL"]["TOTAL_PL"]["ATUAL"] or 0) + (resultado_exercicio or 0)
    pl_anterior = (dpf_tabela["PL"]["TOTAL_PL"]["ANTERIOR"] or 0) + (resultado_exercicio_anterior or 0)
    total_pl_passivo_atual = pl_atual + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ATUAL"] or 0)
    total_pl_passivo_anterior = pl_anterior + (dpf_tabela["PASSIVO"]["TOTAL_PASSIVO"]["ANTERIOR"] or 0)

    dpf_tabela = annotate_percents(dpf_tabela, pl_atual, pl_anterior)

    dados_dmpl = gerar_dados_dmpl(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )
    dfc_tabela, variacao_atual, variacao_ant = gerar_tabela_dfc(
        fundo_id=fundo.id, data_atual=data_atual, data_anterior=data_anterior, zerar_anterior=zerar_anterior
    )

    if zerar_anterior:
        resultado_exercicio_anterior = 0
        variacao_ant = 0

    docx_context = build_docx_context(
        fundo=fundo,
        data_atual=data_atual,
        data_anterior=data_anterior,
        dre_tabela=dre_tabela,
        dpf_tabela=dpf_tabela,
        dados_dmpl=dados_dmpl,
        dfc_tabela=dfc_tabela,
        resultado_exercicio=resultado_exercicio,
        resultado_exercicio_anterior=resultado_exercicio_anterior,
        pl_ajustado_atual=pl_atual,
        pl_ajustado_anterior=pl_anterior,
        total_pl_passivo_atual=total_pl_passivo_atual,
        total_pl_passivo_anterior=total_pl_passivo_anterior,
        variacao_atual=variacao_atual,
        variacao_ant=variacao_ant,
    )

    buffer = gerar_docx(docx_context)

    response = HttpResponse(
        buffer,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    nome_curto = "_".join(str(fundo.nome).replace("-", "").split())
    if data_anterior:
        response["Content-Disposition"] = f"attachment; filename=DFs_{data_atual.strftime('%Y%m%d')}_{data_anterior.strftime('%Y%m%d')}_{nome_curto}.docx"
    else:
        response["Content-Disposition"] = f"attachment; filename=DFs_{data_atual.strftime('%Y%m%d')}_{nome_curto}.docx"

    HistoricoEmissaoDF.objects.create(
        fundo=fundo,
        empresa=fundo.empresa,
        usuario=request.user if request.user.is_authenticated else None,
        data_referencia_df=data_atual,
        data_anterior_df=data_anterior,
        tipo_exportacao='word',
        periodo_df=periodo,
    )

    return response

# ===========================
# Alteração manual de status
# ===========================
@login_required
@company_can_manage_fundos
def atualizar_status_manual(request, periodo_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método não permitido'}, status=405)

    empresa = get_empresa_escopo(request)
    periodo = get_object_or_404(PeriodoDF, id=periodo_id, empresa=empresa)

    novo_status = request.POST.get('status', '').strip()
    STATUS_MANUAIS = {'nao_iniciada', 'em_andamento', 'finalizada'}
    if novo_status not in STATUS_MANUAIS:
        return JsonResponse({'ok': False, 'error': 'Status inválido'}, status=400)

    if periodo.status == 'vencida' and novo_status == 'em_andamento':
        return JsonResponse({
            'ok': False,
            'error': 'Período vencido não pode ser marcado como Em Andamento'
        }, status=400)

    periodo.status = novo_status
    periodo.save(update_fields=['status', 'atualizado_em'])
    return JsonResponse({'ok': True, 'status': novo_status})


# ===========================
# CRUD de Fundos (inalterado)
# ===========================
@login_required
@company_can_view_data
def listar_fundos(request):
    empresa = get_empresa_escopo(request)
    fundos = query_por_empresa_ativa(
        Fundo.objects.select_related("empresa", "gestora").prefetch_related("configuracoes_df"),
        request, "empresa"
    ).order_by("nome")
    gestoras = list(Gestora.objects.filter(empresa=empresa).order_by('nome')) if empresa else []
    form = FundoForm(empresa=empresa)
    gestora_form = GestoraForm(prefix='gestora')
    aba_ativa = request.GET.get('tab', 'fundos')
    return render(request, "fundos/listar.html", {
        "fundos": fundos,
        "form": form,
        "can_manage_fundos": _can_manage_fundos(request),
        "gestoras": gestoras,
        "gestora_form": gestora_form,
        "aba_ativa": aba_ativa,
        "tipo_fundo_choices": Fundo.TIPO_FUNDO_CHOICES,
        "fundos_usados": empresa.total_fundos() if empresa else None,
        "fundos_max": empresa.max_fundos if empresa else None,
        "pode_adicionar_fundo": empresa.pode_adicionar_fundo() if empresa else True,
    })


@login_required
@company_can_manage_fundos
def adicionar_fundo(request):
    empresa_ativa = getattr(request, "empresa_ativa", None)
    if request.method == "POST":
        form = FundoForm(request.POST, empresa=empresa_ativa)
        if form.is_valid():
            fundo = form.save(commit=False)

            empresas_user = list(_empresas_do_usuario(request.user))
            empresa_ativa = getattr(request, "empresa_ativa", None)
            empresa_id_post = (
                request.POST.get("empresa")
                or request.POST.get("empresa_id")
                or (empresa_ativa.id if empresa_ativa else None)
                or request.session.get("empresa_ativa_id")
            )

            if getattr(request.user, "has_global_scope", None) and request.user.has_global_scope():
                if not getattr(fundo, "empresa_id", None):
                    if empresa_id_post:
                        fundo.empresa_id = empresa_id_post
                    else:
                        messages.error(request, "Selecione a empresa do Fundo (ou escolha uma empresa ativa na navbar).")
                        return redirect("listar_fundos")
            else:
                if len(empresas_user) == 1:
                    fundo.empresa = empresas_user[0]
                else:
                    if not getattr(fundo, "empresa_id", None):
                        if empresa_id_post and any(str(e.id) == str(empresa_id_post) for e in empresas_user):
                            fundo.empresa_id = empresa_id_post
                        else:
                            messages.error(request, "Selecione uma empresa válida que você participa.")
                            return redirect("listar_fundos")

            empresa_dona = Empresa.objects.filter(pk=fundo.empresa_id).first()
            if empresa_dona and not empresa_dona.pode_adicionar_fundo():
                messages.error(
                    request,
                    f"Limite de fundos atingido para {empresa_dona.nome} "
                    f"({empresa_dona.total_fundos()}/{empresa_dona.max_fundos}). "
                    f"Contate o administrador para aumentar o limite."
                )
                return redirect("listar_fundos")

            fundo.save()
            form.save_configuracoes(fundo)
            messages.success(request, "Fundo criado com sucesso.")
            return redirect("listar_fundos")
    else:
        form = FundoForm(empresa=empresa_ativa)
    return render(request, "fundos/form.html", {"form": form, "modo": "Adicionar"})


@login_required
@company_can_manage_fundos
def editar_fundo(request, fundo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)
    tipo_fundo_anterior = fundo.tipo_fundo

    if request.method == "POST":
        form = FundoForm(request.POST, instance=fundo)
        if form.is_valid():
            obj = form.save(commit=False)
            nova_empresa_id = getattr(obj, "empresa_id", fundo.empresa_id)
            if nova_empresa_id != fundo.empresa_id:
                empresas_user_ids = set(_empresas_do_usuario(request.user).values_list("id", flat=True))
                if (getattr(request.user, "has_global_scope", None) and request.user.has_global_scope()) or (
                    nova_empresa_id in empresas_user_ids
                ):
                    pass
                else:
                    messages.error(request, "Você não tem permissão para mover o fundo para essa empresa.")
                    return redirect("listar_fundos")
            obj.save()
            form.save_configuracoes(obj)

            if obj.tipo_fundo != tipo_fundo_anterior:
                from df.services.checklist_service import ressincronizar_checklist_por_tipo
                n = ressincronizar_checklist_por_tipo(obj)
                if n:
                    messages.info(
                        request,
                        f"Tipo alterado para {obj.tipo_fundo}: checklist de documentos "
                        f"atualizado em {n} período(s) não finalizado(s)."
                    )

            messages.success(request, "Fundo atualizado com sucesso.")
            return redirect("listar_fundos")
    else:
        form = FundoForm(instance=fundo)
    return render(request, "fundos/form.html", {"form": form, "modo": "Editar"})


@login_required
@company_can_manage_fundos
def excluir_fundo(request, fundo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)

    if request.method == "POST":
        try:
            fundo.delete()
        except ProtectedError:
            messages.error(
                request,
                "Este fundo possui documentos anexados e não pode ser excluído. "
                "Peça ao administrador da plataforma para expurgar os documentos primeiro.",
            )
            return redirect("listar_fundos")
        messages.success(request, "Fundo excluído com sucesso.")
        return redirect("listar_fundos")
    return render(request, "fundos/confirmar_exclusao.html", {"fundo": fundo})


# ===========================
# CRUD de Gestoras
# ===========================
@login_required
@company_can_manage_fundos
def adicionar_gestora(request):
    if request.method != "POST":
        return redirect('listar_fundos')
    empresa = get_empresa_escopo(request)
    if not empresa:
        messages.error(request, "Nenhuma empresa ativa.")
        return redirect('listar_fundos')
    form = GestoraForm(request.POST, prefix='gestora')
    if form.is_valid():
        gestora = form.save(commit=False)
        gestora.empresa = empresa
        try:
            gestora.save()
            messages.success(request, f"Gestora '{gestora.nome}' criada com sucesso.")
        except Exception:
            messages.error(request, "Erro ao criar gestora. Verifique se CNPJ ou nome já estão cadastrados.")
    else:
        messages.error(request, "Erro ao criar gestora. Verifique os campos informados.")
    from django.urls import reverse
    return redirect(reverse('listar_fundos') + '?tab=gestoras')


@login_required
@company_can_manage_fundos
def editar_gestora(request, gestora_id):
    empresa = get_empresa_escopo(request)
    gestora = get_object_or_404(Gestora, id=gestora_id, empresa=empresa)
    if request.method == "POST":
        form = GestoraForm(request.POST, instance=gestora)
        if form.is_valid():
            form.save()
            messages.success(request, f"Gestora '{gestora.nome}' atualizada com sucesso.")
        else:
            messages.error(request, "Erro ao atualizar gestora. Verifique os campos informados.")
    from django.urls import reverse
    return redirect(reverse('listar_fundos') + '?tab=gestoras')


@login_required
@company_can_manage_fundos
def excluir_gestora(request, gestora_id):
    empresa = get_empresa_escopo(request)
    gestora = get_object_or_404(Gestora, id=gestora_id, empresa=empresa)
    if request.method == "POST":
        nome = gestora.nome
        gestora.delete()
        messages.success(request, f"Gestora '{nome}' excluída com sucesso.")
        from django.urls import reverse
        return redirect(reverse('listar_fundos') + '?tab=gestoras')
    fundos_vinculados = list(gestora.fundos.all())
    return render(request, "gestoras/confirmar_exclusao.html", {
        "gestora": gestora,
        "fundos_vinculados": fundos_vinculados,
    })


# ===========================
# Gerenciamento de Períodos de DF
# ===========================
@login_required
@company_can_view_data
def gerenciar_periodos(request, fundo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)

    periodos_qs = PeriodoDF.objects.filter(fundo=fundo).order_by("-ano", "tipo_periodo")

    # Filtros opcionais por URL params
    ano_filtro = request.GET.get("ano")
    status_filtro = request.GET.get("status")
    if ano_filtro:
        periodos_qs = periodos_qs.filter(ano=ano_filtro)
    if status_filtro:
        periodos_qs = periodos_qs.filter(status=status_filtro)

    periodos_info = []
    for periodo in periodos_qs:
        status_info = calcular_status_periodo(periodo)
        dias = status_info["dias_ate_vencimento"]
        status_info["dias_vencida"] = abs(dias) if dias < 0 else 0
        status_info["n_balancete"] = periodo.balancete_items.count()
        status_info["n_mec"] = periodo.mec_items.count()
        status_info["status"] = periodo.status  # usa o status salvo, não o calculado
        periodos_info.append({"periodo": periodo, **status_info})

    anos_disponiveis = (
        PeriodoDF.objects.filter(fundo=fundo)
        .values_list("ano", flat=True)
        .distinct()
        .order_by("-ano")
    )

    form_manual = PeriodoDFManualForm(fundo=fundo)

    return render(request, "periodos/gerenciar.html", {
        "fundo": fundo,
        "periodos_info": periodos_info,
        "anos_disponiveis": anos_disponiveis,
        "ano_filtro": ano_filtro,
        "status_filtro": status_filtro,
        "form_manual": form_manual,
        "can_manage_fundos": _can_manage_fundos(request),
        "ano_atual": date.today().year,
        "ano_inicial_sugerido": date.today().year - 5,
    })


@login_required
@company_can_manage_fundos
def criar_periodo_manual(request, fundo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)

    if request.method == "POST":
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        form = PeriodoDFManualForm(request.POST, fundo=fundo)
        if form.is_valid():
            periodo = form.save()
            from df.services.checklist_service import criar_checklist_para_periodo
            criar_checklist_para_periodo(periodo)
            if is_ajax:
                return JsonResponse({'ok': True, 'message': f'Período {periodo.nome_exibicao} criado com sucesso.'})
            messages.success(request, "Período criado com sucesso.")
        else:
            erros = '; '.join(e for errs in form.errors.values() for e in errs)
            if is_ajax:
                return JsonResponse({'ok': False, 'error': erros}, status=400)
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

    return redirect("gerenciar_periodos", fundo_id=fundo_id)


# ===========================
# Excluir Período de DF
# ===========================
@login_required
@company_can_manage_fundos
def excluir_periodo(request, fundo_id, periodo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)
    periodo = get_object_or_404(PeriodoDF, id=periodo_id, fundo=fundo)

    if request.method != "POST":
        return redirect("gerenciar_periodos", fundo_id=fundo_id)

    tem_balancete = periodo.balancete_items.exists()
    tem_mec = periodo.mec_items.exists()
    tem_dados = tem_balancete or tem_mec

    if tem_dados and not request.POST.get("confirmar_exclusao"):
        messages.error(request, "Para excluir um período com dados importados, confirme a exclusão marcando a caixa de verificação.")
        return redirect("gerenciar_periodos", fundo_id=fundo_id)

    nome = periodo.nome_exibicao
    n_balancete = periodo.balancete_items.count() if tem_balancete else 0
    n_mec = periodo.mec_items.count() if tem_mec else 0
    try:
        periodo.delete()
    except ProtectedError:
        messages.error(
            request,
            f"O período '{nome}' possui documentos anexados e não pode ser excluído. "
            "Peça ao administrador da plataforma para expurgar os documentos primeiro.",
        )
        return redirect("gerenciar_periodos", fundo_id=fundo_id)

    if tem_dados:
        messages.warning(request, f"Período '{nome}' excluído junto com {n_balancete} registro(s) de balancete e {n_mec} registro(s) de MEC.")
    else:
        messages.success(request, f"Período '{nome}' excluído com sucesso.")

    return redirect("gerenciar_periodos", fundo_id=fundo_id)


# ===========================
# Gerar Períodos Históricos
# ===========================
@login_required
@company_can_manage_fundos
def gerar_periodos_historicos(request, fundo_id):
    qs = query_por_empresa_ativa(Fundo.objects.all(), request, "empresa")
    fundo = get_object_or_404(qs, id=fundo_id)

    if request.method != "POST":
        return redirect("gerenciar_periodos", fundo_id=fundo_id)

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _err(msg):
        if is_ajax:
            return JsonResponse({'ok': False, 'error': msg}, status=400)
        messages.error(request, msg)
        return redirect("gerenciar_periodos", fundo_id=fundo_id)

    try:
        ano_inicial = int(request.POST.get("ano_inicial", 0))
        ano_final = int(request.POST.get("ano_final", 0))
    except (ValueError, TypeError):
        return _err("Informe anos válidos.")

    ano_atual = date.today().year
    if ano_inicial < 1990 or ano_final > ano_atual or ano_inicial > ano_final:
        return _err(f"Intervalo de anos inválido. Use entre 1990 e {ano_atual}.")

    if not fundo.configuracoes_df.exists():
        return _err("Este fundo não possui configuração de DF Anual.")

    from df.services.periodo_service import gerar_periodos_para_anos
    resultado = gerar_periodos_para_anos(fundo, ano_inicial, ano_final)

    # Corrige imediatamente os períodos gerados que já estejam vencidos
    PeriodoDF.objects.filter(
        fundo=fundo,
        status='nao_iniciada',
        data_vencimento__lt=date.today(),
    ).update(status='vencida')

    total = resultado["total"]
    if total == 0:
        msg = "Nenhum período novo criado — todos os períodos desse intervalo já existiam."
        if is_ajax:
            return JsonResponse({'ok': True, 'message': msg})
        messages.info(request, msg)
    else:
        msg = f"{total} período(s) anual(is) criado(s) para {ano_inicial}–{ano_final}."
        if is_ajax:
            return JsonResponse({'ok': True, 'message': msg})
        messages.success(request, msg)

    return redirect("gerenciar_periodos", fundo_id=fundo_id)


# ===========================
# Perfil do usuário
# ===========================
@login_required
def editar_perfil(request):
    user = request.user

    if request.method == "POST":
        form = EditarPerfilForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save()
            messages.success(request, "Perfil atualizado com sucesso!")

            # Se a senha foi alterada, manter o usuário logado
            if form.cleaned_data.get('password1'):
                update_session_auth_hash(request, user)

            return redirect("editar_perfil")
        else:
            messages.error(request, "Por favor, corrija os erros abaixo.")
    else:
        form = EditarPerfilForm(instance=user)

    return render(request, "editar_perfil.html", {'form': form})


# ===========================
# CHECKLIST DE DOCUMENTOS
# ===========================

def _checklist_summary(periodo):
    total = periodo.checklist_items.count()
    recebidos = periodo.checklist_items.filter(recebido=True).count()
    return {'total': total, 'recebidos': recebidos}




def _documento_json(d):
    return {
        'id': d.id,
        'nome': d.nome_original,
        'tamanho': d.tamanho_bytes,
        'enviado_em': d.enviado_em.isoformat(),
        'enviado_por': str(d.enviado_por) if d.enviado_por else '—',
        'contem_dados_pessoais': d.contem_dados_pessoais,
    }


@login_required
@company_can_view_data
def api_checklist_periodo(request, periodo_id):
    if request.method != 'GET':
        return JsonResponse({'ok': False}, status=405)

    empresa = get_empresa_escopo(request)
    periodo = get_object_or_404(PeriodoDF, id=periodo_id, empresa=empresa)
    itens = list(ChecklistItemPeriodo.objects.filter(periodo_df=periodo).order_by('ordem'))

    # Uma query só, agrupada em memória — nunca uma query por item (seriam
    # ~38 queries por abertura de modal).
    documentos = (
        DocumentoDF.objects
        .filter(periodo_df=periodo, excluido_em__isnull=True)
        .select_related('enviado_por')
        .order_by('-enviado_em')
    )
    docs_por_item = defaultdict(list)
    docs_avulsos = []
    for d in documentos:
        if d.checklist_item_id:
            docs_por_item[d.checklist_item_id].append(_documento_json(d))
        else:
            docs_avulsos.append(_documento_json(d))

    items_data = [
        {
            'id': i.id,
            'secao': i.secao,
            'texto': i.texto,
            'prazo': i.prazo,
            'responsavel': i.responsavel,
            'ordem': i.ordem,
            'recebido': i.recebido,
            'documentos': docs_por_item.get(i.id, []),
        }
        for i in itens
    ]
    summary = {'total': len(itens), 'recebidos': sum(1 for i in itens if i.recebido)}
    return JsonResponse({
        'ok': True,
        'items': items_data,
        'summary': summary,
        'periodo_nome': periodo.nome_exibicao,
        'documentos_avulsos': docs_avulsos,
        'pode_baixar': pode_baixar_documentos(request.user, empresa),
        'pode_anexar': _pode_anexar_documentos(request),
        'periodo_finalizado': periodo.status == 'finalizada',
    })


@login_required
@company_can_manage_fundos
def adicionar_item_checklist(request, periodo_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    periodo = get_object_or_404(PeriodoDF, id=periodo_id, empresa=empresa)
    texto = request.POST.get('texto', '').strip()
    if not texto or len(texto) > 255:
        return JsonResponse({'ok': False, 'error': 'Texto inválido.'}, status=400)
    secao = request.POST.get('secao', '').strip()[:120]
    prazo = request.POST.get('prazo', '').strip()[:120]
    responsavel = request.POST.get('responsavel', '').strip()[:120]

    from django.db.models import Max
    max_ordem = ChecklistItemPeriodo.objects.filter(periodo_df=periodo).aggregate(m=Max('ordem'))['m'] or 0
    item = ChecklistItemPeriodo.objects.create(
        periodo_df=periodo, secao=secao, texto=texto,
        prazo=prazo, responsavel=responsavel, ordem=max_ordem + 1,
    )
    return JsonResponse({
        'ok': True,
        'item': {
            'id': item.id, 'secao': item.secao, 'texto': item.texto,
            'prazo': item.prazo, 'responsavel': item.responsavel,
            'ordem': item.ordem, 'recebido': item.recebido,
        },
        'summary': _checklist_summary(periodo),
    })


@login_required
@company_can_manage_fundos
def toggle_item_checklist(request, item_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    item = get_object_or_404(ChecklistItemPeriodo, id=item_id, periodo_df__empresa=empresa)
    item.recebido = not item.recebido
    item.save(update_fields=['recebido'])
    return JsonResponse({
        'ok': True,
        'recebido': item.recebido,
        'item_id': item.id,
        'summary': _checklist_summary(item.periodo_df),
    })


@login_required
@company_can_manage_fundos
def editar_item_checklist(request, item_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    item = get_object_or_404(ChecklistItemPeriodo, id=item_id, periodo_df__empresa=empresa)
    texto = request.POST.get('texto', '').strip()
    if not texto or len(texto) > 255:
        return JsonResponse({'ok': False, 'error': 'Texto inválido.'}, status=400)

    item.texto = texto
    item.secao = request.POST.get('secao', item.secao).strip()[:120]
    item.prazo = request.POST.get('prazo', item.prazo).strip()[:120]
    item.responsavel = request.POST.get('responsavel', item.responsavel).strip()[:120]
    item.save(update_fields=['texto', 'secao', 'prazo', 'responsavel'])
    return JsonResponse({'ok': True, 'item': {
        'id': item.id, 'secao': item.secao, 'texto': item.texto,
        'prazo': item.prazo, 'responsavel': item.responsavel, 'recebido': item.recebido,
    }})


@login_required
@company_can_manage_fundos
def excluir_item_checklist(request, item_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    item = get_object_or_404(ChecklistItemPeriodo, id=item_id, periodo_df__empresa=empresa)
    periodo = item.periodo_df
    item.delete()
    return JsonResponse({'ok': True, 'summary': _checklist_summary(periodo)})


# ===========================
# DOCUMENTOS ANEXADOS (upload/download/exclusão)
# ===========================

@login_required
@company_can_manage_fundos
def upload_documento_periodo(request, periodo_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    periodo = get_object_or_404(PeriodoDF, id=periodo_id, empresa=empresa)

    if not empresa.documentos_habilitados:
        return JsonResponse({
            'ok': False,
            'error': 'Upload de documentos não habilitado para esta empresa.',
        }, status=403)

    arquivo = request.FILES.get('arquivo')
    if not arquivo:
        return JsonResponse({'ok': False, 'error': 'Nenhum arquivo enviado.'}, status=400)

    try:
        _ext, sha256 = validar_documento_upload(arquivo)
    except ValidationError as e:
        return JsonResponse({'ok': False, 'error': '; '.join(e.messages)}, status=400)

    if empresa.quota_documentos_gb:
        limite_bytes = empresa.quota_documentos_gb * 1024 ** 3
        uso_atual = uso_documentos_bytes(empresa)
        if uso_atual + arquivo.size > limite_bytes:
            return JsonResponse({
                'ok': False,
                'error': (
                    f'Quota de armazenamento excedida: '
                    f'{uso_atual / 1024 ** 3:.1f} GB usados de {empresa.quota_documentos_gb} GB.'
                ),
            }, status=400)

    checklist_item = None
    checklist_item_id = request.POST.get('checklist_item_id')
    if checklist_item_id:
        checklist_item = get_object_or_404(ChecklistItemPeriodo, id=checklist_item_id, periodo_df=periodo)

    texto_referencia = checklist_item.texto if checklist_item else ''
    contem_dados_pessoais = sugerir_contem_dados_pessoais(texto_referencia)
    if 'contem_dados_pessoais' in request.POST:
        contem_dados_pessoais = request.POST.get('contem_dados_pessoais') in ('1', 'true', 'on', 'True')

    documento = DocumentoDF.objects.create(
        empresa=empresa,
        periodo_df=periodo,
        checklist_item=checklist_item,
        checklist_texto=texto_referencia,
        arquivo=arquivo,
        nome_original=arquivo.name[:255],
        tamanho_bytes=arquivo.size,
        content_type=getattr(arquivo, 'content_type', '') or '',
        sha256=sha256,
        retencao_ate=calcular_retencao_ate(periodo, empresa),
        contem_dados_pessoais=contem_dados_pessoais,
        enviado_por=request.user,
    )
    registrar_acesso(documento, request, LogAcessoDocumento.Acao.UPLOAD)

    return JsonResponse({
        'ok': True,
        'documento': _documento_json(documento),
        'checklist_item_id': documento.checklist_item_id,
    })


@login_required
def download_documento(request, documento_id):
    # Não é AJAX — é navegação normal (window.location), então não exige o
    # header X-Requested-With como as demais views desta seção.
    empresa = get_empresa_escopo(request)
    if not empresa:
        return HttpResponseForbidden("Nenhuma empresa selecionada.")

    documento = resolver_documento_escopo(documento_id, empresa, request)
    if documento is None:
        return HttpResponseForbidden("Você não tem permissão para acessar este documento.")

    if documento.excluido_em:
        return HttpResponseForbidden("Este documento foi excluído.")

    if not pode_baixar_documentos(request.user, empresa):
        registrar_acesso(documento, request, LogAcessoDocumento.Acao.NEGADO)
        return HttpResponseForbidden("Seu papel nesta empresa não permite baixar documentos.")

    registrar_acesso(documento, request, LogAcessoDocumento.Acao.DOWNLOAD)
    return resposta_download(documento)


@login_required
@company_can_manage_fundos
def excluir_documento(request, documento_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'ok': False}, status=400)

    empresa = get_empresa_escopo(request)
    documento = resolver_documento_escopo(documento_id, empresa, request)
    if documento is None:
        return JsonResponse({'ok': False, 'error': 'Você não tem permissão para excluir este documento.'}, status=403)

    if documento.excluido_em:
        return JsonResponse({'ok': False, 'error': 'Documento já foi excluído.'}, status=400)

    if documento.periodo_df.status == 'finalizada':
        return JsonResponse({
            'ok': False,
            'error': 'Período finalizado — exclusão de documentos bloqueada.',
        }, status=403)

    from django.utils import timezone
    documento.excluido_em = timezone.now()
    documento.excluido_por = request.user
    documento.save(update_fields=['excluido_em', 'excluido_por'])
    registrar_acesso(documento, request, LogAcessoDocumento.Acao.EXCLUSAO)

    return JsonResponse({'ok': True})


# ===========================
# PAINEL ADMINISTRATIVO DE DOCUMENTOS (Fase 5 — só Global Admin)
# ===========================

@login_required
@platform_admin_required
def painel_documentos(request):
    """Visão de operador da plataforma sobre uso de armazenamento por empresa.

    Não escopado por empresa_ativa como o resto do sistema — é justamente o
    contrário: uma única tela vendo todas as empresas de uma vez, por isso
    não usa get_empresa_escopo/query_por_empresa_ativa.
    """
    empresas = []
    uso_total_bytes = 0
    documentos_ativos_total = 0
    documentos_pii_total = 0
    empresas_habilitadas_count = 0

    for empresa in estatisticas_por_empresa():
        uso_bytes = empresa.uso_bytes or 0
        documentos_ativos = empresa.documentos_ativos or 0
        documentos_pii = empresa.documentos_pii or 0
        quota_bytes = empresa.quota_documentos_gb * 1024 ** 3
        pct_uso = (uso_bytes / quota_bytes * 100) if quota_bytes else None

        uso_total_bytes += uso_bytes
        documentos_ativos_total += documentos_ativos
        documentos_pii_total += documentos_pii
        if empresa.documentos_habilitados:
            empresas_habilitadas_count += 1

        empresas.append({
            'empresa': empresa,
            'uso_bytes': uso_bytes,
            'documentos_ativos': documentos_ativos,
            'documentos_pii': documentos_pii,
            'pct_uso': pct_uso,
        })

    # Gatilho de migração para S3 sugerido no plano (§5): ~100 GB de disco total.
    limite_alerta_global_bytes = 100 * 1024 ** 3

    # Fase 5.6 — gráfico de crescimento: disco físico real (não filtra
    # excluído — ver docstring de serie_crescimento_mensal).
    crescimento_labels, crescimento_total_gb, crescimento_series = serie_crescimento_mensal()
    crescimento_data = json.dumps({
        'labels': crescimento_labels,
        'total_gb': crescimento_total_gb,
        'series': crescimento_series,
        'gatilho_gb': 100,
    })

    return render(request, 'painel_documentos/index.html', {
        'empresas': empresas,
        'uso_total_bytes': uso_total_bytes,
        'documentos_ativos_total': documentos_ativos_total,
        'documentos_pii_total': documentos_pii_total,
        'empresas_habilitadas_count': empresas_habilitadas_count,
        'disco_perto_do_limite': uso_total_bytes >= limite_alerta_global_bytes * 0.8,
        'crescimento_data': crescimento_data,
        'crescimento_tem_dados': bool(crescimento_labels),
    })


@login_required
@platform_admin_required
def painel_documentos_empresa(request, empresa_id):
    """Detalhe de uma empresa no painel: lista de documentos ativos (com
    filtro de PII), log de acesso filtrável por ação, e config rápida
    (documentos_habilitados/retencao_anos/quota_documentos_gb), sem precisar
    do Django /admin."""
    empresa = get_object_or_404(Empresa, id=empresa_id)

    # ---- Documentos (aba 1) — relatório de PII é este mesmo filtro (§6, item 7) ----
    somente_pii = request.GET.get('pii') == '1'
    documentos_qs = (
        DocumentoDF.objects
        .filter(empresa=empresa, excluido_em__isnull=True)
        .select_related('enviado_por', 'periodo_df__fundo')
        .order_by('-enviado_em')
    )
    if somente_pii:
        documentos_qs = documentos_qs.filter(contem_dados_pessoais=True)
    doc_paginator = Paginator(documentos_qs, 25)
    doc_page_obj = doc_paginator.get_page(request.GET.get('doc_page'))

    # ---- Log de acesso (aba 2) — append-only, só leitura aqui ----
    log_acao_filtro = request.GET.get('log_acao', '')
    logs_qs = (
        LogAcessoDocumento.objects
        .filter(empresa=empresa)
        .select_related('usuario', 'documento')
        .order_by('-ocorrido_em')
    )
    if log_acao_filtro:
        logs_qs = logs_qs.filter(acao=log_acao_filtro)
    log_paginator = Paginator(logs_qs, 25)
    log_page_obj = log_paginator.get_page(request.GET.get('log_page'))

    # ---- Vencidos (aba 3) — retenção expirada, ativos ou já excluídos ----
    # Documento pode ter passado do prazo sem NUNCA ter sido excluído por
    # ninguém — por isso não filtra excluido_em aqui, ao contrário da aba 1.
    vencidos_qs = (
        DocumentoDF.objects
        .filter(empresa=empresa, retencao_ate__lt=date.today())
        .select_related('enviado_por', 'periodo_df__fundo')
        .order_by('retencao_ate')
    )
    vencidos_paginator = Paginator(vencidos_qs, 25)
    vencidos_page_obj = vencidos_paginator.get_page(request.GET.get('vencidos_page'))

    # Qual aba fica ativa ao recarregar a página — se o link veio de um
    # filtro/página de outra aba, reabre nela em vez de voltar pra padrão.
    if request.GET.get('vencidos') == '1' or request.GET.get('vencidos_page'):
        tab_ativa = 'vencidos'
    elif request.GET.get('log_page') or log_acao_filtro:
        tab_ativa = 'log'
    else:
        tab_ativa = 'documentos'

    uso_bytes = uso_documentos_bytes(empresa)
    quota_bytes = empresa.quota_documentos_gb * 1024 ** 3
    pct_uso = (uso_bytes / quota_bytes * 100) if quota_bytes else None

    config_form = EmpresaDocumentosConfigForm(instance=empresa)

    return render(request, 'painel_documentos/empresa.html', {
        'empresa': empresa,
        'page_obj': doc_page_obj,
        'somente_pii': somente_pii,
        'log_page_obj': log_page_obj,
        'log_acao_filtro': log_acao_filtro,
        'log_acoes': LogAcessoDocumento.Acao.choices,
        'vencidos_page_obj': vencidos_page_obj,
        'tab_ativa': tab_ativa,
        'uso_bytes': uso_bytes,
        'pct_uso': pct_uso,
        'config_form': config_form,
    })


@login_required
@platform_admin_required
def expurgar_documento(request, documento_id):
    """Apaga fisicamente o arquivo e o registro do documento. Só chega aqui
    por um clique explícito com confirm() por documento — nunca em lote,
    nunca automático (risco nº 12 do plano). O log de acesso sobrevive: o FK
    documento vira NULL (SET_NULL) mas documento_id_hist preserva o rastro."""
    if request.method != 'POST':
        return HttpResponseForbidden()

    documento = get_object_or_404(DocumentoDF, id=documento_id)
    empresa_id = documento.empresa_id
    nome = documento.nome_original

    from django.urls import reverse
    destino = reverse('painel_documentos_empresa', args=[empresa_id]) + '?vencidos=1'

    # Arquivo primeiro: se o storage (ex.: S3) falhar, nada mais é feito — o registro
    # e o log ficam como estavam, em vez de o registro sumir com o arquivo ainda no
    # bucket (PII órfã sem ninguém apontando pra ela). Apagar um arquivo que já não
    # existe não dá erro, então repetir o expurgo é seguro.
    try:
        documento.arquivo.delete(save=False)
    except Exception:
        logger.exception("Falha ao apagar do storage o arquivo do documento #%s (empresa %s)", documento_id, empresa_id)
        messages.error(
            request,
            f'Não foi possível apagar o arquivo de "{nome}" do armazenamento. '
            'Nada foi removido; tente novamente.',
        )
        return redirect(destino)

    with transaction.atomic():
        registrar_acesso(documento, request, LogAcessoDocumento.Acao.EXPURGO)
        documento.delete()

    messages.success(request, f'Documento "{nome}" expurgado definitivamente.')
    return redirect(destino)


@login_required
@platform_admin_required
def painel_documentos_config(request, empresa_id):
    if request.method != 'POST':
        return HttpResponseForbidden()

    empresa = get_object_or_404(Empresa, id=empresa_id)
    form = EmpresaDocumentosConfigForm(request.POST, instance=empresa)
    if form.is_valid():
        form.save()
        messages.success(request, 'Configuração de documentos atualizada.')
    else:
        messages.error(request, 'Erro ao atualizar configuração. Verifique os campos informados.')
    return redirect('painel_documentos_empresa', empresa_id=empresa.id)
