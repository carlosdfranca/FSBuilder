# usuarios/views_selecao.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from usuarios.models import Empresa
from usuarios.utils.acesso import empresas_acessiveis

@login_required
def selecionar_empresa(request):
    """
    Página neutra para o usuário escolher a empresa ativa.
    - Para globais: lista todas as empresas
    - Para não-globais: lista as que o usuário alcança (vínculo direto ou escritório de contabilidade)
    """
    user = request.user

    # empresas visíveis
    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        empresas = Empresa.objects.all().only("id", "nome").order_by("nome")
    else:
        empresas = empresas_acessiveis(user).only("id", "nome").order_by("nome")

    if request.method == "POST":
        empresa_id = request.POST.get("empresa_id")
        if not empresa_id:
            messages.error(request, "Selecione uma empresa válida.")
            return redirect("selecionar_empresa")
        # valida se pode ver essa empresa (mesmo para globais, por segurança)
        if getattr(user, "has_global_scope", None) and user.has_global_scope():
            ok = Empresa.objects.filter(id=empresa_id).exists()
        else:
            ok = empresas.filter(id=empresa_id).exists()
        if not ok:
            messages.error(request, "Você não tem acesso a essa empresa.")
            return redirect("selecionar_empresa")
        request.session["empresa_ativa_id"] = str(empresa_id)
        # destino padrão após escolher
        return redirect("demonstracao_financeira")

    return render(request, "base/selecionar_empresa.html", {"empresas": empresas})
