# usuarios/context_processors.py
from usuarios.models import Empresa, Membership, Usuario, Convite

def _empresas_do_usuario(user):
    if not user.is_authenticated:
        return Empresa.objects.none()
    # Globais veem todas
    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        return Empresa.objects.all().only("id", "nome").order_by("nome")
    # Demais: empresas do vínculo
    empresa_ids = Membership.objects.filter(
        usuario=user, is_active=True
    ).values_list("empresa_id", flat=True)
    return Empresa.objects.filter(id__in=list(empresa_ids)).only("id", "nome").order_by("nome")

def _primeira_empresa_do_usuario(user):
    memb = Membership.objects.filter(usuario=user, is_active=True).select_related("empresa").first()
    return memb.empresa if memb else None

def _role_do_usuario_na_empresa(user, empresa):
    if not user.is_authenticated or not empresa:
        return None
    memb = Membership.objects.filter(usuario=user, empresa=empresa, is_active=True).only("role").first()
    return memb.role if memb else None

def empresas_contexto(request):
    """
    - empresa_ativa: do middleware; se ausente e usuário não-global, tenta primeira empresa do membership
    - empresas_disponiveis: empresas que o usuário pode ver
    - user_is_global: qualquer papel global
    - user_is_global_admin: apenas PLATFORM_ADMIN (ou superuser)
    - user_can_view_company_users: True p/ globais ou MASTER/ADMIN da empresa_ativa
    - user_can_manage_company_users: True p/ Global Admin ou MASTER/ADMIN
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    user = request.user
    # Flags globais
    is_global = bool(getattr(user, "has_global_scope", None) and user.has_global_scope())
    is_global_admin = bool(user.is_superuser or getattr(user, "global_role", "") == Usuario.GlobalRole.PLATFORM_ADMIN)

    empresa_ativa = getattr(request, "empresa_ativa", None)
    if not empresa_ativa and not is_global:
        # Para usuários não-globais, se não houver empresa ativa, tenta a primeira empresa do vínculo
        empresa_ativa = _primeira_empresa_do_usuario(user)

    empresas = _empresas_do_usuario(user)
    role = _role_do_usuario_na_empresa(user, empresa_ativa) if empresa_ativa else None

    # Quem PODE VER a página de gestão?
    user_can_view_company_users = is_global or (role in {Membership.Role.MASTER, Membership.Role.ADMIN})
    # Quem PODE GERENCIAR (CRUD)?
    user_can_manage_company_users = is_global_admin or (role in {Membership.Role.MASTER, Membership.Role.ADMIN})

    # Contagem de convites pendentes (apenas se tiver empresa ativa e puder ver usuários)
    convites_pendentes_count = 0
    if empresa_ativa and user_can_view_company_users:
        convites_pendentes_count = Convite.objects.filter(
            empresa=empresa_ativa,
            status=Convite.Status.PENDING
        ).count()

    return {
        "empresa_ativa": empresa_ativa,
        "empresas_disponiveis": empresas,
        "user_is_global": is_global,
        "user_is_global_admin": is_global_admin,
        "user_can_view_company_users": user_can_view_company_users,
        "user_can_manage_company_users": user_can_manage_company_users,
        "convites_pendentes_count": convites_pendentes_count,
    }
