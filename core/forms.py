from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from datetime import date
from df.models import Fundo
from usuarios.models import Usuario

class FundoForm(forms.ModelForm):
    # Campos customizados para dia e mês
    dia_vencimento_df = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=31,
        label="Dia",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ex: 15'
        })
    )
    mes_vencimento_df = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=12,
        label="Mês",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ex: 3'
        })
    )
    
    class Meta:
        model = Fundo
        fields = ['nome', 'cnpj']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Se estiver editando e já houver data, popular os campos
        if self.instance.pk and self.instance.data_vencimento_df:
            self.fields['dia_vencimento_df'].initial = self.instance.data_vencimento_df.day
            self.fields['mes_vencimento_df'].initial = self.instance.data_vencimento_df.month
    
    def clean(self):
        cleaned_data = super().clean()
        dia = cleaned_data.get('dia_vencimento_df')
        mes = cleaned_data.get('mes_vencimento_df')
        
        # Ambos devem ser fornecidos ou ambos vazios
        if (dia and not mes) or (mes and not dia):
            raise ValidationError("Por favor, informe tanto o dia quanto o mês, ou deixe ambos em branco.")
        
        # Se ambos fornecidos, validar e criar a data
        if dia and mes:
            try:
                # Usar ano fixo 2000 (não importa para a lógica)
                cleaned_data['data_vencimento_df'] = date(2000, mes, dia)
            except ValueError:
                raise ValidationError(f"Data inválida: dia {dia} não existe no mês {mes}.")
        else:
            cleaned_data['data_vencimento_df'] = None
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.data_vencimento_df = self.cleaned_data.get('data_vencimento_df')
        if commit:
            instance.save()
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