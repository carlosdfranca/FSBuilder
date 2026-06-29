from django.db import migrations

ITENS_PADRAO = [
    (1, "Balancete do período de referência"),
    (2, "Demonstração de posição MEC (cotas e patrimônio líquido)"),
    (3, "Aprovação dos demonstrativos pela gestora"),
]


def seed_checklist(apps, schema_editor):
    Empresa = apps.get_model("usuarios", "Empresa")
    ChecklistItemPadrao = apps.get_model("df", "ChecklistItemPadrao")
    ChecklistItemPeriodo = apps.get_model("df", "ChecklistItemPeriodo")
    PeriodoDF = apps.get_model("df", "PeriodoDF")

    for empresa in Empresa.objects.all():
        # Só cria os padrões se a empresa ainda não tiver nenhum
        if ChecklistItemPadrao.objects.filter(empresa=empresa).exists():
            continue
        itens_criados = []
        for ordem, texto in ITENS_PADRAO:
            item = ChecklistItemPadrao.objects.create(
                empresa=empresa, texto=texto, ordem=ordem
            )
            itens_criados.append(item)

        # Aplica a todos os períodos da empresa que ainda não têm itens
        for periodo in PeriodoDF.objects.filter(empresa=empresa):
            if ChecklistItemPeriodo.objects.filter(periodo_df=periodo).exists():
                continue
            for item_padrao in itens_criados:
                ChecklistItemPeriodo.objects.create(
                    periodo_df=periodo,
                    texto=item_padrao.texto,
                    ordem=item_padrao.ordem,
                )


def undo_seed(apps, schema_editor):
    ChecklistItemPadrao = apps.get_model("df", "ChecklistItemPadrao")
    ChecklistItemPeriodo = apps.get_model("df", "ChecklistItemPeriodo")
    ChecklistItemPeriodo.objects.all().delete()
    ChecklistItemPadrao.objects.filter(texto__in=[t for _, t in ITENS_PADRAO]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("df", "0022_checklistitempadrao_checklistitemperiodo"),
        ("usuarios", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_checklist, undo_seed),
    ]
