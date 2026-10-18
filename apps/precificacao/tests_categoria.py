from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria

from .tests import cliente, criar_ficha, nova_precificacao, novo_canal, novo_insumo, usuario


class FiltroCategoriaPrecificacaoTests(TestCase):
    def test_filtra_por_categoria_do_produto(self):
        matriz = cliente("Matriz", "11222333000181")
        self.client.force_login(usuario("u", matriz))
        farinha = novo_insumo(matriz, "Farinha", "kg", "1", 1000)
        canal = novo_canal(matriz)
        ficha_a = criar_ficha(matriz, "Ficha A", [(farinha, "kg", "1")])
        ficha_b = criar_ficha(matriz, "Ficha B", [(farinha, "kg", "1")])
        outra = Categoria.objects.create(cliente=matriz, nome="Salgados")
        ficha_b.produto.categoria = outra
        ficha_b.produto.save()
        nova_precificacao(matriz, ficha_a, canal)
        nova_precificacao(matriz, ficha_b, canal)
        resposta = self.client.get(reverse("precificacao"), {"categoria": outra.pk})
        self.assertEqual([p.ficha.nome for p in resposta.context["precificacoes"]], ["Ficha B"])
        self.assertContains(resposta, 'name="categoria"')
