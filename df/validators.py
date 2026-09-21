"""Validações de upload de documentos da DF.

Escopo deliberadamente pequeno: allowlist de extensão + sniff de magic bytes
nos primeiros bytes do arquivo. Não usa python-magic para evitar dependência
nativa chata de empacotar no Windows.
"""
import hashlib
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

# .svg e .html ficam deliberadamente de fora — são os dois vetores de XSS
# armazenado mais comuns em upload de arquivo.
EXTENSOES_PERMITIDAS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ods", "csv", "txt", "xml",
    "png", "jpg", "jpeg", "zip", "p7s",
}

# Assinaturas de magic bytes conhecidas, por extensão. Extensões sem entrada
# aqui (csv, txt, xml, p7s) não têm um formato binário fixo e não são
# checadas por header — só pela extensão e pelo tamanho.
_ASSINATURAS = {
    "pdf": [b"%PDF"],
    "docx": [b"PK\x03\x04"],
    "xlsx": [b"PK\x03\x04"],
    "ods": [b"PK\x03\x04"],
    "zip": [b"PK\x03\x04"],
    "doc": [b"\xD0\xCF\x11\xE0"],
    "xls": [b"\xD0\xCF\x11\xE0"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xFF\xD8\xFF"],
    "jpeg": [b"\xFF\xD8\xFF"],
}


def validar_extensao(nome_arquivo: str) -> str:
    """Retorna a extensão (sem ponto, minúscula) se permitida; senão levanta ValidationError."""
    ext = Path(nome_arquivo).suffix.lower().lstrip(".")
    if ext not in EXTENSOES_PERMITIDAS:
        raise ValidationError(
            f'Extensão ".{ext}" não permitida. Extensões aceitas: '
            + ", ".join(sorted(EXTENSOES_PERMITIDAS))
        )
    return ext


def validar_tamanho(tamanho_bytes: int) -> None:
    limite = settings.DOCUMENTOS_MAX_UPLOAD_MB * 1024 * 1024
    if tamanho_bytes > limite:
        raise ValidationError(
            f"Arquivo maior que o limite de {settings.DOCUMENTOS_MAX_UPLOAD_MB} MB."
        )


def validar_header(ext: str, primeiros_bytes: bytes) -> None:
    """Confere que o conteúdo real bate com a extensão declarada.

    Nunca confia no content_type informado pelo cliente — só nos bytes reais.
    """
    assinaturas = _ASSINATURAS.get(ext)
    if not assinaturas:
        return
    if not any(primeiros_bytes.startswith(sig) for sig in assinaturas):
        raise ValidationError("O conteúdo do arquivo não corresponde à extensão informada.")


def calcular_sha256(arquivo) -> str:
    """Calcula o sha256 em streaming, sem carregar o arquivo inteiro na memória.

    Deixa o cursor do arquivo no início ao final, pronto para ser salvo.
    """
    hasher = hashlib.sha256()
    arquivo.seek(0)
    chunks = arquivo.chunks() if hasattr(arquivo, "chunks") else iter(lambda: arquivo.read(65536), b"")
    for chunk in chunks:
        hasher.update(chunk)
    arquivo.seek(0)
    return hasher.hexdigest()


def validar_documento_upload(uploaded_file) -> tuple[str, str]:
    """Roda todas as validações de upload e retorna (extensao, sha256).

    Ponto único chamado pela view de upload — nenhuma outra rota grava um
    DocumentoDF sem passar por aqui.
    """
    ext = validar_extensao(uploaded_file.name)
    validar_tamanho(uploaded_file.size)
    primeiros_bytes = uploaded_file.read(16)
    uploaded_file.seek(0)
    validar_header(ext, primeiros_bytes)
    sha256 = calcular_sha256(uploaded_file)
    return ext, sha256
