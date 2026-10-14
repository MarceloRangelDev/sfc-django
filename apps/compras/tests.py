"""Lista de compras: fórmula de sugestão, inclusão idempotente, itens manuais/serviços, auditoria, tenant e API."""
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.estoque.services import definir_minimo, registrar_entrada
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .models import ItemCompra
from .services import adicionar_sugestoes, quantidade_sugerida, sugestoes_de_compra

D = Decimal
V1 = "/api/v1"


def novo_usuario(sufixo, cnpj, permissoes=None):
    cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=f"Empresa {sufixo}")
    for modulo in ("insumo", "estoque", "compras"):
        habilitar_modulo(cliente, modulo)
    grupo = Group.objects.create(name=f"G {sufixo}")
    consulta = Permission.objects.filter(content_type__app_label__in=["compras", "estoque", "insumo"])
    if permissoes is not None:
        consulta = Permission.objects.filter(content_type__app_label="compras", codename__in=permissoes)
    grupo.permissions.set(consulta)
    usuario = User.objects.create_user(username=f"u_{sufixo}", email=f"{sufixo}@t.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)
    return cliente, usuario


class FormulaTests(TestCase):
    def test_exemplos(self):
        # mínimo 5, fator 2, saldo 3 -> 7
        self.assertEqual(quantidade_sugerida(D("5"), D("3"), D("2")), D("7"))
        # com múltiplo 5 -> arredonda PARA CIMA: 10
        self.assertEqual(quantidade_sugerida(D("5"), D("3"), D("2"), D("5")), D("10"))
        # exatamente no múltiplo não sobe
        self.assertEqual(quantidade_sugerida(D("5"), D("0"), D("2"), D("5")), D("10"))
        # fator fracionário
        self.assertEqual(quantidade_sugerida(D("10"), D("2"), D("1.5")), D("13"))
        # saldo acima do alvo: nada a comprar
        self.assertEqual(quantidade_sugerida(D("5"), D("12"), D("2")), D("0"))


class SugestoesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        self.acucar = novo_insumo(self.cliente, "Açúcar", "kg", "1", 500)
        self.sal = novo_insumo(self.cliente, "Sal", "kg", "1", 100)
        definir_minimo(None, self.farinha, D("5"))  # saldo 3 -> baixo
        registrar_entrada(self.user, self.farinha, D("3"), medida("kg"))
        definir_minimo(None, self.acucar, D("5"), D("2"), D("5"))  # saldo 0, múltiplo 5
        definir_minimo(None, self.sal, D("2"))
        registrar_entrada(self.user, self.sal, D("10"), medida("kg"))  # acima do mínimo: sem sugestão

    def test_sugere_so_abaixo_do_minimo_com_formula(self):
        por_nome = {s["insumo"].nome: s for s in sugestoes_de_compra(self.cliente)}
        self.assertEqual(set(por_nome), {"Farinha", "Açúcar"})
        self.assertEqual(por_nome["Farinha"]["quantidade"], D("7"))
        self.assertEqual(por_nome["Açúcar"]["quantidade"], D("10"))
        self.assertIn("(5 × 2) − 3", por_nome["Farinha"]["calculo"])

    def test_sem_minimo_nao_sugere_e_outro_tenant_nao_aparece(self):
        self.assertEqual(sugestoes_de_compra(self.outro), [])

    def test_adicionar_e_idempotente_e_audita(self):
        class R:  # request mínimo (usuário autenticado)
            user = self.user
            META = {}
        primeira = adicionar_sugestoes(R, self.cliente)
        segunda = adicionar_sugestoes(R, self.cliente)
        self.assertEqual((len(primeira), len(segunda)), (2, 0))
        self.assertEqual(ItemCompra.objects.count(), 2)
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="compras", acao="criar").count(), 2)
        self.assertEqual(sugestoes_de_compra(self.cliente), [])  # quem já está na lista não é sugerido de novo

    def test_estoque_mudou_depois_a_quantidade_e_do_servidor(self):
        self.client.force_login(self.user)
        # o corpo tenta impor quantidade: é ignorada
        self.client.post(reverse("compras-sugestoes-adicionar"), {"insumo": self.farinha.pk, "quantidade": "999"})
        self.assertEqual(ItemCompra.objects.get(insumo=self.farinha).quantidade, D("7"))

    def test_post_com_insumo_de_outro_tenant_ou_invalido_nao_cria(self):
        alheio = novo_insumo(self.outro, "Alheio", "kg", "1", 100)
        definir_minimo(None, alheio, D("5"))
        self.client.force_login(self.user)
        self.client.post(reverse("compras-sugestoes-adicionar"), {"insumo": alheio.pk})
        self.client.post(reverse("compras-sugestoes-adicionar"), {"insumo": "abc"})
        self.assertFalse(ItemCompra.objects.filter(insumo=alheio).exists())
        self.assertEqual(ItemCompra.objects.count(), 0)


class ItensTests(TestCase):
    def setUp(self):
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        self.client.force_login(self.user)

    def criar(self, **extra):
        dados = {"quantidade": "2", "medida": medida("kg").pk, **extra}
        return self.client.post(reverse("compras-create"), dados)

    def test_item_de_insumo_copia_nome_e_audita(self):
        self.assertEqual(self.criar(insumo=self.farinha.pk, descricao="ignorado").status_code, 302)
        item = ItemCompra.objects.get()
        self.assertEqual((item.descricao, item.origem, item.cliente_id, item.criado_por_id), ("Farinha", "manual", self.cliente.pk, self.user.pk))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="compras", acao="criar", registro_id=str(item.pk)).exists())

    def test_servico_avulso(self):
        self.criar(descricao="Manutenção do forno", medida=medida("un").pk, quantidade="1")
        item = ItemCompra.objects.get()
        self.assertEqual((item.descricao, item.insumo_id), ("Manutenção do forno", None))

    def test_exige_insumo_ou_descricao_e_unidade_compativel_e_sem_duplicar(self):
        self.assertEqual(self.criar().status_code, 200)  # nem insumo nem descrição
        self.assertEqual(self.criar(insumo=self.farinha.pk, medida=medida("L").pk).status_code, 200)  # dimensão errada
        self.criar(insumo=self.farinha.pk)
        self.assertEqual(self.criar(insumo=self.farinha.pk).status_code, 200)  # duplicado
        self.assertEqual(ItemCompra.objects.count(), 1)

    def test_quantidade_invalida(self):
        for valor in ("0", "-1", "abc"):
            self.assertEqual(self.criar(descricao="X", quantidade=valor).status_code, 200, valor)
        self.assertFalse(ItemCompra.objects.exists())

    def test_insumo_de_outro_tenant_e_rejeitado(self):
        alheio = novo_insumo(self.outro, "Alheio", "kg", "1", 100)
        self.assertEqual(self.criar(insumo=alheio.pk).status_code, 200)
        self.assertFalse(ItemCompra.objects.exists())

    def test_editar_audita_antes_depois_e_excluir_audita(self):
        self.criar(insumo=self.farinha.pk)
        item = ItemCompra.objects.get()
        self.client.post(reverse("compras-edit", args=[item.pk]), {"insumo": self.farinha.pk, "quantidade": "8", "medida": medida("kg").pk, "observacao": "urgente"})
        registro = RegistroAuditoria.objects.filter(modulo="compras", acao="editar").latest("id")
        campos = {a["campo"] for a in registro.alteracoes}
        self.assertIn("quantidade", campos)
        self.assertIn("observacao", campos)
        self.client.post(reverse("compras-excluir", args=[item.pk]))
        self.assertFalse(ItemCompra.objects.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="compras", acao="excluir", registro_id=str(item.pk)).exists())

    def test_outro_tenant_nao_ve_edita_nem_exclui(self):
        self.criar(descricao="Segredo")
        item = ItemCompra.objects.get()
        self.client.force_login(self.user_b)
        self.assertNotIn("Segredo", self.client.get(reverse("compras")).content.decode())
        self.assertEqual(self.client.get(reverse("compras-edit", args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("compras-excluir", args=[item.pk])).status_code, 404)
        self.assertEqual(ItemCompra.objects.count(), 1)

    def test_permissoes(self):
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_itemcompra"])
        self.criar(descricao="X")
        item = ItemCompra.objects.get()  # do tenant A; o usuário C é de outro tenant, mas valida 403 antes
        self.client.force_login(so_ver)
        self.assertEqual(self.client.get(reverse("compras")).status_code, 200)
        self.assertEqual(self.client.post(reverse("compras-create"), {"descricao": "Y", "quantidade": "1", "medida": medida("un").pk}).status_code, 403)
        self.assertEqual(self.client.post(reverse("compras-excluir", args=[item.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("compras-sugestoes-adicionar")).status_code, 403)

    def test_pagina_mostra_sugestoes_com_calculo(self):
        definir_minimo(None, self.farinha, D("5"))
        pagina = self.client.get(reverse("compras")).content.decode()
        self.assertIn("(5 × 2) − 0", pagina)


class APITests(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente, self.ua = novo_usuario("a", "11222333000181")
        self.outro, self.ub = novo_usuario("b", "11444777000161")
        self.api_a, self.api_b = self.api(self.ua), self.api(self.ub)
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        definir_minimo(None, self.farinha, D("5"))

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def test_sugestoes_e_adicionar_idempotente(self):
        lista = self.api_a.get(f"{V1}/compras/sugestoes/").json()["data"]
        self.assertEqual((lista[0]["nome"], lista[0]["quantidade_sugerida"]), ("Farinha", "10.0000"))
        r1 = self.api_a.post(f"{V1}/compras/sugestoes/adicionar/", {"insumos": [self.farinha.pk]}, format="json")
        r2 = self.api_a.post(f"{V1}/compras/sugestoes/adicionar/", {"insumos": [self.farinha.pk]}, format="json")
        self.assertEqual((r1.status_code, len(r1.json()["data"])), (201, 1))
        self.assertEqual((r2.status_code, len(r2.json()["data"])), (200, 0))
        self.assertEqual(ItemCompra.objects.count(), 1)

    def test_adicionar_valida_corpo(self):
        self.assertEqual(self.api_a.post(f"{V1}/compras/sugestoes/adicionar/", {"insumos": "x"}, format="json").status_code, 400)
        self.assertEqual(self.api_a.post(f"{V1}/compras/sugestoes/adicionar/", {"insumos": [True]}, format="json").status_code, 400)

    def test_crud_de_itens(self):
        r = self.api_a.post(f"{V1}/compras/itens/", {"descricao": "Reparo da geladeira", "quantidade": "1", "medida": medida("un").pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        item = r.json()["data"]
        self.assertEqual((item["origem"], item["insumo"]), ("manual", None))
        r = self.api_a.patch(f"{V1}/compras/itens/{item['id']}/", {"quantidade": "3"}, format="json")
        self.assertEqual((r.status_code, r.json()["data"]["quantidade"]), (200, "3.0000"))
        self.assertEqual(self.api_a.get(f"{V1}/compras/itens/", {"q": "geladeira"}).json()["data"]["itens"][0]["id"], item["id"])
        self.assertEqual(self.api_a.delete(f"{V1}/compras/itens/{item['id']}/").status_code, 204)
        self.assertFalse(ItemCompra.objects.exists())

    def test_tenant_e_permissoes(self):
        r = self.api_a.post(f"{V1}/compras/itens/", {"descricao": "X", "quantidade": "1", "medida": medida("un").pk}, format="json")
        pk = r.json()["data"]["id"]
        self.assertEqual(self.api_b.get(f"{V1}/compras/itens/{pk}/").status_code, 404)
        self.assertEqual(self.api_b.delete(f"{V1}/compras/itens/{pk}/").status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/compras/itens/").json()["data"]["itens"], [])
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_itemcompra"])
        api_ver = self.api(so_ver)
        self.assertEqual(api_ver.post(f"{V1}/compras/itens/", {"descricao": "Y", "quantidade": "1", "medida": medida("un").pk}, format="json").status_code, 403)
        self.assertEqual(api_ver.post(f"{V1}/compras/sugestoes/adicionar/", {}, format="json").status_code, 403)
        self.assertEqual(APIClient().get(f"{V1}/compras/itens/").status_code, 401)

    def test_minimo_com_fator_e_multiplo_pela_api_e_auditoria(self):
        r = self.api_a.put(f"{V1}/estoque/saldos/{self.farinha.pk}/minimo/", {"minimo": "5", "fator_reposicao": "3", "multiplo_compra": "4"}, format="json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        sugestao = self.api_a.get(f"{V1}/compras/sugestoes/").json()["data"][0]
        self.assertEqual(sugestao["quantidade_sugerida"], "16.0000")  # 5*3-0=15 -> múltiplo de 4 -> 16
        registro = RegistroAuditoria.objects.filter(modulo="estoque").latest("id")
        self.assertIn("fator_reposicao", {a["campo"] for a in registro.alteracoes})
        # omitir mantém; null remove o múltiplo
        self.api_a.put(f"{V1}/estoque/saldos/{self.farinha.pk}/minimo/", {"minimo": "5"}, format="json")
        self.assertEqual(self.api_a.get(f"{V1}/compras/sugestoes/").json()["data"][0]["quantidade_sugerida"], "16.0000")
        self.api_a.put(f"{V1}/estoque/saldos/{self.farinha.pk}/minimo/", {"minimo": "5", "multiplo_compra": None}, format="json")
        self.assertEqual(self.api_a.get(f"{V1}/compras/sugestoes/").json()["data"][0]["quantidade_sugerida"], "15.0000")
