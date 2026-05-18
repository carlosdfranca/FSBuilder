from django.db import models
from decimal import Decimal
from usuarios.models import Empresa


# =========================
# FUNDOS (escopo por empresa)
# =========================
class Fundo(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="fundos",
        db_index=True,
        help_text="Empresa (tenant) proprietária deste fundo."
    )
    nome = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=20)

    class Meta:
        verbose_name = "Fundo"
        verbose_name_plural = "Fundos"
        ordering = ["empresa", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "cnpj"], name="uq_fundo_empresa_cnpj"),
            models.UniqueConstraint(fields=["empresa", "nome"], name="uq_fundo_empresa_nome"),
        ]
        indexes = [
            models.Index(fields=["empresa", "nome"], name="idx_fundo_emp_nome"),
            models.Index(fields=["empresa", "cnpj"], name="idx_fundo_emp_cnpj"),
        ]

    def __str__(self):
        return f"{self.nome} [{self.cnpj}] - {self.empresa.nome}"


# =========================
# GRUPÕES (nível 1)
# =========================
class GrupoGrande(models.Model):
    nome = models.CharField(max_length=255)
    ordem = models.IntegerField(null=True, blank=True, default=None)

    TIPO_CHOICES = [
        (1, "Ativo"),
        (2, "Passivo"),
        (3, "Patrimônio Líquido"),
        (4, "Resultado"),
    ]
    tipo = models.IntegerField(choices=TIPO_CHOICES, null=True, blank=True)

    class Meta:
        verbose_name = "Grupão de Contas"
        verbose_name_plural = "Grupões de Contas"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["nome", "tipo"], name="uq_grupogrande_nome_tipo")
        ]

    def __str__(self):
        return f"{self.nome} ({self.get_tipo_display()})"


# =========================
# GRUPINHOS (nível 2)
# =========================
class GrupoPequeno(models.Model):
    nome = models.CharField(max_length=255)
    grupao = models.ForeignKey(
        GrupoGrande,
        on_delete=models.CASCADE,
        related_name="grupinhos"
    )

    class Meta:
        verbose_name = "Grupinho de Contas"
        verbose_name_plural = "Grupinhos de Contas"
        ordering = ["grupao", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["nome", "grupao"], name="uq_grupopequeno_nome_grupao")
        ]

    def __str__(self):
        return f"{self.nome} (→ {self.grupao.nome})"


# =========================
# MAPA DE CONTAS
# =========================
class MapeamentoContas(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="mapeamentos_contas",
        db_index=True,
        help_text="Empresa (tenant) proprietária deste mapeamento."
    )
    conta = models.CharField(max_length=30)
    grupo_pequeno = models.ForeignKey(
        GrupoPequeno,
        on_delete=models.PROTECT,
        related_name="contas",
        null=True,
        blank=True
    )
    descricao = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        verbose_name = "Mapeamento de CC"
        verbose_name_plural = "Mapeamentos de CC"
        ordering = ["empresa", "grupo_pequeno", "conta"]
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "conta"],
                name="uq_mapeamento_empresa_conta"
            )
        ]
        indexes = [
            models.Index(fields=["empresa", "conta"], name="idx_map_empresa_conta"),
        ]
    
    def __str__(self):
        return f"{self.conta} - {self.empresa.nome}"


# =================================================
# BALANCETE (por fundo, ano e conta mapeada)
# =================================================
class BalanceteItem(models.Model):
    fundo = models.ForeignKey(
        Fundo,
        on_delete=models.CASCADE,
        related_name="balancete",
        db_index=True,
    )
    data_referencia = models.DateField(null=True, blank=True, db_index=True)
    conta_corrente = models.ForeignKey(
        MapeamentoContas,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="itens",
    )
    saldo_final = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    data_importacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item do Balancete"
        verbose_name_plural = "Itens do Balancete"
        ordering = ["fundo", "conta_corrente"]
        constraints = [
            models.UniqueConstraint(
                fields=["fundo", "data_referencia", "conta_corrente"],
                name="uq_balancete_fundo_data_conta"
            )
        ]
        indexes = [
            models.Index(fields=["data_referencia"], name="idx_bal_data_referencia"),
            models.Index(fields=["fundo", "data_referencia"], name="idx_bal_fundo_data_referencia"),
        ]

    def __str__(self):
        conta = self.conta_corrente.conta if self.conta_corrente else "—"
        saldo = f"{self.saldo_final:.2f}" if self.saldo_final is not None else "—"
        return f"[{self.data_referencia}] {self.fundo.nome} | {conta} | Saldo: R$ {saldo}"


# =================================================
# MEC (por fundo, e Data da posição)
# =================================================
class MecItem(models.Model):
    fundo = models.ForeignKey(
        Fundo,
        on_delete=models.CASCADE,
        related_name="mec_itens",
        db_index=True,
    )
    data_posicao = models.DateField(db_index=True)
    aplicacao = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    resgate = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    estorno = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    pl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    qtd_cotas = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"))
    cota = models.DecimalField(max_digits=24, decimal_places=8, default=Decimal("0"))

    class Meta:
        verbose_name = "Item MEC"
        verbose_name_plural = "Itens MEC"
        ordering = ["fundo", "data_posicao"]
        constraints = [
            models.UniqueConstraint(fields=["fundo", "data_posicao"], name="uq_mecitem_fundo_data"),
            models.CheckConstraint(
                check=models.Q(aplicacao__gte=0) & models.Q(resgate__gte=0) & models.Q(estorno__gte=0),
                name="ck_mecitem_valores_nao_negativos",
            ),
            models.CheckConstraint(
                check=models.Q(pl__gte=0) & models.Q(qtd_cotas__gte=0) & models.Q(cota__gte=0),
                name="ck_mecitem_posicoes_nao_negativas",
            ),
        ]
        indexes = [
            models.Index(fields=["data_posicao"], name="idx_mecitem_data"),
            models.Index(fields=["fundo", "data_posicao"], name="idx_mecitem_fundo_data"),
        ]

    def __str__(self):
        return f"[{self.data_posicao:%d/%m/%Y}] {self.fundo.nome} | PL R$ {self.pl} | Cotas {self.qtd_cotas} | Cota {self.cota}"
