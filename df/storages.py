from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages
from django.utils.http import content_disposition_header
from storages.backends.s3 import S3Storage


class DocumentosLocalStorage(FileSystemStorage):
    """Storage local para documentos da DF — fora do MEDIA_ROOT público.

    base_url=None por construção: não existe URL pública para estes arquivos.
    Todo acesso passa pela view de download (df/services/documento_service.py),
    que valida escopo por empresa e registra log antes de servir o conteúdo.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.DOCUMENTOS_ROOT)
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)


class DocumentosS3Storage(S3Storage):
    """Storage dos documentos da DF no AWS S3.

    Configuração (bucket, região, chaves, URL assinada de 60 s, sem sobrescrita
    silenciosa, sem ACL) vem de cinnamon/settings.py via variáveis AWS_*; o
    bucket é privado (Block Public Access) e nenhum arquivo tem URL pública —
    o único caminho de acesso é a view de download, que valida o escopo, grava o
    log e só então entrega uma URL assinada que expira em AWS_QUERYSTRING_EXPIRE
    segundos.
    """

    def url_assinada_download(self, name, nome_arquivo):
        """URL assinada para baixar `name` como anexo, com o nome original.

        Os cabeçalhos de resposta são forçados na própria assinatura
        (Content-Disposition: attachment e Content-Type: octet-stream) — o
        equivalente, no S3, do que resposta_download faz no storage local.
        """
        return self.url(
            name,
            parameters={
                "ResponseContentDisposition": content_disposition_header(True, nome_arquivo),
                "ResponseContentType": "application/octet-stream",
            },
            expire=settings.AWS_QUERYSTRING_EXPIRE,
        )


def documentos_storage():
    """Callable resolvido pelo Django ao carregar o model.

    O FileField do DocumentoDF grava apenas esta referência na migration, não o
    backend concreto — então trocar STORAGES["documentos"]["BACKEND"] (local ↔
    S3, via DOCUMENTOS_STORAGE_BACKEND no .env) não gera migration nova.
    """
    return storages["documentos"]
