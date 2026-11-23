from django.db import migrations

# Do filho para o pai: as chaves estrangeiras impedem apagar o pai antes.
TABELAS_DO_LEGADO = (
    "df_balanceteitem",
    "df_balancetelinhapendente",
    "df_mapeamentocontas",
    "df_grupopequeno",
    "df_grupogrande",
)


def apagar_tabelas(apps, schema_editor):
    """
    Apaga as tabelas do balancete e do mapeamento por empresa, só as que existirem.

    Por que não o RemoveField/DeleteModel automático: ele removia antes a coluna `grupao` de
    GrupoPequeno, que fazia parte da regra de nome único (nome + grupão); sem ela o mesmo nome em
    grupos diferentes passa a se repetir e o MySQL recusa. Como o MySQL não desfaz DDL, uma falha
    no meio deixava o banco pela metade e impedia rodar de novo. Apagar a tabela inteira dispensa
    mexer em colunas e pode ser repetido com segurança.
    """
    existentes = set(schema_editor.connection.introspection.table_names())
    for tabela in TABELAS_DO_LEGADO:
        if tabela in existentes:
            schema_editor.execute(f"DROP TABLE {schema_editor.quote_name(tabela)}")


class Migration(migrations.Migration):

    dependencies = [
        ('df', '0035_recalcular_status_periodos'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Estado do Django: remove os modelos, dos que referenciam para os referenciados
            state_operations=[
                migrations.DeleteModel(name='BalanceteItem'),
                migrations.DeleteModel(name='BalanceteLinhaPendente'),
                migrations.DeleteModel(name='MapeamentoContas'),
                migrations.DeleteModel(name='GrupoPequeno'),
                migrations.DeleteModel(name='GrupoGrande'),
            ],
            # Banco: apaga as tabelas (irreversível; os dados antigos só voltam pelo dump)
            database_operations=[
                migrations.RunPython(apagar_tabelas, migrations.RunPython.noop),
            ],
        ),
    ]
