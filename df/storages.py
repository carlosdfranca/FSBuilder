from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages


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


def documentos_storage():
    """Callable resolvido em tempo de execução pelo Django.

    O FileField do DocumentoDF grava apenas esta referência na migration, não o
    backend concreto — então trocar STORAGES["documentos"]["BACKEND"] (ex.: para
    S3 via django-storages) não gera migration nova.
    """
    return storages["documentos"]
