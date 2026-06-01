from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
import uuid
from datetime import timedelta

_cnpj_regex_validator = RegexValidator(
    regex=r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$|^\d{14}$",
    message="CNPJ deve estar no formato 00.000.000/0000-00 ou apenas 14 dígitos."
)

# ===== Usuário =====
class Usuario(AbstractUser):
    class GlobalRole(models.TextChoices):
        NONE = "NONE", "Sem papel global"
        PLATFORM_ADMIN = "PLATFORM_ADMIN", "Admin da Plataforma"   # vê/edita tudo
        PLATFORM_VIEWER = "PLATFORM_VIEWER", "Viewer da Plataforma" # vê tudo, sem editar

    global_role = models.CharField(
        max_length=20,
        choices=GlobalRole.choices,
        default=GlobalRole.NONE,
        help_text="Papel global (plataforma). Admin/Viewer vê todas as empresas."
    )

    def __str__(self):
        return f"{self.get_full_name() or self.username}"

    # ---- helpers globais ----
    def is_platform_admin(self) -> bool:
        # superuser continua sendo 'deus'; global admin também
        return self.is_superuser or self.global_role == self.GlobalRole.PLATFORM_ADMIN

    def is_platform_viewer(self) -> bool:
        return self.is_superuser or self.global_role in {
            self.GlobalRole.PLATFORM_ADMIN, self.GlobalRole.PLATFORM_VIEWER
        }

    def has_global_scope(self) -> bool:
        # qualquer papel global (viewer/admin) ou superuser
        return self.is_platform_viewer()


# ===== Empresa =====
class Empresa(models.Model):
    """
    Entidade 'tenant' que agrupa usuários e dados.
    Mantém um ponteiro explícito para o usuário Master para garantir unicidade
    de forma simples e performática (especialmente em MySQL).
    """
    nome = models.CharField(max_length=255, unique=True)
    cnpj = models.CharField(
        max_length=18, null=True, blank=True, unique=True,
        validators=[_cnpj_regex_validator],
        help_text="Opcional. Use 00.000.000/0000-00 ou 14 dígitos."
    )

    # Um (e apenas um) Master por empresa.
    # Usamos PROTECT para impedir deletar o Master sem antes trocar o master da empresa.
    master = models.ForeignKey(
        "Usuario",
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="empresas_como_master",
        help_text="Usuário Master desta empresa (um por empresa)."
    )

    # Campos operacionais úteis
    is_ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        indexes = [
            models.Index(name="idx_empresa_nome", fields=["nome"]),
        ]

    def __str__(self):
        return self.nome

    # ---- Regras de consistência com Membership (iremos criar a classe Membership depois) ----
    def clean(self):
        """
        Se um master está definido, ele precisa ter vínculo com a empresa e role MASTER.
        """
        if self.master_id:
            has_master_link = Membership.objects.filter(
                empresa=self, usuario_id=self.master_id, role=Membership.Role.MASTER
            ).exists()
            if not has_master_link:
                raise ValidationError(_("O usuário definido como master precisa ter vínculo MASTER nesta empresa."))

    @transaction.atomic
    def definir_master(self, usuario):
        """
        Centraliza a troca do Master: cria/ajusta Membership e seta empresa.master.
        """
        memb, _ = Membership.objects.get_or_create(
            empresa=self, usuario=usuario, defaults={"role": Membership.Role.MASTER}
        )
        if memb.role != Membership.Role.MASTER:
            memb.role = Membership.Role.MASTER
            memb.save(update_fields=["role"])
        self.master = usuario
        self.full_clean()
        self.save(update_fields=["master"])

    # Azulejos de conveniência
    def is_master(self, usuario):
        return self.master_id == getattr(usuario, "id", None)

    def usuarios(self):
        """
        Retorna todos os usuários vinculados à empresa via Membership.
        """
        return Usuario.objects.filter(
            memberships__empresa=self,
            memberships__is_active=True
        )


class Membership(models.Model):
    class Role(models.TextChoices):
        MASTER = "MASTER", "Master (administra tudo da empresa)"
        ADMIN = "ADMIN", "Admin (gerencia usuários/permissões da empresa)"
        MEMBER = "MEMBER", "Membro (acesso padrão)"
        VIEWER = "VIEWER", "Somente leitura"

    empresa = models.ForeignKey("Empresa", on_delete=models.CASCADE, related_name="memberships")
    usuario = models.ForeignKey("Usuario", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vínculo de Usuário com Empresa"
        verbose_name_plural = "Vínculos de Usuários com Empresas"
        unique_together = (("empresa", "usuario"),)  # um vínculo por par empresa-usuário
        indexes = [
            models.Index(fields=["empresa", "role"], name="idx_memb_empresa_role"),
            models.Index(fields=["usuario", "empresa"], name="idx_memb_usuario_empresa"),
        ]

    def __str__(self):
        return f"{self.usuario} @ {self.empresa} ({self.role})"

    # --- Regras de integridade e sincronização com Empresa.master ---
    def clean(self):
        """
        Garante coerência do papel MASTER com Empresa.master.
        - Se este vínculo é MASTER e a empresa já tem outro master, erro.
        - Se a empresa.master está definido e for diferente do usuário, este vínculo não pode ser MASTER.
        """
        if not self.empresa_id or not self.usuario_id:
            return

        empresa = self.empresa

        if self.role == self.Role.MASTER:
            if empresa.master_id and empresa.master_id != self.usuario_id:
                raise ValidationError(_("Esta empresa já possui um Master diferente."))
        else:
            pass

    @transaction.atomic
    def save(self, *args, **kwargs):
        creating = self._state.adding
        old_role = None
        if not creating:
            old = type(self).objects.filter(pk=self.pk).only("role").first()
            old_role = old.role if old else None

        super().save(*args, **kwargs)

        # Sincronização com Empresa.master
        empresa = self.empresa
        if self.role == self.Role.MASTER:
            if empresa.master_id != self.usuario_id:
                empresa.master = self.usuario
                empresa.full_clean(exclude=None)
                empresa.save(update_fields=["master"])
        else:
            if empresa.master_id == self.usuario_id:
                empresa.master = None
                empresa.save(update_fields=["master"])

    # --- Helpers de permissão ---
    def can_manage_company_users(self) -> bool:
        return self.role in {self.Role.MASTER, self.Role.ADMIN} and self.is_active

    def can_edit_data(self) -> bool:
        return self.role in {self.Role.MASTER, self.Role.ADMIN, self.Role.MEMBER} and self.is_active

    def can_view(self) -> bool:
        return self.is_active


# ===== Convite =====
class Convite(models.Model):
    """
    Representa um convite por email para um usuário se juntar a uma empresa.
    
    Fluxo:
    1. MASTER/ADMIN cria convite (email + role)
    2. Sistema envia email com link único (token UUID4)
    3. Usuário clica no link e completa cadastro
    4. Sistema cria Usuario + Membership, marca convite como ACCEPTED
    
    Estados:
    - PENDING: Aguardando aceite do usuário
    - ACCEPTED: Usuário completou cadastro
    - EXPIRED: Convite expirou (não aceito no prazo)
    - CANCELLED: Admin cancelou o convite
    """
    
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pendente")
        ACCEPTED = "ACCEPTED", _("Aceito")
        EXPIRED = "EXPIRED", _("Expirado")
        CANCELLED = "CANCELLED", _("Cancelado")
    
    # ===== Relações =====
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="convites",
        verbose_name=_("Empresa"),
        help_text=_("Empresa para a qual o usuário está sendo convidado")
    )
    
    convidado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="convites_enviados",
        verbose_name=_("Convidado por"),
        help_text=_("Usuário que criou o convite (MASTER/ADMIN)")
    )
    
    usuario_criado = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="convites_aceitos",
        verbose_name=_("Usuário criado"),
        help_text=_("Usuário que foi criado ao aceitar este convite")
    )
    
    # ===== Dados do Convite =====
    email = models.EmailField(
        max_length=254,
        db_index=True,
        verbose_name=_("Email"),
        help_text=_("Email do usuário convidado")
    )
    
    role = models.CharField(
        max_length=10,
        choices=Membership.Role.choices,
        verbose_name=_("Papel"),
        help_text=_("Papel que o usuário terá na empresa ao aceitar o convite")
    )
    
    # ===== Token e Segurança =====
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        verbose_name=_("Token"),
        help_text=_("Token único para validação do convite (UUID4)")
    )
    
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("Status"),
        help_text=_("Estado atual do convite")
    )
    
    # ===== Timestamps =====
    criado_em = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Criado em"),
        help_text=_("Data e hora de criação do convite")
    )
    
    expira_em = models.DateTimeField(
        verbose_name=_("Expira em"),
        help_text=_("Data e hora de expiração do convite")
    )
    
    aceito_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Aceito em"),
        help_text=_("Data e hora em que o convite foi aceito")
    )
    
    cancelado_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Cancelado em"),
        help_text=_("Data e hora em que o convite foi cancelado")
    )
    
    # ===== Audit e Controle =====
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP"),
        help_text=_("Endereço IP do usuário que aceitou o convite")
    )
    
    user_agent = models.TextField(
        blank=True,
        verbose_name=_("User Agent"),
        help_text=_("Browser/device do usuário que aceitou o convite")
    )
    
    tentativas_envio = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("Tentativas de envio"),
        help_text=_("Número de vezes que o email foi enviado (original + reenvios)")
    )
    
    ultimo_envio_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Último envio em"),
        help_text=_("Data e hora do último envio do email")
    )
    
    class Meta:
        verbose_name = _("Convite")
        verbose_name_plural = _("Convites")
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "status"], name="idx_convite_empresa_status"),
            models.Index(fields=["email", "empresa"], name="idx_convite_email_empresa"),
            models.Index(fields=["token"], name="idx_convite_token"),
            models.Index(fields=["status", "expira_em"], name="idx_convite_status_expira"),
        ]
        constraints = [
            # Apenas um convite pendente por email+empresa
            models.UniqueConstraint(
                fields=["empresa", "email"],
                condition=models.Q(status="PENDING"),
                name="uq_convite_pendente_email_empresa",
                violation_error_message=_("Já existe um convite pendente para este email nesta empresa.")
            )
        ]
    
    def __str__(self):
        return f"Convite para {self.email} @ {self.empresa.nome} ({self.get_status_display()})"
    
    def save(self, *args, **kwargs):
        """
        Ao criar um novo convite, define automaticamente a data de expiração.
        """
        if not self.pk and not self.expira_em:
            # Importa aqui para evitar circular import
            from django.conf import settings
            dias = getattr(settings, 'CONVITE_EXPIRACAO_DIAS', 7)
            self.expira_em = timezone.now() + timedelta(days=dias)
        super().save(*args, **kwargs)
    
    # ===== Métodos de validação =====
    def is_valido(self) -> bool:
        """
        Verifica se o convite está válido para ser aceito.
        
        Returns:
            bool: True se status é PENDING e não expirou
        """
        return (
            self.status == self.Status.PENDING
            and timezone.now() <= self.expira_em
            and self.empresa.is_ativo
        )
    
    def marcar_expirado(self):
        """
        Marca o convite como expirado se ele ainda estiver pendente e passou da data de expiração.
        """
        if self.status == self.Status.PENDING and timezone.now() > self.expira_em:
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])
    
    def get_link_aceite(self, base_url: str = None) -> str:
        """
        Gera o link completo para aceitar o convite.
        
        Args:
            base_url: URL base do site (ex: https://fsbuilder.com)
                     Se não fornecido, retorna apenas o path relativo
        
        Returns:
            str: URL completa ou path relativo para aceitar o convite
        """
        path = f"/convites/aceitar/{self.token}/"
        if base_url:
            return f"{base_url.rstrip('/')}{path}"
        return path
    
    def pode_reenviar(self) -> bool:
        """
        Verifica se o convite pode ser reenviado.
        
        Returns:
            bool: True se status é PENDING ou EXPIRED e não excedeu limite de reenvios
        """
        from django.conf import settings
        max_reenvios = getattr(settings, 'CONVITE_MAX_REENVIOS', 3)
        return (
            self.status in {self.Status.PENDING, self.Status.EXPIRED}
            and self.tentativas_envio < max_reenvios
        )
