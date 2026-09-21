from django.contrib import admin
from .models import (
    Fundo,
    GrupoGrande,
    GrupoPequeno,
    MapeamentoContas,
    BalanceteItem,
    MecItem,
    ChecklistItemPadrao,
    DocumentoDF,
    LogAcessoDocumento,
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


@admin.register(GrupoGrande)
class GrupoGrandeAdmin(admin.ModelAdmin):
    list_display = ("nome", "tipo")
    list_filter = ("tipo", )
    search_fields = ("nome",)
    ordering = ("nome", )


@admin.register(GrupoPequeno)
class GrupoPequenoAdmin(admin.ModelAdmin):
    list_display = ("nome", "grupao")
    search_fields = ("nome", "grupao__nome")
    ordering = ("grupao", "nome")


@admin.register(MapeamentoContas)
class MapeamentoContasAdmin(TenantScopedAdminMixin, admin.ModelAdmin):
    list_display = ("conta", "empresa", "grupo_pequeno",)
    list_filter = ("empresa", )
    search_fields = ("empresa__nome", )
    ordering = ("empresa", "grupo_pequeno__grupao__nome", "grupo_pequeno__nome", "conta")
    autocomplete_fields = ("grupo_pequeno",)

    @admin.display(ordering="grupo_pequeno__grupao__nome", description="Grupão")
    def get_grupao(self, obj):
        return obj.grupo_pequeno.grupao.nome if obj.grupo_pequeno else "—"
    
    def save_model(self, request, obj, form, change):
        # Auto-atribuir empresa se não estiver definida (para usuários não-globais)
        if not obj.empresa_id and not request.user.has_global_scope():
            empresa = getattr(request, "empresa_ativa", None)
            if empresa:
                obj.empresa = empresa
        super().save_model(request, obj, form, change)


@admin.register(BalanceteItem)
class BalanceteItemAdmin(admin.ModelAdmin):
    list_display = ("data_referencia", "fundo", "get_conta", "saldo_final")
    list_filter = ("data_referencia", "fundo")
    search_fields = ("fundo__nome", "conta_corrente__conta")
    ordering = ("-data_referencia", "fundo")
    autocomplete_fields = ("fundo", "conta_corrente")
    readonly_fields = ("data_importacao",)

    @admin.display(ordering="conta_corrente__conta", description="Conta")
    def get_conta(self, obj):
        return obj.conta_corrente.conta if obj.conta_corrente else "—"


@admin.register(MecItem)
class MecItemAdmin(admin.ModelAdmin):
    list_display = ("data_posicao", "fundo", "pl", "qtd_cotas", "cota")
    list_filter = ("fundo", "data_posicao")
    search_fields = ("fundo__nome",)
    ordering = ("-data_posicao", "fundo")
    autocomplete_fields = ("fundo",)
