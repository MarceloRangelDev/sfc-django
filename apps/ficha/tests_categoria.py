from django.test import TestCase
from django.urls import reverse

from apps.categoria.models import Categoria
from apps.produto.models import Produto

from .tests import cliente, criar, novo_insumo, usuario


class FiltroCategoriaFichaTests(TestCase):
    def test_filtra_por_categoria_do_produto(self):
        matriz = cliente("Matriz", "11222333000181")
        self.client.force_login(usuario("u", matriz))
        farinha = novo_insumo(matriz, "Farinha", "kg", "1", 500)
        doces = Categoria.objects.get_or_create(cliente=matriz, nome="Doces")[0]
        salgados = Categoria.objects.create(cliente=matriz, nome="Salgados")
        bolo = Produto.objects.create(cliente=matriz, nome="Bolo", categoria=doces)
        coxinha = Produto.objects.create(cliente=matriz, nome="Coxinha", categoria=salgados)
        criar(matriz, "Ficha bolo", [(farinha, "g", "100")], bolo)
        criar(matriz, "Ficha coxinha", [(farinha, "g", "100")], coxinha)
        resposta = self.client.get(reverse("ficha"), {"categoria": salgados.pk})
        self.assertEqual([f.nome for f in resposta.context["fichas"]], ["Ficha coxinha"])
        self.assertContains(resposta, 'name="categoria"')
        self.assertEqual({c.nome for c in resposta.context["categorias"]}, {"Doces", "Salgados"})
