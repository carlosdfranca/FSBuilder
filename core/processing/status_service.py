"""Status de emissão das Demonstrações Financeiras de um período (usado na tela de períodos)."""
from datetime import date

from df.models import HistoricoEmissaoDF


def calcular_status_periodo(periodo_df):
    """
    Calcula o status de um período de DF específico.
    
    Retorna um dicionário com:
    - status: 'nao_iniciada' | 'em_andamento' | 'finalizada' | 'vencida' (o 'historico' é escolha do usuário e vem de periodo.status)
    - tem_dados: bool (tem movimentação, balancete ou MEC no período)
    - tem_emissao: bool (tem registro no histórico de emissão)
    - ultima_emissao: HistoricoEmissaoDF ou None
    - dias_ate_vencimento: int (negativo se vencida)
    """
    hoje = date.today()
    
    # Verificar se tem dados importados vinculados a este período
    from df.services.periodo_service import periodo_tem_dados
    tem_dados = periodo_tem_dados(periodo_df)
    
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
