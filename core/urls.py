from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.views.decorators.clickjacking import xframe_options_exempt
from .views import *
from usuarios.views import trocar_empresa_ativa

urlpatterns = [
    # Portal split login
    path('portal/', login_split, name='login_split'),
    path('portal-redirect/', xframe_options_exempt(portal_redirect), name='portal_redirect'),

    # User Views
    path('login/', xframe_options_exempt(auth_views.LoginView.as_view(template_name='login.html')), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('perfil/', editar_perfil, name='editar_perfil'),
    path('trocar-empresa/', trocar_empresa_ativa, name='trocar_empresa_ativa'),

    path("", include("usuarios.urls")),

    # Demonstração Financeira
    path('', demonstracao_financeira, name='demonstracao_financeira'),
    path('controle-emissoes/', controle_emissoes, name='controle_emissoes'),
    path("importar-balancete/", importar_balancete_view, name="importar_balancete"),
    path("importar-mec/", importar_mec_view, name="importar_mec"),
    path("api/fundo/<int:fundo_id>/periodos/", api_periodos_fundo, name="api_periodos_fundo"),
    path("api/periodo/<int:periodo_id>/status/", atualizar_status_manual, name="atualizar_status_manual"),
    path('dre-resultado/<int:periodo_df_id>/', df_resultado, name='dre_resultado'),
    path("dre-resultado/<int:periodo_df_id>/exportar/", exportar_dfs_excel, name="exportar_dfs_excel"),
    path("dre-resultado/<int:periodo_df_id>/exportar-docx/", exportar_dfs_docx, name="exportar_dfs_docx"),


    # Fundos
    path('fundos/', listar_fundos, name='listar_fundos'),
    path('fundos/adicionar/', adicionar_fundo, name='adicionar_fundo'),
    path('fundos/<int:fundo_id>/editar/', editar_fundo, name='editar_fundo'),
    path('fundos/<int:fundo_id>/excluir/', excluir_fundo, name='excluir_fundo'),

    # Gestoras
    path('gestoras/adicionar/', adicionar_gestora, name='adicionar_gestora'),
    path('gestoras/<int:gestora_id>/editar/', editar_gestora, name='editar_gestora'),
    path('gestoras/<int:gestora_id>/excluir/', excluir_gestora, name='excluir_gestora'),
    path('fundos/<int:fundo_id>/periodos/', gerenciar_periodos, name='gerenciar_periodos'),
    path('fundos/<int:fundo_id>/periodos/criar/', criar_periodo_manual, name='criar_periodo_manual'),
    path('fundos/<int:fundo_id>/periodos/gerar-historico/', gerar_periodos_historicos, name='gerar_periodos_historicos'),
    path('fundos/<int:fundo_id>/periodos/<int:periodo_id>/excluir/', excluir_periodo, name='excluir_periodo'),
]   