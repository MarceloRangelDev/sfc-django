"""Segmentação dos insumos globais pelos Segmentos e telas do usuário (olho na senha, perfis por cliente, permissões ocultas)."""
from django.urls import reverse

from apps.auditoria.models import RegistroAuditoria
from apps.insumo.models import Insumo
from apps.insumo.tests import medida
from apps.modulo.models import Segmento

from .models import InsumoGlobal
from .tests import Base


class SegmentacaoTests(Base):
    def setUp(self):
        super().setUp()
        self.seg_produto = Segmento.objects.get(slug="produto")  # habilitar_modulo coloca o cliente neste segmento
        self.seg_servico = Segmento.objects.get(slug="servico")

    def test_global_so_aparece_para_o_segmento_marcado(self):
        self.acucar.segmentos.set([self.seg_servico])
        self.logar(self.gestor)
        self.cadastrar("acucar")  # o cliente é do segmento produto: o padrão não é oferecido
        insumo = Insumo.objects.get(cliente=self.a)
        self.assertEqual((insumo.insumo_global, insumo.nao_conforme), (None, True))

    def test_global_do_segmento_do_cliente_padroniza(self):
        self.acucar.segmentos.set([self.seg_produto])
        self.logar(self.gestor)
        self.cadastrar("acucar")
        self.assertEqual(Insumo.objects.get(cliente=self.a).insumo_global, self.acucar)

    def test_global_sem_segmento_vale_para_todos(self):
        self.logar(self.gestor)
        self.cadastrar("acucar")
        self.assertEqual(Insumo.objects.get(cliente=self.a).insumo_global, self.acucar)

    def test_lista_de_insumos_so_traz_os_globais_do_segmento(self):
        InsumoGlobal.objects.create(nome="Fermento").segmentos.set([self.seg_servico])
        self.acucar.segmentos.set([self.seg_produto])
        self.logar(self.gestor)
        pagina = self.client.get(reverse("insumo-create")).content.decode()
        self.assertIn(">Açúcar</option>", pagina)
        self.assertNotIn("Fermento", pagina)

    def test_tela_de_segmentos_atualiza_os_insumos_globais_e_audita(self):
        self.logar(self.admin)
        farinha = InsumoGlobal.objects.create(nome="Farinha")
        pagina = self.client.get(reverse("segmento-edit", args=[self.seg_produto.pk]))
        self.assertContains(pagina, "Insumos globais do segmento")
        dados = {"nome": self.seg_produto.nome, "descricao": "", "tipo_precificacao": self.seg_produto.tipo_precificacao, "ativo": "on",
                 "modulos": [m.pk for m in self.seg_produto.modulos.all()], "insumos_globais": [farinha.pk]}
        self.client.post(reverse("segmento-edit", args=[self.seg_produto.pk]), dados)
        self.assertEqual(list(self.seg_produto.insumos_globais.all()), [farinha])
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="modulo", descricao__contains="Insumos globais do segmento").exists())
        self.assertContains(self.client.get(reverse("segmento-list")), "Insumos globais")

    def test_promover_e_redirecionar_acrescentam_o_segmento_do_cliente(self):
        self.acucar.segmentos.set([self.seg_servico])
        insumo = Insumo.objects.create(cliente=self.a, nome="Asucar", medida=medida("kg"), quantidade=1, preco_pago=1000, nao_conforme=True)
        self.logar(self.admin)
        self.client.post(reverse("insumo-nao-conforme-redirecionar", args=[insumo.pk]), {"destino": self.acucar.pk})
        self.assertTrue(self.acucar.segmentos.filter(pk=self.seg_produto.pk).exists())
        novo = Insumo.objects.create(cliente=self.a, nome="Cúrcuma", medida=medida("kg"), quantidade=1, preco_pago=1000, nao_conforme=True)
        self.client.post(reverse("insumo-nao-conforme-promover", args=[novo.pk]))
        self.assertEqual(list(InsumoGlobal.objects.get(nome="Cúrcuma").segmentos.all()), [self.seg_produto])


class TelaUsuarioTests(Base):
    def test_formulario_traz_olho_na_senha_perfis_por_cliente_e_permissoes_ocultas(self):
        self.logar(self.admin)
        pagina = self.client.get(reverse("usuario-create")).content.decode()
        self.assertIn("tabler-eye", pagina)
        self.assertIn('id="verPermissoesEspeciais"', pagina)
        self.assertIn('id="blocoPermissoesEspeciais" class="d-none"', pagina)
        self.assertIn("jQuery(cliente).on('change'", pagina)  # o select2 só dispara "change" pelo jQuery
        self.assertIn("perfis-por-cliente", pagina)


class ListaVaziaTests(Base):
    def test_explica_por_que_a_lista_esta_vazia(self):
        from apps.modulo.models import Segmento

        self.acucar.segmentos.set([Segmento.objects.get(slug="servico")])  # o cliente é do segmento produto
        self.logar(self.gestor)
        pagina = self.client.get(reverse("insumo-create")).content.decode()
        self.assertIn("Nenhum insumo global liberado para o segmento", pagina)
