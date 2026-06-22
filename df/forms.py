"""
Formulários para gerenciamento de configurações de DF e períodos.
"""
from django import forms
from django.core.exceptions import ValidationError
from datetime import date
from df.models import PeriodoDF, ConfiguracaoDF


class ConfiguracaoDFForm(forms.ModelForm):
    """
    Form para configurar o vencimento da DF Anual de um fundo.
    """
    class Meta:
        model = ConfiguracaoDF
        fields = ['anual_dia', 'anual_mes']
        widgets = {
            'anual_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'anual_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
        }
        labels = {
            'anual_dia': 'Anual - Dia', 'anual_mes': 'Anual - Mês',
        }

    def clean(self):
        cleaned_data = super().clean()
        dia = cleaned_data.get('anual_dia')
        mes = cleaned_data.get('anual_mes')
        if not dia or not mes:
            raise ValidationError('DF Anual deve ter dia e mês configurados.')
        try:
            date(2000, mes, dia)
        except ValueError:
            raise ValidationError(f'Data inválida para anual: dia {dia} não existe no mês {mes}.')
        return cleaned_data


class PeriodoDFManualForm(forms.ModelForm):
    """
    Form para criar períodos manuais (Transitória/Encerramento).
    """
    class Meta:
        model = PeriodoDF
        fields = ['tipo_periodo', 'data_vencimento', 'descricao']
        widgets = {
            'tipo_periodo': forms.Select(
                attrs={'class': 'form-select'},
                choices=[
                    ('transitoria', 'DF Transitória'),
                    ('encerramento', 'DF de Encerramento'),
                ]
            ),
            'data_vencimento': forms.DateInput(
                attrs={
                    'class': 'form-control',
                    'type': 'date',
                },
                format='%Y-%m-%d'
            ),
            'descricao': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'placeholder': 'Ex: Fusão com Fundo XYZ',
                }
            ),
        }
        labels = {
            'tipo_periodo': 'Tipo de DF',
            'data_vencimento': 'Data de Vencimento',
            'descricao': 'Descrição (opcional)',
        }
    
    def __init__(self, *args, fundo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fundo = fundo
        
        # Restringir escolhas apenas para tipos manuais
        self.fields['tipo_periodo'].choices = [
            ('transitoria', 'DF Transitória'),
            ('encerramento', 'DF de Encerramento'),
        ]
    
    def clean_tipo_periodo(self):
        tipo = self.cleaned_data['tipo_periodo']
        if tipo not in ['transitoria', 'encerramento']:
            raise ValidationError('Apenas períodos Transitória ou Encerramento podem ser criados manualmente.')
        return tipo
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        if self.fundo:
            instance.fundo = self.fundo
            instance.empresa = self.fundo.empresa
        
        instance.ano = instance.data_vencimento.year
        instance.criado_manualmente = True
        instance.status = 'nao_iniciada'
        
        if commit:
            instance.save()
        
        return instance


class PeriodoSelecaoForm(forms.Form):
    """
    Form simples para seleção de período na UI de geração de DF.
    """
    periodo = forms.ModelChoiceField(
        queryset=PeriodoDF.objects.none(),
        label='Selecione o Período',
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label='-- Selecione --'
    )
    
    def __init__(self, *args, fundo=None, **kwargs):
        super().__init__(*args, **kwargs)
        
        if fundo:
            # Listar apenas períodos que têm dados
            self.fields['periodo'].queryset = PeriodoDF.objects.filter(
                fundo=fundo
            ).exclude(
                status='nao_iniciada'
            ).order_by('-ano', 'tipo_periodo')
            
            # Personalizar exibição no dropdown
            self.fields['periodo'].label_from_instance = lambda obj: f"{obj.nome_exibicao} - {obj.get_status_display()}"
