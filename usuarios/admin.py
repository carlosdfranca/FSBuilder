from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.urls import reverse
from .models import (
    Usuario, Empresa, Membership, Convite,
    EscritorioContabil, MembroEscritorio, VinculoContabil, ConviteEscritorio,
)

# ----- Inlines -----
class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("usuario",)
    fields = ("usuario", "role", "is_active", "criado_em", "atualizado_em")
    readonly_fields = ("criado_em", "atualizado_em")
    show_change_link = True


class VinculoContabilInline(admin.TabularInline):
    """Escritório responsável pela empresa. A regra 'um ativo por empresa' é validada no modelo."""
    model = VinculoContabil
    extra = 0
    autocomplete_fields = ("escritorio",)
    fields = ("escritorio", "is_active", "criado_por", "criado_em")
    readonly_fields = ("criado_por", "criado_em")


class MembroEscritorioInline(admin.TabularInline):
    model = MembroEscritorio
    extra = 0
    autocomplete_fields = ("usuario",)
    fields = ("usuario", "role", "is_active", "criado_em")
    readonly_fields = ("criado_em",)


class VinculoPorEscritorioInline(admin.TabularInline):
    model = VinculoContabil
    extra = 0
    autocomplete_fields = ("empresa",)
    fields = ("empresa", "is_active", "criado_por", "criado_em")
    readonly_fields = ("criado_por", "criado_em")


class RegistraCriadorMixin:
    """Preenche `criado_por` nos vínculos contábeis novos criados pelo admin."""

    def save_formset(self, request, form, formset, change):
        instancias = formset.save(commit=False)
        for obj in instancias:
            if isinstance(obj, VinculoContabil) and not obj.pk:
                obj.criado_por = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


# ----- Usuario -----
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "email", "global_role", "is_active", "is_staff", "last_login")
    search_fields = ("username", "first_name", "last_name", "email")
    list_filter = ("global_role", "is_staff", "is_superuser", "is_active", "groups")
    ordering = ("username",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Informações pessoais", {"fields": ("first_name", "last_name", "email")}),
        ("Escopo Global", {"fields": ("global_role",)}),  # << aqui
        ("Permissões", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Datas importantes", {"fields": ("last_login", "date_joined")}),
    )

    inlines = [MembershipInline]


# ----- Empresa -----
@admin.register(Empresa)
class EmpresaAdmin(RegistraCriadorMixin, admin.ModelAdmin):
    list_display = ("nome", "cnpj", "master", "max_fundos", "is_ativo",
                     "documentos_habilitados", "criado_em")
    list_editable = ("max_fundos",)
    search_fields = ("nome", "cnpj", "master__username", "master__first_name", "master__last_name")
    list_filter = ("is_ativo", "documentos_habilitados")
    autocomplete_fields = ("master",)
    readonly_fields = (
        "criado_em", "atualizado_em",
        "contexto_operacional_atualizado_em", "contexto_operacional_atualizado_por",
    )
    inlines = [MembershipInline, VinculoContabilInline]

    fieldsets = (
        (None, {"fields": ("nome", "cnpj", "master", "is_ativo", "max_fundos")}),
        ("Texto da DF (Word)", {
            "fields": (
                "contexto_operacional",
                "contexto_operacional_atualizado_em",
                "contexto_operacional_atualizado_por",
            ),
        }),
        ("Documentos", {
            "fields": ("documentos_habilitados", "retencao_anos", "quota_documentos_gb"),
            "description": "documentos_habilitados só deve ser ligado após o contrato "
                            "de operador (DPA) estar assinado com esta empresa.",
        }),
        ("Datas", {"fields": ("criado_em", "atualizado_em")}),
    )


# ----- Escritório de contabilidade -----
@admin.register(EscritorioContabil)
class EscritorioContabilAdmin(RegistraCriadorMixin, admin.ModelAdmin):
    list_display = ("nome", "cnpj", "is_ativo", "qtd_membros", "qtd_empresas", "criado_em")
    list_filter = ("is_ativo",)
    search_fields = ("nome", "cnpj")
    readonly_fields = ("criado_em", "atualizado_em")
    inlines = [MembroEscritorioInline, VinculoPorEscritorioInline]

    @admin.display(description="Membros ativos")
    def qtd_membros(self, obj):
        return obj.membros.filter(is_active=True).count()

    @admin.display(description="Empresas vinculadas")
    def qtd_empresas(self, obj):
        return obj.vinculos.filter(is_active=True).count()


# ----- Membership -----
@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("usuario", "empresa", "role", "is_active", "criado_em")
    list_filter = ("role", "is_active", "empresa")
    search_fields = (
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "empresa__nome",
        "empresa__cnpj",
    )
    autocomplete_fields = ("usuario", "empresa")
    readonly_fields = ("criado_em", "atualizado_em")
    ordering = ("empresa", "usuario")


# ----- Convite -----
@admin.register(Convite)
class ConviteAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "empresa",
        "role",
        "status_badge",
        "convidado_por",
        "criado_em",
        "expira_em",
        "tentativas_envio",
    )
    list_filter = ("status", "role", "empresa", "criado_em")
    search_fields = (
        "email",
        "empresa__nome",
        "convidado_por__username",
        "convidado_por__first_name",
        "usuario_criado__username",
    )
    autocomplete_fields = ("empresa", "convidado_por", "usuario_criado")
    readonly_fields = (
        "token",
        "criado_em",
        "aceito_em",
        "cancelado_em",
        "ultimo_envio_em",
        "ip_address",
        "user_agent",
        "link_aceite_display",
    )
    ordering = ("-criado_em",)
    
    fieldsets = (
        ("Informações do Convite", {
            "fields": ("empresa", "email", "role", "status")
        }),
        ("Token e Segurança", {
            "fields": ("token", "link_aceite_display")
        }),
        ("Rastreamento", {
            "fields": ("convidado_por", "usuario_criado")
        }),
        ("Datas", {
            "fields": ("criado_em", "expira_em", "aceito_em", "cancelado_em")
        }),
        ("Envio de Email", {
            "fields": ("tentativas_envio", "ultimo_envio_em")
        }),
        ("Audit", {
            "fields": ("ip_address", "user_agent"),
            "classes": ("collapse",)
        }),
    )
    
    def status_badge(self, obj):
        """Exibe o status com cor."""
        colors = {
            Convite.Status.PENDING: "#ffc107",  # amarelo
            Convite.Status.ACCEPTED: "#28a745",  # verde
            Convite.Status.EXPIRED: "#dc3545",  # vermelho
            Convite.Status.CANCELLED: "#6c757d",  # cinza
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = "Status"
    
    def link_aceite_display(self, obj):
        """Exibe o link de aceite (apenas para visualização)."""
        if obj.token:
            link = obj.get_link_aceite()
            return format_html(
                '<a href="{}" target="_blank">{}</a>',
                link,
                link
            )
        return "-"
    link_aceite_display.short_description = "Link de Aceite"
    
    def has_add_permission(self, request):
        """Desabilita criação via admin (usar sistema de convites)."""
        return False



# ----- Convite de escritório de contabilidade (consulta: o fluxo é feito pela tela de escritórios) -----
@admin.register(ConviteEscritorio)
class ConviteEscritorioAdmin(admin.ModelAdmin):
    list_display = ("email", "escritorio", "role", "status", "convidado_por", "criado_em", "expira_em", "tentativas_envio")
    list_filter = ("status", "role", "escritorio")
    search_fields = ("email", "escritorio__nome")
    ordering = ("-criado_em",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # o token é a credencial do convite: só a tela/serviço o altera
