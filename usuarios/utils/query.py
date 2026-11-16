# usuarios/utils/query.py
from usuarios.utils.acesso import ids_empresas_acessiveis

def restrict_by_empresa(queryset, user, empresa_field: str):
    """
    Restringe queryset a empresas do usuário, a menos que ele tenha escopo global.
    - queryset: ex.: Fundo.objects.all()
    - user: request.user
    - empresa_field: caminho até empresa, ex.: "empresa" ou "fundo__empresa"
    """
    # global scope (viewer/admin/superuser) vê tudo
    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        return queryset
    # empresas que o usuário alcança (vínculos diretos + escritório de contabilidade)
    return queryset.filter(**{f"{empresa_field}__in": ids_empresas_acessiveis(user)})
