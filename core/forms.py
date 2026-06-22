from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from datetime import date
from df.models import Fundo, ConfiguracaoDF
from usuarios.models import Usuario

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
        fields = ['nome', 'cnpj']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control'}),
            'cnpj': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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