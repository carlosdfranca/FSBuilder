"""Copia os documentos que estão no disco local para o S3, conferindo a integridade.

Segurança:
- Só COPIA: nunca apaga nada do disco local (apague você, depois de conferir o relatório).
- Idempotente: o que já está no S3 com o mesmo tamanho é pulado; se o tamanho divergir, é
  reportado como erro (nunca sobrescreve). Pode rodar quantas vezes precisar.
- Cada arquivo copiado é lido de volta do S3 e comparado (tamanho e sha256) com o
  registro do banco; se não bater, a cópia é removida do S3 e o erro é reportado.
- Arquivos que o banco conhece mas que não existem no disco local são apenas
  reportados (ex.: removidos manualmente por engano) — não há o que migrar.

Use --dry-run primeiro para ver o que seria feito sem enviar nada.
"""
import hashlib

from django.core.files import File
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from df.models import DocumentoDF
from df.storages import DocumentosLocalStorage


def _sha256_e_tamanho(arquivo):
    hasher = hashlib.sha256()
    tamanho = 0
    for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
        hasher.update(bloco)
        tamanho += len(bloco)
    return hasher.hexdigest(), tamanho


class Command(BaseCommand):
    help = "Copia os documentos do disco local para o S3 (só copia, confere o hash, não apaga nada)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Só mostra o que seria copiado.")

    def _origem(self):
        return DocumentosLocalStorage()

    def _destino(self):
        return storages["documentos"]

    def handle(self, *args, dry_run=False, **options):
        destino = self._destino()
        if not hasattr(destino, "url_assinada_download"):
            raise CommandError(
                "O backend de documentos ainda é o LOCAL — não há para onde migrar. Defina "
                "DOCUMENTOS_STORAGE_BACKEND=df.storages.DocumentosS3Storage no .env e rode "
                "`python manage.py verificar_s3` antes."
            )
        origem = self._origem()

        copiados = ja_no_s3 = ausentes = erros = 0
        for doc in DocumentoDF.objects.exclude(arquivo="").order_by("id").iterator(chunk_size=200):
            nome = doc.arquivo.name
            rotulo = f"#{doc.id} {doc.nome_original} ({nome})"

            if destino.exists(nome):
                tamanho_remoto = destino.size(nome)
                if origem.exists(nome) and origem.size(nome) != tamanho_remoto:
                    erros += 1
                    self.stdout.write(self.style.ERROR(
                        f"ERRO          {rotulo}: já existe no S3 com tamanho diferente "
                        f"({tamanho_remoto} no S3 x {origem.size(nome)} local) — não foi sobrescrito"
                    ))
                    continue
                ja_no_s3 += 1
                self.stdout.write(f"já no S3      {rotulo}")
                continue
            if not origem.exists(nome):
                ausentes += 1
                self.stdout.write(self.style.WARNING(f"SEM ARQUIVO LOCAL {rotulo} — nada a migrar"))
                continue
            if dry_run:
                copiados += 1
                self.stdout.write(f"copiaria      {rotulo}")
                continue

            try:
                with origem.open(nome, "rb") as local:
                    nome_salvo = destino.save(nome, File(local))
                if nome_salvo != nome:
                    destino.delete(nome_salvo)
                    raise CommandError(f"o S3 gravou com outro nome ({nome_salvo}); cópia desfeita")

                with destino.open(nome, "rb") as remoto:
                    sha_remoto, tamanho_remoto = _sha256_e_tamanho(remoto)
                with origem.open(nome, "rb") as local:
                    sha_local, tamanho_local = _sha256_e_tamanho(local)

                if (sha_remoto, tamanho_remoto) != (sha_local, tamanho_local) or (
                    doc.sha256 and doc.sha256 != sha_remoto
                ):
                    destino.delete(nome)
                    raise CommandError("conteúdo no S3 diferente do original; cópia desfeita")
            except Exception as exc:  # um arquivo com problema não derruba o lote
                erros += 1
                self.stdout.write(self.style.ERROR(f"ERRO          {rotulo}: {exc}"))
                continue

            copiados += 1
            self.stdout.write(self.style.SUCCESS(f"copiado e conferido  {rotulo}"))

        verbo = "seriam copiados" if dry_run else "copiados e conferidos"
        self.stdout.write(
            f"\nResumo: {copiados} {verbo} | {ja_no_s3} já estavam no S3 | "
            f"{ausentes} sem arquivo local | {erros} com erro"
        )
        if erros:
            raise CommandError(f"{erros} documento(s) com erro — veja as linhas ERRO acima.")
        if not dry_run and copiados:
            self.stdout.write("Os arquivos locais NÃO foram apagados. Confira o relatório antes de removê-los.")
