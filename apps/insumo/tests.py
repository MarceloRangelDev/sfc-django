from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.medida.models import Medida
from apps.modulo.models import ClienteModulo, Modulo
from web_project.regras import RegraNegocioError

from .models import Insumo


def medida(sigla):
    return Medida.objects.get(sigla=sigla)


def novo_insumo(cliente, nome="Insumo", sigla="un", quantidade="1", preco_pago=100):
    return Insumo.objects.create(
        cliente=cliente, nome=nome, medida=medida(sigla), quantidade=Decimal(quantidade), preco_pago=preco_pago
    )


def criar_cliente_com_acesso_total(sufixo, cpf_cnpj):
    """Cria um Cliente com o módulo de Insumos habilitado e um usuário com
    todas as permissões de Insumo — pronto para testar o CRUD."""
    cliente = Cliente.objects.create(
        tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cpf_cnpj, nome_razao_social=f"Empresa {sufixo}"
    )
    modulo, _ = Modulo.objects.get_or_create(slug="insumo", defaults={"nome": "Insumos", "ativo": True})
    ClienteModulo.objects.get_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": True})

    grupo, _ = Group.objects.get_or_create(name=f"Grupo Insumo {sufixo}")
    permissoes = Permission.objects.filter(content_type__app_label="insumo")
    grupo.permissions.set(permissoes)

    usuario = User.objects.create_user(
        username=f"usuario_{sufixo.lower()}",
        email=f"usuario_{sufixo.lower()}@teste.local",
        password="SenhaForte123!",
    )
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)
    return cliente, usuario


class InsumoCalculoTests(TestCase):
    """Exemplos do PROMPT_MESTRE (seções 20 e 40)."""

    def setUp(self):
        self.cliente, _ = criar_cliente_com_acesso_total("A", "11222333000181")

    def test_100_gramas_por_4_reais_da_40_reais_o_kg(self):
        insumo = novo_insumo(self.cliente, "Queijo", "g", "100", 400)
        self.assertEqual(insumo.preco_pago, 400)
        self.assertEqual(insumo.preco_unidade, 4000)
        self.assertEqual(insumo.preco_unidade_formatado, "R$ 40,00")
        self.assertEqual(insumo.rotulo_preco, "PREÇO (por KG)")
        self.assertEqual(insumo.quantidade_por_extenso, "100 gramas")

    def test_500_ml_por_10_reais_da_20_reais_o_litro(self):
        insumo = novo_insumo(self.cliente, "Leite", "ml", "500", 1000)
        self.assertEqual(insumo.preco_unidade, 2000)
        self.assertEqual(insumo.rotulo_preco, "PREÇO (por L)")
        self.assertEqual(insumo.quantidade_por_extenso, "500 mililitros")

    def test_unidade(self):
        insumo = novo_insumo(self.cliente, "Ovo", "un", "12", 1200)
        self.assertEqual(insumo.preco_unidade, 100)
        self.assertEqual(insumo.rotulo_preco, "PREÇO (por UN)")

    def test_medida_caseira_converte_pelo_fator_do_banco(self):
        # 2 colheres de sopa = 30 ml; R$ 3,00 -> R$ 0,10/ml -> R$ 100,00/L
        insumo = novo_insumo(self.cliente, "Azeite", "c. sopa", "2", 300)
        self.assertEqual(insumo.preco_unidade, 10000)

    def test_preco_unidade_e_recalculado_ao_editar(self):
        insumo = novo_insumo(self.cliente, "Queijo", "g", "100", 400)
        insumo.preco_pago = 800
        insumo.save()
        self.assertEqual(insumo.preco_unidade, 8000)

    def test_quantidade_zero_nao_calcula(self):
        insumo = Insumo(cliente=self.cliente, nome="Zero", medida=medida("g"), quantidade=Decimal("0"), preco_pago=100)
        self.assertIsNone(insumo.calcular_preco_unidade())

    def test_preco_unidade_gigante_e_recusado(self):
        insumo = Insumo(cliente=self.cliente, nome="X", medida=medida("g"), quantidade=Decimal("0.0001"), preco_pago=10**15)
        with self.assertRaises(RegraNegocioError):
            insumo.calcular_preco_unidade()


class InsumoValidacaoTests(TestCase):
    """Validação de valores — nunca confiar no que vem do formulário."""

    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("A", "11222333000181")
        self.client.login(username="usuario_a", password="SenhaForte123!")

    def _payload(self, **overrides):
        dados = {
            "nome": "Farinha de Trigo",
            "medida": medida("kg").pk,
            "quantidade": "10",
            "preco_pago": "12,90",
            "ativo": "on",
        }
        dados.update(overrides)
        return dados

    def _rejeita(self, **overrides):
        resposta = self.client.post(reverse("insumo-create"), self._payload(**overrides))
        self.assertEqual(resposta.status_code, 200)  # re-renderiza o form com erro
        self.assertFalse(Insumo.objects.filter(nome="Farinha de Trigo").exists())

    def test_valor_negativo_e_rejeitado(self):
        self._rejeita(preco_pago="-10")

    def test_valor_nao_numerico_e_rejeitado(self):
        self._rejeita(preco_pago="abc")

    def test_valor_infinity_e_rejeitado(self):
        self._rejeita(preco_pago="Infinity")

    def test_quantidade_nao_numerica_e_rejeitada(self):
        self._rejeita(quantidade="abc")

    def test_quantidade_nan_e_rejeitada(self):
        self._rejeita(quantidade="NaN")

    def test_quantidade_zero_ou_negativa_e_rejeitada(self):
        self._rejeita(quantidade="0")
        self._rejeita(quantidade="-1")

    def test_medida_inexistente_ou_inativa_e_rejeitada(self):
        self._rejeita(medida=99999)
        Medida.objects.filter(sigla="kg").update(ativo=False)
        self._rejeita(medida=medida("kg").pk)

    def test_categoria_nao_e_aceita_nem_exigida(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(categoria="123"))
        self.assertRedirects(resposta, reverse("insumo"))
        self.assertIsNone(Insumo.objects.get(nome="Farinha de Trigo").categoria_id)

    def test_valor_no_formato_brasileiro_e_gravado_em_centavos(self):
        resposta = self.client.post(reverse("insumo-create"), self._payload(preco_pago="1.234,56"))
        self.assertRedirects(resposta, reverse("insumo"))
        insumo = Insumo.objects.get(nome="Farinha de Trigo")
        self.assertEqual(insumo.preco_pago, 123456)
        self.assertIsInstance(insumo.preco_pago, int)

    def test_cadastro_completo_calcula_preco_por_kg(self):
        self.client.post(reverse("insumo-create"), self._payload(medida=medida("g").pk, quantidade="100", preco_pago="4,00"))
        insumo = Insumo.objects.get(nome="Farinha de Trigo")
        self.assertEqual((insumo.preco_pago, insumo.preco_unidade), (400, 4000))

    def test_nome_duplicado_no_mesmo_cliente_e_rejeitado(self):
        novo_insumo(self.cliente, "Farinha de Trigo")
        resposta = self.client.post(reverse("insumo-create"), self._payload())
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Insumo.objects.filter(nome="Farinha de Trigo").count(), 1)

    def test_edicao_mostra_valores_no_padrao_brasileiro(self):
        insumo = novo_insumo(self.cliente, "Queijo", "g", "100.5", 400)
        resposta = self.client.get(reverse("insumo-edit", args=[insumo.pk]))
        self.assertContains(resposta, 'value="100,5"')  # nunca '100.5000' (a máscara quebraria)
        self.assertContains(resposta, 'value="4,00"')

    def test_edicao_mantem_medida_inativa_ja_usada(self):
        insumo = novo_insumo(self.cliente, "Queijo", "g", "100", 400)
        Medida.objects.filter(sigla="g").update(ativo=False)
        resposta = self.client.get(reverse("insumo-edit", args=[insumo.pk]))
        self.assertIn(medida("g"), resposta.context["form"].fields["medida"].queryset)


class InsumoListagemTests(TestCase):
    """Pesquisa, ordenação e paginação da listagem."""

    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("B", "11222333000181")
        self.client.login(username="usuario_b", password="SenhaForte123!")
        for i in range(3):
            novo_insumo(self.cliente, f"Insumo {i}", "un", "1", i * 100)

    def test_pesquisa_filtra_por_nome(self):
        resposta = self.client.get(reverse("insumo"), {"q": "Insumo 1"})
        insumos = list(resposta.context["insumos"])
        self.assertEqual([i.nome for i in insumos], ["Insumo 1"])

    def test_ordenacao_por_preco_desc(self):
        resposta = self.client.get(reverse("insumo"), {"sort": "preco", "dir": "desc"})
        self.assertEqual([i.nome for i in resposta.context["insumos"]], ["Insumo 2", "Insumo 1", "Insumo 0"])

    def test_ordenacao_por_preco_unidade(self):
        resposta = self.client.get(reverse("insumo"), {"sort": "preco_unidade", "dir": "desc"})
        self.assertEqual(resposta.context["insumos"][0].nome, "Insumo 2")

    def test_coluna_de_ordenacao_invalida_nao_quebra_e_usa_padrao(self):
        self.assertEqual(self.client.get(reverse("insumo"), {"sort": "cliente__cpf_cnpj"}).status_code, 200)

    def test_listagem_nao_faz_consulta_por_linha(self):
        # select_related("medida"): o número de queries não cresce com as linhas.
        with self.assertNumQueries(self._consultas_base()):
            self.client.get(reverse("insumo"))
        for i in range(10, 15):
            novo_insumo(self.cliente, f"Extra {i}")
        with self.assertNumQueries(self._consultas_base()):
            self.client.get(reverse("insumo"))

    def _consultas_base(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as contexto:
            self.client.get(reverse("insumo"))
        return len(contexto)

    def test_paginacao_respeita_per_page(self):
        for i in range(3, 12):
            novo_insumo(self.cliente, f"Insumo {i}")
        resposta = self.client.get(reverse("insumo"), {"per_page": "10"})
        self.assertEqual(len(resposta.context["insumos"]), 10)
        self.assertTrue(resposta.context["page_obj"].has_next())

    def test_per_page_fora_da_whitelist_usa_padrao(self):
        self.assertEqual(self.client.get(reverse("insumo"), {"per_page": "999999"}).context["per_page"], 25)

    def test_exportacao_excel_retorna_arquivo(self):
        resposta = self.client.get(reverse("insumo"), {"export": "xlsx"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_exportacao_pdf_retorna_arquivo(self):
        resposta = self.client.get(reverse("insumo"), {"export": "pdf"})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/pdf")


class InsumoExclusaoLogicaTests(TestCase):
    def setUp(self):
        self.cliente, self.usuario = criar_cliente_com_acesso_total("C", "11222333000181")
        self.client.login(username="usuario_c", password="SenhaForte123!")
        self.insumo = novo_insumo(self.cliente, "Sal", "kg", "1", 200)

    def test_excluir_marca_como_inativo_sem_apagar(self):
        self.client.post(reverse("insumo-excluir", args=[self.insumo.pk]))
        self.insumo.refresh_from_db()
        self.assertFalse(self.insumo.ativo)
        self.assertEqual(self.insumo.preco_unidade, 200)  # soft delete não mexe no preço

    def test_insumo_excluido_nao_aparece_na_listagem(self):
        self.client.post(reverse("insumo-excluir", args=[self.insumo.pk]))
        resposta = self.client.get(reverse("insumo"))
        self.assertNotIn(self.insumo, list(resposta.context["insumos"]))


class MedidaEmUsoTests(TestCase):
    """Regras da tabela medidas que dependem de haver insumos usando-a."""

    def setUp(self):
        self.cliente, _ = criar_cliente_com_acesso_total("D", "11222333000181")
        novo_insumo(self.cliente, "Queijo", "g", "100", 400)
        grupo = Group.objects.create(name="Admin medidas")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="medida"))
        admin = User.objects.create_user(username="admin_m", email="admin_m@teste.local", password="SenhaForte123!")
        admin.groups.add(grupo)
        self.client.login(username="admin_m", password="SenhaForte123!")

    def test_medida_em_uso_e_detectada(self):
        self.assertTrue(medida("g").em_uso())
        self.assertFalse(medida("kg").em_uso())

    def test_excluir_medida_em_uso_apenas_inativa(self):
        self.client.post(reverse("medida-excluir", args=[medida("g").pk]))
        usada = Medida.objects.get(sigla="g")
        self.assertFalse(usada.ativo)
        self.assertEqual(Insumo.objects.get(nome="Queijo").preco_unidade, 4000)  # nada se perdeu

    def test_medida_em_uso_nao_pode_mudar_fator_nem_dimensao(self):
        g = medida("g")
        dados = {
            "medida": g.medida, "sigla": g.sigla, "descricao": "", "equivale": "", "dimensao": "massa",
            "fator_base": "2", "nome_singular": "grama", "nome_plural": "gramas", "ativo": "on",
        }
        resposta = self.client.post(reverse("medida-edit", args=[g.pk]), dados)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("fator_base", resposta.context["form"].errors)
        g.refresh_from_db()
        self.assertEqual(g.fator_base, Decimal("1"))

    def test_medida_em_uso_permite_editar_outros_campos(self):
        g = medida("g")
        dados = {
            "medida": g.medida, "sigla": g.sigla, "descricao": "ajustada", "equivale": "", "dimensao": "massa",
            "fator_base": "1", "nome_singular": "grama", "nome_plural": "gramas", "ativo": "on",
        }
        self.assertRedirects(self.client.post(reverse("medida-edit", args=[g.pk]), dados), reverse("medida-list"))

    def test_calculo_de_previa(self):
        resposta = self.client.get(
            reverse("medida-calcular"), {"medida": medida("g").pk, "quantidade": "100", "preco": "4,00"}
        ).json()
        self.assertEqual(resposta["quantidade_extenso"], "100 gramas")
        self.assertEqual(resposta["rotulo_preco"], "PREÇO (por KG)")
        self.assertEqual(resposta["preco_unidade"], {"centavos": 4000, "decimal": "40.00", "formatado": "R$ 40,00"})

    def test_calculo_de_previa_com_entrada_incompleta_nao_quebra(self):
        for params in [{}, {"medida": "abc"}, {"medida": medida("g").pk}, {"medida": medida("g").pk, "quantidade": "x"}]:
            self.assertEqual(self.client.get(reverse("medida-calcular"), params).status_code, 200)

    def test_calculo_de_previa_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("medida-calcular")).status_code, 302)


class InsumoSegurancaMultiTenantTests(TestCase):
    """Um usuário do Cliente B nunca deve ver, editar ou excluir um
    registro do Cliente A (proteção contra IDOR)."""

    def setUp(self):
        self.cliente_a, self.usuario_a = criar_cliente_com_acesso_total("A", "11222333000181")
        self.cliente_b, self.usuario_b = criar_cliente_com_acesso_total("B", "11444777000161")
        self.insumo_a = novo_insumo(self.cliente_a, "Insumo do Cliente A")
        self.client.login(username="usuario_b", password="SenhaForte123!")

    def test_insumo_de_outro_cliente_nao_aparece_na_listagem(self):
        resposta = self.client.get(reverse("insumo"))
        self.assertNotIn(self.insumo_a, list(resposta.context["insumos"]))

    def test_editar_insumo_de_outro_cliente_retorna_404(self):
        self.assertEqual(self.client.get(reverse("insumo-edit", args=[self.insumo_a.pk])).status_code, 404)

    def test_excluir_insumo_de_outro_cliente_retorna_404_e_nao_altera_nada(self):
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.insumo_a.pk])).status_code, 404)
        self.insumo_a.refresh_from_db()
        self.assertTrue(self.insumo_a.ativo)

    def test_post_de_edicao_nao_transfere_insumo_para_outro_cliente(self):
        insumo_b = novo_insumo(self.cliente_b, "Insumo do Cliente B")
        self.client.post(
            reverse("insumo-edit", args=[insumo_b.pk]),
            {"nome": "Insumo do Cliente B", "medida": medida("un").pk, "quantidade": "1", "preco_pago": "1,00",
             "cliente": self.cliente_a.pk, "ativo": "on"},
        )
        insumo_b.refresh_from_db()
        self.assertEqual(insumo_b.cliente_id, self.cliente_b.pk)  # mass assignment ignorado


class InsumoPermissaoTests(TestCase):
    """O acesso ao módulo já é coberto por apps/modulo/tests.py — aqui
    testamos que a ação específica (excluir) exige a permissão certa."""

    def setUp(self):
        self.cliente = Cliente.objects.create(
            tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj="11222333000181", nome_razao_social="Empresa Sem Permissao"
        )
        modulo, _ = Modulo.objects.get_or_create(slug="insumo", defaults={"nome": "Insumos", "ativo": True})
        ClienteModulo.objects.create(cliente=self.cliente, modulo=modulo, ativo=True)

        # Grupo só com permissão de VISUALIZAR — não pode excluir.
        grupo = Group.objects.create(name="Somente Visualizar")
        grupo.permissions.add(Permission.objects.get(content_type__app_label="insumo", codename="view_insumo"))

        self.usuario = User.objects.create_user(
            username="visualizador", email="visualizador@teste.local", password="SenhaForte123!"
        )
        self.usuario.profile.cliente = self.cliente
        self.usuario.profile.save()
        self.usuario.groups.add(grupo)

        self.insumo = novo_insumo(self.cliente, "Insumo")
        self.client.login(username="visualizador", password="SenhaForte123!")

    def test_usuario_sem_permissao_de_excluir_recebe_403(self):
        self.assertEqual(self.client.post(reverse("insumo-excluir", args=[self.insumo.pk])).status_code, 403)
        self.insumo.refresh_from_db()
        self.assertTrue(self.insumo.ativo)

    def test_usuario_sem_permissao_de_criar_recebe_403(self):
        self.assertEqual(self.client.get(reverse("insumo-create")).status_code, 403)
