from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0017_delete_periodos_trimestrais'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='configuracaodf',
            name='uq_configuracaodf_fundo_tipo',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='tipo',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim1_dia',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim1_mes',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim2_dia',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim2_mes',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim3_dia',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim3_mes',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim4_dia',
        ),
        migrations.RemoveField(
            model_name='configuracaodf',
            name='trim4_mes',
        ),
        migrations.AddConstraint(
            model_name='configuracaodf',
            constraint=models.UniqueConstraint(fields=['fundo'], name='uq_configuracaodf_fundo'),
        ),
    ]
