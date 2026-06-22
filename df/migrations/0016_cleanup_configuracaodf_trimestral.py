from django.db import migrations


def deletar_configuracoes_trimestrais(apps, schema_editor):
    ConfiguracaoDF = apps.get_model('df', 'ConfiguracaoDF')
    ConfiguracaoDF.objects.filter(tipo='trimestral').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0015_redesign_configuracaodf'),
    ]

    operations = [
        migrations.RunPython(
            deletar_configuracoes_trimestrais,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
