from django.contrib import admin
from .models import (
    Fundo,
    GrupoGrande,
    GrupoPequeno,
    MapeamentoContas,
    BalanceteItem,
    MecItem,
)
from .admin_mixins import TenantScopedAdminMixin


@admin.register(Fundo)
class FundoAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "empresa")
    list_filter = ("empresa",)
    search_fields = ("nome", "cnpj", "empresa__nome")
    ordering = ("empresa", "nome")


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
