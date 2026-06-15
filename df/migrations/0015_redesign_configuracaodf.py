from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0014_remove_fundo_data_vencimento_df_remove_fundo_tipo_df'),
    ]

    operations = [
        migrations.DeleteModel(
            name='ConfiguracaoDF',
        ),
        migrations.CreateModel(
            name='ConfiguracaoDF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(
                    choices=[('trimestral', 'Trimestral'), ('anual', 'Anual')],
                    max_length=20,
                    help_text='Tipo de DF configurado neste registro',
                )),
                ('trim1_dia', models.IntegerField(blank=True, null=True, help_text='Dia do vencimento do 1º trimestre (1-31)')),
                ('trim1_mes', models.IntegerField(blank=True, null=True, help_text='Mês do vencimento do 1º trimestre (1-12)')),
                ('trim2_dia', models.IntegerField(blank=True, null=True, help_text='Dia do vencimento do 2º trimestre (1-31)')),
                ('trim2_mes', models.IntegerField(blank=True, null=True, help_text='Mês do vencimento do 2º trimestre (1-12)')),
                ('trim3_dia', models.IntegerField(blank=True, null=True, help_text='Dia do vencimento do 3º trimestre (1-31)')),
                ('trim3_mes', models.IntegerField(blank=True, null=True, help_text='Mês do vencimento do 3º trimestre (1-12)')),
                ('trim4_dia', models.IntegerField(blank=True, null=True, help_text='Dia do vencimento do 4º trimestre (1-31)')),
                ('trim4_mes', models.IntegerField(blank=True, null=True, help_text='Mês do vencimento do 4º trimestre (1-12)')),
                ('anual_dia', models.IntegerField(blank=True, null=True, help_text='Dia do vencimento anual (1-31)')),
                ('anual_mes', models.IntegerField(blank=True, null=True, help_text='Mês do vencimento anual (1-12)')),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('fundo', models.ForeignKey(
                    help_text='Fundo ao qual esta configuração pertence',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='configuracoes_df',
                    to='df.fundo',
                )),
            ],
            options={
                'verbose_name': 'Configuração de DF',
                'verbose_name_plural': 'Configurações de DFs',
            },
        ),
        migrations.AddConstraint(
            model_name='configuracaodf',
            constraint=models.UniqueConstraint(fields=['fundo', 'tipo'], name='uq_configuracaodf_fundo_tipo'),
        ),
    ]
