"""Data migration: popula DocumentoDF.retencao_ate para os documentos
enviados antes da Fase 5.4 (quando o campo passou a ser calculado no upload).

Cálculo inline (não importa df.services.documento_service.calcular_retencao_ate):
migrations não devem depender de código da app fora do snapshot histórico via
apps.get_model — o cálculo aqui é a mesma conta de 2 linhas, mantida em sincronia
manualmente por ser trivial o bastante para não valer a pena a indireção.
"""
from datetime import date

from django.db import migrations


def backfill_retencao_ate(apps, schema_editor):
    DocumentoDF = apps.get_model('df', 'DocumentoDF')
    pendentes = DocumentoDF.objects.filter(retencao_ate__isnull=True).select_related('periodo_df', 'empresa')
    for documento in pendentes:
        if not (documento.periodo_df_id and documento.empresa_id):
            continue
        ano_expurgo = documento.periodo_df.ano + documento.empresa.retencao_anos
        documento.retencao_ate = date(ano_expurgo, 12, 31)
        documento.save(update_fields=['retencao_ate'])


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0025_logacessodocumento'),
    ]

    operations = [
        migrations.RunPython(backfill_retencao_ate, migrations.RunPython.noop),
    ]
