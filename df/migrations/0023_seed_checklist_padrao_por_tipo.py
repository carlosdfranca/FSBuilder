# -*- coding: utf-8 -*-
from django.db import migrations
from django.db.models import Count


def seed_checklist(apps, schema_editor):
    from df.data.checklist_padrao_seed import SEED

    ChecklistItemPadrao = apps.get_model("df", "ChecklistItemPadrao")
    ChecklistItemPeriodo = apps.get_model("df", "ChecklistItemPeriodo")
    PeriodoDF = apps.get_model("df", "PeriodoDF")

    # 1) Popula os templates padrão por tipo (idempotente por tipo).
    for tipo, itens in SEED.items():
        if ChecklistItemPadrao.objects.filter(tipo_fundo=tipo).exists():
            continue
        bulk = [
            ChecklistItemPadrao(
                tipo_fundo=tipo,
                secao=secao,
                texto=texto,
                prazo=prazo,
                responsavel=responsavel,
                ordem=ordem,
            )
            for ordem, (secao, texto, prazo, responsavel) in enumerate(itens, start=1)
        ]
        ChecklistItemPadrao.objects.bulk_create(bulk)

    # 2) Backfill: para cada PeriodoDF sem checklist, copia o template do tipo do fundo.
    padrao_por_tipo = {}
    for item in ChecklistItemPadrao.objects.all().order_by("ordem"):
        padrao_por_tipo.setdefault(item.tipo_fundo, []).append(item)

    periodos_sem_checklist = (
        PeriodoDF.objects
        .annotate(n_items=Count("checklist_items"))
        .filter(n_items=0)
        .select_related("fundo")
    )
    for periodo in periodos_sem_checklist:
        itens_padrao = padrao_por_tipo.get(periodo.fundo.tipo_fundo, [])
        if not itens_padrao:
            continue
        ChecklistItemPeriodo.objects.bulk_create([
            ChecklistItemPeriodo(
                periodo_df=periodo,
                secao=ip.secao,
                texto=ip.texto,
                prazo=ip.prazo,
                responsavel=ip.responsavel,
                ordem=ip.ordem,
            )
            for ip in itens_padrao
        ])


def undo_seed(apps, schema_editor):
    ChecklistItemPadrao = apps.get_model("df", "ChecklistItemPadrao")
    ChecklistItemPeriodo = apps.get_model("df", "ChecklistItemPeriodo")
    ChecklistItemPeriodo.objects.all().delete()
    ChecklistItemPadrao.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("df", "0022_fundo_tipo_fundo_checklistitempadrao_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_checklist, undo_seed),
    ]
