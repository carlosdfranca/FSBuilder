# usuarios/views_company.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from usuarios.models import Empresa
from usuarios.utils.acesso import empresas_acessiveis
from usuarios.utils.company_scope import set_empresa_ativa


def _destino(request):
    """`next` do formulário (só no mesmo host) ou a página de onde veio."""
    alvo = request.POST.get("next") or ""
    if alvo and url_has_allowed_host_and_scheme(alvo, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return alvo
    return request.META.get("HTTP_REFERER", "/")

@login_required
def trocar_empresa_ativa(request):
    if request.method != "POST":
        return redirect(request.META.get("HTTP_REFERER", "/"))

    emp_id = request.POST.get("empresa_id")
    if not emp_id:
        set_empresa_ativa(request, None)
        messages.info(request, "Empresa ativa limpa.")
        return redirect(_destino(request))

    empresa = get_object_or_404(Empresa, id=emp_id)

    user = request.user
    # Validação: globais podem tudo; os demais, só empresas que alcançam (vínculo direto ou escritório)
    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        set_empresa_ativa(request, empresa.id)
        return redirect(_destino(request))

    if not empresas_acessiveis(user).filter(id=empresa.id).exists():
        messages.error(request, "Você não tem acesso a essa empresa.")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    set_empresa_ativa(request, empresa.id)
    return redirect(_destino(request))
