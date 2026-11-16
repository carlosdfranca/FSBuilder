from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.upload._excel_utils import clean_text, normalize_header
from df.models import Conta

COLUNAS = {
    "CODIGO": "codigo",
    "DESCRICAO": "descricao",
    "DT INI": "dt_ini",
    "DT FIM": "dt_fim",
    "TIPO": "tipo",
    "CONTA SUPERIOR": "superior",
    "NIVEL": "nivel",
    "NATUREZA": "natureza",
    "ORIENTACOES": "orientacoes",
}

PAPEIS = {
    "2.03.04.01.01": Conta.Papel.LUCROS_ACUMULADOS,
    "2.03.04.01.11": Conta.Papel.PREJUIZOS_ACUMULADOS,
}


def _codigo(valor) -> str | None:
    texto = clean_text(valor)
    return texto or None


def _pai_do_codigo(codigo: str) -> str | None:
    """O código é o caminho na árvore: o pai é o código sem o último segmento."""
    return codigo.rsplit(".", 1)[0] if "." in codigo else None


def natureza_compativel(pai: int, filha: int) -> bool:
    # No plano referencial o PL (natureza 3) é filho de PASSIVO (natureza 2).
    return filha == pai or (pai == 2 and filha == 3)


def _data(valor) -> date | None:
    """Vigência no formato ddmmaaaa (texto ou número)."""
    texto = clean_text(valor)
    if not texto:
        return None
    texto = texto.zfill(8)
    try:
        return date(int(texto[4:]), int(texto[2:4]), int(texto[:2]))
    except ValueError:
        return None


def ler_aba(caminho: Path, aba: str) -> tuple[list[dict], list[str]]:
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    if aba not in wb.sheetnames:
        raise CommandError(f"Aba '{aba}' não encontrada. Abas: {', '.join(wb.sheetnames[:10])}...")

    linhas = list(wb[aba].iter_rows(values_only=True))
    cabecalho = [normalize_header(c) for c in linhas[0]]
    faltando = [c for c in ("CODIGO", "DESCRICAO", "TIPO", "CONTA SUPERIOR", "NIVEL", "NATUREZA") if c not in cabecalho]
    if faltando:
        raise CommandError(f"Aba '{aba}': colunas ausentes {faltando}")
    idx = {COLUNAS[c]: i for i, c in enumerate(cabecalho) if c in COLUNAS}

    contas, erros = [], []
    for n, linha in enumerate(linhas[1:], start=2):
        codigo = _codigo(linha[idx["codigo"]])
        if codigo is None:
            continue
        try:
            contas.append(
                {
                    "codigo": codigo,
                    "descricao": clean_text(linha[idx["descricao"]]),
                    "tipo": clean_text(linha[idx["tipo"]]).upper(),
                    "superior": _pai_do_codigo(codigo),
                    "superior_planilha": _codigo(linha[idx["superior"]]),
                    "nivel": int(linha[idx["nivel"]]),
                    "natureza": int(linha[idx["natureza"]]),
                    "vigencia_inicio": _data(linha[idx["dt_ini"]]) if "dt_ini" in idx else None,
                    "vigencia_fim": _data(linha[idx["dt_fim"]]) if "dt_fim" in idx else None,
                    "orientacoes": clean_text(linha[idx["orientacoes"]]) if "orientacoes" in idx else "",
                    "origem_tabela": aba,
                    "_linha": f"{aba}:{n}",
                }
            )
        except (TypeError, ValueError) as exc:
            erros.append(f"{aba}:{n} ({codigo}): {exc}")
    return contas, erros


def validar_arvore(contas: list[dict]) -> list[str]:
    erros = []
    por_codigo: dict[str, dict] = {}
    for c in contas:
        if c["codigo"] in por_codigo:
            erros.append(f"{c['_linha']}: código duplicado {c['codigo']}")
        por_codigo[c["codigo"]] = c

    com_filhas = {c["superior"] for c in contas if c["superior"]}
    for c in contas:
        ref = f"{c['_linha']} ({c['codigo']})"
        if c["tipo"] not in ("S", "A"):
            erros.append(f"{ref}: tipo inválido '{c['tipo']}'")
        if c["natureza"] not in (1, 2, 3, 4):
            erros.append(f"{ref}: natureza inválida {c['natureza']}")
        if c["tipo"] == "A" and c["codigo"] in com_filhas:
            erros.append(f"{ref}: conta analítica com filhas")
        if c["tipo"] == "S" and c["codigo"] not in com_filhas:
            erros.append(f"{ref}: conta sintética sem filhas")

        pai = por_codigo.get(c["superior"]) if c["superior"] else None
        if c["superior"] and pai is None:
            erros.append(f"{ref}: conta superior {c['superior']} inexistente")
        elif pai:
            if not c["codigo"].startswith(f"{pai['codigo']}."):
                erros.append(f"{ref}: código não começa com o da conta superior")
            if c["nivel"] != pai["nivel"] + 1:
                erros.append(f"{ref}: nível {c['nivel']} diferente de {pai['nivel'] + 1}")
            if not natureza_compativel(pai["natureza"], c["natureza"]):
                erros.append(f"{ref}: natureza diferente da conta superior")
        elif c["nivel"] != 1:
            erros.append(f"{ref}: raiz com nível {c['nivel']}")
    return erros


class Command(BaseCommand):
    help = "Carrega o plano de contas único (abas do Plano Referencial RFB). Idempotente."

    def add_arguments(self, parser):
        parser.add_argument("arquivo", help="Caminho do .xlsx")
        parser.add_argument("--aba", action="append", required=True, help="Aba a importar (repetível)")
        parser.add_argument("--dry-run", action="store_true", help="Valida sem gravar")

    def handle(self, *args, **opts):
        caminho = Path(opts["arquivo"])
        if not caminho.exists():
            raise CommandError(f"Arquivo não encontrado: {caminho}")

        contas, erros = [], []
        for aba in opts["aba"]:
            lidas, erros_aba = ler_aba(caminho, aba)
            contas += lidas
            erros += erros_aba
        erros += validar_arvore(contas)

        for c in contas:
            if c["superior_planilha"] != c["superior"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"{c['_linha']} ({c['codigo']}): conta superior da planilha "
                        f"'{c['superior_planilha']}' ignorada; usando '{c['superior']}' (pelo código)."
                    )
                )

        if erros:
            for e in erros[:50]:
                self.stderr.write(e)
            raise CommandError(f"{len(erros)} erro(s) no plano de contas; nada foi gravado.")

        contas.sort(key=lambda c: c["nivel"])  # pais antes dos filhos
        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS(f"OK (dry-run): {len(contas)} contas válidas."))
            return

        criadas = atualizadas = 0
        with transaction.atomic():
            objetos: dict[str, Conta] = {}
            for c in contas:
                obj, criada = Conta.objects.update_or_create(
                    codigo=c["codigo"],
                    defaults={
                        "descricao": c["descricao"][:255],
                        "tipo": c["tipo"],
                        "conta_superior": objetos.get(c["superior"]) if c["superior"] else None,
                        "nivel": c["nivel"],
                        "natureza": c["natureza"],
                        "vigencia_inicio": c["vigencia_inicio"],
                        "vigencia_fim": c["vigencia_fim"],
                        "orientacoes": c["orientacoes"],
                        "origem_tabela": c["origem_tabela"],
                    },
                )
                objetos[c["codigo"]] = obj
                criadas += criada
                atualizadas += not criada

            for codigo, papel in PAPEIS.items():
                if codigo in objetos:
                    Conta.objects.filter(papel=papel).exclude(codigo=codigo).update(papel=None)
                    Conta.objects.filter(codigo=codigo).update(papel=papel)

        sinteticas = sum(c["tipo"] == "S" for c in contas)
        self.stdout.write(
            self.style.SUCCESS(
                f"{criadas} criada(s), {atualizadas} atualizada(s) — "
                f"{sinteticas} sintéticas e {len(contas) - sinteticas} analíticas."
            )
        )
