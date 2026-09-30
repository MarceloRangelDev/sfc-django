from django.test import TestCase

from .models import Cliente
from .validators import formatar_documento, normalizar_documento, validar_cnpj, validar_cpf


class ValidadorCpfTests(TestCase):
    def test_cpf_valido_com_mascara_e_aceito(self):
        self.assertTrue(validar_cpf("529.982.247-25"))

    def test_cpf_valido_sem_mascara_e_aceito(self):
        self.assertTrue(validar_cpf("52998224725"))

    def test_cpf_com_todos_digitos_iguais_e_rejeitado(self):
        self.assertFalse(validar_cpf("111.111.111-11"))

    def test_cpf_com_digito_verificador_errado_e_rejeitado(self):
        self.assertFalse(validar_cpf("529.982.247-00"))

    def test_cpf_com_tamanho_errado_e_rejeitado(self):
        self.assertFalse(validar_cpf("123"))


class ValidadorCnpjTests(TestCase):
    def test_cnpj_valido_com_mascara_e_aceito(self):
        self.assertTrue(validar_cnpj("11.222.333/0001-81"))

    def test_cnpj_valido_sem_mascara_e_aceito(self):
        self.assertTrue(validar_cnpj("11222333000181"))

    def test_cnpj_com_todos_digitos_iguais_e_rejeitado(self):
        self.assertFalse(validar_cnpj("11.111.111/1111-11"))

    def test_cnpj_com_digito_verificador_errado_e_rejeitado(self):
        self.assertFalse(validar_cnpj("11.222.333/0001-00"))


class NormalizacaoDocumentoTests(TestCase):
    def test_normalizar_remove_pontuacao(self):
        self.assertEqual(normalizar_documento("529.982.247-25"), "52998224725")

    def test_formatar_cpf(self):
        self.assertEqual(formatar_documento("52998224725", "PF"), "529.982.247-25")

    def test_formatar_cnpj(self):
        self.assertEqual(formatar_documento("11222333000181", "PJ"), "11.222.333/0001-81")


class ClienteModelTests(TestCase):
    def test_cliente_com_cpf_invalido_nao_e_salvo(self):
        cliente = Cliente(tipo_pessoa=Cliente.TIPO_PF, cpf_cnpj="111.111.111-11", nome_razao_social="Fulano")
        with self.assertRaises(Exception):
            cliente.full_clean()

    def test_cliente_com_cpf_valido_e_salvo_normalizado(self):
        cliente = Cliente(tipo_pessoa=Cliente.TIPO_PF, cpf_cnpj="529.982.247-25", nome_razao_social="Fulano")
        cliente.full_clean()
        cliente.save()
        self.assertEqual(cliente.cpf_cnpj, "52998224725")

    def test_documento_duplicado_e_rejeitado(self):
        Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa A")
        duplicado = Cliente(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11.222.333/0001-81", nome_razao_social="Empresa B")
        with self.assertRaises(Exception):
            duplicado.full_clean()

    def test_cnpj_com_mascara_completa_cabe_no_campo_antes_de_normalizar(self):
        # Regressão: "11.222.333/0001-81" tem 18 caracteres, mais que um
        # CNPJ só com dígitos (14). Como Django valida o max_length do
        # campo ANTES de chamar Cliente.clean() (onde a máscara é
        # removida), o max_length do model precisa comportar a entrada
        # ainda mascarada — ver o comentário em Cliente.cpf_cnpj.
        cliente = Cliente(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11.444.777/0001-61", nome_razao_social="Empresa C")
        cliente.full_clean()
        self.assertEqual(cliente.cpf_cnpj, "11444777000161")
