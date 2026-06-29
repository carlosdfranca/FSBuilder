"""
Serviço para gerenciamento de períodos de DF.
Responsável por gerar períodos anuais automaticamente
e auxiliar na criação manual (Transitória/Encerramento).
"""
from datetime import date
from django.db import transaction
from df.models import PeriodoDF, Fundo


def _vencimento_apos_fim(ano: int, mes_config: int, dia_config: int, fim_periodo: date) -> date:
    """
    Retorna a data de vencimento correta para um período.

    O vencimento configurado (dia+mês) representa um prazo que sempre cai
    DEPOIS do fim do período — se date(ano, mes, dia) ainda não passou do fim,
    o vencimento é no ano seguinte.
    """
    try:
        candidato = date(ano, mes_config, dia_config)
    except ValueError:
        candidato = None

    if candidato and candidato > fim_periodo:
        return candidato

    return date(ano + 1, mes_config, dia_config)


def gerar_periodos_anuais(fundo, ano_inicial, ano_final=None):
    """
    Gera períodos anuais para um fundo baseado na sua configuração.
    
    Args:
        fundo: Instância do modelo Fundo
        ano_inicial: Ano inicial (int)
        ano_final: Ano final (int), se None usa apenas ano_inicial
    
    Returns:
        Lista de PeriodoDF criados
    """
    if ano_final is None:
        ano_final = ano_inicial
    
    config = fundo.configuracoes_df.first()
    if not config:
        return []

    if config.anual_dia is None or config.anual_mes is None:
        return []
    
    periodos_criados = []
    
    for ano in range(ano_inicial, ano_final + 1):
        # Período anual sempre termina em 31/12
        fim_periodo = date(ano, 12, 31)

        try:
            data_vencimento = _vencimento_apos_fim(ano, config.anual_mes, config.anual_dia, fim_periodo)
        except ValueError:
            continue

        periodo, created = PeriodoDF.objects.get_or_create(
            fundo=fundo,
            empresa=fundo.empresa,
            tipo_periodo='anual',
            ano=ano,
            defaults={
                'data_vencimento': data_vencimento,
                'status': 'nao_iniciada',
                'criado_manualmente': False,
            }
        )

        if created:
            periodos_criados.append(periodo)
            from df.services.checklist_service import criar_checklist_para_periodo
            criar_checklist_para_periodo(periodo)

    return periodos_criados


def gerar_periodos_para_ano(fundo, ano):
    """
    Gera o período anual para um fundo em um ano específico.

    Returns:
        Dict com lista de períodos criados por tipo
    """
    return {
        'anual': gerar_periodos_anuais(fundo, ano),
    }


def gerar_periodos_para_anos(fundo, ano_inicial, ano_final):
    """
    Gera períodos anuais para um fundo em um intervalo de anos.

    Returns:
        Dict com contadores de períodos criados
    """
    total_anual = gerar_periodos_anuais(fundo, ano_inicial, ano_final)
    return {
        'anual': len(total_anual),
        'total': len(total_anual),
    }


def obter_ou_criar_periodo(fundo, tipo_periodo, ano, data_vencimento=None, descricao=None):
    """
    Obtém ou cria um período de DF de forma idempotente.
    
    Args:
        fundo: Instância do modelo Fundo
        tipo_periodo: 'anual', 'transitoria', 'encerramento'
        ano: Ano (int)
        data_vencimento: Data de vencimento (obrigatório para tipos manuais)
        descricao: Descrição opcional para tipos manuais
    
    Returns:
        Tupla (periodo, created)
    """
    if tipo_periodo in ['transitoria', 'encerramento']:
        if data_vencimento is None:
            raise ValueError(f"data_vencimento é obrigatório para tipo {tipo_periodo}")

        periodo = PeriodoDF.objects.create(
            fundo=fundo,
            empresa=fundo.empresa,
            tipo_periodo=tipo_periodo,
            ano=ano,
            data_vencimento=data_vencimento,
            status='nao_iniciada',
            criado_manualmente=True,
            descricao=descricao,
        )
        return periodo, True

    elif tipo_periodo == 'anual':
        config = fundo.configuracoes_df.first()
        if not config:
            raise ValueError(f"Fundo {fundo.nome} não possui configuração de DF Anual")

        if config.anual_dia is None or config.anual_mes is None:
            raise ValueError("DF Anual não configurada para este fundo")

        data_vencimento = _vencimento_apos_fim(ano, config.anual_mes, config.anual_dia, date(ano, 12, 31))
        
        periodo, created = PeriodoDF.objects.get_or_create(
            fundo=fundo,
            empresa=fundo.empresa,
            tipo_periodo='anual',
            ano=ano,
            defaults={
                'data_vencimento': data_vencimento,
                'status': 'nao_iniciada',
                'criado_manualmente': False,
            }
        )
        return periodo, created


def obter_periodo_anterior(periodo_df):
    """
    Retorna o período anterior na sequência para um dado período.
    
    Args:
        periodo_df: Instância de PeriodoDF
    
    Returns:
        PeriodoDF ou None se não houver período anterior
    """
    if periodo_df.tipo_periodo == 'anual':
        return PeriodoDF.objects.filter(
            fundo=periodo_df.fundo,
            tipo_periodo='anual',
            ano=periodo_df.ano - 1
        ).first()

    else:
        # Para transitória/encerramento, buscar o período finalizado mais recente
        return PeriodoDF.objects.filter(
            fundo=periodo_df.fundo,
            status='finalizada',
            criado_em__lt=periodo_df.criado_em
        ).order_by('-criado_em').first()


def copiar_saldo_de_periodo(periodo_origem, periodo_destino):
    """
    Copia a data_referencia do período origem como data_anterior do período destino.
    
    Args:
        periodo_origem: PeriodoDF de onde copiar
        periodo_destino: PeriodoDF para onde copiar
    
    Returns:
        periodo_destino atualizado
    """
    if periodo_origem.data_referencia is None:
        raise ValueError(f"Período de origem {periodo_origem.nome_exibicao} não possui data de referência")
    
    periodo_destino.data_anterior = periodo_origem.data_referencia
    periodo_destino.save(update_fields=['data_anterior', 'atualizado_em'])
    
    return periodo_destino


def listar_periodos_por_fundo(fundo, ano=None, status=None, tipo_periodo=None):
    """
    Lista todos os períodos de um fundo com filtros opcionais.
    
    Args:
        fundo: Instância do Fundo
        ano: Filtrar por ano (int), None para todos
        status: Filtrar por status, None para todos
        tipo_periodo: Filtrar por tipo, None para todos
    
    Returns:
        QuerySet de PeriodoDF
    """
    queryset = PeriodoDF.objects.filter(fundo=fundo)
    
    if ano is not None:
        queryset = queryset.filter(ano=ano)
    
    if status is not None:
        queryset = queryset.filter(status=status)
    
    if tipo_periodo is not None:
        queryset = queryset.filter(tipo_periodo=tipo_periodo)
    
    return queryset.order_by('-ano', 'tipo_periodo')


def atualizar_status_automatico(periodo_df):
    """
    Atualiza automaticamente o status de um período baseado nas condições:
    - vencida: data_vencimento < hoje e não finalizada
    - em_andamento: tem dados importados mas não finalizada
    - nao_iniciada: sem dados e não finalizada
    - finalizada: permanece finalizada
    
    Args:
        periodo_df: Instância de PeriodoDF
    
    Returns:
        periodo_df atualizado
    """
    from datetime import date
    
    # Se já finalizada, não alterar
    if periodo_df.status == 'finalizada':
        return periodo_df
    
    hoje = date.today()
    tem_dados = (
        periodo_df.balancete_items.exists() or 
        periodo_df.mec_items.exists()
    )
    tem_emissao = periodo_df.historico_emissoes.exists()
    
    if tem_emissao:
        novo_status = 'finalizada'
    elif tem_dados:
        novo_status = 'em_andamento'
    elif periodo_df.data_vencimento < hoje:
        novo_status = 'vencida'
    else:
        novo_status = 'nao_iniciada'
    
    if periodo_df.status != novo_status:
        periodo_df.status = novo_status
        periodo_df.save(update_fields=['status', 'atualizado_em'])
    
    return periodo_df
