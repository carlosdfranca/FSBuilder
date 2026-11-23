from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ===== Estilos comuns =====
bold = Font(bold=True)
italic = Font(italic=True)
left = Alignment(horizontal="left")
right = Alignment(horizontal="right")
center = Alignment(horizontal="center")
indent2 = Alignment(horizontal="left", indent=2)
bottom_border = Border(bottom=Side(style="thin"))
underline_single = Border(bottom=Side(style="thin"))
underline_double = Border(bottom=Side(style="double"))
double_bottom_border = Border(bottom=Side(style="double"))


def data_str(data, default="—"):
    if not data:
        return default
    return data.strftime("%d/%m/%Y")

# ================================================================
# Função auxiliar para rodapé padronizado
# ================================================================
def adicionar_rodape(ws, ultima_coluna=3, mensagem=None):
    """
    Insere o rodapé padrão em uma planilha, garantindo exatamente
    UMA linha em branco entre os dados e o rodapé.

    - Se a última linha já for vazia, reaproveitamos como "espaço".
    - Se não for, criamos uma linha vazia antes do rodapé.
    """
    from openpyxl.styles import Font, Alignment

    if not mensagem:
        mensagem = "As notas explicativas são parte integrante das demonstrações financeiras."

    # Verifica se a última linha já está em branco
    last_row = ws.max_row
    is_blank = True
    for col in range(1, ws.max_column + 1):
        if ws.cell(row=last_row, column=col).value not in (None, ""):
            is_blank = False
            break

    # Se a última linha tiver conteúdo, adicionamos UMA linha em branco
    if not is_blank:
        ws.append([]); last_row = ws.max_row + 1

    # Rodapé será sempre na linha seguinte
    footer_row = last_row + 1
    last_col = max(ws.max_column, ultima_coluna)

    ws.merge_cells(
        start_row=footer_row,
        start_column=1,
        end_row=footer_row,
        end_column=last_col
    )
    cell = ws.cell(row=footer_row, column=1)
    cell.value = mensagem
    cell.font = Font(italic=True, bold=True)
    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)


# ================================================================
# Linhas da árvore do plano (compartilhado por DPF, DRE e DFC)
# ================================================================
FORMATO_MONETARIO = "#,##0_);(#,##0)"


def _valor(v):
    """Inteiro em R$ mil; zero e ausente viram '-'."""
    if v is None:
        return None
    v = int(v)
    return "-" if v == 0 else v


def _estilo_linha(ws, linha, tipo, nivel, col_desc, cols_valor, cols_perc=()):
    """Negrito/itálico, recuo e sublinhado conforme o tipo da linha da árvore."""
    desc = ws.cell(row=linha, column=col_desc)
    desc.alignment = Alignment(horizontal="left", indent=max(0, nivel - 1))
    negrito = tipo in ("grupo", "total")
    for col in (col_desc, *cols_valor, *cols_perc):
        c = ws.cell(row=linha, column=col)
        if negrito:
            c.font = bold
        elif tipo == "virtual":
            c.font = italic
        if tipo == "total" and col != col_desc:
            c.border = underline_double
    for col in cols_valor:
        c = ws.cell(row=linha, column=col)
        c.alignment = right
        c.number_format = FORMATO_MONETARIO
    for col in cols_perc:
        c = ws.cell(row=linha, column=col)
        c.alignment = right
        c.number_format = "#,##0.00"


def _cabecalho_fundo(ws, fundo, titulo, periodo, nota, administrado=True):
    ws.sheet_view.showGridLines = False
    ws["A1"] = str(fundo.nome).upper(); ws["A1"].font = bold; ws["A1"].alignment = left
    ws["A2"] = f"CNPJ: {fundo.cnpj}"; ws["A2"].font = bold; ws["A2"].alignment = left
    ws["A3"] = f"Administrado por {fundo.empresa.nome}" if administrado else fundo.empresa.nome
    ws["A3"].alignment = left
    ws["A4"] = f"CNPJ: {fundo.empresa.cnpj or ''}"; ws["A4"].alignment = left
    ws["A6"] = titulo; ws["A6"].font = bold; ws["A6"].alignment = left
    ws["A7"] = periodo; ws["A7"].alignment = left
    ws["A8"] = nota; ws["A8"].font = italic; ws["A8"].alignment = left


# ================================================================
# GUIA DPF (gerada pela árvore do plano de contas)
# ================================================================
def criar_aba_dpf(wb, fundo, data_atual, data_anterior, dpf):
    ws = wb.active
    ws.title = "DPF"
    _cabecalho_fundo(
        ws, fundo, "Demonstração da Posição Financeira",
        f"Em {data_atual.strftime('%d/%m/%Y')} e {data_str(data_anterior)}",
        "(Valores expressos em milhares de reais, exceto quando apresentado de outra forma)",
    )

    COL = {"DESC": 1, "R_CUR": 2, "P_CUR": 3, "SEP": 4, "R_PRI": 5, "P_PRI": 6}
    for col, largura in {1: 62, 2: 14, 3: 16, 4: 3, 5: 14, 6: 16}.items():
        ws.column_dimensions[get_column_letter(col)].width = largura

    topo = 10
    ws.cell(row=topo, column=COL["R_CUR"], value=data_atual.strftime("%d/%m/%Y")).font = bold
    ws.merge_cells(start_row=topo, start_column=COL["R_CUR"], end_row=topo, end_column=COL["P_CUR"])
    ws.cell(row=topo, column=COL["R_PRI"], value=data_str(data_anterior)).font = bold
    ws.merge_cells(start_row=topo, start_column=COL["R_PRI"], end_row=topo, end_column=COL["P_PRI"])
    for col in (COL["R_CUR"], COL["R_PRI"]):
        ws.cell(row=topo, column=col).alignment = center

    cabecalhos = [
        (COL["DESC"], "Descrição", center),
        (COL["R_CUR"], "R$", center),
        (COL["P_CUR"], "% sobre o patrimônio líquido", Alignment(horizontal="center", vertical="center", wrap_text=True)),
        (COL["R_PRI"], "R$", center),
        (COL["P_PRI"], "% sobre o patrimônio líquido", Alignment(horizontal="center", vertical="center", wrap_text=True)),
    ]
    for col, texto, alinhamento in cabecalhos:
        c = ws.cell(row=topo + 1, column=col, value=texto)
        c.font = bold
        c.alignment = alinhamento
        c.border = bottom_border

    linha = topo + 2

    def escrever(linhas):
        nonlocal linha
        for l in linhas:
            ws.cell(row=linha, column=COL["DESC"], value=l["descricao"])
            ws.cell(row=linha, column=COL["R_CUR"], value=_valor(l["atual"]))
            ws.cell(row=linha, column=COL["R_PRI"], value=_valor(l["anterior"]))
            for chave, col in (("perc_atual", COL["P_CUR"]), ("perc_anterior", COL["P_PRI"])):
                p = l.get(chave)
                ws.cell(row=linha, column=col, value=float(p) if p is not None else "-")
            _estilo_linha(
                ws, linha, l["tipo_linha"], l["nivel"], COL["DESC"],
                (COL["R_CUR"], COL["R_PRI"]), (COL["P_CUR"], COL["P_PRI"]),
            )
            linha += 1

    def titulo_secao(texto):
        nonlocal linha
        ws.cell(row=linha, column=COL["DESC"], value=texto).font = bold
        linha += 1

    for texto, chave in (("Ativo", "secao_ativo"), ("Passivo", "secao_passivo"), ("Patrimônio líquido", "secao_pl")):
        titulo_secao(texto)
        escrever(dpf[chave])
        linha += 1
    escrever(dpf["secao_fechamento"])

    ws.append([])
    adicionar_rodape(ws, ultima_coluna=6)


# ================================================================
# GUIA DRE (gerada pela árvore do plano de contas)
# ================================================================
def criar_aba_dre(wb, fundo, data_atual, data_anterior, dre):
    ws = wb.create_sheet(title="DRE")
    _cabecalho_fundo(
        ws, fundo, "Demonstração do Resultado do Exercício",
        f"Períodos findos em {data_atual.strftime('%d/%m/%Y')} e {data_str(data_anterior)}",
        "(Valores expressos em milhares de reais)", administrado=False,
    )
    ws["A7"].font = bold

    for col, largura in {1: 70, 2: 14, 3: 14}.items():
        ws.column_dimensions[get_column_letter(col)].width = largura

    topo = 10
    for col, texto in ((2, data_atual.strftime("%d/%m/%Y")), (3, data_str(data_anterior))):
        c = ws.cell(row=topo, column=col, value=texto)
        c.font = bold
        c.alignment = right
        c.border = bottom_border

    linha = topo + 1
    for l in dre["linhas"]:
        ws.cell(row=linha, column=1, value=l["descricao"])
        ws.cell(row=linha, column=2, value=_valor(l["atual"]))
        ws.cell(row=linha, column=3, value=_valor(l["anterior"]))
        _estilo_linha(ws, linha, l["tipo_linha"], l["nivel"], 1, (2, 3))
        linha += 1

    linha += 1
    ws.cell(row=linha, column=1, value="Resultado do exercício")
    ws.cell(row=linha, column=2, value=_valor(dre["resultado_atual"]))
    ws.cell(row=linha, column=3, value=_valor(dre["resultado_anterior"]))
    _estilo_linha(ws, linha, "total", 1, 1, (2, 3))

    adicionar_rodape(ws, ultima_coluna=3)


# ================================================================
# GUIA DMPL (versão por datas)
# ================================================================
def criar_aba_dmpl(
    wb, fundo, data_atual, data_anterior,
    dados_dmpl, resultado_exercicio, resultado_exercicio_anterior,
    pl_atual, pl_anterior
):

    ws = wb.create_sheet(title="DMPL")
    ws.sheet_view.showGridLines = False

    nome_fundo = str(fundo.nome).upper()

    # =====================
    # Cabeçalho
    # =====================
    ws["A1"] = nome_fundo; ws["A1"].font = bold; ws["A1"].alignment = left
    ws["A2"] = f"CNPJ: {fundo.cnpj}"; ws["A2"].font = bold; ws["A2"].alignment = left
    ws["A3"] = f"Administrado por {fundo.empresa.nome}"; ws["A3"].alignment = left
    ws["A4"] = f"CNPJ: {fundo.empresa.cnpj or ''}"; ws["A4"].alignment = left
    ws.append([])

    ws["A6"] = "Demonstração das Mutações do Patrimônio Líquido"
    ws["A6"].font = bold; ws["A6"].alignment = left
    ws["A7"] = f"Períodos findos em {data_atual.strftime('%d/%m/%Y')} e {data_str(data_anterior)}"
    ws["A7"].font = bold; ws["A7"].alignment = left
    ws["A8"] = "(Valores expressos em milhares de reais, exceto o valor unitário da cota)"
    ws["A8"].font = italic; ws["A8"].alignment = left
    ws.append([])

    # =====================
    # Cabeçalho colunas
    # =====================
    ws.append(["Descrição", data_atual.strftime("%d/%m/%Y"), data_str(data_anterior)])
    row_header = ws.max_row
    for col in (2, 3):
        c = ws.cell(row=row_header, column=col)
        c.alignment = right
        c.font = bold
        c.border = bottom_border

    # =====================
    # PL inicial
    # =====================
    ws.append(["Patrimônio líquido no início do período",
               dados_dmpl["valor_primeiro"], dados_dmpl["valor_primeiro_ant"]])
    ws.append([f"Total de {dados_dmpl['qtd_cotas_inicio']} cotas a R$ {dados_dmpl['cota_inicio']}",
               dados_dmpl["valor_primeiro"], "-"])
    ws.append([f"Total de {dados_dmpl['qtd_cotas_inicio_ant']} cotas a R$ {dados_dmpl['cota_inicio_ant']}",
               "-", dados_dmpl["valor_primeiro_ant"]])
    ws.append([])

    # =====================
    # Emissão
    # =====================
    ws.append(["Emissão de cotas", "", ""])
    ws.cell(row=ws.max_row, column=1).font = bold
    ws.append([f"Total de {dados_dmpl['aplicacoes_qtd']} cotas",
               dados_dmpl["aplicacoes_valor"], "-"])
    ws.append([])

    # =====================
    # Resgate
    # =====================
    ws.append(["Resgate de cotas", "", ""])
    ws.cell(row=ws.max_row, column=1).font = bold
    ws.append([f"Total de {dados_dmpl['resgates_qtd']} cotas",
               dados_dmpl["resgates_valor"], "-"])
    ws.append([])

    # =====================
    # PL antes do resultado
    # =====================
    ws.append([
        "Patrimônio líquido antes do resultado do período",
        dados_dmpl["pl_antes_resultado_periodo"],
        dados_dmpl["valor_primeiro_ant"]
    ])
    ws.append([])

    # =====================
    # Resultado
    # =====================
    ws.append(["Resultado do período", resultado_exercicio, resultado_exercicio_anterior])
    ws.append([])

    # =====================
    # PL final
    # =====================
    ws.append(["Patrimônio líquido no final do exercício/período", pl_atual, pl_anterior])
    ws.append([f"Total de {dados_dmpl['qtd_cotas_fim']} cotas a R$ {dados_dmpl['cota_fim']}",
               dados_dmpl["valor_ultimo"], "-"])
    ws.append([f"Total de {dados_dmpl['qtd_cotas_inicio']} cotas a R$ {dados_dmpl['cota_inicio']}",
               "-", dados_dmpl["valor_primeiro"]])
    ws.append([])

    # Rodapé padronizado
    adicionar_rodape(ws, ultima_coluna=3)

    # =====================
    # Ajuste de largura
    # =====================
    for col_num, width in {1: 65, 2: 15, 3: 15}.items():
        ws.column_dimensions[get_column_letter(col_num)].width = width


# ================================================================
# GUIA DFC (gerada pelas linhas cadastradas em LinhaDFC)
# ================================================================
def criar_aba_dfc(wb, fundo, data_atual, data_anterior, dfc):
    ws = wb.create_sheet(title="DFC")
    _cabecalho_fundo(
        ws, fundo, "Demonstração dos Fluxos de Caixa – Método indireto",
        f"Períodos findos em {data_atual.strftime('%d/%m/%Y')} e {data_str(data_anterior)}",
        "(Valores expressos em milhares de reais)",
    )
    ws["A7"].font = bold

    for col, largura in {1: 70, 2: 15, 3: 15}.items():
        ws.column_dimensions[get_column_letter(col)].width = largura

    topo = 10
    ws.cell(row=topo, column=1, value="Descrição")
    ws.cell(row=topo, column=2, value=data_atual.strftime("%d/%m/%Y"))
    ws.cell(row=topo, column=3, value=data_str(data_anterior))
    for col in (1, 2, 3):
        c = ws.cell(row=topo, column=col)
        c.font = bold
        c.border = bottom_border
        if col > 1:
            c.alignment = right

    linha = topo + 1
    for l in dfc["linhas"]:
        if l["tipo_linha"] == "grupo" and linha > topo + 1:
            linha += 1  # respiro antes de cada bloco de atividades
        ws.cell(row=linha, column=1, value=l["descricao"])
        ws.cell(row=linha, column=2, value=_valor(l["atual"]))
        ws.cell(row=linha, column=3, value=_valor(l["anterior"]))
        _estilo_linha(ws, linha, l["tipo_linha"], l["nivel"], 1, (2, 3))
        linha += 1

    ws.append([])
    adicionar_rodape(ws, ultima_coluna=3)
