# usuarios/urls.py
from django.urls import path
from usuarios.views_gerenciar import (
    gerenciar_usuarios,
    # empresa_usuario_adicionar,  # DEPRECATED: removido
    empresa_usuario_editar,
    empresa_usuario_excluir,
)
from usuarios.views_selecao import selecionar_empresa
from usuarios.views_contabilidade import painel_contabilidade
from usuarios import views_escritorios as esc
from usuarios.views_convite import (
    convidar_usuario,
    listar_convites,
    reenviar_convite,
    cancelar_convite,
    aceitar_convite,
)
from usuarios.views_password_reset import (
    esqueci_senha,
    redefinir_senha,
)

urlpatterns = [
    # Painel da contabilidade (empresas vinculadas ao escritório do usuário)
    path("contabilidade/", painel_contabilidade, name="painel_contabilidade"),

    # Escritórios de contabilidade: cadastro, equipe (convites) e vínculos com empresas
    path("escritorios/", esc.escritorios_listar, name="escritorios_listar"),
    path("escritorios/criar/", esc.escritorio_criar, name="escritorio_criar"),
    path("escritorios/convite/<uuid:token>/", esc.aceitar_convite_escritorio, name="aceitar_convite_escritorio"),
    path("escritorios/<int:escritorio_id>/", esc.escritorio_detalhe, name="escritorio_detalhe"),
    path("escritorios/<int:escritorio_id>/editar/", esc.escritorio_editar, name="escritorio_editar"),
    path("escritorios/<int:escritorio_id>/convites/criar/", esc.escritorio_convite_criar, name="escritorio_convite_criar"),
    path("escritorios/<int:escritorio_id>/convites/<int:convite_id>/reenviar/", esc.escritorio_convite_reenviar, name="escritorio_convite_reenviar"),
    path("escritorios/<int:escritorio_id>/convites/<int:convite_id>/cancelar/", esc.escritorio_convite_cancelar, name="escritorio_convite_cancelar"),
    path("escritorios/<int:escritorio_id>/membros/<int:membro_id>/editar/", esc.escritorio_membro_editar, name="escritorio_membro_editar"),
    path("escritorios/<int:escritorio_id>/empresas/vincular/", esc.escritorio_empresa_vincular, name="escritorio_empresa_vincular"),
    path("escritorios/<int:escritorio_id>/empresas/<int:vinculo_id>/alterar/", esc.escritorio_empresa_alterar, name="escritorio_empresa_alterar"),
    path("api/empresas/buscar/", esc.empresas_buscar, name="empresas_buscar"),

    # Gestão de usuários
    path("empresa/usuarios/", gerenciar_usuarios, name="gerenciar_usuarios"),
    # DEPRECATED: Método antigo removido - Use o sistema de convites
    # path("empresa/usuarios/adicionar/", empresa_usuario_adicionar, name="empresa_usuario_adicionar"),
    path("empresa/usuarios/<int:membership_id>/editar/", empresa_usuario_editar, name="empresa_usuario_editar"),
    path("empresa/usuarios/<int:membership_id>/excluir/", empresa_usuario_excluir, name="empresa_usuario_excluir"),

    # Gestão de convites
    path("empresa/usuarios/convidar/", convidar_usuario, name="convidar_usuario"),
    path("empresa/usuarios/convites/", listar_convites, name="listar_convites"),
    path("empresa/usuarios/convites/<int:convite_id>/reenviar/", reenviar_convite, name="reenviar_convite"),
    path("empresa/usuarios/convites/<int:convite_id>/cancelar/", cancelar_convite, name="cancelar_convite"),
    
    # Aceitar convite (público - sem empresa/ no path)
    path("convites/aceitar/<uuid:token>/", aceitar_convite, name="aceitar_convite"),

    # Esqueci minha senha (público)
    path("senha/esqueci/", esqueci_senha, name="esqueci_senha"),
    path("senha/redefinir/<uuid:token>/", redefinir_senha, name="redefinir_senha"),

    # Seleção de empresa
    path("selecionar-empresa/", selecionar_empresa, name="selecionar_empresa"),
]
