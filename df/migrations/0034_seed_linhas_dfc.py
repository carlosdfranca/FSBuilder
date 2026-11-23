from django.db import migrations

# (secao, ordem, descricao, prefixos, sinal). Os prefixos particionam o plano referencial:
# toda conta fora do caixa (1.01.01) cai em exatamente uma linha. Classificação inicial,
# a ser validada pela contabilidade e ajustável no admin.
LINHAS = [
    ("operacional", 10, "Resultado líquido do período", "3,2.03.04.01.01,2.03.04.01.11", 1),
    ("operacional", 20, "(Aumento) redução em créditos", "1.01.02", -1),
    ("operacional", 30, "(Aumento) redução em estoques", "1.01.03", -1),
    ("operacional", 40, "(Aumento) redução em despesas do exercício seguinte", "1.01.05", -1),
    ("operacional", 50, "(Aumento) redução em outros ativos circulantes", "1.01.10,1.01.11", -1),
    ("operacional", 60, "(Aumento) redução em realizável a longo prazo", "1.02.01", -1),
    ("operacional", 70, "Aumento (redução) em obrigações do circulante", "2.01", 1),
    ("operacional", 80, "Aumento (redução) em outras obrigações de longo prazo",
     "2.02.01.02,2.02.01.03,2.02.01.05,2.02.01.09,2.02.01.10,2.02.01.11,2.02.01.21", 1),
    ("operacional", 90, "Outras variações no patrimônio líquido",
     "2.03.02,2.03.03,2.03.04.01.05,2.03.04.01.10,2.03.04.01.12,2.03.04.01.15,2.03.04.01.90", 1),
    ("investimento", 10, "(Aquisição) alienação de investimentos", "1.02.02", -1),
    ("investimento", 20, "(Aquisição) baixa de imobilizado", "1.02.03", -1),
    ("investimento", 30, "(Aquisição) baixa de intangível e diferido", "1.02.05,1.02.06", -1),
    ("financiamento", 10, "Empréstimos e financiamentos de longo prazo", "2.02.01.01", 1),
    ("financiamento", 20, "Títulos de dívida de longo prazo", "2.02.01.07", 1),
    ("financiamento", 30, "Integralização (redução) de capital", "2.03.01", 1),
]


def semear(apps, schema_editor):
    LinhaDFC = apps.get_model("df", "LinhaDFC")
    if LinhaDFC.objects.exists():
        return  # não sobrescreve ajustes feitos no admin
    LinhaDFC.objects.bulk_create(
        LinhaDFC(secao=s, ordem=o, descricao=d, prefixos=p, sinal=sinal) for s, o, d, p, sinal in LINHAS
    )


def desfazer(apps, schema_editor):
    apps.get_model("df", "LinhaDFC").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("df", "0033_linhadfc")]
    operations = [migrations.RunPython(semear, desfazer)]
