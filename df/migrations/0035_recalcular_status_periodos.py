from datetime import date

from django.db import migrations


def recalcular(apps, schema_editor):
    """
    Ao sair o balancete, períodos que estavam "em andamento" só por terem balancete importado
    precisam voltar ao status real. Regra igual à de atualizar_status_automatico: emissão →
    finalizada; MEC → em andamento; senão vencida/não iniciada pelo prazo. Finalizada é mantida.
    """
    PeriodoDF = apps.get_model("df", "PeriodoDF")
    MecItem = apps.get_model("df", "MecItem")
    Historico = apps.get_model("df", "HistoricoEmissaoDF")

    hoje = date.today()
    com_mec = set(MecItem.objects.filter(periodo_df__isnull=False).values_list("periodo_df_id", flat=True))
    com_emissao = set(Historico.objects.filter(periodo_df__isnull=False).values_list("periodo_df_id", flat=True))

    for periodo in PeriodoDF.objects.exclude(status="finalizada"):
        if periodo.id in com_emissao:
            novo = "finalizada"
        elif periodo.id in com_mec:
            novo = "em_andamento"
        elif periodo.data_vencimento < hoje:
            novo = "vencida"
        else:
            novo = "nao_iniciada"
        if periodo.status != novo:
            PeriodoDF.objects.filter(pk=periodo.pk).update(status=novo)


class Migration(migrations.Migration):
    dependencies = [("df", "0034_seed_linhas_dfc")]
    operations = [migrations.RunPython(recalcular, migrations.RunPython.noop)]
