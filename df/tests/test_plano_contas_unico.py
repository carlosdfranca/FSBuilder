from django.core.exceptions import ValidationError
from django.test import TestCase

from df.management.commands.importar_plano_contas import natureza_compativel, validar_arvore
from df.models import Conta


def c(codigo, tipo, nivel, natureza, superior=None):
    return {
        "codigo": codigo,
        "tipo": tipo,
        "nivel": nivel,
        "natureza": natureza,
        "superior": superior,
        "_linha": f"T:{codigo}",
    }


class ValidarArvoreTests(TestCase):
    def arvore_ok(self):
        return [
            c("1", "S", 1, 1),
            c("1.01", "S", 2, 1, "1"),
            c("1.01.01", "A", 3, 1, "1.01"),
            c("2", "S", 1, 2),
            c("2.03", "S", 2, 3, "2"),
            c("2.03.01", "A", 3, 3, "2.03"),
        ]

    def test_arvore_valida_incluindo_pl_sob_passivo(self):
        self.assertEqual(validar_arvore(self.arvore_ok()), [])

    def test_duplicado(self):
        contas = self.arvore_ok() + [c("1.01.01", "A", 3, 1, "1.01")]
        self.assertTrue(any("duplicado" in e for e in validar_arvore(contas)))

    def test_pai_inexistente(self):
        contas = self.arvore_ok() + [c("1.09.01", "A", 3, 1, "1.09")]
        self.assertTrue(any("inexistente" in e for e in validar_arvore(contas)))

    def test_analitica_com_filha(self):
        contas = self.arvore_ok() + [c("1.01.01.01", "A", 4, 1, "1.01.01")]
        self.assertTrue(any("analítica com filhas" in e for e in validar_arvore(contas)))

    def test_sintetica_sem_filha(self):
        contas = self.arvore_ok() + [c("1.02", "S", 2, 1, "1")]
        self.assertTrue(any("sintética sem filhas" in e for e in validar_arvore(contas)))

    def test_nivel_errado(self):
        contas = self.arvore_ok()
        contas[2]["nivel"] = 5
        self.assertTrue(any("nível" in e for e in validar_arvore(contas)))

    def test_natureza(self):
        self.assertTrue(natureza_compativel(1, 1))
        self.assertTrue(natureza_compativel(2, 3))
        self.assertFalse(natureza_compativel(1, 2))
        self.assertFalse(natureza_compativel(3, 2))


class ContaModelTests(TestCase):
    def setUp(self):
        self.pl = Conta.objects.create(codigo="2", descricao="PASSIVO", tipo="S", nivel=1, natureza=2)
        self.pl_s = Conta.objects.create(
            codigo="2.03", descricao="PL", tipo="S", nivel=2, natureza=3, conta_superior=self.pl
        )
        self.lucros = Conta.objects.create(
            codigo="2.03.01", descricao="Lucros", tipo="A", nivel=3, natureza=3, conta_superior=self.pl_s
        )

    def test_clean_aceita_pl_sob_passivo(self):
        self.lucros.clean()

    def test_clean_recusa_filha_de_analitica(self):
        filha = Conta(codigo="2.03.01.01", descricao="x", tipo="A", nivel=4, natureza=3, conta_superior=self.lucros)
        with self.assertRaises(ValidationError):
            filha.clean()

    def test_papel_unico(self):
        self.lucros.papel = Conta.Papel.LUCROS_ACUMULADOS
        self.lucros.save()
        outra = Conta.objects.create(
            codigo="2.03.02", descricao="Outra", tipo="A", nivel=3, natureza=3, conta_superior=self.pl_s
        )
        outra.papel = Conta.Papel.LUCROS_ACUMULADOS
        with self.assertRaises(ValidationError):
            outra.clean()

    def test_papel_exige_pl_analitica(self):
        self.pl_s.papel = Conta.Papel.LUCROS_ACUMULADOS
        with self.assertRaises(ValidationError):
            self.pl_s.clean()
