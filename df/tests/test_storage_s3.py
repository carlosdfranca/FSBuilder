"""Testes da integração com S3 (Fase 6). Usam `moto` (S3 simulado em memória, que
exercita o boto3 de verdade) — nenhum teste fala com a AWS real.

Dependência só de desenvolvimento: pip install -r requirements-dev.txt
"""
import hashlib
import tempfile
from contextlib import contextmanager
from datetime import date
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

import boto3
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponseRedirect
from django.test import TestCase, override_settings
from django.urls import reverse
from moto import mock_aws

from df.management.commands.migrar_documentos_para_s3 import Command as MigrarCommand
from df.management.commands.verificar_s3 import Command as VerificarCommand
from df.models import DocumentoDF, Fundo, LogAcessoDocumento, PeriodoDF
from df.services.documento_service import resposta_download
from df.storages import DocumentosLocalStorage, DocumentosS3Storage
from usuarios.models import Empresa, Membership, Usuario

BUCKET = "bucket-de-teste"
AWS_FAKE = dict(
    AWS_STORAGE_BUCKET_NAME=BUCKET,
    AWS_S3_REGION_NAME="sa-east-1",
    AWS_ACCESS_KEY_ID="AKIAFAKEFAKEFAKEFAKE",
    AWS_SECRET_ACCESS_KEY="segredo-falso-para-teste",
)


@contextmanager
def usar_storage_nos_documentos(storage):
    """O FileField resolve o storage ao carregar o model; aqui trocamos por um
    storage S3 simulado só durante o teste (os FieldFile novos passam a usá-lo)."""
    campo = DocumentoDF._meta.get_field("arquivo")
    original = campo.storage
    campo.storage = storage
    try:
        yield
    finally:
        campo.storage = original


@override_settings(**AWS_FAKE)
class ComS3Simulado(TestCase):
    """Base: moto ligado, bucket criado na região certa, storage S3 real apontando pra ele."""

    def setUp(self):
        self._moto = mock_aws()
        self._moto.start()
        self.addCleanup(self._moto.stop)
        boto3.client(
            "s3", region_name="sa-east-1",
            aws_access_key_id=AWS_FAKE["AWS_ACCESS_KEY_ID"], aws_secret_access_key=AWS_FAKE["AWS_SECRET_ACCESS_KEY"],
        ).create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "sa-east-1"})
        self.s3 = DocumentosS3Storage()

        self.empresa = Empresa.objects.create(nome="Empresa S3", documentos_habilitados=True)
        fundo = Fundo.objects.create(empresa=self.empresa, nome="Fundo S3", cnpj="12.000.000/0001-00", tipo_fundo="FIDC")
        self.periodo = PeriodoDF.objects.create(
            fundo=fundo, empresa=self.empresa, tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )

    def _doc_no_s3(self, nome="documentos/emp_1/fundo_1/2026/periodo_1/abc.pdf", conteudo=b"%PDF conteudo", periodo=None, empresa=None):
        self.s3.save(nome, ContentFile(conteudo))
        return DocumentoDF.objects.create(
            empresa=empresa or self.empresa, periodo_df=periodo or self.periodo, arquivo=nome,
            nome_original="balancete.pdf", tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
        )


class UrlAssinadaTests(ComS3Simulado):

    def _query(self, url):
        return parse_qs(urlparse(url).query)

    def test_url_expira_em_60_segundos_e_forca_download(self):
        url = self.s3.url_assinada_download("documentos/emp_1/x.pdf", "balancete.pdf")
        q = self._query(url)

        self.assertEqual(urlparse(url).netloc, f"{BUCKET}.s3.sa-east-1.amazonaws.com")
        self.assertEqual(q["X-Amz-Expires"], ["60"])
        self.assertIn("X-Amz-Signature", q)
        self.assertIn("attachment", q["response-content-disposition"][0])
        self.assertIn("balancete.pdf", q["response-content-disposition"][0])
        self.assertEqual(q["response-content-type"], ["application/octet-stream"])

    def test_nome_com_acento_vai_no_formato_rfc5987(self):
        url = self.s3.url_assinada_download("documentos/x.pdf", "Relatório Anual.pdf")
        self.assertIn("filename*=", self._query(url)["response-content-disposition"][0])

    def test_envio_grava_criptografado_sem_acl(self):
        self.s3.save("documentos/emp_1/cripto.pdf", ContentFile(b"x"))
        cliente = boto3.client("s3", region_name="sa-east-1", aws_access_key_id="a", aws_secret_access_key="b")
        meta = cliente.head_object(Bucket=BUCKET, Key="documentos/emp_1/cripto.pdf")
        self.assertEqual(meta["ServerSideEncryption"], "AES256")

    def test_nao_sobrescreve_arquivo_existente(self):
        a = self.s3.save("documentos/emp_1/igual.pdf", ContentFile(b"primeiro"))
        b = self.s3.save("documentos/emp_1/igual.pdf", ContentFile(b"segundo"))
        self.assertNotEqual(a, b)
        with self.s3.open(a, "rb") as f:
            self.assertEqual(f.read(), b"primeiro")


class DownloadViaViewTests(ComS3Simulado):

    def setUp(self):
        super().setUp()
        self.member = Usuario.objects.create_user(username="member_s3", password="x")
        Membership.objects.create(usuario=self.member, empresa=self.empresa, role=Membership.Role.MEMBER, is_active=True)
        self.viewer = Usuario.objects.create_user(username="viewer_s3", password="x")
        Membership.objects.create(usuario=self.viewer, empresa=self.empresa, role=Membership.Role.VIEWER, is_active=True)

        self.outra = Empresa.objects.create(nome="Outra Empresa S3", documentos_habilitados=True)
        self.outro_user = Usuario.objects.create_user(username="outro_s3", password="x")
        Membership.objects.create(usuario=self.outro_user, empresa=self.outra, role=Membership.Role.MASTER, is_active=True)

        self.doc = self._doc_no_s3()

    def test_membro_recebe_redirect_para_url_assinada_e_log(self):
        self.client.force_login(self.member)
        with usar_storage_nos_documentos(self.s3):
            resposta = self.client.get(reverse("download_documento", args=[self.doc.id]))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("X-Amz-Signature", resposta["Location"])
        self.assertEqual(resposta["Cache-Control"], "no-store")
        self.assertTrue(
            LogAcessoDocumento.objects.filter(documento=self.doc, acao=LogAcessoDocumento.Acao.DOWNLOAD).exists()
        )

    def test_usuario_de_outra_empresa_nao_recebe_url(self):
        self.client.force_login(self.outro_user)
        with usar_storage_nos_documentos(self.s3):
            resposta = self.client.get(reverse("download_documento", args=[self.doc.id]))
        self.assertEqual(resposta.status_code, 403)
        self.assertNotIn("Location", resposta)

    def test_viewer_nao_recebe_url(self):
        self.client.force_login(self.viewer)
        with usar_storage_nos_documentos(self.s3):
            resposta = self.client.get(reverse("download_documento", args=[self.doc.id]))
        self.assertEqual(resposta.status_code, 403)

    def test_resposta_download_devolve_redirect(self):
        with usar_storage_nos_documentos(self.s3):
            doc = DocumentoDF.objects.get(id=self.doc.id)
            self.assertIsInstance(resposta_download(doc), HttpResponseRedirect)


class ExpurgoNoS3Tests(ComS3Simulado):

    def setUp(self):
        super().setUp()
        self.admin = Usuario.objects.create_user(
            username="admin_s3", password="x", global_role=Usuario.GlobalRole.PLATFORM_ADMIN,
        )
        self.doc = self._doc_no_s3()

    def test_expurgo_remove_o_objeto_do_bucket(self):
        nome = self.doc.arquivo.name
        self.assertTrue(self.s3.exists(nome))

        self.client.force_login(self.admin)
        with usar_storage_nos_documentos(self.s3):
            self.client.post(reverse("expurgar_documento", args=[self.doc.id]))

        self.assertFalse(self.s3.exists(nome), "O objeto precisa sumir do bucket de verdade.")
        self.assertFalse(DocumentoDF.objects.filter(id=self.doc.id).exists())

    def test_falha_do_s3_nao_apaga_registro_nem_grava_log_de_expurgo(self):
        erro = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "DeleteObject")
        self.client.force_login(self.admin)
        with usar_storage_nos_documentos(self.s3), mock.patch.object(self.s3, "delete", side_effect=erro):
            resposta = self.client.post(reverse("expurgar_documento", args=[self.doc.id]), follow=True)

        self.assertTrue(DocumentoDF.objects.filter(id=self.doc.id).exists(), "Sem apagar o arquivo, o registro fica.")
        self.assertTrue(self.s3.exists(self.doc.arquivo.name))
        self.assertFalse(
            LogAcessoDocumento.objects.filter(documento_id_hist=self.doc.id, acao=LogAcessoDocumento.Acao.EXPURGO).exists(),
            "Não pode haver log de um expurgo que não aconteceu.",
        )
        mensagens = [str(m) for m in resposta.context["messages"]]
        self.assertTrue(any("Não foi possível apagar o arquivo" in m for m in mensagens))


class MigrarDocumentosParaS3Tests(ComS3Simulado):

    def setUp(self):
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.origem = DocumentosLocalStorage(location=self._dir.name)

    def _doc_local(self, nome, conteudo=b"conteudo do documento", sha256=None, grava_local=True):
        if grava_local:
            self.origem.save(nome, ContentFile(conteudo))
        return DocumentoDF.objects.create(
            empresa=self.empresa, periodo_df=self.periodo, arquivo=nome,
            nome_original=nome.split("/")[-1], tamanho_bytes=len(conteudo),
            sha256=sha256 if sha256 is not None else hashlib.sha256(conteudo).hexdigest(),
        )

    def _migrar(self, *args):
        saida = StringIO()
        try:
            with mock.patch.object(MigrarCommand, "_origem", return_value=self.origem), \
                 mock.patch.object(MigrarCommand, "_destino", return_value=self.s3):
                call_command("migrar_documentos_para_s3", *args, stdout=saida)
        finally:
            self.saida = saida.getvalue()

    def test_copia_confere_e_nao_apaga_o_local(self):
        self._doc_local("documentos/emp_1/a.pdf")
        self._migrar()
        self.assertTrue(self.s3.exists("documentos/emp_1/a.pdf"))
        self.assertTrue(self.origem.exists("documentos/emp_1/a.pdf"), "O arquivo local nunca pode ser apagado.")
        self.assertIn("1 copiados e conferidos", self.saida)

    def test_segunda_execucao_nao_copia_de_novo(self):
        self._doc_local("documentos/emp_1/a.pdf")
        self._migrar()
        self._migrar()
        self.assertIn("0 copiados e conferidos | 1 já estavam no S3", self.saida)

    def test_dry_run_nao_envia_nada(self):
        self._doc_local("documentos/emp_1/a.pdf")
        self._migrar("--dry-run")
        self.assertFalse(self.s3.exists("documentos/emp_1/a.pdf"))
        self.assertIn("1 seriam copiados", self.saida)

    def test_documento_sem_arquivo_local_so_e_reportado(self):
        self._doc_local("documentos/emp_1/sumiu.pdf", grava_local=False)
        self._migrar()
        self.assertIn("SEM ARQUIVO LOCAL", self.saida)
        self.assertIn("1 sem arquivo local", self.saida)

    def test_hash_diferente_do_banco_desfaz_a_copia_e_falha(self):
        self._doc_local("documentos/emp_1/corrompido.pdf", sha256="0" * 64)
        with self.assertRaises(CommandError):
            self._migrar()
        self.assertFalse(self.s3.exists("documentos/emp_1/corrompido.pdf"))
        self.assertIn("ERRO", self.saida)

    def test_ja_existente_com_tamanho_diferente_vira_erro_sem_sobrescrever(self):
        self._doc_local("documentos/emp_1/diverge.pdf", conteudo=b"versao local, maior")
        self.s3.save("documentos/emp_1/diverge.pdf", ContentFile(b"curto"))
        with self.assertRaises(CommandError):
            self._migrar()
        with self.s3.open("documentos/emp_1/diverge.pdf", "rb") as f:
            self.assertEqual(f.read(), b"curto", "Nunca sobrescrever o que já está no S3.")
        self.assertIn("tamanho diferente", self.saida)

    def test_backend_local_e_recusado(self):
        with mock.patch.object(MigrarCommand, "_destino", return_value=self.origem):
            with self.assertRaises(CommandError):
                call_command("migrar_documentos_para_s3", stdout=StringIO())


class VerificarS3Tests(ComS3Simulado):

    class _Resposta:
        headers = {"Content-Disposition": "attachment; filename=teste.txt"}

        def __init__(self, corpo):
            self._corpo = corpo

        def read(self):
            return self._corpo

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen_que_le_do_s3(self):
        # moto intercepta o boto3, não o urllib — então o "download" pela URL é simulado
        # lendo o mesmo objeto do bucket simulado.
        def fake(url, timeout=None):
            chave = urlparse(url).path.lstrip("/")
            with self.s3.open(chave, "rb") as f:
                return self._Resposta(f.read())
        return fake

    def test_fluxo_completo_ok_e_nao_deixa_lixo(self):
        saida = StringIO()
        with mock.patch.object(VerificarCommand, "_obter_storage", return_value=self.s3), \
             mock.patch("urllib.request.urlopen", side_effect=self._urlopen_que_le_do_s3()):
            call_command("verificar_s3", stdout=saida)
        self.assertIn("Tudo certo", saida.getvalue())
        self.assertEqual(self.s3.listdir("_verificacao")[1], [])

    def test_bucket_inexistente_vira_mensagem_com_dica(self):
        errado = DocumentosS3Storage(bucket_name="bucket-que-nao-existe")
        with mock.patch.object(VerificarCommand, "_obter_storage", return_value=errado):
            with self.assertRaises(CommandError) as ctx:
                call_command("verificar_s3", stdout=StringIO())
        self.assertIn("NoSuchBucket", str(ctx.exception))

    def test_access_denied_vira_mensagem_com_dica(self):
        erro = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "PutObject")
        with mock.patch.object(VerificarCommand, "_obter_storage", return_value=self.s3), \
             mock.patch.object(self.s3, "save", side_effect=erro):
            with self.assertRaises(CommandError) as ctx:
                call_command("verificar_s3", stdout=StringIO())
        self.assertIn("AccessDenied", str(ctx.exception))
        self.assertIn("política", str(ctx.exception))

    def test_backend_local_e_recusado(self):
        with self.assertRaises(CommandError):
            call_command("verificar_s3", stdout=StringIO())
