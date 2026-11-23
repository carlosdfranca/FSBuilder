# usuarios/utils/company_scope.py
from typing import Optional
from usuarios.models import Empresa
from usuarios.utils.query import restrict_by_empresa  # já existente

SESSION_KEY = "empresa_ativa_id"

def get_empresa_ativa(request) -> Optional[Empresa]:
    return getattr(request, "empresa_ativa", None)

def set_empresa_ativa(request, empresa_id: Optional[int]):
    if empresa_id:
        request.session[SESSION_KEY] = int(empresa_id)
    else:
        request.session.pop(SESSION_KEY, None)

def query_por_empresa_ativa(qs, request, empresa_field: str = "empresa"):
    """
    Se há empresa ativa em sessão -> filtra por ela (globais) ou pela interseção com o que o usuário
    alcança (demais). Sem empresa ativa: globais não veem nada; demais seguem restrict_by_empresa.
    """
    user = request.user
    empresa = get_empresa_ativa(request)

    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        if empresa:
            return qs.filter(**{f"{empresa_field}_id": empresa.id})
        # Sem empresa ativa, por segurança não retorna nada
        return qs.none()

    # Não-global: com empresa ativa (validada pelo middleware) filtra só por ela, como nos globais;
    # sem ela, cai na regra de todas as empresas que o usuário alcança.
    if empresa:
        return restrict_by_empresa(qs, user, empresa_field).filter(**{f"{empresa_field}_id": empresa.id})
    return restrict_by_empresa(qs, user, empresa_field)
