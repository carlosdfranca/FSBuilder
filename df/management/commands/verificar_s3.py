"""Teste de ponta a ponta da ligação do FSBuilder com o S3.

Envia, confere, baixa por URL assinada e apaga um arquivinho de teste, imprimindo
OK/FALHA em cada etapa e dizendo o que provavelmente está errado quando falha.
Rodar depois de configurar o .env e antes de liberar o upload para clientes.
"""
import urllib.error
import urllib.request
import uuid

from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

CONTEUDO_TESTE = b"fsbuilder-verificacao-s3"

DICAS = {
    "AccessDenied": (
        "Sem permissão. Confira se o nome do bucket na política do usuário IAM é exatamente o "
        "do .env (nas duas linhas Resource) e se a política inclui s3:ListBucket, "
        "s3:PutObject, s3:GetObject e s3:DeleteObject."
    ),
    "403": "Sem permissão. Confira a política do usuário IAM (ver dica de AccessDenied).",
    "NoSuchBucket": "O bucket não existe nessa região. Confira AWS_STORAGE_BUCKET_NAME e AWS_S3_REGION_NAME.",
    "InvalidAccessKeyId": "A Access Key ID não existe. Confira AWS_ACCESS_KEY_ID no .env.",
    "SignatureDoesNotMatch": "A Secret Access Key está errada ou foi copiada com espaço sobrando no começo/fim.",
    "PermanentRedirect": "O bucket está em outra região. Corrija AWS_S3_REGION_NAME.",
}


class Command(BaseCommand):
    help = "Verifica a conexão com o S3: envia, lê, baixa por URL assinada e apaga um arquivo de teste."

    def _obter_storage(self):
        return storages["documentos"]

    def _etapa(self, titulo, funcao):
        try:
            resultado = funcao()
        except CommandError:
            raise
        except ClientError as exc:
            codigo = exc.response.get("Error", {}).get("Code", "?")
            dica = DICAS.get(codigo, "Erro retornado pela AWS; procure o código no guia.")
            raise CommandError(f"FALHA em '{titulo}' [{codigo}]: {dica}")
        except NoCredentialsError:
            raise CommandError(
                f"FALHA em '{titulo}': nenhuma credencial encontrada. "
                "Preencha AWS_ACCESS_KEY_ID e AWS_SECRET_ACCESS_KEY no .env."
            )
        except EndpointConnectionError as exc:
            raise CommandError(f"FALHA em '{titulo}': sem conexão com a AWS ({exc}).")
        except ImproperlyConfigured as exc:
            raise CommandError(f"FALHA em '{titulo}': configuração incompleta — {exc}")
        self.stdout.write(self.style.SUCCESS(f"OK     {titulo}"))
        return resultado

    def handle(self, *args, **options):
        storage = self._obter_storage()

        if not hasattr(storage, "url_assinada_download"):
            raise CommandError(
                "O backend de documentos ainda é o LOCAL. Defina "
                "DOCUMENTOS_STORAGE_BACKEND=df.storages.DocumentosS3Storage no .env e rode de novo."
            )
        if not settings.AWS_STORAGE_BUCKET_NAME:
            raise CommandError("AWS_STORAGE_BUCKET_NAME está vazio no .env.")

        self.stdout.write(
            f"Bucket: {settings.AWS_STORAGE_BUCKET_NAME} | região: {settings.AWS_S3_REGION_NAME} | "
            f"chaves: {'informadas no .env' if settings.AWS_ACCESS_KEY_ID else 'NÃO informadas (cadeia padrão do boto3)'}"
        )

        nome = None
        try:
            nome = self._etapa(
                "enviar arquivo de teste",
                lambda: storage.save(f"_verificacao/{uuid.uuid4().hex}.txt", ContentFile(CONTEUDO_TESTE)),
            )

            existe = self._etapa("conferir que o arquivo existe", lambda: storage.exists(nome))
            if not existe:
                raise CommandError("FALHA: o arquivo foi enviado mas o S3 diz que ele não existe.")

            def ler():
                with storage.open(nome, "rb") as f:
                    return f.read()

            if self._etapa("ler o arquivo de volta", ler) != CONTEUDO_TESTE:
                raise CommandError("FALHA: o conteúdo lido é diferente do enviado.")

            def baixar_por_url():
                url = storage.url_assinada_download(nome, "teste.txt")
                with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - URL assinada gerada acima
                    if "attachment" not in (resp.headers.get("Content-Disposition") or ""):
                        raise CommandError("FALHA: a URL assinada não forçou download (Content-Disposition).")
                    return resp.read()

            try:
                corpo = self._etapa("baixar por URL assinada (como o usuário vai baixar)", baixar_por_url)
            except urllib.error.HTTPError as exc:
                raise CommandError(f"FALHA ao baixar por URL assinada: HTTP {exc.code}. {DICAS.get(str(exc.code), '')}")
            if corpo != CONTEUDO_TESTE:
                raise CommandError("FALHA: o arquivo baixado por URL assinada é diferente do enviado.")

            self._etapa("apagar o arquivo de teste", lambda: storage.delete(nome))
            if storage.exists(nome):
                raise CommandError("FALHA: o arquivo de teste continua no bucket depois de apagar.")
            nome = None
        finally:
            if nome:
                try:
                    storage.delete(nome)
                except Exception:  # limpeza best-effort; o erro original é o que importa
                    pass

        self.stdout.write(self.style.SUCCESS("\nTudo certo: o FSBuilder está conversando com o S3."))
