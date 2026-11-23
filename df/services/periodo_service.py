"""
Serviço para gerenciamento de períodos de DF.
Responsável por gerar períodos anuais automaticamente
e auxiliar na criação manual (Transitória/Encerramento).
"""
from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from df.models import AlteracaoStatusPeriodo, Movimentacao, PeriodoDF, Fundo, SaldoInicial

# Vencimento (dia, mês) do ano seguinte para o fundo sem configuração de DF anual.
# Valor provisório: confirmar com o cliente (hoje = 3 meses após o fim do ano).
VENCIMENTO_PADRAO = (31, 3)

# Status que não mudam sozinhos: o usuário os escolheu (ou o sistema, para anos antigos).
STATUS_FIXOS = ("finalizada", "historico")
STATUS_MANUAIS = ("nao_iniciada", "em_andamento", "finalizada", "historico")


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


def datas_padrao_periodo(tipo_periodo: str, ano: int, data_vencimento: date) -> tuple[date, date]:
    """
    (data_referencia, data_anterior) de um período.
    Anual: 31/12 do ano. Manuais (transitória/encerramento): a data de vencimento informada,
    como o sistema já fazia. O anterior é sempre 31/12 do ano anterior à referência.
    """
    referencia = date(ano, 12, 31) if tipo_periodo == 'anual' else data_vencimento
    return referencia, date(referencia.year - 1, 12, 31)


def garantir_datas_periodo(periodo_df) -> None:
    """
    Preenche as datas dos períodos criados antes de elas serem definidas na criação.
    Sem dados antes do início do fundo o comparativo sai zerado, igual ao antigo "zerado".
    """
    campos = []
    if periodo_df.data_referencia is None:
        periodo_df.data_referencia, _ = datas_padrao_periodo(
            periodo_df.tipo_periodo, periodo_df.ano, periodo_df.data_vencimento
        )
        campos.append('data_referencia')
    if periodo_df.data_anterior is None:
        periodo_df.data_anterior = date(periodo_df.data_referencia.year - 1, 12, 31)
        campos.append('data_anterior')
    if campos:
        periodo_df.save(update_fields=campos + ['atualizado_em'])


def movimentacoes_na_janela(periodo_df):
    """Movimentações e saldos iniciais do fundo dentro de (data_anterior, data_referencia]."""
    ref = periodo_df.data_referencia
    movs = Movimentacao.objects.filter(fundo_id=periodo_df.fundo_id, data__lte=ref)
    saldos = SaldoInicial.objects.filter(fundo_id=periodo_df.fundo_id, data_base__lte=ref)
    if periodo_df.data_anterior:
        movs = movs.filter(data__gt=periodo_df.data_anterior)
        saldos = saldos.filter(data_base__gt=periodo_df.data_anterior)
    return movs, saldos


def periodo_tem_movimentacao(periodo_df) -> bool:
    """O fundo tem movimentação (ou saldo inicial) dentro da janela do período."""
    if periodo_df.data_referencia is None:
        return False
    movs, saldos = movimentacoes_na_janela(periodo_df)
    return movs.exists() or saldos.exists()


def periodo_tem_dados(periodo_df) -> bool:
    """Há algum dado para a DF do período: movimentações, saldos iniciais ou posição do MEC."""
    return periodo_tem_movimentacao(periodo_df) or periodo_df.mec_items.exists()


def atualizar_periodos_da_data(fundo, data) -> None:
    """Reavalia o status dos períodos do fundo cuja janela contém `data`."""
    candidatos = PeriodoDF.objects.filter(fundo=fundo, data_referencia__gte=data)
    for periodo in candidatos:
        if periodo.data_anterior is None or periodo.data_anterior < data:
            atualizar_status_automatico(periodo)


def atualizar_periodos_do_intervalo(fundo, data_min, data_max) -> None:
    """Reavalia o status dos períodos do fundo cuja janela cruza [data_min, data_max]."""
    candidatos = PeriodoDF.objects.filter(fundo=fundo, data_referencia__gte=data_min)
    for periodo in candidatos:
        if periodo.data_anterior is None or periodo.data_anterior < data_max:
            atualizar_status_automatico(periodo)


def vencimento_anual(fundo, ano: int) -> date:
    """Vencimento da DF anual: o da configuração do fundo ou, sem ela, VENCIMENTO_PADRAO."""
    fim_periodo = date(ano, 12, 31)
    config = fundo.configuracoes_df.first()
    if config and config.anual_dia and config.anual_mes:
        try:
            return _vencimento_apos_fim(ano, config.anual_mes, config.anual_dia, fim_periodo)
        except ValueError:
            pass  # configuração impossível (ex.: 31/02): cai no padrão
    dia, mes = VENCIMENTO_PADRAO
    return _vencimento_apos_fim(ano, mes, dia, fim_periodo)


def garantir_periodos_anuais(fundo, anos) -> list:
    """
    Cria os períodos anuais que faltam para os `anos` informados e devolve os criados.

    Ano cujo prazo de entrega já passou nasce como "Histórico" (concluído, sem alerta e sem
    travar os dados); os demais nascem "Não iniciada" e os ganchos de movimentação os levam a
    "Em andamento". Idempotente. O MySQL não aplica a unique condicional do model, então a
    criação é serializada pela linha do fundo.
    """
    anos = sorted({int(a) for a in anos})
    if not anos:
        return []

    def faltantes():
        existentes = set(
            PeriodoDF.objects.filter(fundo=fundo, tipo_periodo='anual', ano__in=anos).values_list('ano', flat=True)
        )
        return [a for a in anos if a not in existentes]

    if not faltantes():
        return []

    from df.services.checklist_service import criar_checklist_para_periodo

    hoje = date.today()
    criados = []
    with transaction.atomic():
        Fundo.objects.select_for_update().get(pk=fundo.pk)
        for ano in faltantes():
            vencimento = vencimento_anual(fundo, ano)
            referencia, anterior = datas_padrao_periodo('anual', ano, vencimento)
            periodo = PeriodoDF.objects.create(
                fundo=fundo,
                empresa=fundo.empresa,
                tipo_periodo='anual',
                ano=ano,
                data_vencimento=vencimento,
                data_referencia=referencia,
                data_anterior=anterior,
                status='historico' if vencimento < hoje else 'nao_iniciada',
                criado_manualmente=False,
            )
            criar_checklist_para_periodo(periodo)
            criados.append(periodo)
    return criados


def ano_do_saldo_inicial(data_base: date) -> int:
    """Um saldo em 31/12/2019 é a abertura de 2020: o período é o do dia seguinte."""
    return (data_base + timedelta(days=1)).year


def gerar_periodos_anuais(fundo, ano_inicial, ano_final=None):
    """Gera os períodos anuais de `ano_inicial` a `ano_final` (os que já existem são mantidos)."""
    if ano_final is None:
        ano_final = ano_inicial
    return garantir_periodos_anuais(fundo, range(ano_inicial, ano_final + 1))


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

        referencia, anterior = datas_padrao_periodo(tipo_periodo, ano, data_vencimento)
        periodo = PeriodoDF.objects.create(
            fundo=fundo,
            empresa=fundo.empresa,
            tipo_periodo=tipo_periodo,
            ano=ano,
            data_vencimento=data_vencimento,
            data_referencia=referencia,
            data_anterior=anterior,
            status='nao_iniciada',
            criado_manualmente=True,
            descricao=descricao,
        )
        return periodo, True

    elif tipo_periodo == 'anual':
        criados = garantir_periodos_anuais(fundo, [ano])
        if criados:
            return criados[0], True
        return PeriodoDF.objects.filter(fundo=fundo, tipo_periodo='anual', ano=ano).first(), False


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
    - finalizada / historico: permanecem como estão
    
    Args:
        periodo_df: Instância de PeriodoDF
    
    Returns:
        periodo_df atualizado
    """
    from datetime import date
    
    # Finalizada e Histórico são escolhas: não mudam sozinhas
    if periodo_df.status in STATUS_FIXOS:
        return periodo_df
    
    hoje = date.today()
    tem_dados = periodo_tem_dados(periodo_df)
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


@transaction.atomic
def alterar_status_manual(periodo_df, novo_status: str, usuario=None, motivo: str = "") -> bool:
    """
    Troca manual de status (selo na tela). Sair de "Finalizada" é uma reabertura: exige motivo.
    Toda troca fica registrada em AlteracaoStatusPeriodo. Devolve False se nada mudou.
    """
    if novo_status not in STATUS_MANUAIS:
        raise ValidationError("Status inválido.")
    anterior = periodo_df.status
    if anterior == novo_status:
        return False
    if anterior == 'vencida' and novo_status == 'em_andamento':
        raise ValidationError("Período vencido não pode ser marcado como Em Andamento.")
    motivo = (motivo or "").strip()
    if anterior == 'finalizada' and not motivo:
        raise ValidationError("Informe o motivo da reabertura do período.")

    periodo_df.status = novo_status
    periodo_df.save(update_fields=['status', 'atualizado_em'])
    AlteracaoStatusPeriodo.objects.create(
        periodo=periodo_df, status_anterior=anterior, status_novo=novo_status, motivo=motivo[:2000], usuario=usuario,
    )
    return True
