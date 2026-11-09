"""Testes da página Saldos da DF: lançamento manual (atual e anterior), permissões e isolamento
entre empresas, importação com Mesclar/Substituir e a regressão da FK `periodo_df` do saldo anterior."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.processing.dpf_service import gerar_dados_dpf
from core.processing.import_service import import_balancete
from core.upload.balancete_parser import BalanceteRowDTO
from df.models import (
    BalanceteItem, BalanceteLinhaPendente, Fundo, GrupoGrande, GrupoPequeno, MapeamentoContas, PeriodoDF,
)
from usuarios.models import Empresa, Membership, Usuario


def _usuario(username, empresa, role):
    u = Usuario.objects.create_user(username=username, password="x")
    Membership.objects.create(usuario=u, empresa=empresa, role=role, is_active=True)
    return u


def _linha(conta, saldo):
    return BalanceteRowDTO(conta=conta, saldo_atual=saldo, saldo_anterior=None, raw={"CONTA": conta})


class SaldosBase(TestCase):

    def setUp(self):
        self.empresa_a = Empresa.objects.create(nome="Empresa A")
        self.empresa_b = Empresa.objects.create(nome="Empresa B")
        self.grupao = GrupoGrande.objects.create(nome="Disponibilidades", tipo=1, ordem=1)
        self.grupinho = GrupoPequeno.objects.create(nome="Bancos", grupao=self.grupao)
        self.fundo = Fundo.objects.create(
            empresa=self.empresa_a, nome="Fundo A", cnpj="11.111.111/0001-11", tipo_fundo="FIDC",
        )
        self.periodo_2024 = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa_a, tipo_periodo="anual", ano=2024,
            data_vencimento=date(2025, 4, 30), data_referencia=date(2024, 12, 31),
        )
        self.periodo = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa_a, tipo_periodo="anual", ano=2025,
            data_vencimento=date(2026, 4, 30),
        )
        self.conta = MapeamentoContas.objects.create(
            empresa=self.empresa_a, conta="1.1.01", descricao="Caixa", grupo_pequeno=self.grupinho,
        )
        self.conta_sem_grupo = MapeamentoContas.objects.create(empresa=self.empresa_a, conta="9.9.99")
        self.conta_b = MapeamentoContas.objects.create(
            empresa=self.empresa_b, conta="1.1.01", grupo_pequeno=self.grupinho,
        )
        self.master = _usuario("master_sx", self.empresa_a, Membership.Role.MASTER)
        self.member = _usuario("member_sx", self.empresa_a, Membership.Role.MEMBER)
        self.viewer = _usuario("viewer_sx", self.empresa_a, Membership.Role.VIEWER)
        self.outsider = _usuario("outsider_sx", self.empresa_b, Membership.Role.MASTER)

    def _salvar(self, mapeamento=None, coluna="atual", valor="1500000,50", periodo=None):
        periodo = periodo or self.periodo
        return self.client.post(
            reverse("salvar_saldo", args=[periodo.id]),
            {"mapeamento": (mapeamento or self.conta).id, "coluna": coluna, "saldo_final": valor},
        )

    def _item(self, data, conta=None):
        return BalanceteItem.objects.get(
            fundo=self.fundo, conta_corrente=conta or self.conta, data_referencia=data,
        )


class SaldosPermissaoTests(SaldosBase):

    def test_todos_os_roles_da_empresa_veem_a_pagina(self):
        url = reverse("saldos_periodo", args=[self.periodo.id])
        for u in (self.master, self.member, self.viewer):
            self.client.force_login(u)
            self.assertEqual(self.client.get(url).status_code, 200, u.username)

    def test_viewer_nao_grava_nem_exclui(self):
        BalanceteItem.objects.create(
            fundo=self.fundo, conta_corrente=self.conta, data_referencia=date(2025, 12, 31),
            saldo_final=Decimal("10"),
        )
        item = self._item(date(2025, 12, 31))
        self.client.force_login(self.viewer)
        self.assertEqual(self._salvar().status_code, 403)
        r = self.client.post(reverse("excluir_saldo", args=[self.periodo.id, item.id]))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(BalanceteItem.objects.count(), 1)
        self.assertEqual(self._item(date(2025, 12, 31)).saldo_final, Decimal("10.00"))

    def test_get_nas_apis_de_escrita_e_proibido(self):
        self.client.force_login(self.master)
        self.assertEqual(self.client.get(reverse("salvar_saldo", args=[self.periodo.id])).status_code, 405)

    def test_outra_empresa_recebe_404(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("saldos_periodo", args=[self.periodo.id])).status_code, 404)
        self.assertEqual(self._salvar().status_code, 404)

    def test_anonimo_vai_para_login(self):
        r = self.client.get(reverse("saldos_periodo", args=[self.periodo.id]))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)


class SaldosCrudTests(SaldosBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.master)

    def test_cria_saldo_atual_manual(self):
        r = self._salvar()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["linha"]["atual"]["valor"], "1500000.50")
        self.assertEqual(body["kpis"]["ativo"], "1500000.50")

        item = self._item(date(2025, 12, 31))
        self.assertEqual(item.origem, "manual")
        self.assertEqual(item.atualizado_por, self.master)
        self.assertEqual(item.periodo_df_id, self.periodo.id)
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.data_referencia, date(2025, 12, 31))
        self.assertEqual(self.periodo.status, "em_andamento")

    def test_saldo_manual_aparece_na_dpf(self):
        self._salvar(valor="1500000")
        _, totais = gerar_dados_dpf(self.fundo.id, date(2025, 12, 31), None, zerar_anterior=True)
        self.assertTrue(any(totais.values()), totais)

    def test_aceita_formato_brasileiro_e_negativo(self):
        self._salvar(valor="-1.234,56")
        self.assertEqual(self._item(date(2025, 12, 31)).saldo_final, Decimal("-1234.56"))

    def test_valor_invalido_e_rejeitado(self):
        r = self._salvar(valor="abc")
        self.assertEqual(r.status_code, 400)
        self.assertIn("saldo_final", r.json()["errors"])
        self.assertEqual(BalanceteItem.objects.count(), 0)

    def test_saldo_anterior_grava_na_data_anterior_e_aponta_para_o_periodo_dono(self):
        r = self._salvar(coluna="anterior", valor="200")
        self.assertEqual(r.status_code, 200, r.content)
        item = self._item(date(2024, 12, 31))
        self.assertEqual(item.periodo_df_id, self.periodo_2024.id)
        self.assertEqual(item.origem, "manual")
        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.data_anterior, date(2024, 12, 31))

    def test_transitoria_sem_data_anterior_da_400(self):
        trans = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa_a, tipo_periodo="transitoria", ano=2025,
            data_vencimento=date(2025, 6, 30),
        )
        r = self._salvar(coluna="anterior", periodo=trans)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(BalanceteItem.objects.count(), 0)
        # o saldo atual da transitória usa a própria data de vencimento
        self.assertEqual(self._salvar(periodo=trans).status_code, 200)
        self.assertTrue(BalanceteItem.objects.filter(data_referencia=date(2025, 6, 30)).exists())

    def test_edita_valor_existente_sem_duplicar(self):
        self._salvar(valor="100")
        self._salvar(valor="250")
        self.assertEqual(BalanceteItem.objects.count(), 1)
        self.assertEqual(self._item(date(2025, 12, 31)).saldo_final, Decimal("250.00"))

    def test_exclui_saldo(self):
        self._salvar(valor="100")
        item = self._item(date(2025, 12, 31))
        r = self.client.post(reverse("excluir_saldo", args=[self.periodo.id, item.id]))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()["linha"])
        self.assertEqual(BalanceteItem.objects.count(), 0)
        self.periodo.refresh_from_db()
        # sem dados o período volta a nao_iniciada/vencida, conforme o vencimento (depende de "hoje")
        self.assertNotEqual(self.periodo.status, "em_andamento")

    def test_exclusao_de_item_de_outra_data_ou_fundo_da_404(self):
        outro_fundo = Fundo.objects.create(
            empresa=self.empresa_a, nome="Fundo B", cnpj="22.222.222/0001-22", tipo_fundo="FIDC",
        )
        de_outro_fundo = BalanceteItem.objects.create(
            fundo=outro_fundo, conta_corrente=self.conta, data_referencia=date(2025, 12, 31),
            saldo_final=Decimal("1"),
        )
        de_outra_data = BalanceteItem.objects.create(
            fundo=self.fundo, conta_corrente=self.conta, data_referencia=date(2020, 12, 31),
            saldo_final=Decimal("1"),
        )
        for item in (de_outro_fundo, de_outra_data):
            r = self.client.post(reverse("excluir_saldo", args=[self.periodo.id, item.id]))
            self.assertEqual(r.status_code, 404, item.id)
        self.assertEqual(BalanceteItem.objects.count(), 2)

    def test_conta_sem_grupo_ou_de_outra_empresa_e_rejeitada(self):
        for m in (self.conta_sem_grupo, self.conta_b):
            r = self._salvar(mapeamento=m)
            self.assertEqual(r.status_code, 400, m.conta)
            self.assertIn("mapeamento", r.json()["errors"])
        self.assertEqual(BalanceteItem.objects.count(), 0)

    def test_lancamento_manual_limpa_pendencia_da_mesma_conta_e_data(self):
        BalanceteLinhaPendente.objects.create(
            empresa=self.empresa_a, fundo=self.fundo, data_referencia=date(2025, 12, 31),
            conta="1.1.01", saldo_final=Decimal("5"),
        )
        BalanceteLinhaPendente.objects.create(
            empresa=self.empresa_a, fundo=self.fundo, data_referencia=date(2025, 12, 31),
            conta="7.7.77", saldo_final=Decimal("5"),
        )
        self._salvar()
        restantes = list(BalanceteLinhaPendente.objects.values_list("conta", flat=True))
        self.assertEqual(restantes, ["7.7.77"])

    def test_pagina_renderiza_com_dados(self):
        self._salvar(valor="100")
        r = self.client.get(reverse("saldos_periodo", args=[self.periodo.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "sx-linhas")
        self.assertEqual(len(r.context["linhas_json"]), 1)


class SaldosImportacaoTests(SaldosBase):

    def test_mesclar_mantem_linhas_manuais_de_contas_fora_do_arquivo(self):
        outra = MapeamentoContas.objects.create(
            empresa=self.empresa_a, conta="1.1.02", grupo_pequeno=self.grupinho,
        )
        self.client.force_login(self.master)
        self._salvar(mapeamento=outra, valor="77")

        import_balancete(
            fundo_id=self.fundo.id, data_referencia=date(2025, 12, 31),
            rows=[_linha("1.1.01", 10.0)], periodo_df_id=self.periodo.id, usuario=self.master,
        )
        self.assertEqual(self._item(date(2025, 12, 31), outra).saldo_final, Decimal("77.00"))
        importado = self._item(date(2025, 12, 31))
        self.assertEqual(importado.origem, "importado")
        self.assertEqual(importado.atualizado_por, self.master)

    def test_substituir_remove_manuais_e_pendentes_da_data(self):
        outra = MapeamentoContas.objects.create(
            empresa=self.empresa_a, conta="1.1.02", grupo_pequeno=self.grupinho,
        )
        self.client.force_login(self.master)
        self._salvar(mapeamento=outra, valor="77")
        BalanceteLinhaPendente.objects.create(
            empresa=self.empresa_a, fundo=self.fundo, data_referencia=date(2025, 12, 31),
            conta="8.8.88", saldo_final=Decimal("3"),
        )
        # saldo de outra data não pode ser tocado
        BalanceteItem.objects.create(
            fundo=self.fundo, conta_corrente=outra, data_referencia=date(2024, 12, 31),
            saldo_final=Decimal("5"),
        )

        import_balancete(
            fundo_id=self.fundo.id, data_referencia=date(2025, 12, 31),
            rows=[_linha("1.1.01", 10.0)], periodo_df_id=self.periodo.id, substituir=True,
        )
        datas_contas = set(
            BalanceteItem.objects.values_list("data_referencia", "conta_corrente__conta")
        )
        self.assertEqual(datas_contas, {(date(2025, 12, 31), "1.1.01"), (date(2024, 12, 31), "1.1.02")})
        self.assertFalse(BalanceteLinhaPendente.objects.exists())

    def test_importar_saldo_anterior_nao_zera_a_fk_do_periodo_do_ano_anterior(self):
        """Regressão: o modo 'planilha' chamava import_balancete sem periodo_df_id e gravava NULL."""
        BalanceteItem.objects.create(
            fundo=self.fundo, conta_corrente=self.conta, data_referencia=date(2024, 12, 31),
            saldo_final=Decimal("1"), periodo_df=self.periodo_2024,
        )
        import_balancete(
            fundo_id=self.fundo.id, data_referencia=date(2024, 12, 31), rows=[_linha("1.1.01", 99.0)],
        )
        item = self._item(date(2024, 12, 31))
        self.assertEqual(item.saldo_final, Decimal("99.00"))
        self.assertEqual(item.periodo_df_id, self.periodo_2024.id)

    def test_importar_saldo_anterior_sem_dono_preserva_fk_existente(self):
        sem_dono = PeriodoDF.objects.create(
            fundo=self.fundo, empresa=self.empresa_a, tipo_periodo="anual", ano=2023,
            data_vencimento=date(2024, 4, 30),
        )
        BalanceteItem.objects.create(
            fundo=self.fundo, conta_corrente=self.conta, data_referencia=date(2023, 12, 31),
            saldo_final=Decimal("1"), periodo_df=sem_dono,
        )
        import_balancete(
            fundo_id=self.fundo.id, data_referencia=date(2023, 12, 31), rows=[_linha("1.1.01", 5.0)],
        )
        self.assertEqual(self._item(date(2023, 12, 31)).periodo_df_id, sem_dono.id)

    def test_view_de_importacao_valida_antes_de_gravar(self):
        """Modo 'clone' sem período de origem deve falhar sem deixar o balancete principal gravado."""
        import io
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["CONTA", "SALDO ATUAL", "SALDO ANTERIOR"])
        ws.append(["1.1.01", 10, 0])
        buf = io.BytesIO()
        wb.save(buf)
        buf.name = "balancete.xlsx"
        buf.seek(0)

        self.client.force_login(self.master)
        r = self.client.post(
            reverse("importar_balancete"),
            {
                "fundo_id": self.fundo.id, "periodo_df_id": self.periodo.id,
                "saldo_anterior_mode": "clone", "modo_importacao": "mesclar",
                "arquivo_balancete": buf,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(BalanceteItem.objects.count(), 0)
