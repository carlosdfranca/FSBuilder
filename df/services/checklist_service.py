from df.models import ChecklistItemPadrao, ChecklistItemPeriodo


def criar_checklist_para_periodo(periodo):
    """
    Copia os ChecklistItemPadrao do tipo do fundo do período para o período informado.
    Idempotente: não duplica se o período já tiver itens.
    """
    itens_padrao = list(
        ChecklistItemPadrao.objects
        .filter(tipo_fundo=periodo.fundo.tipo_fundo)
        .order_by("ordem")
    )
    if not itens_padrao:
        return []

    if ChecklistItemPeriodo.objects.filter(periodo_df=periodo).exists():
        return []

    bulk = [
        ChecklistItemPeriodo(
            periodo_df=periodo,
            secao=item.secao,
            texto=item.texto,
            prazo=item.prazo,
            responsavel=item.responsavel,
            ordem=item.ordem,
        )
        for item in itens_padrao
    ]
    ChecklistItemPeriodo.objects.bulk_create(bulk)
    return bulk


def ressincronizar_checklist_por_tipo(fundo):
    """
    Re-sincroniza o checklist dos períodos de um fundo com o template do
    tipo atual do fundo. Usado quando o tipo_fundo muda.

    - Períodos FINALIZADOS são preservados (registro histórico).
    - Para os demais, o checklist é substituído pelo template do novo tipo,
      preservando o estado 'recebido' de documentos cujo texto coincide.

    Retorna o número de períodos re-sincronizados.
    """
    itens_padrao = list(
        ChecklistItemPadrao.objects
        .filter(tipo_fundo=fundo.tipo_fundo)
        .order_by("ordem")
    )
    if not itens_padrao:
        return 0

    ressincronizados = 0
    for periodo in fundo.periodos_df.all():
        if periodo.status == "finalizada":
            continue

        recebidos_textos = set(
            periodo.checklist_items
            .filter(recebido=True)
            .values_list("texto", flat=True)
        )
        periodo.checklist_items.all().delete()
        ChecklistItemPeriodo.objects.bulk_create([
            ChecklistItemPeriodo(
                periodo_df=periodo,
                secao=item.secao,
                texto=item.texto,
                prazo=item.prazo,
                responsavel=item.responsavel,
                ordem=item.ordem,
                recebido=(item.texto in recebidos_textos),
            )
            for item in itens_padrao
        ])
        ressincronizados += 1

    return ressincronizados
