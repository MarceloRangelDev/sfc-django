from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente

from .models import ClienteModulo, Modulo


def criar_usuario_com_cliente(username, cliente):
    usuario = User.objects.create_user(username=username, email=f"{username}@teste.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    permissao = Permission.objects.get(content_type__app_label="insumo", codename="view_insumo")
    grupo, _ = Group.objects.get_or_create(name="Grupo Teste Insumo")
    grupo.permissions.add(permissao)
    usuario.groups.add(grupo)
    return usuario


class ModuloGateTests(TestCase):
    """O módulo precisa ser bloqueado no BACKEND (acesso direto à URL),
    não só escondido do menu — ver web_project/decorators.py::module_required."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa Teste"
        )
        self.modulo_insumo, _ = Modulo.objects.get_or_create(
            slug="insumo", defaults={"nome": "Insumos", "ativo": True}
        )
        self.usuario = criar_usuario_com_cliente("usuario_modulo", self.cliente)
        self.client.login(username="usuario_modulo", password="SenhaForte123!")

    def test_acesso_negado_quando_modulo_nao_habilitado_para_o_cliente(self):
        # Nenhum ClienteModulo foi criado ainda para este cliente: o módulo
        # não está habilitado por padrão (só quem já existia antes da Fase 3
        # ganha tudo habilitado automaticamente, via seed_modulos).
        resposta = self.client.get(reverse("insumo"))
        self.assertEqual(resposta.status_code, 403)

    def test_acesso_permitido_quando_modulo_habilitado(self):
        ClienteModulo.objects.create(cliente=self.cliente, modulo=self.modulo_insumo, ativo=True)
        resposta = self.client.get(reverse("insumo"))
        self.assertEqual(resposta.status_code, 200)

    def test_acesso_negado_quando_modulo_desabilitado_explicitamente(self):
        ClienteModulo.objects.create(cliente=self.cliente, modulo=self.modulo_insumo, ativo=False)
        resposta = self.client.get(reverse("insumo"))
        self.assertEqual(resposta.status_code, 403)

    def test_acesso_negado_quando_modulo_esta_globalmente_indisponivel(self):
        ClienteModulo.objects.create(cliente=self.cliente, modulo=self.modulo_insumo, ativo=True)
        self.modulo_insumo.ativo = False
        self.modulo_insumo.save()
        resposta = self.client.get(reverse("insumo"))
        self.assertEqual(resposta.status_code, 403)

    def test_reabilitar_modulo_restaura_o_acesso(self):
        vinculo = ClienteModulo.objects.create(cliente=self.cliente, modulo=self.modulo_insumo, ativo=False)
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 403)

        vinculo.ativo = True
        vinculo.save()
        self.assertEqual(self.client.get(reverse("insumo")).status_code, 200)
