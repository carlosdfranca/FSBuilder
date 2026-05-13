from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.urls import reverse
from .models import Usuario, Empresa, Membership, Convite

# ----- Inlines -----
class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ("usuario",)
    fields = ("usuario", "role", "is_active", "criado_em", "atualizado_em")
    readonly_fields = ("criado_em", "atualizado_em")
    show_change_link = True


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
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "master", "is_ativo", "criado_em")
    search_fields = ("nome", "cnpj", "master__username", "master__first_name", "master__last_name")
    list_filter = ("is_ativo",)
    autocomplete_fields = ("master",)
    readonly_fields = ("criado_em", "atualizado_em")
    inlines = [MembershipInline]


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

