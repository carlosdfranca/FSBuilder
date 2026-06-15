"""
Formulários para gerenciamento de configurações de DF e períodos.
"""
from django import forms
from django.core.exceptions import ValidationError
from datetime import date
from df.models import PeriodoDF, ConfiguracaoDF


class ConfiguracaoDFForm(forms.ModelForm):
    """
    Form para configurar um tipo de DF (trimestral ou anual) de um fundo.
    Um registro por tipo — instanciar com tipo='trimestral' ou tipo='anual'.
    """
    class Meta:
        model = ConfiguracaoDF
        fields = [
            'tipo',
            'trim1_dia', 'trim1_mes',
            'trim2_dia', 'trim2_mes',
            'trim3_dia', 'trim3_mes',
            'trim4_dia', 'trim4_mes',
            'anual_dia', 'anual_mes',
        ]
        widgets = {
            'tipo': forms.HiddenInput(),
            'trim1_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'trim1_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
            'trim2_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'trim2_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
            'trim3_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'trim3_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
            'trim4_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'trim4_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
            'anual_dia': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Dia', 'min': 1, 'max': 31}),
            'anual_mes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'placeholder': 'Mês', 'min': 1, 'max': 12}),
        }
        labels = {
            'trim1_dia': '1º Trimestre - Dia', 'trim1_mes': '1º Trimestre - Mês',
            'trim2_dia': '2º Trimestre - Dia', 'trim2_mes': '2º Trimestre - Mês',
            'trim3_dia': '3º Trimestre - Dia', 'trim3_mes': '3º Trimestre - Mês',
            'trim4_dia': '4º Trimestre - Dia', 'trim4_mes': '4º Trimestre - Mês',
            'anual_dia': 'Anual - Dia', 'anual_mes': 'Anual - Mês',
        }

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')

        if tipo == 'trimestral':
            for i in range(1, 5):
                dia = cleaned_data.get(f'trim{i}_dia')
                mes = cleaned_data.get(f'trim{i}_mes')
                if not dia or not mes:
                    raise ValidationError(f'Todos os 4 trimestres devem ter dia e mês configurados (faltando {i}º trimestre).')
                try:
                    date(2000, mes, dia)
                except ValueError:
                    raise ValidationError(f'Data inválida para {i}º trimestre: dia {dia} não existe no mês {mes}.')

        elif tipo == 'anual':
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
        instance.trimestre = None
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
            ).order_by('-ano', 'tipo_periodo', 'trimestre')
            
            # Personalizar exibição no dropdown
            self.fields['periodo'].label_from_instance = lambda obj: f"{obj.nome_exibicao} - {obj.get_status_display()}"
