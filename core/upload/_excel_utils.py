# core/upload/_excel_utils.py
from __future__ import annotations

import unicodedata


def normalize_header(s) -> str:
    """
    Normaliza nome de coluna para comparação:
    remove acentos, upper, troca separadores por espaço e comprime espaços.
    """
    s = str(s or "").strip()
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = s.replace("_", " ").replace("-", " ").replace("/", " ")
    return " ".join(s.split()).upper()


def clean_text(value) -> str:
    """Texto sem espaços não separáveis (\\xa0) e sem espaços duplicados nas pontas."""
    return " ".join(str(value or "").replace("\xa0", " ").split())
