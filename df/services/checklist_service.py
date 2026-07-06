from df.models import ChecklistItemPadrao, ChecklistItemPeriodo


def criar_checklist_para_periodo(periodo):
    """
    Copia os ChecklistItemPadrao da empresa para o período informado.
    Idempotente: não duplica se o período já tiver itens.
    """
    itens_padrao = list(
        ChecklistItemPadrao.objects.filter(empresa=periodo.empresa).order_by("ordem")
    )
    if not itens_padrao:
        return []

    if ChecklistItemPeriodo.objects.filter(periodo_df=periodo).exists():
        return []

    bulk = [
        ChecklistItemPeriodo(
            periodo_df=periodo,
            texto=item.texto,
            ordem=item.ordem,
        )
        for item in itens_padrao
    ]
    ChecklistItemPeriodo.objects.bulk_create(bulk)
    return bulk
