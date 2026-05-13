# Variáveis para o template `modelo_df.docx`

Este arquivo documenta todas as variáveis e loops disponíveis para uso no template Word.
A geração é feita via **docxtpl** (sintaxe Jinja2).

---

## Como usar

| Tipo | Sintaxe no Word | Quando usar |
|---|---|---|
| Variável escalar | `{{nome_variavel}}` | Valor único (texto, número) |
| Loop em tabela | `{%tr for row in lista %}` … `{%tr endfor %}` | Uma linha da tabela para cada item |
| Condicional | `{% if condição %}` … `{% endif %}` | Exibir/ocultar trechos |

> **Atenção**: os marcadores de loop `{%tr for %}` e `{%tr endfor %}` devem estar sozinhos em células de linhas separadas da tabela no Word — cada um em sua própria linha-protótipo.

---

## 1. Cabeçalho (compartilhado por todas as demonstrações)

| Variável | Descrição | Exemplo |
|---|---|---|
| `{{fundo_nome}}` | Nome do fundo em maiúsculas | `FUNDO XYZ FIDC` |
| `{{fundo_cnpj}}` | CNPJ do fundo | `12.345.678/0001-90` |
| `{{empresa_nome}}` | Nome da empresa administradora | `Gestora ABC Ltda` |
| `{{empresa_cnpj}}` | CNPJ da empresa administradora | `98.765.432/0001-11` |
| `{{data_atual}}` | Data atual formatada | `31/12/2024` |
| `{{data_anterior}}` | Data anterior formatada (ou `—` se zerado) | `31/12/2023` |
| `{{data_atual_extenso}}` | Data atual por extenso | `31 de dezembro de 2024` |
| `{{data_anterior_extenso}}` | Data anterior por extenso (ou `—` se zerado) | `31 de dezembro de 2023` |

---

## 2. DPF — Demonstração da Posição Financeira

### 2.1 Loop de linhas do Ativo

**Variável da lista:** `ativo_rows`

```
{%tr for row in ativo_rows %}
{{row.descricao}}  |  {{row.atual}}  |  {{row.anterior}}  |  {{row.perc_atual}}  |  {{row.perc_anterior}}
{%tr endfor %}
```

| Campo | Tipo | Descrição |
|---|---|---|
| `row.descricao` | texto | Nome do grupo ou sub-item |
| `row.atual` | número | Valor atual em R$ mil (inteiro) |
| `row.anterior` | número | Valor anterior em R$ mil (inteiro) |
| `row.perc_atual` | número | % sobre PL atual (float, 2 casas) |
| `row.perc_anterior` | número | % sobre PL anterior (float, 2 casas) |
| `row.tipo` | texto | `"grupo"` (linha em negrito) ou `"item"` (sub-item recuado) |

### 2.2 Loop de linhas do Passivo

**Variável da lista:** `passivo_rows`  
*(campos idênticos ao `ativo_rows`)*

### 2.3 Totais e PL (escalares)

| Variável | Descrição |
|---|---|
| `{{dpf_total_ativo_atual}}` | Total do ativo — data atual |
| `{{dpf_total_ativo_anterior}}` | Total do ativo — data anterior |
| `{{dpf_total_ativo_perc_atual}}` | % total ativo sobre PL — atual |
| `{{dpf_total_ativo_perc_anterior}}` | % total ativo sobre PL — anterior |
| `{{dpf_total_passivo_atual}}` | Total do passivo — data atual |
| `{{dpf_total_passivo_anterior}}` | Total do passivo — data anterior |
| `{{dpf_total_passivo_perc_atual}}` | % total passivo sobre PL — atual |
| `{{dpf_total_passivo_perc_anterior}}` | % total passivo sobre PL — anterior |
| `{{pl_ajustado_atual}}` | Patrimônio líquido — data atual |
| `{{pl_ajustado_anterior}}` | Patrimônio líquido — data anterior |
| `{{total_pl_passivo_atual}}` | Total PL + Passivo — data atual |
| `{{total_pl_passivo_anterior}}` | Total PL + Passivo — data anterior |

---

## 3. DRE — Demonstração do Resultado do Exercício

### 3.1 Loop de linhas

**Variável da lista:** `dre_rows`

```
{%tr for row in dre_rows %}
{{row.descricao}}  |  {{row.atual}}  |  {{row.anterior}}
{%tr endfor %}
```

| Campo | Tipo | Descrição |
|---|---|---|
| `row.descricao` | texto | Nome do grupo ou sub-item |
| `row.atual` | número | Valor atual em R$ mil |
| `row.anterior` | número | Valor anterior em R$ mil |
| `row.tipo` | texto | `"grupo"` (negrito, com soma) ou `"item"` (recuado) |

### 3.2 Resultado final (escalares)

| Variável | Descrição |
|---|---|
| `{{resultado_exercicio}}` | Resultado do exercício — data atual |
| `{{resultado_exercicio_anterior}}` | Resultado do exercício — data anterior |

---

## 4. DMPL — Demonstração das Mutações do Patrimônio Líquido

Não há loops. Todas as variáveis são escalares.

### 4.1 PL no início do período

| Variável | Origem (`dados_dmpl`) | Descrição |
|---|---|---|
| `{{dmpl_pl_inicio_atual}}` | `valor_primeiro` | PL inicial — data atual |
| `{{dmpl_pl_inicio_anterior}}` | `valor_primeiro_ant` | PL inicial — data anterior |
| `{{dmpl_cotas_inicio_qtd}}` | `qtd_cotas_inicio` | Qtd de cotas início — atual |
| `{{dmpl_cotas_inicio_valor}}` | `cota_inicio` | Valor da cota no início — atual |
| `{{dmpl_cotas_inicio_ant_qtd}}` | `qtd_cotas_inicio_ant` | Qtd de cotas início — anterior |
| `{{dmpl_cotas_inicio_ant_valor}}` | `cota_inicio_ant` | Valor da cota no início — anterior |

### 4.2 Emissão de cotas

| Variável | Origem (`dados_dmpl`) | Descrição |
|---|---|---|
| `{{dmpl_emissao_qtd}}` | `aplicacoes_qtd` | Qtd de cotas emitidas |
| `{{dmpl_emissao_valor}}` | `aplicacoes_valor` | Valor total emitido em R$ mil |

### 4.3 Resgate de cotas

| Variável | Origem (`dados_dmpl`) | Descrição |
|---|---|---|
| `{{dmpl_resgate_qtd}}` | `resgates_qtd` | Qtd de cotas resgatadas |
| `{{dmpl_resgate_valor}}` | `resgates_valor` | Valor total resgatado em R$ mil (negativo) |

### 4.4 PL antes do resultado

| Variável | Origem (`dados_dmpl`) | Descrição |
|---|---|---|
| `{{dmpl_pl_antes_resultado}}` | `pl_antes_resultado_periodo` | PL antes do resultado do período |

### 4.5 Resultado do período

*(Mesmas variáveis da DRE)*

| Variável | Descrição |
|---|---|
| `{{resultado_exercicio}}` | Resultado — data atual |
| `{{resultado_exercicio_anterior}}` | Resultado — data anterior |

### 4.6 PL no final do período

| Variável | Origem | Descrição |
|---|---|---|
| `{{pl_ajustado_atual}}` | context geral | PL final — data atual *(mesma var do DPF)* |
| `{{pl_ajustado_anterior}}` | context geral | PL final — data anterior *(mesma var do DPF)* |
| `{{dmpl_cotas_fim_qtd}}` | `qtd_cotas_fim` | Qtd de cotas no fim — atual |
| `{{dmpl_cotas_fim_valor}}` | `cota_fim` | Valor da cota no fim — atual |
| `{{dmpl_valor_ultimo}}` | `valor_ultimo` | Valor total das cotas no fim — atual |

---

## 5. DFC — Demonstração dos Fluxos de Caixa

### 5.1 Bloco Operacional

| Variável | Descrição |
|---|---|
| `{{dfc_op_titulo}}` | Título do bloco (ex: "Atividades operacionais") |
| `{{dfc_resultado_liq_titulo}}` | Rótulo resultado líquido |
| `{{dfc_resultado_liq_atual}}` | Resultado líquido — atual |
| `{{dfc_resultado_liq_anterior}}` | Resultado líquido — anterior |
| `{{dfc_ajustes_titulo}}` | Título dos ajustes |
| `{{dfc_rendimento_dc_titulo}}` | Rótulo rendimento de direitos creditórios |
| `{{dfc_rendimento_dc_atual}}` | Rendimento DC — atual |
| `{{dfc_rendimento_dc_anterior}}` | Rendimento DC — anterior |
| `{{dfc_provisao_perdas_titulo}}` | Rótulo provisão para perdas |
| `{{dfc_provisao_perdas_atual}}` | Provisão perdas — atual |
| `{{dfc_provisao_perdas_anterior}}` | Provisão perdas — anterior |
| `{{dfc_taxa_adm_titulo}}` | Rótulo taxa de administração |
| `{{dfc_taxa_adm_atual}}` | Taxa adm — atual |
| `{{dfc_taxa_adm_anterior}}` | Taxa adm — anterior |
| `{{dfc_taxa_gestao_titulo}}` | Rótulo taxa de gestão |
| `{{dfc_taxa_gestao_atual}}` | Taxa gestão — atual |
| `{{dfc_taxa_gestao_anterior}}` | Taxa gestão — anterior |
| `{{dfc_resultado_ajustado_titulo}}` | Rótulo resultado ajustado |
| `{{dfc_resultado_ajustado_atual}}` | Resultado ajustado — atual |
| `{{dfc_resultado_ajustado_anterior}}` | Resultado ajustado — anterior |
| `{{dfc_aumento_dc_titulo}}` | Rótulo aumento em direitos creditórios |
| `{{dfc_aumento_dc_atual}}` | Aumento DC — atual |
| `{{dfc_aumento_dc_anterior}}` | Aumento DC — anterior |
| `{{dfc_aumento_receber_titulo}}` | Rótulo aumento em contas a receber |
| `{{dfc_aumento_receber_atual}}` | Aumento a receber — atual |
| `{{dfc_aumento_receber_anterior}}` | Aumento a receber — anterior |
| `{{dfc_reducao_pagar_titulo}}` | Rótulo redução em contas a pagar |
| `{{dfc_reducao_pagar_atual}}` | Redução a pagar — atual |
| `{{dfc_reducao_pagar_anterior}}` | Redução a pagar — anterior |
| `{{dfc_caixa_operacional_titulo}}` | Rótulo caixa gerado nas atividades operacionais |
| `{{dfc_caixa_operacional_atual}}` | Caixa operacional — atual |
| `{{dfc_caixa_operacional_anterior}}` | Caixa operacional — anterior |

### 5.2 Bloco Financiamento

| Variável | Descrição |
|---|---|
| `{{dfc_fin_titulo}}` | Título do bloco (ex: "Atividades de financiamento") |
| `{{dfc_emissao_titulo}}` | Rótulo emissão de cotas |
| `{{dfc_emissao_atual}}` | Emissão — atual |
| `{{dfc_emissao_anterior}}` | Emissão — anterior |
| `{{dfc_resgate_titulo}}` | Rótulo resgate de cotas |
| `{{dfc_resgate_atual}}` | Resgate — atual |
| `{{dfc_resgate_anterior}}` | Resgate — anterior |
| `{{dfc_caixa_financiamento_titulo}}` | Rótulo caixa das atividades de financiamento |
| `{{dfc_caixa_financiamento_atual}}` | Caixa financiamento — atual |
| `{{dfc_caixa_financiamento_anterior}}` | Caixa financiamento — anterior |

### 5.3 Variação e saldos de caixa

| Variável | Descrição |
|---|---|
| `{{dfc_variacao_caixa_titulo}}` | Rótulo variação no caixa e equivalentes |
| `{{dfc_variacao_caixa_atual}}` | Variação caixa — atual |
| `{{dfc_variacao_caixa_anterior}}` | Variação caixa — anterior |
| `{{dfc_caixa_inicio_titulo}}` | Rótulo caixa e equivalentes no início |
| `{{dfc_caixa_inicio_atual}}` | Caixa início — atual |
| `{{dfc_caixa_inicio_anterior}}` | Caixa início — anterior |
| `{{dfc_caixa_final_titulo}}` | Rótulo caixa e equivalentes no final |
| `{{dfc_caixa_final_atual}}` | Caixa final — atual |
| `{{dfc_caixa_final_anterior}}` | Caixa final — anterior |

---

## 6. Resumo rápido — todas as variáveis

### Escalares
```
{{fundo_nome}}  {{fundo_cnpj}}  {{empresa_nome}}  {{empresa_cnpj}}
{{data_atual}}  {{data_anterior}}  {{data_atual_extenso}}  {{data_anterior_extenso}}
{{resultado_exercicio}}  {{resultado_exercicio_anterior}}
{{pl_ajustado_atual}}  {{pl_ajustado_anterior}}
{{total_pl_passivo_atual}}  {{total_pl_passivo_anterior}}
{{dpf_total_ativo_atual}}  {{dpf_total_ativo_anterior}}
{{dpf_total_ativo_perc_atual}}  {{dpf_total_ativo_perc_anterior}}
{{dpf_total_passivo_atual}}  {{dpf_total_passivo_anterior}}
{{dpf_total_passivo_perc_atual}}  {{dpf_total_passivo_perc_anterior}}
{{dmpl_pl_inicio_atual}}  {{dmpl_pl_inicio_anterior}}
{{dmpl_cotas_inicio_qtd}}  {{dmpl_cotas_inicio_valor}}
{{dmpl_cotas_inicio_ant_qtd}}  {{dmpl_cotas_inicio_ant_valor}}
{{dmpl_emissao_qtd}}  {{dmpl_emissao_valor}}
{{dmpl_resgate_qtd}}  {{dmpl_resgate_valor}}
{{dmpl_pl_antes_resultado}}
{{dmpl_cotas_fim_qtd}}  {{dmpl_cotas_fim_valor}}  {{dmpl_valor_ultimo}}
{{dfc_op_titulo}}
{{dfc_resultado_liq_titulo}}  {{dfc_resultado_liq_atual}}  {{dfc_resultado_liq_anterior}}
{{dfc_ajustes_titulo}}
{{dfc_rendimento_dc_titulo}}  {{dfc_rendimento_dc_atual}}  {{dfc_rendimento_dc_anterior}}
{{dfc_provisao_perdas_titulo}}  {{dfc_provisao_perdas_atual}}  {{dfc_provisao_perdas_anterior}}
{{dfc_taxa_adm_titulo}}  {{dfc_taxa_adm_atual}}  {{dfc_taxa_adm_anterior}}
{{dfc_taxa_gestao_titulo}}  {{dfc_taxa_gestao_atual}}  {{dfc_taxa_gestao_anterior}}
{{dfc_resultado_ajustado_titulo}}  {{dfc_resultado_ajustado_atual}}  {{dfc_resultado_ajustado_anterior}}
{{dfc_aumento_dc_titulo}}  {{dfc_aumento_dc_atual}}  {{dfc_aumento_dc_anterior}}
{{dfc_aumento_receber_titulo}}  {{dfc_aumento_receber_atual}}  {{dfc_aumento_receber_anterior}}
{{dfc_reducao_pagar_titulo}}  {{dfc_reducao_pagar_atual}}  {{dfc_reducao_pagar_anterior}}
{{dfc_caixa_operacional_titulo}}  {{dfc_caixa_operacional_atual}}  {{dfc_caixa_operacional_anterior}}
{{dfc_fin_titulo}}
{{dfc_emissao_titulo}}  {{dfc_emissao_atual}}  {{dfc_emissao_anterior}}
{{dfc_resgate_titulo}}  {{dfc_resgate_atual}}  {{dfc_resgate_anterior}}
{{dfc_caixa_financiamento_titulo}}  {{dfc_caixa_financiamento_atual}}  {{dfc_caixa_financiamento_anterior}}
{{dfc_variacao_caixa_titulo}}  {{dfc_variacao_caixa_atual}}  {{dfc_variacao_caixa_anterior}}
{{dfc_caixa_inicio_titulo}}  {{dfc_caixa_inicio_atual}}  {{dfc_caixa_inicio_anterior}}
{{dfc_caixa_final_titulo}}  {{dfc_caixa_final_atual}}  {{dfc_caixa_final_anterior}}
```

### Listas (loops com `{%tr for %}`)
```
ativo_rows    → campos: descricao, atual, anterior, perc_atual, perc_anterior, tipo
passivo_rows  → campos: descricao, atual, anterior, perc_atual, perc_anterior, tipo
dre_rows      → campos: descricao, atual, anterior, tipo
```
