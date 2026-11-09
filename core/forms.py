from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from datetime import date
from decimal import Decimal, InvalidOperation
from df.models import Fundo, ConfiguracaoDF, Gestora, GrupoPequeno, MapeamentoContas
from usuarios.models import Usuario, Empresa


class MapeamentoContasForm(forms.ModelForm):
    """
    Plano de Contas: conta do balancete → grupinho. A empresa nunca vem do POST;
    é passada pela view. Com `travar_conta=True` (conta já usada em balancetes) o
    código não pode mudar, só descrição e grupinho.
    """
    class Meta:
        model = MapeamentoContas
        fields = ['conta', 'descricao', 'grupo_pequeno']

    def __init__(self, *args, empresa, travar_conta=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa
        self.travar_conta = travar_conta
        self.fields['grupo_pequeno'].queryset = (
            GrupoPequeno.objects.select_related('grupao')
            .order_by('grupao__tipo', 'grupao__ordem', 'grupao__nome', 'nome')
        )
        self.fields['grupo_pequeno'].required = False

    def clean_conta(self):
        if self.travar_conta and self.instance.pk:
            return self.instance.conta
        conta = (self.cleaned_data.get('conta') or '').strip()
        if not conta:
            raise ValidationError("Informe o código da conta.")
        duplicada = MapeamentoContas.objects.filter(empresa=self.empresa, conta=conta)
        if self.instance.pk:
            duplicada = duplicada.exclude(pk=self.instance.pk)
        if duplicada.exists():
            raise ValidationError(f"A conta {conta} já está mapeada nesta empresa.")
        return conta

    def clean_descricao(self):
        return (self.cleaned_data.get('descricao') or '').strip() or None


class SaldoBalanceteForm(forms.Form):
    """
    Lançamento manual de um saldo (atual ou anterior) de um período. A empresa vem da
    view; só contas já classificadas no Plano de Contas dessa empresa são aceitas.
    """
    COLUNA_CHOICES = [('atual', 'Saldo atual'), ('anterior', 'Saldo anterior')]

    mapeamento = forms.ModelChoiceField(queryset=MapeamentoContas.objects.none())
    coluna = forms.ChoiceField(choices=COLUNA_CHOICES)
    saldo_final = forms.CharField()

    def __init__(self, *args, empresa, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mapeamento'].queryset = MapeamentoContas.objects.filter(
            empresa=empresa, grupo_pequeno__isnull=False
        )
        self.fields['mapeamento'].error_messages['invalid_choice'] = (
            "Conta inválida ou ainda não classificada no Plano de Contas."
        )

    def clean_saldo_final(self):
        bruto = (self.cleaned_data.get('saldo_final') or '').strip().replace('R$', '').replace(' ', '')
        if not bruto:
            raise ValidationError("Informe o valor do saldo.")
        if ',' in bruto:
            bruto = bruto.replace('.', '').replace(',', '.')
        try:
            valor = Decimal(bruto)
        except InvalidOperation:
            raise ValidationError("Valor inválido.")
        if not valor.is_finite() or abs(valor) >= Decimal('1e18'):
            raise ValidationError("Valor fora do limite permitido.")
        return valor.quantize(Decimal('0.01'))


class GestoraForm(forms.ModelForm):
    class Meta:
        model = Gestora
        fields = ['nome', 'cnpj']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nome da gestora'}),
            'cnpj': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '00.000.000/0000-00'}),
        }


class FundoForm(forms.ModelForm):
    """
    Form para criar/editar Fundo com configuração de DF Anual integrada.
    """
    anual_dia = forms.IntegerField(
        required=True, min_value=1, max_value=31,
        label="Dia do Vencimento",
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'})
    )
    anual_mes = forms.IntegerField(
        required=True, min_value=1, max_value=12,
        label="Mês do Vencimento",
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'})
    )

    class Meta:
        model = Fundo
        fields = ['nome', 'cnpj', 'tipo_fundo', 'gestora']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'cnpj': forms.TextInput(attrs={'class': 'form-control'}),
            'tipo_fundo': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)

        _empresa = empresa
        if _empresa is None and self.instance and self.instance.pk:
            _empresa = self.instance.empresa
        if _empresa is not None:
            self.fields['gestora'].queryset = Gestora.objects.filter(empresa=_empresa).order_by('nome')

        if self.instance.pk:
            config = self.instance.configuracoes_df.first()
            if config:
                self.fields['anual_dia'].initial = config.anual_dia
                self.fields['anual_mes'].initial = config.anual_mes

    def clean(self):
        cleaned_data = super().clean()
        dia = cleaned_data.get('anual_dia')
        mes = cleaned_data.get('anual_mes')
        if dia and mes:
            try:
                date(2000, mes, dia)
            except ValueError:
                raise ValidationError(f'Data inválida para anual: dia {dia} não existe no mês {mes}.')
        return cleaned_data

    def save_configuracoes(self, instance):
        """Salva o registro ConfiguracaoDF. Chamar após fundo.save() quando commit=False."""
        ConfiguracaoDF.objects.update_or_create(
            fundo=instance,
            defaults={
                'anual_dia': self.cleaned_data.get('anual_dia'),
                'anual_mes': self.cleaned_data.get('anual_mes'),
            }
        )
        from df.services.periodo_service import gerar_periodos_para_ano
        from datetime import date as _date
        gerar_periodos_para_ano(instance, _date.today().year)

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self.save_configuracoes(instance)
        return instance


class EmpresaDocumentosConfigForm(forms.ModelForm):
    """Config de documentos de uma empresa, usada no painel administrativo
    (Fase 5.2) — as mesmas 3 colunas que hoje só dá pra editar pelo Django /admin."""
    class Meta:
        model = Empresa
        fields = ['documentos_habilitados', 'retencao_anos', 'quota_documentos_gb']
        widgets = {
            'documentos_habilitados': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'retencao_anos': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'quota_documentos_gb': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }
        labels = {
            'documentos_habilitados': 'Upload de documentos habilitado',
            'retencao_anos': 'Retenção (anos)',
            'quota_documentos_gb': 'Quota (GB — 0 = sem limite)',
        }
        help_texts = {
            'documentos_habilitados': 'Ligar somente após o contrato de operador (DPA) estar assinado.',
        }


class EditarPerfilForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Nova Senha",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Digite uma nova senha (opcional)'
        }),
        required=False,
        help_text="Mínimo 8 caracteres. Não pode ser inteiramente numérica ou muito comum."
    )
    password2 = forms.CharField(
        label="Confirmar Nova Senha",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirme a nova senha'
        }),
        required=False,
        help_text="Digite a mesma senha novamente para confirmação."
    )

    class Meta:
        model = Usuario
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Digite seu nome'
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Digite seu sobrenome'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Digite seu e-mail'
            }),
        }
        labels = {
            'first_name': 'Nome',
            'last_name': 'Sobrenome',
            'email': 'E-mail',
        }

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        
        # Se uma senha foi fornecida, ambas devem coincidir
        if password1 or password2:
            if password1 != password2:
                raise ValidationError({
                    'password2': 'As senhas não coincidem.'
                })
            
            # Validar força da senha se foi fornecida
            if password1:
                try:
                    validate_password(password1)
                except ValidationError as e:
                    raise ValidationError({
                        'password1': e.messages
                    })
        
        return cleaned
    
    def save(self, commit=True):
        user = super().save(commit=False)
        password1 = self.cleaned_data.get('password1')
        
        # Atualizar senha se foi fornecida
        if password1:
            user.set_password(password1)
        
        if commit:
            user.save()
        
        return user