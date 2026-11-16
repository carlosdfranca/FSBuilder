# usuarios/utils/acesso.py
"""
Fonte única de "quais empresas este usuário alcança".

- Globais (PLATFORM_ADMIN/VIEWER): todas.
- Demais: empresas dos vínculos ativos (Membership) + empresas vinculadas aos escritórios de
  contabilidade ativos de que o usuário é membro ativo.
"""
from django.db.models import Q

from usuarios.models import Empresa, EscritorioContabil, Membership, VinculoContabil


def _vinculos_do_usuario(user):
    return VinculoContabil.objects.filter(
        is_active=True,
        escritorio__is_ativo=True,
        escritorio__membros__usuario=user,
        escritorio__membros__is_active=True,
    )


def escritorios_do_usuario(user):
    """Escritórios ativos de que o usuário é membro ativo."""
    if not getattr(user, "is_authenticated", False):
        return EscritorioContabil.objects.none()
    return EscritorioContabil.objects.filter(
        is_ativo=True, membros__usuario=user, membros__is_active=True
    ).distinct()


def empresas_acessiveis(user):
    """QuerySet de Empresa que o usuário pode abrir (inclui as que chegam via escritório)."""
    if not getattr(user, "is_authenticated", False):
        return Empresa.objects.none()
    if getattr(user, "has_global_scope", None) and user.has_global_scope():
        return Empresa.objects.all()
    por_membership = Membership.objects.filter(usuario=user, is_active=True).values("empresa_id")
    por_escritorio = _vinculos_do_usuario(user).values("empresa_id")
    return Empresa.objects.filter(Q(id__in=por_membership) | Q(id__in=por_escritorio))


def ids_empresas_acessiveis(user) -> list[int]:
    return list(empresas_acessiveis(user).values_list("id", flat=True))


def tem_acesso_contabil(user, empresa) -> bool:
    """O usuário chega à empresa como contabilidade (escritório ativo, vínculo ativo, membro ativo)."""
    if not (getattr(user, "is_authenticated", False) and empresa):
        return False
    return _vinculos_do_usuario(user).filter(empresa=empresa).exists()
