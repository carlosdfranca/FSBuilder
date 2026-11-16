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
