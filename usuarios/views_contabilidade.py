# usuarios/views_contabilidade.py
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Min, Q
from django.shortcuts import redirect, render

from usuarios.models import Empresa, VinculoContabil
from usuarios.utils.acesso import escritorios_do_usuario


@login_required
def painel_contabilidade(request):
    """
    Painel de quem trabalha num escritório de contabilidade: as empresas vinculadas aos escritórios
    ativos do usuário, com o andamento das DFs. Só mostra o que o vínculo permite.
    """
    escritorios = list(escritorios_do_usuario(request.user))
    if not escritorios:
        messages.warning(request, "Você não faz parte de nenhum escritório de contabilidade ativo.")
        return redirect("demonstracao_financeira")

    empresas = (
        Empresa.objects
        .filter(vinculos_contabeis__is_active=True, vinculos_contabeis__escritorio__in=escritorios)
        .annotate(
            qtd_fundos=Count("fundos", distinct=True),
            periodos_abertos=Count("periodos_df", filter=~Q(periodos_df__status="finalizada"), distinct=True),
            periodos_vencidos=Count("periodos_df", filter=Q(periodos_df__status="vencida"), distinct=True),
            proximo_vencimento=Min("periodos_df__data_vencimento", filter=~Q(periodos_df__status="finalizada")),
        )
        .only("id", "nome", "cnpj")
        .order_by("nome")
    )

    escritorio_da_empresa = dict(
        VinculoContabil.objects
        .filter(is_active=True, escritorio__in=escritorios)
        .values_list("empresa_id", "escritorio__nome")
    )
    hoje = date.today()
    linhas = [
        {
            "empresa": e,
            "escritorio": escritorio_da_empresa.get(e.id, ""),
            "atrasado": bool(e.proximo_vencimento and e.proximo_vencimento < hoje),
        }
        for e in empresas
    ]

    return render(request, "usuarios/painel_contabilidade.html", {
        "escritorios": escritorios,
        "linhas": linhas,
        "mais_de_um_escritorio": len(escritorios) > 1,
        "total_abertos": sum(l["empresa"].periodos_abertos for l in linhas),
        "total_vencidos": sum(l["empresa"].periodos_vencidos for l in linhas),
    })
