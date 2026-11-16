# usuarios/context_processors.py
from usuarios.models import Membership, Usuario, Convite
from usuarios.permissions import role_na_empresa
from usuarios.utils.acesso import empresas_acessiveis, escritorios_do_usuario


def _empresas_do_usuario(user):
    if not user.is_authenticated:
        return []
    return list(empresas_acessiveis(user).only("id", "nome").order_by("nome"))


def empresas_contexto(request):
    """
    - empresa_ativa: do middleware; se ausente e usuário não-global, tenta primeira empresa do membership
    - empresas_disponiveis: empresas que o usuário pode ver (vínculos diretos e escritório de contabilidade)
    - pode_trocar_empresa / user_is_contabilidade: controlam o seletor e o link do painel da contabilidade
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

    empresas = _empresas_do_usuario(user)
    empresa_ativa = getattr(request, "empresa_ativa", None)
    if not empresa_ativa and not is_global and empresas:
        # Para usuários não-globais, se não houver empresa ativa, usa a primeira que alcançam
        empresa_ativa = empresas[0]

    role = role_na_empresa(user, empresa_ativa) if empresa_ativa else None

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

    # Contas do balancete sem mapeamento (badge do Plano de Contas); só quem pode editar precisa do aviso.
    contas_pendentes_count = 0
    if empresa_ativa and (is_global_admin or role in {Membership.Role.MASTER, Membership.Role.ADMIN}):
        from df.services.mapeamento_service import total_contas_pendentes
        contas_pendentes_count = total_contas_pendentes(empresa_ativa)

    return {
        "contas_pendentes_count": contas_pendentes_count,
        "empresa_ativa": empresa_ativa,
        "empresas_disponiveis": empresas,
        # Seletor de empresa na navbar: globais e qualquer um que alcance mais de uma (ex.: contabilidade)
        "pode_trocar_empresa": is_global or len(empresas) > 1,
        # Admin da plataforma trabalha pelo admin do Django e não precisa do painel (e poupa uma consulta)
        "user_is_contabilidade": (not is_global) and escritorios_do_usuario(user).exists(),
        "user_is_global": is_global,
        "user_is_global_admin": is_global_admin,
        "user_can_view_company_users": user_can_view_company_users,
        "user_can_manage_company_users": user_can_manage_company_users,
        "convites_pendentes_count": convites_pendentes_count,
    }
