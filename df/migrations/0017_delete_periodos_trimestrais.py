from django.db import migrations


def deletar_periodos_trimestrais(apps, schema_editor):
    PeriodoDF = apps.get_model('df', 'PeriodoDF')
    PeriodoDF.objects.filter(tipo_periodo='trimestral').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0016_cleanup_configuracaodf_trimestral'),
    ]

    operations = [
        migrations.RunPython(
            deletar_periodos_trimestrais,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
