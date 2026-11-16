"""Gestão de escritórios de contabilidade: vínculos com empresas e membros da equipe."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction

from usuarios.models import Empresa, EscritorioContabil, MembroEscritorio, VinculoContabil, Usuario
from usuarios.permissions import is_global_admin
from usuarios.services.convite_escritorio_service import (
    ConviteEscritorioError, validar_pode_alterar_membro,
)


class EscritorioError(ConviteEscritorioError):
    """Regra de negócio da gestão do escritório (mensagem pronta para o usuário)."""


def _so_plataforma(user) -> None:
    if not is_global_admin(user):
        raise PermissionDenied("Só o administrador da plataforma gerencia os vínculos com empresas.")


@transaction.atomic
def vincular_empresa(*, escritorio: EscritorioContabil, empresa: Empresa, por: Usuario) -> VinculoContabil:
    """
    Vincula (ou reativa o vínculo de) uma empresa. VinculoContabil.clean() recusa se outro escritório
    já responde por ela; o lock na empresa serializa dois vínculos simultâneos (o MySQL não impõe a regra).
    """
    _so_plataforma(por)
    Empresa.objects.select_for_update().get(pk=empresa.pk)

    existente = VinculoContabil.objects.select_for_update().filter(escritorio=escritorio, empresa=empresa).first()
    if existente is None:
        return VinculoContabil.objects.create(escritorio=escritorio, empresa=empresa, criado_por=por)
    if existente.is_active:
        raise EscritorioError("Esta empresa já está vinculada a este escritório.")
    existente.is_active = True
    existente.save()  # clean() valida se outro escritório assumiu a empresa enquanto estava inativo
    return existente


@transaction.atomic
def definir_vinculo_ativo(vinculo_id: int, *, ativo: bool, por: Usuario) -> VinculoContabil:
    _so_plataforma(por)
    vinculo = VinculoContabil.objects.select_for_update().select_related("empresa", "escritorio").get(id=vinculo_id)
    if ativo:
        Empresa.objects.select_for_update().get(pk=vinculo.empresa_id)
    vinculo.is_active = ativo
    vinculo.save()
    return vinculo


@transaction.atomic
def alterar_membro(
    membro_id: int, *, por: Usuario, role: str | None = None, is_active: bool | None = None
) -> MembroEscritorio:
    membro = MembroEscritorio.objects.select_for_update().select_related("escritorio", "usuario").get(id=membro_id)
    if role is not None and role not in MembroEscritorio.Role.values:
        raise EscritorioError("Papel inválido.")
    validar_pode_alterar_membro(por, membro, role)

    campos = []
    if role is not None and role != membro.role:
        membro.role = role
        campos.append("role")
    if is_active is not None and is_active != membro.is_active:
        membro.is_active = is_active
        campos.append("is_active")
    if campos:
        membro.save(update_fields=campos + ["atualizado_em"])
    return membro
