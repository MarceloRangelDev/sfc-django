from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.modulo.models import Segmento

SENHA = "SenhaForte123!"


def usuario_com_acesso(username, cliente):
    grupo, _ = Group.objects.get_or_create(name="Leitor de clientes")
    grupo.permissions.set(Permission.objects.filter(codename="view_cliente", content_type__app_label="cliente"))
    novo = User.objects.create_user(username=username, email=f"{username}@teste.local", password=SENHA)
    novo.profile.cliente = cliente
    novo.profile.save()
    novo.groups.add(grupo)
    return novo


class ClienteListagemTests(TestCase):
    def setUp(self):
        produto = Segmento.objects.get(slug="produto")
        servico = Segmento.objects.get(slug="servico")
        self.matriz = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Alfa Comércio", nome_fantasia="Alfa Mix", segmento=produto,
        )
        self.filial = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="11444777000161", nome_razao_social="Beta Filial", tipo_estabelecimento="filial",
            matriz=self.matriz, segmento=servico, ativo=False,
        )
        self.pessoa = Cliente.objects.create(
            tipo_pessoa="PF", cpf_cnpj="52998224725", nome_razao_social="Carlos Pessoa", segmento=produto,
        )
        # Outra empresa (outro tenant): nunca pode aparecer para o usuário da matriz.
        self.estranho = Cliente.objects.create(
            tipo_pessoa="PJ", cpf_cnpj="04252011000110", nome_razao_social="Alfa Estranho", segmento=produto,
        )
        self.usuario = usuario_com_acesso("leitor", self.matriz)
        self.client.login(username="leitor", password=SENHA)

    def nomes(self, **params):
        resposta = self.client.get(reverse("cliente-list"), params)
        self.assertEqual(resposta.status_code, 200)
        return [c.nome_razao_social for c in resposta.context["clientes"]]

    def test_sem_filtro_mostra_so_a_estrutura_do_usuario(self):
        self.assertEqual(self.nomes(), ["Alfa Comércio", "Beta Filial"])

    def test_busca_por_nome_fantasia_e_documento_com_mascara_nao_vaza_outro_tenant(self):
        self.assertEqual(self.nomes(q="alfa"), ["Alfa Comércio"])  # "Alfa Estranho" é de outro tenant
        self.assertEqual(self.nomes(q="Alfa Mix"), ["Alfa Comércio"])
        self.assertEqual(self.nomes(q="11.444.777/0001-61"), ["Beta Filial"])
        self.assertEqual(self.nomes(q="04.252.011/0001-10"), [])

    def test_texto_com_numero_nao_vira_busca_por_documento(self):
        self.assertEqual(self.nomes(q="Loja 1"), [])

    def test_filtros_combinados(self):
        self.assertEqual(self.nomes(tipo_estabelecimento="filial"), ["Beta Filial"])
        self.assertEqual(self.nomes(status="inativo"), ["Beta Filial"])
        self.assertEqual(self.nomes(status="ativo"), ["Alfa Comércio"])
        self.assertEqual(self.nomes(tipo_pessoa="PJ", segmento=Segmento.objects.get(slug="servico").pk), ["Beta Filial"])
        self.assertEqual(self.nomes(tipo_pessoa="PJ", status="inativo", q="alfa"), [])

    def test_valores_invalidos_sao_ignorados(self):
        self.assertEqual(self.nomes(tipo_pessoa="XX", segmento="abc", status="?", per_page="9999"), ["Alfa Comércio", "Beta Filial"])

    def test_segmento_de_outro_tenant_nao_e_oferecido_no_filtro(self):
        servico = Segmento.objects.get(slug="servico")
        resposta = self.client.get(reverse("cliente-list"))
        self.assertIn(servico, list(resposta.context["segmentos"]))  # usado pela filial
        Cliente.objects.filter(pk=self.filial.pk).update(segmento=Segmento.objects.get(slug="produto"))
        Cliente.objects.filter(pk=self.estranho.pk).update(segmento=servico)
        resposta = self.client.get(reverse("cliente-list"))
        self.assertNotIn(servico, list(resposta.context["segmentos"]))

    def test_paginacao_total_e_pagina_fora_do_intervalo(self):
        # 25 filiais + matriz + a filial do setUp = 27 clientes na estrutura
        Cliente.objects.bulk_create([
            Cliente(
                tipo_pessoa="PJ", cpf_cnpj=f"{i + 100:014d}", nome_razao_social=f"Filial {i:02d}",
                tipo_estabelecimento="filial", matriz=self.matriz,
            )
            for i in range(25)
        ])
        resposta = self.client.get(reverse("cliente-list"), {"per_page": 10})
        self.assertEqual(resposta.context["page_obj"].paginator.count, 27)
        self.assertEqual(resposta.context["page_obj"].paginator.num_pages, 3)
        self.assertContains(resposta, "Mostrando 1–10 de 27 registros")
        # página além do fim (ex.: o filtro encolheu o total) cai na última página
        resposta = self.client.get(reverse("cliente-list"), {"per_page": 10, "page": 99})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["page_obj"].number, 3)
        resposta = self.client.get(reverse("cliente-list"), {"page": "lixo"})
        self.assertEqual(resposta.context["page_obj"].number, 1)

    def test_paginas_preservam_os_filtros(self):
        Cliente.objects.bulk_create([
            Cliente(
                tipo_pessoa="PJ", cpf_cnpj=f"{i + 100:014d}", nome_razao_social=f"Filial {i:02d}",
                tipo_estabelecimento="filial", matriz=self.matriz,
            )
            for i in range(12)
        ])
        resposta = self.client.get(reverse("cliente-list"), {"per_page": 10, "tipo_estabelecimento": "filial"})
        self.assertContains(resposta, "per_page=10")
        self.assertContains(resposta, "tipo_estabelecimento=filial")
        self.assertContains(resposta, "page=2")

    def test_lista_vazia_com_filtro_mostra_mensagem_propria(self):
        resposta = self.client.get(reverse("cliente-list"), {"q": "inexistente"})
        self.assertContains(resposta, "Nenhum cliente encontrado para os filtros informados.")
        self.assertNotContains(resposta, "Mostrando")

    def test_administrador_global_ve_todos_os_tenants(self):
        admin = User.objects.create_superuser(username="root", email="root@teste.local", password=SENHA)
        self.client.force_login(admin)
        self.assertIn("Alfa Estranho", self.nomes(q="alfa"))

    def test_sem_permissao_continua_negado(self):
        sem = User.objects.create_user(username="sem", email="sem@teste.local", password=SENHA)
        sem.profile.cliente = self.matriz
        sem.profile.save()
        self.client.force_login(sem)
        self.assertEqual(self.client.get(reverse("cliente-list")).status_code, 403)
