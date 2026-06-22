"""
Service para calcular o status de emissão das Demonstrações Financeiras dos fundos.
"""
from datetime import date
from django.db.models import Max, Exists, OuterRef
from df.models import Fundo, BalanceteItem, MecItem, HistoricoEmissaoDF, PeriodoDF


def calcular_status_fundo(fundo):
    """
    Calcula o status de emissão da DF de um fundo (modo legado - por ano).
    
    Retorna um dicionário com:
    - status: 'nao_iniciada' | 'em_andamento' | 'emitida'
    - ultima_emissao_data: datetime ou None
    - tem_dados_importados: bool
    - ultima_data_balancete: date ou None
    - ultima_data_mec: date ou None
    """
    ano_atual = date.today().year
    
    # Verificar se tem balancete ou MEC importado no ano atual
    tem_balancete = BalanceteItem.objects.filter(
        fundo=fundo,
        data_referencia__year=ano_atual
    ).exists()
    
    tem_mec = MecItem.objects.filter(
        fundo=fundo,
        data_posicao__year=ano_atual
    ).exists()
    
    tem_dados_importados = tem_balancete or tem_mec
    
    # Pegar última data de cada
    ultima_data_balancete = BalanceteItem.objects.filter(
        fundo=fundo
    ).aggregate(Max('data_referencia'))['data_referencia__max']
    
    ultima_data_mec = MecItem.objects.filter(
        fundo=fundo
    ).aggregate(Max('data_posicao'))['data_posicao__max']
    
    # Verificar se tem emissão registrada no ano atual
    ultima_emissao = HistoricoEmissaoDF.objects.filter(
        fundo=fundo,
        data_emissao__year=ano_atual
    ).order_by('-data_emissao').first()
    
    # Determinar status
    if not tem_dados_importados and not ultima_emissao:
        status = 'nao_iniciada'
    elif tem_dados_importados and not ultima_emissao:
        status = 'em_andamento'
    else:
        status = 'emitida'
    
    return {
        'status': status,
        'ultima_emissao': ultima_emissao,
        'ultima_emissao_data': ultima_emissao.data_emissao if ultima_emissao else None,
        'tem_dados_importados': tem_dados_importados,
        'ultima_data_balancete': ultima_data_balancete,
        'ultima_data_mec': ultima_data_mec,
    }


def obter_status_todos_fundos(fundos_queryset):
    """
    Calcula o status para uma lista de fundos (modo legado).
    Retorna lista de dicts com informações do fundo + status.
    """
    resultado = []
    
    for fundo in fundos_queryset:
        status_info = calcular_status_fundo(fundo)
        resultado.append({
            'fundo': fundo,
            **status_info
        })
    
    return resultado


# ========================================
# FUNÇÕES NOVAS - BASEADAS EM PERÍODOS
# ========================================

def calcular_status_periodo(periodo_df):
    """
    Calcula o status de um período de DF específico.
    
    Retorna um dicionário com:
    - status: 'nao_iniciada' | 'em_andamento' | 'finalizada' | 'vencida'
    - tem_dados: bool (tem balancete ou MEC vinculado ao período)
    - tem_emissao: bool (tem registro no histórico de emissão)
    - ultima_emissao: HistoricoEmissaoDF ou None
    - dias_ate_vencimento: int (negativo se vencida)
    """
    hoje = date.today()
    
    # Verificar se tem dados importados vinculados a este período
    tem_balancete = BalanceteItem.objects.filter(periodo_df=periodo_df).exists()
    tem_mec = MecItem.objects.filter(periodo_df=periodo_df).exists()
    tem_dados = tem_balancete or tem_mec
    
    # Verificar se tem emissão registrada para este período
    ultima_emissao = HistoricoEmissaoDF.objects.filter(
        periodo_df=periodo_df
    ).order_by('-data_emissao').first()
    tem_emissao = ultima_emissao is not None
    
    # Calcular dias até vencimento
    dias_ate_vencimento = (periodo_df.data_vencimento - hoje).days
    
    # Determinar status
    if tem_emissao:
        status = 'finalizada'
    elif tem_dados:
        status = 'em_andamento'
    elif periodo_df.data_vencimento < hoje:
        status = 'vencida'
    else:
        status = 'nao_iniciada'
    
    return {
        'status': status,
        'tem_dados': tem_dados,
        'tem_emissao': tem_emissao,
        'ultima_emissao': ultima_emissao,
        'ultima_emissao_data': ultima_emissao.data_emissao if ultima_emissao else None,
        'dias_ate_vencimento': dias_ate_vencimento,
        'vencido': dias_ate_vencimento < 0,
    }


def atualizar_status_periodo(periodo_df):
    """
    Atualiza o campo status de um PeriodoDF baseado nas condições atuais.
    
    Args:
        periodo_df: Instância de PeriodoDF
    
    Returns:
        periodo_df atualizado com novo status
    """
    status_info = calcular_status_periodo(periodo_df)
    novo_status = status_info['status']
    
    if periodo_df.status != novo_status:
        periodo_df.status = novo_status
        periodo_df.save(update_fields=['status', 'atualizado_em'])
    
    return periodo_df


def obter_periodos_com_status(fundo, ano=None):
    """
    Retorna todos os períodos de um fundo com status calculado.
    
    Args:
        fundo: Instância do Fundo
        ano: Filtrar por ano específico (opcional)
    
    Returns:
        Lista de dicts com período + informações de status
    """
    queryset = PeriodoDF.objects.filter(fundo=fundo)
    
    if ano is not None:
        queryset = queryset.filter(ano=ano)
    
    queryset = queryset.order_by('-ano', 'tipo_periodo')
    
    resultado = []
    for periodo in queryset:
        status_info = calcular_status_periodo(periodo)
        resultado.append({
            'periodo': periodo,
            **status_info
        })
    
    return resultado


def obter_todos_periodos_com_status(empresa, ano=None):
    """
    Retorna todos os períodos de uma empresa com status calculado.
    Útil para dashboards de controle de emissões.
    
    Args:
        empresa: Instância da Empresa
        ano: Filtrar por ano específico (opcional)
    
    Returns:
        Lista de dicts com período + informações de status
    """
    queryset = PeriodoDF.objects.filter(empresa=empresa)
    
    if ano is not None:
        queryset = queryset.filter(ano=ano)
    
    queryset = queryset.order_by('-ano', 'fundo__nome', 'tipo_periodo')
    
    resultado = []
    for periodo in queryset:
        status_info = calcular_status_periodo(periodo)
        resultado.append({
            'periodo': periodo,
            'fundo': periodo.fundo,
            **status_info
        })
    
    return resultado


def obter_metricas_periodos(empresa, ano=None):
    """
    Calcula métricas agregadas de períodos para dashboards.
    
    Args:
        empresa: Instância da Empresa
        ano: Filtrar por ano específico (opcional)
    
    Returns:
        Dict com métricas agregadas
    """
    periodos = obter_todos_periodos_com_status(empresa, ano)
    
    total = len(periodos)
    nao_iniciadas = sum(1 for p in periodos if p['status'] == 'nao_iniciada')
    em_andamento = sum(1 for p in periodos if p['status'] == 'em_andamento')
    finalizadas = sum(1 for p in periodos if p['status'] == 'finalizada')
    vencidas = sum(1 for p in periodos if p['status'] == 'vencida')
    
    # Vencimentos próximos (30 dias)
    vencimentos_proximos = sum(1 for p in periodos if 0 <= p['dias_ate_vencimento'] <= 30 and p['status'] not in ['finalizada', 'vencida'])
    
    # Agrupar por tipo de período
    por_tipo = {}
    for p in periodos:
        tipo = p['periodo'].tipo_periodo
        if tipo not in por_tipo:
            por_tipo[tipo] = 0
        por_tipo[tipo] += 1
    
    return {
        'total': total,
        'nao_iniciadas': nao_iniciadas,
        'em_andamento': em_andamento,
        'finalizadas': finalizadas,
        'vencidas': vencidas,
        'vencimentos_proximos': vencimentos_proximos,
        'por_tipo': por_tipo,
    }

