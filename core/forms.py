from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from df.models import Fundo
from usuarios.models import Usuario

class FundoForm(forms.ModelForm):
    class Meta:
        model = Fundo
        fields = ['nome', 'cnpj']


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