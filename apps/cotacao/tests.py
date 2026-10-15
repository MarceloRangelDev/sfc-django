"""Cotações: regra de preço (varejo/atacado), comparativo normalizado, vencedor, status, contas a pagar,
exportação, auditoria e isolamento entre tenants (telas)."""
import io
from datetime import timedelta
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.compras.models import ItemCompra
from apps.financeiro.models import CategoriaFinanceira, ContaPagar
from apps.insumo.tests import medida, novo_insumo
from web_project.testing import habilitar_modulo

from .models import Cotacao, CotacaoItem, CotacaoLoja, CotacaoPreco
from .services import avaliar_preco, comparativo

D = Decimal


def novo_usuario(sufixo, cnpj, permissoes=None):
    cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=f"Empresa {sufixo}")
    for modulo in ("insumo", "compras", "cotacao", "financeiro-pagar"):
        habilitar_modulo(cliente, modulo)
    grupo = Group.objects.create(name=f"G {sufixo}")
    if permissoes is None:
        consulta = Permission.objects.filter(content_type__app_label__in=["cotacao", "compras", "insumo", "financeiro"])
    else:
        consulta = Permission.objects.filter(content_type__app_label="cotacao", codename__in=permissoes)
    grupo.permissions.set(consulta)
    usuario = User.objects.create_user(username=f"u_{sufixo}", email=f"{sufixo}@t.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)
    return cliente, usuario


def oferta(quantidade, unitario, sigla="kg", atacado=None, minimo=None, e_atacado=False):
    return CotacaoPreco(
        medida=medida(sigla), quantidade=D(quantidade), preco_unitario=unitario, preco_atacado=atacado,
        qtd_minima_atacado=None if minimo is None else D(minimo), preco_e_atacado=e_atacado,
    )


class RegraDePrecoTests(TestCase):
    def test_varejo_quando_nao_ha_atacado(self):
        a = avaliar_preco(oferta("3", 1000))
        self.assertEqual((a["tipo"], a["total"], a["atende"]), ("varejo", 3000, True))

    def test_atacado_a_partir_do_minimo(self):
        self.assertEqual(avaliar_preco(oferta("10", 1000, atacado=800, minimo="10"))["tipo"], "atacado")  # no limite
        a = avaliar_preco(oferta("10", 1000, atacado=800, minimo="10"))
        self.assertEqual(a["total"], 8000)
        abaixo = avaliar_preco(oferta("9", 1000, atacado=800, minimo="10"))
        self.assertEqual((abaixo["tipo"], abaixo["total"]), ("varejo", 9000))
        self.assertIn("atacado a partir de 10", abaixo["motivo"])

    def test_preco_informado_e_de_atacado(self):
        ok = avaliar_preco(oferta("10", 700, e_atacado=True, minimo="10"))
        self.assertEqual((ok["tipo"], ok["atende"], ok["total"]), ("atacado", True, 7000))
        self.assertTrue(avaliar_preco(oferta("1", 700, e_atacado=True))["atende"])  # sem mínimo: atende
        nao = avaliar_preco(oferta("5", 700, e_atacado=True, minimo="10"))
        self.assertFalse(nao["atende"])
        self.assertIn("abaixo do mínimo", nao["motivo"])

    def test_normaliza_para_preco_por_kg(self):
        # R$ 5,00 por kg == R$ 0,005 por g: mesmo preço normalizado
        kg = avaliar_preco(oferta("1", 500, "kg"))
        self.assertEqual((kg["por_base_centavos"], kg["unidade_base"]), (500, "KG"))
        gr = oferta("2000", 1, "g")  # R$ 0,01 por g -> R$ 10,00 por kg
        self.assertEqual(avaliar_preco(gr)["por_base_centavos"], 1000)
        self.assertEqual(avaliar_preco(gr)["total"], 2000)

    def test_arredondamento_meio_para_cima_no_total(self):
        self.assertEqual(avaliar_preco(oferta("0.5", 5))["total"], 3)  # 2,5 centavos -> 3
        self.assertEqual(avaliar_preco(oferta("0.5", 3))["total"], 2)  # 1,5 -> 2
        self.assertEqual(avaliar_preco(oferta("0.3333", 100))["total"], 33)  # 33,33 -> 33


class FluxoBase(TestCase):
    def setUp(self):
        self.cliente, self.user = novo_usuario("a", "11222333000181")
        self.outro, self.user_b = novo_usuario("b", "11444777000161")
        self.farinha = novo_insumo(self.cliente, "Farinha", "kg", "1", 1000)
        self.client.force_login(self.user)
        self.categoria = CategoriaFinanceira.objects.create(cliente=self.cliente, nome="Insumos", tipo="despesa")

    def nova_cotacao(self, **extra):
        r = self.client.post(reverse("cotacao-create"), {"titulo": "Cotação de outubro", "observacoes": "Entrega na sexta", **extra})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        return Cotacao.objects.latest("pk")

    def add_loja(self, cotacao, nome="Atacadão", **extra):
        self.client.post(reverse("cotacao-loja-create", args=[cotacao.pk]), {"nome": nome, **extra})
        return CotacaoLoja.objects.get(cotacao=cotacao, nome=nome)

    def add_item(self, cotacao, **extra):
        dados = {"insumo": self.farinha.pk, "quantidade": "10", "medida": medida("kg").pk, **extra}
        self.client.post(reverse("cotacao-item-create", args=[cotacao.pk]), dados)
        return cotacao.itens.latest("pk")

    def add_preco(self, cotacao, item, loja, **extra):
        dados = {"loja": loja.pk, "marca": "Marca X", "medida": medida("kg").pk, "quantidade": "10", "preco_unitario": "10,00", **extra}
        r = self.client.post(reverse("cotacao-preco-create", args=[cotacao.pk, item.pk]), dados)
        return r, CotacaoPreco.objects.filter(item=item, loja=loja).first()


class CriacaoETenantTests(FluxoBase):
    def test_cria_audita_e_importa_lista_de_compras(self):
        ItemCompra.objects.create(cliente=self.cliente, insumo=self.farinha, descricao="Farinha", quantidade=D("7"), medida=medida("kg"), observacao="urgente")
        ItemCompra.objects.create(cliente=self.cliente, descricao="Reparo do forno", quantidade=D("1"), medida=medida("un"))
        cotacao = self.nova_cotacao(importar_lista="1")
        self.assertEqual(cotacao.cliente_id, self.cliente.pk)
        self.assertEqual(set(cotacao.itens.values_list("descricao", flat=True)), {"Farinha", "Reparo do forno"})
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="criar", registro_nome="Cotação de outubro").exists())

    def test_importar_depois_e_idempotente(self):
        cotacao = self.nova_cotacao()
        ItemCompra.objects.create(cliente=self.cliente, insumo=self.farinha, descricao="Farinha", quantidade=D("7"), medida=medida("kg"))
        item = ItemCompra.objects.get()
        for _ in range(2):
            self.client.post(reverse("cotacao-importar", args=[cotacao.pk]), {"itens": [str(item.pk)]})
        self.assertEqual(cotacao.itens.count(), 1)
        self.assertEqual(ItemCompra.objects.count(), 1)  # a lista de compras não é consumida

    def test_importar_ignora_ids_de_outro_tenant(self):
        cotacao = self.nova_cotacao()
        alheio = ItemCompra.objects.create(cliente=self.outro, descricao="Alheio", quantidade=D("1"), medida=medida("un"))
        r = self.client.post(reverse("cotacao-importar", args=[cotacao.pk]), {"itens": [str(alheio.pk)]})
        self.assertEqual(r.status_code, 200)  # escolha inválida: volta ao formulário
        self.assertEqual(cotacao.itens.count(), 0)

    def test_outro_tenant_recebe_404_em_tudo(self):
        cotacao = self.nova_cotacao()
        loja = self.add_loja(cotacao)
        item = self.add_item(cotacao)
        _, preco = self.add_preco(cotacao, item, loja)
        self.client.force_login(self.user_b)
        urls_get = [reverse("cotacao-detail", args=[cotacao.pk]), reverse("cotacao-edit", args=[cotacao.pk]), reverse("cotacao-exportar", args=[cotacao.pk]),
                    reverse("cotacao-loja-edit", args=[cotacao.pk, loja.pk]), reverse("cotacao-item-edit", args=[cotacao.pk, item.pk]),
                    reverse("cotacao-preco-edit", args=[cotacao.pk, preco.pk]), reverse("cotacao-importar", args=[cotacao.pk])]
        for url in urls_get:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        urls_post = [reverse("cotacao-excluir", args=[cotacao.pk]), reverse("cotacao-status", args=[cotacao.pk]), reverse("cotacao-preco-escolher", args=[cotacao.pk, preco.pk]),
                     reverse("cotacao-loja-excluir", args=[cotacao.pk, loja.pk]), reverse("cotacao-preco-excluir", args=[cotacao.pk, preco.pk])]
        for url in urls_post:
            self.assertEqual(self.client.post(url, {"acao": "cancelar"}).status_code, 404, url)
        self.assertNotIn("Cotação de outubro", self.client.get(reverse("cotacao")).content.decode())
        self.assertEqual((Cotacao.objects.count(), CotacaoPreco.objects.count()), (1, 1))

    def test_loja_e_item_de_outra_cotacao_nao_se_misturam(self):
        c1, c2 = self.nova_cotacao(), self.nova_cotacao()
        loja2 = self.add_loja(c2, "Outra")
        item1 = self.add_item(c1)
        r, preco = self.add_preco(c1, item1, loja2)  # loja de outra cotação
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(preco)
        self.assertEqual(self.client.get(reverse("cotacao-preco-create", args=[c2.pk, item1.pk])).status_code, 404)

    def test_permissoes(self):
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_cotacao"])
        cotacao = self.nova_cotacao()
        self.client.force_login(so_ver)
        self.assertEqual(self.client.get(reverse("cotacao")).status_code, 200)
        self.assertEqual(self.client.post(reverse("cotacao-create"), {"titulo": "X"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("cotacao-status", args=[cotacao.pk]), {"acao": "enviar"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("cotacao-excluir", args=[cotacao.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("cotacao-exportar", args=[cotacao.pk])).status_code, 404)  # outro tenant: 404


class ValidacaoDePrecoTests(FluxoBase):
    def setUp(self):
        super().setUp()
        self.cotacao = self.nova_cotacao()
        self.loja = self.add_loja(self.cotacao)
        self.item = self.add_item(self.cotacao)

    def tentar(self, **extra):
        r, preco = self.add_preco(self.cotacao, self.item, self.loja, **extra)
        return r.status_code, preco

    def test_valido(self):
        codigo, preco = self.tentar()
        self.assertEqual((codigo, preco.preco_unitario, preco.marca), (302, 1000, "Marca X"))
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="criar", registro_nome__contains="Atacadão").exists())

    def test_marca_obrigatoria_exceto_servico(self):
        self.assertEqual(self.tentar(marca="")[0], 200)
        servico = self.add_item(self.cotacao, insumo="", descricao="Conserto", servico="on", medida=medida("un").pk, quantidade="1")
        r, preco = self.add_preco(self.cotacao, servico, self.loja, marca="", medida=medida("un").pk, quantidade="1", preco_unitario="150,00")
        self.assertEqual((r.status_code, preco.marca), (302, ""))

    def test_regras_de_atacado(self):
        self.assertEqual(self.tentar(preco_atacado="8,00")[0], 200)  # atacado sem mínimo
        self.assertEqual(self.tentar(qtd_minima_atacado="10")[0], 200)  # mínimo sem preço de atacado
        self.assertEqual(self.tentar(preco_atacado="12,00", qtd_minima_atacado="10")[0], 200)  # atacado maior que unitário
        self.assertEqual(self.tentar(preco_e_atacado="on", preco_atacado="8,00")[0], 200)  # já é atacado + preço de atacado
        codigo, preco = self.tentar(preco_atacado="8,00", qtd_minima_atacado="10")
        self.assertEqual((codigo, preco.preco_atacado), (302, 800))

    def test_observacao_maxima_255_validada_no_servidor(self):
        self.assertEqual(self.tentar(observacao="x" * 256)[0], 200)
        self.assertEqual(self.tentar(observacao="x" * 255)[0], 302)

    def test_campos_numericos_e_dimensao(self):
        for extra in ({"preco_unitario": "0,00"}, {"preco_unitario": ""}, {"quantidade": "0"}, {"quantidade": "-1"}, {"medida": medida("L").pk}):
            self.assertEqual(self.tentar(**extra)[0], 200, extra)
        self.assertFalse(CotacaoPreco.objects.exists())

    def test_loja_duplicada_para_o_mesmo_item(self):
        self.tentar()
        self.assertEqual(self.tentar()[0], 200)
        self.assertEqual(CotacaoPreco.objects.count(), 1)

    def test_item_duplicado_e_dimensao(self):
        self.client.post(reverse("cotacao-item-create", args=[self.cotacao.pk]), {"insumo": self.farinha.pk, "quantidade": "1", "medida": medida("kg").pk})
        self.assertEqual(self.cotacao.itens.count(), 1)  # mesmo insumo de novo é recusado
        self.client.post(reverse("cotacao-item-create", args=[self.cotacao.pk]), {"descricao": "Água", "quantidade": "1", "medida": medida("L").pk, "observacao": "y" * 256})
        self.assertEqual(self.cotacao.itens.count(), 1)  # observação longa

    def test_loja_contato_opcional_e_validado(self):
        self.add_loja(self.cotacao, "Mercado", telefone="(11) 99999-1234", email="Compras@LOJA.com", endereco="Rua A, 1")
        loja = CotacaoLoja.objects.get(nome="Mercado")
        self.assertEqual((loja.telefone, loja.email), ("11999991234", "compras@loja.com"))
        self.client.post(reverse("cotacao-loja-create", args=[self.cotacao.pk]), {"nome": "Ruim", "telefone": "123"})
        self.assertFalse(CotacaoLoja.objects.filter(nome="Ruim").exists())
        self.client.post(reverse("cotacao-loja-create", args=[self.cotacao.pk]), {"nome": "mercado"})  # nome repetido
        self.assertEqual(CotacaoLoja.objects.filter(cotacao=self.cotacao).count(), 2)


class ComparativoEVencedorTests(FluxoBase):
    def setUp(self):
        super().setUp()
        self.cotacao = self.nova_cotacao()
        self.item = self.add_item(self.cotacao, quantidade="10")
        self.a, self.b, self.c = self.add_loja(self.cotacao, "A"), self.add_loja(self.cotacao, "B"), self.add_loja(self.cotacao, "C")
        _, self.pa = self.add_preco(self.cotacao, self.item, self.a, preco_unitario="10,00")  # 100
        _, self.pb = self.add_preco(self.cotacao, self.item, self.b, preco_unitario="12,00", preco_atacado="8,00", qtd_minima_atacado="10")  # atacado 8,00
        _, self.pc = self.add_preco(self.cotacao, self.item, self.c, preco_unitario="5,00", preco_e_atacado="on", qtd_minima_atacado="50")  # não atende (10 < 50)

    def test_menor_preco_considera_atacado_e_ignora_quem_nao_atende(self):
        dados = comparativo(self.cotacao)[0]
        melhores = [o["preco"].loja.nome for o in dados["ofertas"] if o["melhor"]]
        self.assertEqual(melhores, ["B"])
        self.assertFalse(next(o for o in dados["ofertas"] if o["preco"].loja.nome == "C")["atende"])
        self.assertEqual(dados["ofertas"][-1]["preco"].loja.nome, "C")  # quem não atende vai para o fim

    def test_comparacao_entre_unidades_diferentes(self):
        d = self.add_loja(self.cotacao, "D")
        # 1000 g a R$ 0,007/g = R$ 7,00 por kg: melhor que B (8,00)
        self.client.post(reverse("cotacao-preco-create", args=[self.cotacao.pk, self.item.pk]),
                         {"loja": d.pk, "marca": "M", "medida": medida("g").pk, "quantidade": "10000", "preco_unitario": "0,01"})
        # 0,01 por g = 10,00 por kg: pior que B; confirma só a conversão
        dados = comparativo(self.cotacao)[0]
        por_loja = {o["preco"].loja.nome: o["por_base_centavos"] for o in dados["ofertas"]}
        self.assertEqual(por_loja["D"], 1000)
        self.assertEqual(por_loja["B"], 800)

    def test_escolher_um_vencedor_por_item_e_trocar(self):
        self.assertEqual(self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, self.pb.pk])).status_code, 302)
        self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, self.pa.pk]))
        self.assertEqual(list(CotacaoPreco.objects.filter(escolhido=True)), [self.pa.__class__.objects.get(pk=self.pa.pk)])
        self.assertEqual(CotacaoPreco.objects.filter(item=self.item, escolhido=True).count(), 1)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="editar", descricao__contains="Escolhido").exists())

    def test_nao_escolhe_oferta_que_nao_atende(self):
        self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, self.pc.pk]))
        self.assertFalse(CotacaoPreco.objects.filter(escolhido=True).exists())

    def test_fluxo_de_status(self):
        url = reverse("cotacao-status", args=[self.cotacao.pk])
        self.client.post(url, {"acao": "fechar"})  # sem vencedor
        self.cotacao.refresh_from_db()
        self.assertEqual(self.cotacao.status, "rascunho")
        self.client.post(url, {"acao": "enviar"})
        self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, self.pb.pk]))
        self.client.post(url, {"acao": "fechar"})
        self.cotacao.refresh_from_db()
        self.assertEqual((self.cotacao.status, bool(self.cotacao.fechada_em)), ("fechada", True))
        # fechada não aceita alterações
        self.client.post(reverse("cotacao-preco-excluir", args=[self.cotacao.pk, self.pa.pk]))
        self.client.post(reverse("cotacao-loja-create", args=[self.cotacao.pk]), {"nome": "Tardia"})
        self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, self.pa.pk]))
        self.assertTrue(CotacaoPreco.objects.filter(pk=self.pa.pk).exists())
        self.assertFalse(CotacaoLoja.objects.filter(nome="Tardia").exists())
        self.assertTrue(CotacaoPreco.objects.get(pk=self.pb.pk).escolhido)
        self.assertEqual(self.client.get(reverse("cotacao-edit", args=[self.cotacao.pk])).status_code, 404)
        self.client.post(url, {"acao": "reabrir"})
        self.cotacao.refresh_from_db()
        self.assertEqual(self.cotacao.status, "enviada")
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="editar", descricao__contains="Status").count() >= 3)

    def test_enviar_exige_itens_e_lojas(self):
        vazia = self.nova_cotacao()
        self.client.post(reverse("cotacao-status", args=[vazia.pk]), {"acao": "enviar"})
        vazia.refresh_from_db()
        self.assertEqual(vazia.status, "rascunho")


class ContasAPagarTests(FluxoBase):
    def setUp(self):
        super().setUp()
        self.cotacao = self.nova_cotacao()
        self.item1 = self.add_item(self.cotacao, quantidade="10")
        self.item2 = self.add_item(self.cotacao, insumo="", descricao="Sal", quantidade="2", medida=medida("kg").pk)
        self.a, self.b = self.add_loja(self.cotacao, "A"), self.add_loja(self.cotacao, "B")
        _, self.p1 = self.add_preco(self.cotacao, self.item1, self.a, preco_unitario="10,00")  # 100,00
        _, self.p2 = self.add_preco(self.cotacao, self.item2, self.a, preco_unitario="3,50", quantidade="2")  # 7,00
        _, self.p3 = self.add_preco(self.cotacao, self.item2, self.b, preco_unitario="3,00", quantidade="2")  # 6,00
        for p in (self.p1, self.p3):
            self.client.post(reverse("cotacao-preco-escolher", args=[self.cotacao.pk, p.pk]))
        self.url = reverse("cotacao-contas", args=[self.cotacao.pk])
        self.dados = {"categoria": self.categoria.pk, "vencimento": (timezone.localdate() + timedelta(days=10)).isoformat()}

    def fechar(self):
        self.client.post(reverse("cotacao-status", args=[self.cotacao.pk]), {"acao": "fechar"})

    def test_exige_cotacao_fechada(self):
        self.client.post(self.url, self.dados)
        self.assertFalse(ContaPagar.objects.exists())

    def test_gera_uma_conta_por_loja_idempotente_e_audita(self):
        self.fechar()
        self.assertEqual(self.client.post(self.url, self.dados).status_code, 302)
        contas = {c.contraparte: c for c in ContaPagar.objects.all()}
        self.assertEqual({k: v.valor for k, v in contas.items()}, {"A": 10000, "B": 600})
        self.assertEqual(contas["A"].cliente_id, self.cliente.pk)
        self.assertEqual(CotacaoLoja.objects.get(pk=self.a.pk).conta_pagar_id, contas["A"].pk)
        self.client.post(self.url, self.dados)  # repetido: não duplica
        self.assertEqual(ContaPagar.objects.count(), 2)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="financeiro", acao="criar").count() == 2)

    def test_depois_de_gerar_nao_reabre_nem_cancela_nem_exclui(self):
        self.fechar()
        self.client.post(self.url, self.dados)
        for acao in ("reabrir", "cancelar"):
            self.client.post(reverse("cotacao-status", args=[self.cotacao.pk]), {"acao": acao})
        self.client.post(reverse("cotacao-excluir", args=[self.cotacao.pk]))
        self.cotacao.refresh_from_db()
        self.assertEqual(self.cotacao.status, "fechada")
        self.assertTrue(Cotacao.objects.filter(pk=self.cotacao.pk).exists())
        self.client.post(reverse("cotacao-loja-excluir", args=[self.cotacao.pk, self.a.pk]))
        self.assertTrue(CotacaoLoja.objects.filter(pk=self.a.pk).exists())

    def test_categoria_de_outra_empresa_ou_de_receita_e_recusada(self):
        self.fechar()
        alheia = CategoriaFinanceira.objects.create(cliente=self.outro, nome="X", tipo="despesa")
        receita = CategoriaFinanceira.objects.create(cliente=self.cliente, nome="Vendas", tipo="receita")
        for categoria in (alheia, receita):
            self.client.post(self.url, {**self.dados, "categoria": categoria.pk})
        self.assertFalse(ContaPagar.objects.exists())

    def test_sem_permissao_financeira_nao_gera(self):
        self.fechar()
        sem = User.objects.create_user(username="semfin", password="SenhaForte123!")
        sem.profile.cliente = self.cliente
        sem.profile.save()
        grupo = Group.objects.create(name="Sem fin")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="cotacao"))
        sem.groups.add(grupo)
        self.client.force_login(sem)
        self.assertEqual(self.client.post(self.url, self.dados).status_code, 404)
        self.assertFalse(ContaPagar.objects.exists())


class ExportacaoTests(FluxoBase):
    def test_pdf_e_excel_com_comparativo_e_tenant(self):
        cotacao = self.nova_cotacao()
        item = self.add_item(cotacao)
        loja = self.add_loja(cotacao, "Loja Boa")
        _, preco = self.add_preco(cotacao, item, loja, observacao="Entrega grátis")
        self.client.post(reverse("cotacao-preco-escolher", args=[cotacao.pk, preco.pk]))
        pdf = self.client.get(reverse("cotacao-exportar", args=[cotacao.pk]), {"formato": "pdf"})
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        xlsx = self.client.get(reverse("cotacao-exportar", args=[cotacao.pk]), {"formato": "xlsx"})
        planilha = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
        textos = [str(c.value) for linha in planilha.iter_rows() for c in linha if c.value is not None]
        for esperado in ("Loja Boa", "Marca X", "VENCEDOR", "Entrega grátis", "Entrega na sexta", "R$ 100,00"):
            self.assertTrue(any(esperado in t for t in textos), esperado)
        self.assertEqual(self.client.get(reverse("cotacao-exportar", args=[cotacao.pk]), {"formato": "exe"}).status_code, 404)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="exportar", registro_id=str(cotacao.pk)).count() == 2)

    def test_excel_neutraliza_formulas(self):
        cotacao = self.nova_cotacao()
        item = self.add_item(cotacao)
        loja = self.add_loja(cotacao, "=HYPERLINK(1)")
        self.add_preco(cotacao, item, loja, marca="+cmd")
        xlsx = self.client.get(reverse("cotacao-exportar", args=[cotacao.pk]), {"formato": "xlsx"})
        planilha = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
        valores = [str(c.value) for linha in planilha.iter_rows() for c in linha if c.value]
        self.assertTrue(any(v.startswith("'=HYPERLINK") for v in valores))
        self.assertFalse(any(v.startswith("=HYPERLINK") for v in valores))

    def test_excluir_cotacao_audita(self):
        cotacao = self.nova_cotacao()
        self.client.post(reverse("cotacao-excluir", args=[cotacao.pk]))
        self.assertFalse(Cotacao.objects.exists())
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="cotacao", acao="excluir", registro_id=str(cotacao.pk)).exists())
