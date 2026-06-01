from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from usuarios.models import Usuario, Membership, Convite

class CompanyUserCreateForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=Membership.Role.choices,
        label="Papel na empresa",
    )
    password1 = forms.CharField(label="Senha", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar senha", widget=forms.PasswordInput)

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "email"]

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("As senhas não coincidem.")
        return cleaned


class CompanyUserUpdateForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=Membership.Role.choices,
        label="Papel na empresa",
    )
    # Campos de redefinição de senha (opcionais)
    password1 = forms.CharField(label="Nova senha", widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(label="Confirmar nova senha", widget=forms.PasswordInput, required=False)

    class Meta:
        model = Usuario
        fields = ["first_name", "email"]

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if (p1 or p2) and p1 != p2:
            raise ValidationError("As senhas não coincidem.")
        return cleaned


# ===== Forms de Convite =====

class ConvidarUsuarioForm(forms.Form):
    """
    Form para criar um convite de usuário.
    Admin preenche apenas email e role.
    """
    email = forms.EmailField(
        label="Email",
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'usuario@exemplo.com',
            'autocomplete': 'email'
        }),
        help_text="O usuário receberá um email com link para completar o cadastro."
    )
    
    role = forms.ChoiceField(
        label="Papel na empresa",
        choices=Membership.Role.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Papel que o usuário terá ao aceitar o convite."
    )
    
    def clean_email(self):
        """Normaliza o email."""
        email = self.cleaned_data.get('email')
        if email:
            email = email.lower().strip()
        return email


class AceitarConviteForm(forms.Form):
    """
    Form para o usuário aceitar o convite e completar o cadastro.
    """
    username = forms.CharField(
        label="Nome de usuário",
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'usuario',
            'autocomplete': 'username'
        }),
        help_text="Escolha um nome de usuário único. Apenas letras, números e @/./+/-/_ permitidos."
    )
    
    first_name = forms.CharField(
        label="Primeiro nome",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'João',
            'autocomplete': 'given-name'
        })
    )
    
    last_name = forms.CharField(
        label="Sobrenome",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Silva',
            'autocomplete': 'family-name'
        })
    )
    
    password1 = forms.CharField(
        label="Senha",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••',
            'autocomplete': 'new-password'
        }),
        help_text="Mínimo 8 caracteres. Não pode ser muito comum ou totalmente numérica."
    )
    
    password2 = forms.CharField(
        label="Confirmar senha",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••',
            'autocomplete': 'new-password'
        })
    )
    
    def clean_username(self):
        """Valida se o username já existe."""
        username = self.cleaned_data.get('username')
        if username and Usuario.objects.filter(username=username).exists():
            raise ValidationError("Este nome de usuário já está em uso. Escolha outro.")
        return username
    
    def clean_password2(self):
        """Valida se as senhas coincidem."""
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2:
            if password1 != password2:
                raise ValidationError("As senhas não coincidem.")
            
            # Valida força da senha usando validadores do Django
            try:
                validate_password(password1)
            except ValidationError as e:
                raise ValidationError(e.messages)
        
        return password2

