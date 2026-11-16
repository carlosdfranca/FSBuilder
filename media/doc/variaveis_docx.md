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

## 1.1 Contexto operacional (texto da empresa)

Texto cadastrado pela empresa na tela da DF (botão **Contexto operacional**) e compartilhado por todos os fundos dela.
Entra como lista de parágrafos (blocos separados por linha em branco). Se estiver vazio, sai o marcador
`[Contexto operacional não preenchido]`.

**Variável da lista:** `contexto_operacional_paragrafos` (lista de strings)

```
{%p for par in contexto_operacional_paragrafos %}
{{ par }}
(parágrafo vazio de espaçamento)
{%p endfor %}
```

> **Atenção**: `{%p for %}` / `{%p endfor %}` ficam cada um em seu próprio parágrafo (estilo *Corpo de texto*).
> Digite a tag inteira de uma vez, sem colar, para o Word não dividi-la em vários trechos.
> Como o `render` usa `autoescape=True`, caracteres como `&` e `<` do texto do usuário saem corretamente.

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
| `row.descricao` | texto | Nome da conta do plano de contas |
| `row.atual` | número | Valor atual em R$ mil (inteiro) |
| `row.anterior` | número | Valor anterior em R$ mil (inteiro) |
| `row.perc_atual` | número | % sobre PL atual (float, 2 casas) |
| `row.perc_anterior` | número | % sobre PL anterior (float, 2 casas) |
| `row.tipo` | texto | `"grupo"` (conta com contas abaixo, em negrito) ou `"item"` (conta final) |
| `row.nivel` | número | Nível da conta no plano (1 = raiz). Use para o recuo da linha |

> As linhas seguem a hierarquia do plano de contas único e o **nível de detalhe** escolhido na tela
> (padrão 4). Os valores de cada linha já incluem as contas abaixo dela.

### 2.2 Loops de linhas do Passivo e do Patrimônio Líquido

**Variáveis das listas:** `passivo_rows` e `pl_rows`  
*(campos idênticos ao `ativo_rows`)*

`passivo_rows` traz só o passivo exigível (circulante e não circulante). O patrimônio líquido vem em
`pl_rows`, que termina com a linha "Resultado do exercício".

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
| `row.descricao` | texto | Nome da conta do plano de contas |
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

A DFC é gerada pelas linhas cadastradas em **Linhas da DFC** (admin), por variação de saldo das contas
do plano. Por isso o conteúdo é uma lista única, e não variáveis fixas por linha.

### 5.1 Loop de linhas

**Variável da lista:** `dfc_rows`

```
{%tr for row in dfc_rows %}
{{row.descricao}}  |  {{row.atual}}  |  {{row.anterior}}
{%tr endfor %}
```

| Campo | Tipo | Descrição |
|---|---|---|
| `row.descricao` | texto | Título da atividade, linha, total ou saldo de caixa |
| `row.atual` | número | Valor atual em R$ mil (vazio nas linhas de título) |
| `row.anterior` | número | Valor do período anterior em R$ mil (vazio nas linhas de título) |
| `row.tipo` | texto | `"grupo"` (título da atividade), `"item"` (linha) ou `"total"` (subtotal/saldo) |
| `row.nivel` | número | 1 = título/total; 2 = linha dentro da atividade |

A lista já traz, em ordem: atividades operacionais, de investimento e de financiamento (cada uma com seu
total), a variação no caixa e os saldos de caixa no início e no final.

### 5.2 Variação e saldos de caixa (escalares)

| Variável | Descrição |
|---|---|
| `{{dfc_variacao_caixa_atual}}` / `{{dfc_variacao_caixa_anterior}}` | Variação no caixa e equivalentes |
| `{{dfc_caixa_inicio_atual}}` / `{{dfc_caixa_inicio_anterior}}` | Caixa no início do período |
| `{{dfc_caixa_final_atual}}` / `{{dfc_caixa_final_anterior}}` | Caixa no final do período |

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
{{dfc_variacao_caixa_atual}}  {{dfc_variacao_caixa_anterior}}
{{dfc_caixa_inicio_atual}}  {{dfc_caixa_inicio_anterior}}
{{dfc_caixa_final_atual}}  {{dfc_caixa_final_anterior}}
```

### Listas (loops com `{%tr for %}`)
```
contexto_operacional_paragrafos → lista de strings (loop com {%p for %})
ativo_rows    → campos: descricao, atual, anterior, perc_atual, perc_anterior, tipo, nivel
passivo_rows  → campos: descricao, atual, anterior, perc_atual, perc_anterior, tipo, nivel
pl_rows       → campos: descricao, atual, anterior, perc_atual, perc_anterior, tipo, nivel
dre_rows      → campos: descricao, atual, anterior, tipo, nivel
dfc_rows      → campos: descricao, atual, anterior, tipo, nivel
```
