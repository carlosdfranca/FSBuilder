import uuid
from pathlib import Path

from django.db import models
from decimal import Decimal
from usuarios.models import Empresa, Usuario

from .storages import documentos_storage


# =========================
# GESTORAS (escopo por empresa)
# =========================
class Gestora(models.Model):
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="gestoras",
        db_index=True,
    )
    nome = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=20)

    class Meta:
        verbose_name = "Gestora"
        verbose_name_plural = "Gestoras"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "nome"], name="uq_gestora_empresa_nome"),
            models.UniqueConstraint(fields=["empresa", "cnpj"], name="uq_gestora_empresa_cnpj"),
        ]

    def __str__(self):
        return self.nome


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
    gestora = models.ForeignKey(
        Gestora,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fundos",
    )
    nome = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=20)

    TIPO_FUNDO_CHOICES = [
        ('FIDC', 'FIDC'),
        ('FIF', 'FIF'),
        ('FII', 'FII'),
        ('FIP', 'FIP'),
    ]
    tipo_fundo = models.CharField(
        max_length=10,
        choices=TIPO_FUNDO_CHOICES,
        default='FIDC',
        db_index=True,
        help_text="Tipo do fundo — define qual checklist de documentos padrão é aplicado."
    )

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

    @property
    def config_anual(self):
        return self.configuracoes_df.first()


# =========================
# HISTÓRICO DE EMISSÃO DE DFs
# =========================
class HistoricoEmissaoDF(models.Model):
    fundo = models.ForeignKey(
        Fundo,
        on_delete=models.CASCADE,
        related_name="historico_emissoes",
        db_index=True,
        help_text="Fundo para o qual a DF foi emitida"
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="historico_emissoes_df",
        db_index=True,
        help_text="Empresa (tenant) - para facilitar queries por escopo"
    )
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emissoes_df",
        help_text="Usuário que gerou/baixou a DF"
    )
    data_emissao = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Data e hora em que a DF foi gerada/baixada"
    )
    data_referencia_df = models.DateField(
        db_index=True,
        help_text="Data de referência (saldo atual) da DF gerada"
    )
    data_anterior_df = models.DateField(
        null=True,
        blank=True,
        help_text="Data anterior (comparação) da DF gerada, ou None se zerado"
    )
    
    TIPO_EXPORTACAO_CHOICES = [
        ('excel', 'Excel'),
        ('word', 'Word'),
        ('visualizacao', 'Visualização'),
    ]
    tipo_exportacao = models.CharField(
        max_length=20,
        choices=TIPO_EXPORTACAO_CHOICES,
        help_text="Formato de exportação ou se foi apenas visualização"
    )
    periodo_df = models.ForeignKey(
        'PeriodoDF',
        on_delete=models.SET_NULL,
        related_name="historico_emissoes",
        null=True,
        blank=True,
        db_index=True,
        help_text="Período de DF ao qual esta emissão pertence (null para emissões legadas)"
    )

    class Meta:
        verbose_name = "Histórico de Emissão de DF"
        verbose_name_plural = "Históricos de Emissões de DFs"
        ordering = ["-data_emissao"]
        indexes = [
            models.Index(fields=["empresa", "fundo", "-data_emissao"], name="idx_hist_emp_fundo_data"),
            models.Index(fields=["fundo", "data_referencia_df"], name="idx_hist_fundo_ref"),
        ]

    def __str__(self):
        return f"[{self.data_emissao:%d/%m/%Y %H:%M}] {self.fundo.nome} - {self.get_tipo_exportacao_display()}"


# =========================
# CONFIGURAÇÃO DE DFs DO FUNDO
# =========================
class ConfiguracaoDF(models.Model):
    """
    Configuração de vencimento da DF Anual de um fundo.
    Transitória e Encerramento não têm configuração — são criadas manualmente.
    """
    fundo = models.ForeignKey(
        Fundo,
        on_delete=models.CASCADE,
        related_name="configuracoes_df",
        help_text="Fundo ao qual esta configuração pertence"
    )

    anual_dia = models.IntegerField(null=True, blank=True, help_text="Dia do vencimento anual (1-31)")
    anual_mes = models.IntegerField(null=True, blank=True, help_text="Mês do vencimento anual (1-12)")

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração de DF"
        verbose_name_plural = "Configurações de DFs"
        constraints = [
            models.UniqueConstraint(fields=['fundo'], name='uq_configuracaodf_fundo')
        ]

    def __str__(self):
        return f"Config DF Anual: {self.fundo.nome}"

    def clean(self):
        from django.core.exceptions import ValidationError
        from datetime import date

        if self.anual_dia is not None and (self.anual_dia < 1 or self.anual_dia > 31):
            raise ValidationError('Dia anual deve estar entre 1 e 31')
        if self.anual_mes is not None and (self.anual_mes < 1 or self.anual_mes > 12):
            raise ValidationError('Mês anual deve estar entre 1 e 12')
        if self.anual_dia is not None and self.anual_mes is not None:
            try:
                date(2000, self.anual_mes, self.anual_dia)
            except ValueError:
                raise ValidationError(f'Data inválida para anual: dia {self.anual_dia} não existe no mês {self.anual_mes}')


# =========================
# PERÍODOS DE DF
# =========================
class PeriodoDF(models.Model):
    """
    Representa um período específico de DF (ex: "2025 Anual").
    Períodos anuais são gerados automaticamente.
    Períodos transitórios e de encerramento são criados manualmente.
    """
    fundo = models.ForeignKey(
        Fundo,
        on_delete=models.CASCADE,
        related_name="periodos_df",
        db_index=True,
        help_text="Fundo ao qual este período pertence"
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="periodos_df",
        db_index=True,
        help_text="Empresa (tenant) - facilita queries por escopo"
    )
    
    TIPO_PERIODO_CHOICES = [
        ('anual', 'Anual'),
        ('transitoria', 'Transitória'),
        ('encerramento', 'Encerramento'),
    ]
    tipo_periodo = models.CharField(
        max_length=20,
        choices=TIPO_PERIODO_CHOICES,
        help_text="Tipo deste período"
    )

    ano = models.IntegerField(help_text="Ano do período (ex: 2025)")
    
    data_vencimento = models.DateField(
        help_text="Data de vencimento deste período"
    )
    
    # Datas de referência dos dados
    data_referencia = models.DateField(
        null=True,
        blank=True,
        help_text="Data de referência (saldo atual) quando dados são importados"
    )
    data_anterior = models.DateField(
        null=True,
        blank=True,
        help_text="Data anterior (saldo comparação) ou null para zerado"
    )
    
    STATUS_CHOICES = [
        ('nao_iniciada', 'Não Iniciada'),
        ('em_andamento', 'Em Andamento'),
        ('finalizada', 'Finalizada'),
        ('vencida', 'Vencida'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='nao_iniciada',
        help_text="Status atual do período"
    )
    
    criado_manualmente = models.BooleanField(
        default=False,
        help_text="True para Transitória/Encerramento criados pelo usuário"
    )
    
    descricao = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Descrição opcional para períodos manuais"
    )
    
    notificado_90 = models.BooleanField(default=False, db_index=True)
    notificado_60 = models.BooleanField(default=False, db_index=True)
    notificado_30 = models.BooleanField(default=False, db_index=True)
    notificado_15 = models.BooleanField(default=False, db_index=True)
    notificado_1  = models.BooleanField(default=False, db_index=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Período de DF"
        verbose_name_plural = "Períodos de DFs"
        ordering = ["empresa", "fundo", "-ano"]
        constraints = [
            models.UniqueConstraint(
                fields=["fundo", "tipo_periodo", "ano"],
                name="uq_periodo_fundo_tipo_ano",
                condition=models.Q(tipo_periodo='anual')
            ),
        ]
        indexes = [
            models.Index(fields=["empresa", "fundo", "ano"], name="idx_periodo_emp_fundo_ano"),
            models.Index(fields=["fundo", "ano", "tipo_periodo"], name="idx_periodo_fundo_ano_tipo"),
            models.Index(fields=["status", "data_vencimento"], name="idx_periodo_status_venc"),
        ]
    
    def __str__(self):
        return f"{self.nome_exibicao} - {self.fundo.nome}"
    
    @property
    def nome_exibicao(self):
        """Retorna nome amigável para exibição: '2025 Anual', '2025 Transitória', etc."""
        if self.tipo_periodo == 'anual':
            return f"{self.ano} Anual"
        elif self.tipo_periodo == 'transitoria':
            desc = f" - {self.descricao}" if self.descricao else ""
            return f"{self.ano} Transitória{desc}"
        elif self.tipo_periodo == 'encerramento':
            desc = f" - {self.descricao}" if self.descricao else ""
            return f"{self.ano} Encerramento{desc}"
        return f"{self.ano} {self.get_tipo_periodo_display()}"


# =========================
# CHECKLIST DE DOCUMENTOS
# =========================
class ChecklistItemPadrao(models.Model):
    """Template global de checklist por tipo de fundo — copiado para cada novo PeriodoDF."""
    tipo_fundo = models.CharField(
        max_length=10,
        choices=Fundo.TIPO_FUNDO_CHOICES,
        db_index=True,
        help_text="Tipo de fundo ao qual este item de checklist padrão se aplica."
    )
    secao = models.CharField(max_length=120, blank=True, default='')
    texto = models.CharField(max_length=255)
    prazo = models.CharField(max_length=120, blank=True, default='')
    responsavel = models.CharField(max_length=120, blank=True, default='')
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Item de Checklist Padrão"
        verbose_name_plural = "Itens de Checklist Padrão"
        ordering = ["tipo_fundo", "ordem"]
        indexes = [
            models.Index(fields=["tipo_fundo", "ordem"], name="idx_checklist_padrao_tipo"),
        ]

    def __str__(self):
        return f"[{self.tipo_fundo}] {self.texto}"


class ChecklistItemPeriodo(models.Model):
    """Instância real do checklist para um PeriodoDF — editável individualmente."""
    periodo_df = models.ForeignKey(
        PeriodoDF,
        on_delete=models.CASCADE,
        related_name="checklist_items",
        db_index=True,
    )
    secao = models.CharField(max_length=120, blank=True, default='')
    texto = models.CharField(max_length=255)
    prazo = models.CharField(max_length=120, blank=True, default='')
    responsavel = models.CharField(max_length=120, blank=True, default='')
    ordem = models.PositiveIntegerField(default=0)
    recebido = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Item de Checklist do Período"
        verbose_name_plural = "Itens de Checklist do Período"
        ordering = ["periodo_df", "ordem"]
        indexes = [
            models.Index(fields=["periodo_df", "recebido"], name="idx_checklist_periodo_recebido"),
        ]

    def __str__(self):
        status = "✓" if self.recebido else "○"
        return f"{status} {self.texto}"


def documento_df_upload_to(instance, filename):
    """Caminho opaco (UUID), não o nome original.

    Elimina path traversal, nomes reservados do Windows (CON, PRN, NUL),
    unicode problemático e colisões. Também evita vazar PII no caminho: nomes
    reais chegam como "CPF_12345678900_joao_silva.pdf", e o caminho aparece em
    log de servidor, backup e listagem de diretório. O nome original fica em
    `nome_original` e volta no Content-Disposition do download.
    """
    ext = Path(filename).suffix.lower()[:10]
    periodo = instance.periodo_df
    return (
        f"documentos/emp_{periodo.empresa_id}/fundo_{periodo.fundo_id}/"
        f"{periodo.ano}/periodo_{periodo.id}/{uuid.uuid4().hex}{ext}"
    )


class DocumentoDF(models.Model):
    """Arquivo anexado a um período de DF, opcionalmente vinculado a um item do checklist.

    Model separado do checklist (não um FileField em ChecklistItemPeriodo) porque
    ressincronizar_checklist_por_tipo() apaga e recria os itens de checklist quando
    o tipo do fundo muda — um FileField ali perderia o arquivo nesse resync. Aqui o
    vínculo usa SET_NULL e é refeito por texto, do mesmo jeito que `recebido` já é
    preservado hoje.
    """

    # Tenant denormalizado — mesmo padrão de PeriodoDF e HistoricoEmissaoDF, para
    # permitir restrict_by_empresa(qs, user, "empresa") em um hop.
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="documentos_df",
        db_index=True,
        help_text="Empresa (tenant) - para facilitar queries por escopo"
    )
    periodo_df = models.ForeignKey(
        PeriodoDF,
        on_delete=models.CASCADE,
        related_name="documentos",
        db_index=True,
    )
    checklist_item = models.ForeignKey(
        ChecklistItemPeriodo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos",
        help_text="Item de checklist ao qual este arquivo está vinculado, se houver."
    )
    checklist_texto = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Snapshot do texto do item no momento do upload — usado para "
                   "re-vincular o documento por texto quando o checklist é resincronizado."
    )

    arquivo = models.FileField(
        upload_to=documento_df_upload_to,
        storage=documentos_storage,
        max_length=500,
    )
    nome_original = models.CharField(max_length=255)
    tamanho_bytes = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default="")
    sha256 = models.CharField(max_length=64, db_index=True, blank=True, default="")

    contem_dados_pessoais = models.BooleanField(
        default=False,
        help_text="Arquivo contém dados pessoais de terceiros (ex.: kits cadastrais "
                   "de cotistas). Usado para localizar PII em pedidos de titular (LGPD)."
    )
    retencao_ate = models.DateField(
        null=True, blank=True,
        help_text="Data a partir da qual o documento pode ser expurgado."
    )

    enviado_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_enviados",
    )
    enviado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    excluido_em = models.DateTimeField(null=True, blank=True, db_index=True)
    excluido_por = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_excluidos",
    )

    class Meta:
        verbose_name = "Documento da DF"
        verbose_name_plural = "Documentos da DF"
        ordering = ["-enviado_em"]
        indexes = [
            models.Index(fields=["periodo_df", "excluido_em"], name="idx_doc_periodo_ativo"),
            models.Index(fields=["empresa", "excluido_em"], name="idx_doc_empresa_ativo"),
            models.Index(fields=["empresa", "retencao_ate"], name="idx_doc_retencao"),
        ]

    def __str__(self):
        return self.nome_original


class LogAcessoDocumento(models.Model):
    """Registro append-only de operações sobre documentos da DF — Art. 37 da LGPD
    (registro das operações de tratamento).

    Nunca deve haver update/delete numa linha aqui, nem na UI, nem no admin.
    `documento_id_hist` preserva o rastro mesmo depois de um expurgo físico do
    documento (quando `documento` vira NULL via SET_NULL).
    """

    class Acao(models.TextChoices):
        UPLOAD = "upload", "Upload"
        DOWNLOAD = "download", "Download"
        EXCLUSAO = "exclusao", "Exclusão"
        NEGADO = "negado", "Acesso negado"
        EXPURGO = "expurgo", "Expurgo físico"

    documento = models.ForeignKey(
        DocumentoDF,
        on_delete=models.SET_NULL,
        null=True,
        related_name="logs_acesso",
    )
    documento_id_hist = models.BigIntegerField(
        null=True,
        help_text="Id do documento no momento do evento — sobrevive mesmo se o "
                   "documento for expurgado fisicamente e o FK acima virar NULL."
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name="logs_acesso_documentos",
        db_index=True,
    )
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        related_name="logs_acesso_documentos",
    )
    acao = models.CharField(max_length=20, choices=Acao.choices, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    ocorrido_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Log de Acesso a Documento"
        verbose_name_plural = "Logs de Acesso a Documentos"
        ordering = ["-ocorrido_em"]
        indexes = [
            models.Index(fields=["documento", "-ocorrido_em"], name="idx_logacesso_doc_data"),
            models.Index(fields=["empresa", "-ocorrido_em"], name="idx_logacesso_emp_data"),
        ]

    def __str__(self):
        return f"[{self.get_acao_display()}] doc #{self.documento_id_hist} — {self.ocorrido_em:%d/%m/%Y %H:%M}"


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
    periodo_df = models.ForeignKey(
        'PeriodoDF',
        on_delete=models.CASCADE,
        related_name="balancete_items",
        null=True,
        blank=True,
        db_index=True,
        help_text="Período de DF ao qual estes dados pertencem (null para dados legados)"
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
    periodo_df = models.ForeignKey(
        'PeriodoDF',
        on_delete=models.CASCADE,
        related_name="mec_items",
        null=True,
        blank=True,
        db_index=True,
        help_text="Período de DF ao qual estes dados pertencem (null para dados legados)"
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
