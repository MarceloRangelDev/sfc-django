"""Cadastro do insumo global com marca: o mesmo nome padrão aceita marcas diferentes (cada uma uma variação)."""
from django.urls import reverse

from .models import InsumoGlobal, InsumoGlobalMarca, MarcaGlobal
from .tests import Base


class MarcaNoCadastroTests(Base):
    def criar(self, nome, marca=""):
        self.logar(self.admin)
        return self.client.post(reverse("insumo-global-create"), {"nome": nome, "marca": marca, "descricao": "", "ativo": "on"})

    def test_novo_insumo_com_marca_cria_o_padrao_e_a_variacao(self):
        resposta = self.criar("Farinha de trigo", "Dona Benta")
        self.assertEqual(resposta.status_code, 302)
        farinha = InsumoGlobal.objects.get(nome="Farinha de trigo")
        self.assertEqual([v.marca.nome for v in farinha.variacoes.all()], ["Dona Benta"])

    def test_mesmo_nome_com_outra_marca_acrescenta_a_variacao_sem_duplicar_o_padrao(self):
        self.criar("Farinha de trigo", "Dona Benta")
        resposta = self.criar("farinha de TRIGO", "Renata")  # mesmo nome (sem acento/caixa), outra marca
        farinha = InsumoGlobal.objects.get(nome="Farinha de trigo")
        self.assertRedirects(resposta, reverse("insumo-global-edit", args=[farinha.pk]))
        self.assertEqual(InsumoGlobal.objects.filter(nome_normalizado="farinha de trigo").count(), 1)
        self.assertEqual(sorted(v.marca.nome for v in farinha.variacoes.all()), ["Dona Benta", "Renata"])

    def test_mesmo_nome_sem_marca_continua_recusado(self):
        resposta = self.criar("Açúcar")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "informe a marca")
        self.assertEqual(InsumoGlobal.objects.filter(nome="Açúcar").count(), 1)

    def test_repetir_a_mesma_marca_nao_duplica(self):
        self.criar("Açúcar", "União")
        self.criar("Açúcar", "uniao")
        self.assertEqual(InsumoGlobalMarca.objects.filter(insumo_global=self.acucar).count(), 1)
        self.assertEqual(MarcaGlobal.objects.filter(nome_normalizado="uniao").count(), 1)

    def test_lista_mostra_as_marcas_de_cada_padrao(self):
        self.criar("Açúcar", "União")
        self.criar("Açúcar", "Caravelas")
        pagina = self.client.get(reverse("insumo-global")).content.decode()
        self.assertIn("União", pagina)
        self.assertIn("Caravelas", pagina)

    def test_na_edicao_o_campo_marca_adiciona_uma_variacao(self):
        self.logar(self.admin)
        self.client.post(reverse("insumo-global-edit", args=[self.acucar.pk]), {"nome": "Açúcar", "marca": "Guarani", "descricao": "", "ativo": "on"})
        self.assertEqual([v.marca.nome for v in self.acucar.variacoes.all()], ["Guarani"])
