from django.urls import path, include
from django.contrib.auth import views as auth_views
from .views import *
from usuarios.views import trocar_empresa_ativa
from . import views_movimentacoes, views_plano_unico

urlpatterns = [
    # User Views
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
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
    path("periodos/<int:periodo_id>/saldos/", saldos_periodo, name="saldos_periodo"),
    path("api/periodo/<int:periodo_id>/saldos/salvar/", salvar_saldo_view, name="salvar_saldo"),
    path("api/periodo/<int:periodo_id>/saldos/<int:item_id>/excluir/", excluir_saldo_view, name="excluir_saldo"),
    path('dre-resultado/<int:periodo_df_id>/', df_resultado, name='dre_resultado'),
    path("dre-resultado/<int:periodo_df_id>/exportar/", exportar_dfs_excel, name="exportar_dfs_excel"),
    path("dre-resultado/<int:periodo_df_id>/exportar-docx/", exportar_dfs_docx, name="exportar_dfs_docx"),
    path("dre-resultado/<int:periodo_df_id>/contexto-operacional/", salvar_contexto_operacional, name="salvar_contexto_operacional"),


    # Fundos
    path('fundos/', listar_fundos, name='listar_fundos'),
    path('fundos/adicionar/', adicionar_fundo, name='adicionar_fundo'),
    path('fundos/<int:fundo_id>/editar/', editar_fundo, name='editar_fundo'),
    path('fundos/<int:fundo_id>/excluir/', excluir_fundo, name='excluir_fundo'),

    # Plano de Contas (mapeamento de contas por empresa)
    path('plano-de-contas/', plano_de_contas, name='plano_de_contas'),
    path('api/plano-de-contas/', criar_mapeamento, name='criar_mapeamento'),
    path('api/plano-de-contas/<int:mapeamento_id>/editar/', editar_mapeamento, name='editar_mapeamento'),
    path('api/plano-de-contas/<int:mapeamento_id>/excluir/', excluir_mapeamento, name='excluir_mapeamento'),
    path('api/plano-de-contas/pendentes/descartar/', descartar_pendente, name='descartar_pendente'),

    # Plano de Contas Único (global)
    path('plano-de-contas-unico/', views_plano_unico.plano_contas_unico, name='plano_contas_unico'),
    path('api/plano-de-contas-unico/', views_plano_unico.criar_conta, name='criar_conta'),
    path('api/plano-de-contas-unico/<int:conta_id>/editar/', views_plano_unico.editar_conta, name='editar_conta'),
    path('api/plano-de-contas-unico/buscar/', views_movimentacoes.contas_buscar, name='contas_buscar'),

    # Movimentações e saldos iniciais (por fundo)
    path('fundos/<int:fundo_id>/movimentacoes/', views_movimentacoes.movimentacoes_fundo, name='movimentacoes_fundo'),
    path('fundos/<int:fundo_id>/movimentacoes/nova/', views_movimentacoes.movimentacao_criar, name='movimentacao_criar'),
    path('fundos/<int:fundo_id>/movimentacoes/<int:mov_id>/editar/', views_movimentacoes.movimentacao_editar, name='movimentacao_editar'),
    path('fundos/<int:fundo_id>/movimentacoes/<int:mov_id>/excluir/', views_movimentacoes.movimentacao_excluir, name='movimentacao_excluir'),
    path('fundos/<int:fundo_id>/movimentacoes/importar/', views_movimentacoes.movimentacoes_importar, name='movimentacoes_importar'),
    path('fundos/<int:fundo_id>/movimentacoes/importar/modelo/', views_movimentacoes.movimentacoes_modelo, name='movimentacoes_modelo'),
    path('fundos/<int:fundo_id>/movimentacoes/lotes/<int:lote_id>/desfazer/', views_movimentacoes.movimentacoes_lote_desfazer, name='movimentacoes_lote_desfazer'),
    path('fundos/<int:fundo_id>/saldos-iniciais/', views_movimentacoes.saldos_iniciais_fundo, name='saldos_iniciais_fundo'),
    path('fundos/<int:fundo_id>/saldos-iniciais/salvar/', views_movimentacoes.saldo_inicial_salvar, name='saldo_inicial_salvar'),
    path('fundos/<int:fundo_id>/saldos-iniciais/importar/', views_movimentacoes.saldos_iniciais_importar, name='saldos_iniciais_importar'),
    path('fundos/<int:fundo_id>/saldos-iniciais/<int:saldo_id>/excluir/', views_movimentacoes.saldo_inicial_excluir, name='saldo_inicial_excluir'),

    # Gestoras
    path('gestoras/adicionar/', adicionar_gestora, name='adicionar_gestora'),
    path('gestoras/<int:gestora_id>/editar/', editar_gestora, name='editar_gestora'),
    path('gestoras/<int:gestora_id>/excluir/', excluir_gestora, name='excluir_gestora'),
    path('fundos/<int:fundo_id>/periodos/', gerenciar_periodos, name='gerenciar_periodos'),
    path('fundos/<int:fundo_id>/periodos/criar/', criar_periodo_manual, name='criar_periodo_manual'),
    path('fundos/<int:fundo_id>/periodos/gerar-historico/', gerar_periodos_historicos, name='gerar_periodos_historicos'),
    path('fundos/<int:fundo_id>/periodos/<int:periodo_id>/excluir/', excluir_periodo, name='excluir_periodo'),

    # Checklist por Período
    path('api/periodo/<int:periodo_id>/checklist/', api_checklist_periodo, name='api_checklist_periodo'),
    path('api/periodo/<int:periodo_id>/checklist/adicionar/', adicionar_item_checklist, name='adicionar_item_checklist'),
    path('api/checklist-item/<int:item_id>/toggle/', toggle_item_checklist, name='toggle_item_checklist'),
    path('api/checklist-item/<int:item_id>/editar/', editar_item_checklist, name='editar_item_checklist'),
    path('api/checklist-item/<int:item_id>/excluir/', excluir_item_checklist, name='excluir_item_checklist'),

    # Documentos anexados à DF
    path('api/periodo/<int:periodo_id>/documento/upload/', upload_documento_periodo, name='upload_documento_periodo'),
    path('documento/<int:documento_id>/download/', download_documento, name='download_documento'),
    path('api/documento/<int:documento_id>/excluir/', excluir_documento, name='excluir_documento'),

    # Painel administrativo de documentos (Fase 5 — só Global Admin)
    path('painel-documentos/', painel_documentos, name='painel_documentos'),
    path('painel-documentos/<int:empresa_id>/', painel_documentos_empresa, name='painel_documentos_empresa'),
    path('painel-documentos/<int:empresa_id>/config/', painel_documentos_config, name='painel_documentos_config'),
    path('painel-documentos/documento/<int:documento_id>/expurgar/', expurgar_documento, name='expurgar_documento'),
]