"""
Leitura de planilhas de movimentações (e de saldos iniciais): XLSX ou CSV, sem tocar no banco.

Colunas (cabeçalho reconhecido sem acento/caixa): DATA, CONTA, VALOR ou DÉBITO + CRÉDITO,
e opcionais HISTÓRICO e DOCUMENTO. Cada linha malformada gera um erro com o número da linha
da planilha; o chamador decide recusar o arquivo inteiro.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import openpyxl

from core.upload._excel_utils import clean_text, normalize_header

MAX_BYTES = 5 * 1024 * 1024
MAX_LINHAS = 20000

ALIASES = {
    "data": {"DATA", "DATA MOVIMENTO", "DATA DO MOVIMENTO", "DATA DA MOVIMENTACAO", "DT"},
    "conta": {"CONTA", "CODIGO", "COD CONTA", "CODIGO CONTA", "CODIGO DA CONTA"},
    "valor": {"VALOR", "VALOR MOVIMENTO", "VALOR DO MOVIMENTO", "SALDO"},
    "debito": {"DEBITO", "VALOR DEBITO"},
    "credito": {"CREDITO", "VALOR CREDITO"},
    "historico": {"HISTORICO", "DESCRICAO", "DESCRICAO DO MOVIMENTO"},
    "documento": {"DOCUMENTO", "DOC", "NUMERO DOCUMENTO", "NUMERO DO DOCUMENTO"},
}
FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y")
CENTAVOS = Decimal("0.01")


class PlanilhaInvalida(Exception):
    """Problema estrutural (arquivo ilegível, sem colunas, grande demais...)."""

    def __init__(self, mensagem: str):
        super().__init__(mensagem)
        self.mensagem = mensagem


@dataclass(frozen=True)
class LinhaPlanilha:
    linha: int
    data: date | None
    conta: str
    valor: Decimal | None
    debito: Decimal | None
    credito: Decimal | None
    historico: str
    documento: str


def _parse_data(valor) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = clean_text(valor)
    for formato in FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida '{texto}' (use dd/mm/aaaa)")


def _parse_valor(valor) -> Decimal | None:
    """Número da planilha ou texto no formato brasileiro (1.234,56 / (1.234,56) / -10,5)."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, bool):
        raise ValueError("valor inválido")
    if isinstance(valor, (int, float, Decimal)):
        numero = Decimal(str(valor))
    else:
        texto = clean_text(valor).replace("R$", "").replace(" ", "")
        negativo = texto.startswith("(") and texto.endswith(")")
        texto = texto.strip("()")
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):  # 1.234.567 = milhares
            texto = texto.replace(".", "")
        try:
            numero = Decimal(texto)
        except InvalidOperation:
            raise ValueError(f"valor inválido '{clean_text(valor)}'") from None
        numero = -abs(numero) if negativo else numero
    if not numero.is_finite():
        raise ValueError("valor inválido")
    return numero.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


parse_valor = _parse_valor  # usado também pelo formulário de lançamento manual


def _parse_conta(valor) -> str:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)  # a célula "1" vem como 1.0
    return clean_text(valor)


def _linhas_brutas(nome: str, conteudo: bytes) -> list[list]:
    if nome.lower().endswith(".csv"):
        for codificacao in ("utf-8-sig", "latin-1"):
            try:
                texto = conteudo.decode(codificacao)
                break
            except UnicodeDecodeError:
                continue
        amostra = texto[:4096]
        try:
            dialeto = csv.Sniffer().sniff(amostra, delimiters=";,\t")
        except csv.Error:
            dialeto = csv.excel
        return [list(r) for r in csv.reader(io.StringIO(texto), dialeto)]
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:  # arquivo corrompido, não é xlsx...
        raise PlanilhaInvalida("Não foi possível ler o arquivo. Envie um .xlsx ou .csv válido.") from exc
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def ler_planilha(nome: str, conteudo: bytes, *, exigir_data: bool = True) -> tuple[list[LinhaPlanilha], list[dict]]:
    """
    Devolve (linhas, erros). `erros` = [{"linha": n, "mensagem": ...}] só de formato; a existência da
    conta e as regras de negócio são checadas pelo serviço de importação.
    `exigir_data=False` serve ao arquivo de saldos iniciais (a data-base vem do formulário).
    """
    if len(conteudo) > MAX_BYTES:
        raise PlanilhaInvalida(f"Arquivo maior que {MAX_BYTES // (1024 * 1024)} MB.")
    brutas = _linhas_brutas(nome, conteudo)
    if not brutas:
        raise PlanilhaInvalida("A planilha está vazia.")

    cabecalho = [normalize_header(c) for c in brutas[0]]
    idx = {}
    for chave, nomes in ALIASES.items():
        for i, c in enumerate(cabecalho):
            if c in nomes and chave not in idx:
                idx[chave] = i

    faltando = []
    if exigir_data and "data" not in idx:
        faltando.append("DATA")
    if "conta" not in idx:
        faltando.append("CONTA")
    if "valor" not in idx and not ("debito" in idx and "credito" in idx):
        faltando.append("VALOR (ou DÉBITO e CRÉDITO)")
    if faltando:
        raise PlanilhaInvalida(f"Colunas ausentes: {', '.join(faltando)}.")

    def celula(linha, chave):
        i = idx.get(chave)
        return linha[i] if i is not None and i < len(linha) else None

    linhas, erros = [], []
    for n, bruta in enumerate(brutas[1:], start=2):
        if all(c is None or not str(c).strip() for c in bruta):
            continue
        if len(linhas) + len(erros) >= MAX_LINHAS:
            raise PlanilhaInvalida(f"Arquivo com mais de {MAX_LINHAS} linhas. Divida em mais de um arquivo.")
        try:
            conta = _parse_conta(celula(bruta, "conta"))
            if not conta:
                raise ValueError("conta não informada")
            data = _parse_data(celula(bruta, "data")) if exigir_data else None
            if "valor" in idx:
                valor, debito, credito = _parse_valor(celula(bruta, "valor")), None, None
                if valor is None:
                    raise ValueError("valor não informado")
            else:
                debito = _parse_valor(celula(bruta, "debito")) or Decimal("0.00")
                credito = _parse_valor(celula(bruta, "credito")) or Decimal("0.00")
                valor = None
                if debito == 0 and credito == 0:
                    raise ValueError("informe débito ou crédito")
                if debito != 0 and credito != 0:
                    raise ValueError("informe só débito ou só crédito na mesma linha")
        except ValueError as exc:
            erros.append({"linha": n, "mensagem": str(exc)})
            continue
        linhas.append(
            LinhaPlanilha(
                linha=n, data=data, conta=conta, valor=valor, debito=debito, credito=credito,
                historico=clean_text(celula(bruta, "historico")), documento=clean_text(celula(bruta, "documento")),
            )
        )
    return linhas, erros
