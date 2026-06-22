from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0018_simplify_configuracaodf'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='periododf',
            name='uq_periodo_fundo_tipo_ano_tri',
        ),
        migrations.RemoveField(
            model_name='periododf',
            name='trimestre',
        ),
        migrations.AlterModelOptions(
            name='periododf',
            options={
                'ordering': ['empresa', 'fundo', '-ano'],
                'verbose_name': 'Período de DF',
                'verbose_name_plural': 'Períodos de DFs',
            },
        ),
        migrations.AddConstraint(
            model_name='periododf',
            constraint=models.UniqueConstraint(
                condition=models.Q(tipo_periodo='anual'),
                fields=['fundo', 'tipo_periodo', 'ano'],
                name='uq_periodo_fundo_tipo_ano',
            ),
        ),
    ]
