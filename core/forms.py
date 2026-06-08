from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from datetime import date
from df.models import Fundo, ConfiguracaoDF
from usuarios.models import Usuario

class FundoForm(forms.ModelForm):
    """
    Form para criar/editar Fundo com configuração de DFs integrada.
    """
    # Checkboxes para tipos recorrentes
    tem_trimestral = forms.BooleanField(
        required=False,
        label="DF Trimestral",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    tem_anual = forms.BooleanField(
        required=False,
        label="DF Anual",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    # Campos para vencimentos trimestrais
    trim1_dia = forms.IntegerField(required=False, min_value=1, max_value=31, label="1º Tri - Dia", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'}))
    trim1_mes = forms.IntegerField(required=False, min_value=1, max_value=12, label="1º Tri - Mês", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'}))
    trim2_dia = forms.IntegerField(required=False, min_value=1, max_value=31, label="2º Tri - Dia", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'}))
    trim2_mes = forms.IntegerField(required=False, min_value=1, max_value=12, label="2º Tri - Mês", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'}))
    trim3_dia = forms.IntegerField(required=False, min_value=1, max_value=31, label="3º Tri - Dia", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'}))
    trim3_mes = forms.IntegerField(required=False, min_value=1, max_value=12, label="3º Tri - Mês", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'}))
    trim4_dia = forms.IntegerField(required=False, min_value=1, max_value=31, label="4º Tri - Dia", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'}))
    trim4_mes = forms.IntegerField(required=False, min_value=1, max_value=12, label="4º Tri - Mês", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'}))

    # Campos para vencimento anual
    anual_dia = forms.IntegerField(required=False, min_value=1, max_value=31, label="Anual - Dia", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia'}))
    anual_mes = forms.IntegerField(required=False, min_value=1, max_value=12, label="Anual - Mês", widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês'}))

    class Meta:
        model = Fundo
        fields = ['nome', 'cnpj']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'cnpj': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            config_trim = self.instance.configuracoes_df.filter(tipo='trimestral').first()
            if config_trim:
                self.fields['tem_trimestral'].initial = True
                for i in range(1, 5):
                    self.fields[f'trim{i}_dia'].initial = getattr(config_trim, f'trim{i}_dia')
                    self.fields[f'trim{i}_mes'].initial = getattr(config_trim, f'trim{i}_mes')

            config_anual = self.instance.configuracoes_df.filter(tipo='anual').first()
            if config_anual:
                self.fields['tem_anual'].initial = True
                self.fields['anual_dia'].initial = config_anual.anual_dia
                self.fields['anual_mes'].initial = config_anual.anual_mes

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get('tem_trimestral'):
            for i in range(1, 5):
                dia = cleaned_data.get(f'trim{i}_dia')
                mes = cleaned_data.get(f'trim{i}_mes')
                if not dia or not mes:
                    raise ValidationError(f'Se DF Trimestral está ativa, todos os 4 trimestres devem ter dia e mês configurados (faltando {i}º trimestre).')
                try:
                    date(2000, mes, dia)
                except ValueError:
                    raise ValidationError(f'Data inválida para {i}º trimestre: dia {dia} não existe no mês {mes}.')

        if cleaned_data.get('tem_anual'):
            dia = cleaned_data.get('anual_dia')
            mes = cleaned_data.get('anual_mes')
            if not dia or not mes:
                raise ValidationError('Se DF Anual está ativa, deve ter dia e mês configurados.')
            try:
                date(2000, mes, dia)
            except ValueError:
                raise ValidationError(f'Data inválida para anual: dia {dia} não existe no mês {mes}.')

        return cleaned_data

    def save_configuracoes(self, instance):
        """Salva os registros ConfiguracaoDF. Chamar após fundo.save() quando commit=False."""
        if self.cleaned_data.get('tem_trimestral'):
            defaults = {}
            for i in range(1, 5):
                defaults[f'trim{i}_dia'] = self.cleaned_data.get(f'trim{i}_dia')
                defaults[f'trim{i}_mes'] = self.cleaned_data.get(f'trim{i}_mes')
            ConfiguracaoDF.objects.update_or_create(fundo=instance, tipo='trimestral', defaults=defaults)
        else:
            ConfiguracaoDF.objects.filter(fundo=instance, tipo='trimestral').delete()

        if self.cleaned_data.get('tem_anual'):
            ConfiguracaoDF.objects.update_or_create(
                fundo=instance, tipo='anual',
                defaults={
                    'anual_dia': self.cleaned_data.get('anual_dia'),
                    'anual_mes': self.cleaned_data.get('anual_mes'),
                }
            )
        else:
            ConfiguracaoDF.objects.filter(fundo=instance, tipo='anual').delete()

        tipos_ativos = []
        if self.cleaned_data.get('tem_trimestral'):
            tipos_ativos.append('trimestral')
        if self.cleaned_data.get('tem_anual'):
            tipos_ativos.append('anual')
        if tipos_ativos:
            from df.services.periodo_service import gerar_periodos_para_ano
            from datetime import date as _date
            gerar_periodos_para_ano(instance, _date.today().year)

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self.save_configuracoes(instance)
        return instance


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