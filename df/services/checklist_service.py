from collections import defaultdict

from django.db import transaction

from df.models import ChecklistItemPadrao, ChecklistItemPeriodo, DocumentoDF


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
    - Documentos anexados (DocumentoDF) sobrevivem ao delete() abaixo — o FK
      checklist_item usa on_delete=SET_NULL — e são re-vinculados por texto ao
      item recriado, exatamente como já é feito com `recebido`. Um documento
      cujo item de checklist não existe mais no novo template fica com
      checklist_item=NULL (documento avulso do período), nunca é apagado.

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

        with transaction.atomic():
            recebidos_textos = set(
                periodo.checklist_items
                .filter(recebido=True)
                .values_list("texto", flat=True)
            )

            docs_por_texto = defaultdict(list)
            for doc_id, texto in (
                DocumentoDF.objects
                .filter(periodo_df=periodo, checklist_item__isnull=False)
                .values_list("id", "checklist_item__texto")
            ):
                docs_por_texto[texto].append(doc_id)

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

            if docs_por_texto:
                # bulk_create não garante pk populado em todo backend (MySQL puro
                # não retorna id); re-consulta os itens recém-criados para pegar
                # os pks reais antes de vincular.
                for novo_item in periodo.checklist_items.all():
                    doc_ids = docs_por_texto.get(novo_item.texto)
                    if doc_ids:
                        DocumentoDF.objects.filter(id__in=doc_ids).update(checklist_item=novo_item)

        ressincronizados += 1

    return ressincronizados
