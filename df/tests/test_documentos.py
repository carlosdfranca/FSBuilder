"""Testes do risco nº 1 do plano de upload de documentos: ressincronizar_checklist_por_tipo()
apaga e recria ChecklistItemPeriodo quando o tipo do fundo muda. Um DocumentoDF anexado não
pode desaparecer nesse processo — precisa sobreviver com checklist_item=NULL (avulso) ou,
quando o texto do item persiste no novo tipo, voltar vinculado ao item recriado.
"""
from datetime import date

from django.core.files.base import ContentFile
from django.test import TestCase

from df.data.checklist_padrao_seed import SEED
from df.models import ChecklistItemPeriodo, DocumentoDF, Fundo, PeriodoDF
from df.services.checklist_service import criar_checklist_para_periodo, ressincronizar_checklist_por_tipo
from usuarios.models import Empresa, Usuario


class ResyncPreservaDocumentosTests(TestCase):

    def setUp(self):
        self.empresa = Empresa.objects.create(nome="Empresa Teste Resync")
        self.usuario = Usuario.objects.create_user(username="tester_resync", password="x")
        self.fundo = Fundo.objects.create(
            empresa=self.empresa, nome="Fundo Teste Resync",
            cnpj="11.111.111/0001-11", tipo_fundo="FIDC",
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa,
            tipo_periodo="anual", ano=2026, data_vencimento=date(2027, 3, 31),
        )
        criar_checklist_para_periodo(self.periodo)
        self._docs_to_clean = []

    def tearDown(self):
        # Limpa os arquivos físicos gravados em DOCUMENTOS_ROOT durante o teste —
        # o rollback do TestCase desfaz as linhas do banco, mas não os arquivos.
        for doc in self._docs_to_clean:
            if doc.arquivo:
                doc.arquivo.delete(save=False)

    def _anexar_documento(self, item):
        conteudo = b"conteudo de teste para o teste de resync"
        doc = DocumentoDF.objects.create(
            empresa=self.empresa,
            periodo_df=self.periodo,
            checklist_item=item,
            checklist_texto=item.texto,
            arquivo=ContentFile(conteudo, name="teste.pdf"),
            nome_original="teste.pdf",
            tamanho_bytes=len(conteudo),
            enviado_por=self.usuario,
        )
        self._docs_to_clean.append(doc)
        return doc

    def test_resync_preserva_documento_sem_item_correspondente(self):
        """Texto do item some no novo tipo: o documento sobrevive, mas fica avulso (checklist_item=NULL) — nunca é apagado."""
        textos_fif = {t[1] for t in SEED["FIF"]}
        item = self.periodo.checklist_items.exclude(texto__in=textos_fif).first()
        self.assertIsNotNone(
            item, "Precisa de ao menos um item exclusivo do FIDC (fora do FIF) para este teste fazer sentido."
        )
        doc = self._anexar_documento(item)

        self.fundo.tipo_fundo = "FIF"
        self.fundo.save(update_fields=["tipo_fundo"])
        ressincronizar_checklist_por_tipo(self.fundo)

        doc.refresh_from_db()
        self.assertTrue(DocumentoDF.objects.filter(pk=doc.pk).exists(), "O documento não pode ser apagado pelo resync.")
        self.assertIsNone(doc.checklist_item_id, "Sem item correspondente no novo tipo, deve ficar avulso (NULL), não vinculado a um item errado.")
        self.assertEqual(doc.checklist_texto, item.texto, "O snapshot de texto precisa sobreviver para permitir re-vínculo futuro.")

    def test_resync_revincula_documento_por_texto_comum(self):
        """Texto do item existe também no novo tipo: o documento volta vinculado ao item recriado (não ao antigo, que foi apagado)."""
        textos_fidc = {t[1] for t in SEED["FIDC"]}
        textos_fif = {t[1] for t in SEED["FIF"]}
        texto_comum = next(iter(textos_fidc & textos_fif), None)
        self.assertIsNotNone(texto_comum, "Seed do FIDC e do FIF precisa ter ao menos um texto em comum para este teste.")

        item = self.periodo.checklist_items.get(texto=texto_comum)
        item_antigo_id = item.id
        doc = self._anexar_documento(item)

        self.fundo.tipo_fundo = "FIF"
        self.fundo.save(update_fields=["tipo_fundo"])
        ressincronizar_checklist_por_tipo(self.fundo)

        doc.refresh_from_db()
        self.assertFalse(ChecklistItemPeriodo.objects.filter(pk=item_antigo_id).exists(), "Precondição: o item antigo precisa ter sido de fato apagado pelo resync.")
        self.assertIsNotNone(doc.checklist_item_id, "Documento deveria ter voltado vinculado ao item recriado com o mesmo texto.")
        self.assertNotEqual(doc.checklist_item_id, item_antigo_id, "O vínculo precisa apontar para o item NOVO, não para o pk do item apagado.")
        self.assertEqual(doc.checklist_item.texto, texto_comum)

    def test_resync_nao_mexe_em_periodo_finalizado(self):
        """Período finalizado é preservado pelo resync — o vínculo do documento não é tocado."""
        self.periodo.status = "finalizada"
        self.periodo.save(update_fields=["status"])
        item = self.periodo.checklist_items.first()
        doc = self._anexar_documento(item)

        self.fundo.tipo_fundo = "FIF"
        self.fundo.save(update_fields=["tipo_fundo"])
        ressincronizar_checklist_por_tipo(self.fundo)

        doc.refresh_from_db()
        self.assertEqual(doc.checklist_item_id, item.id)
