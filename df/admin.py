from django.contrib import admin
from .models import (
    Fundo,
    MecItem,
    ChecklistItemPadrao,
    DocumentoDF,
    LogAcessoDocumento,
    Conta,
    LinhaDFC,
    Movimentacao,
    SaldoInicial,
    AlteracaoStatusPeriodo,
)
from .admin_mixins import TenantScopedAdminMixin


@admin.register(Fundo)
class FundoAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "tipo_fundo", "empresa")
    list_filter = ("tipo_fundo", "empresa")
    search_fields = ("nome", "cnpj", "empresa__nome")
    ordering = ("empresa", "nome")


@admin.register(ChecklistItemPadrao)
class ChecklistItemPadraoAdmin(admin.ModelAdmin):
    list_display = ("tipo_fundo", "secao", "texto", "prazo", "responsavel", "ordem")
    list_filter = ("tipo_fundo", "secao", "responsavel")
    search_fields = ("texto", "secao", "responsavel")
    ordering = ("tipo_fundo", "ordem")
    list_editable = ("ordem",)


@admin.register(DocumentoDF)
class DocumentoDFAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    """Visão de suporte/auditoria — a criação/exclusão de verdade acontece pela
    tela de checklist, com validação, log de acesso e cálculo de quota."""
    list_display = ("nome_original", "empresa", "periodo_df", "tamanho_bytes",
                     "contem_dados_pessoais", "enviado_por", "enviado_em", "excluido_em")
    list_filter = ("empresa", "contem_dados_pessoais", "excluido_em")
    search_fields = ("nome_original", "empresa__nome", "periodo_df__fundo__nome", "sha256")
    ordering = ("-enviado_em",)
    autocomplete_fields = ("enviado_por", "excluido_por")
    readonly_fields = ("tamanho_bytes", "content_type", "sha256", "enviado_em")


@admin.register(LogAcessoDocumento)
class LogAcessoDocumentoAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    """Append-only por design: sem add/change/delete pela UI do admin. Só existe
    para dar visibilidade de auditoria — a gravação de verdade é sempre feita
    por df.services.documento_service.registrar_acesso()."""
    list_display = ("ocorrido_em", "acao", "documento_id_hist", "empresa", "usuario", "ip_address")
    list_filter = ("acao", "empresa")
    search_fields = ("documento_id_hist", "empresa__nome", "usuario__username", "ip_address")
    ordering = ("-ocorrido_em",)
    date_hierarchy = "ocorrido_em"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MecItem)
class MecItemAdmin(admin.ModelAdmin):
    list_display = ("data_posicao", "fundo", "pl", "qtd_cotas", "cota")
    list_filter = ("fundo", "data_posicao")
    search_fields = ("fundo__nome",)
    ordering = ("-data_posicao", "fundo")
    autocomplete_fields = ("fundo",)


@admin.register(LinhaDFC)
class LinhaDFCAdmin(admin.ModelAdmin):
    """Linhas da DFC. O clean() do modelo recusa código inexistente e sobreposição entre linhas."""
    list_display = ("secao", "ordem", "descricao", "prefixos", "sinal", "is_ativa")
    list_filter = ("secao", "is_ativa")
    list_editable = ("ordem", "is_ativa")
    search_fields = ("descricao", "prefixos")
    ordering = ("secao", "ordem")


class SomenteConsultaAdmin(admin.ModelAdmin):
    """Alterações passam pelos services (período fechado, vigência, saldo inicial), não pelo admin."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Conta)
class ContaAdmin(SomenteConsultaAdmin):
    list_display = ("codigo", "descricao", "tipo", "natureza", "nivel", "papel", "is_ativa")
    list_filter = ("natureza", "tipo", "origem_tabela", "is_ativa")
    search_fields = ("codigo", "descricao")


@admin.register(SaldoInicial)
class SaldoInicialAdmin(SomenteConsultaAdmin):
    list_display = ("fundo", "conta", "data_base", "valor")
    list_filter = ("fundo",)
    search_fields = ("conta__codigo", "conta__descricao", "fundo__nome")


@admin.register(Movimentacao)
class MovimentacaoAdmin(SomenteConsultaAdmin):
    list_display = ("data", "fundo", "conta", "valor", "historico", "origem")
    list_filter = ("fundo", "origem")
    search_fields = ("conta__codigo", "historico", "documento", "fundo__nome")
    date_hierarchy = "data"


@admin.register(AlteracaoStatusPeriodo)
class AlteracaoStatusPeriodoAdmin(admin.ModelAdmin):
    """Trilha de auditoria: somente leitura."""
    list_display = ("periodo", "status_anterior", "status_novo", "usuario", "criado_em")
    list_filter = ("status_novo",)
    readonly_fields = ("periodo", "status_anterior", "status_novo", "motivo", "usuario", "criado_em")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
