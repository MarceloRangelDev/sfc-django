"""Pedidos e cozinha: pedidos novos na fila, Finalizar com mensagem, status de entrega em Vendas, mensagens ao enviar da comanda e o
stream de eventos (SSE) com isolamento por unidade."""
import json
from unittest import mock

from django.urls import reverse

from apps.pdv import services as pdv
from apps.pdv.models import Venda

from . import services as s
from . import views
from .models import Pedido
from .tests import PedidoBase


class FilaENovosTests(PedidoBase):
    def test_pedido_novo_aparece_na_fila_com_acao_de_confirmar(self):
        pedido = self.pedido("manual")  # fica NOVO (aguardando confirmação)
        self.client.force_login(self.operador)
        dados = self.client.get(reverse("cozinha-api-fila")).json()["pedidos"]
        achado = next(p for p in dados if p["id"] == pedido.pk)
        self.assertEqual((achado["status"], achado["pode_avancar"], achado["pode_voltar"]), ("novo", True, False))

    def test_fila_nao_traz_pedido_de_outra_unidade(self):
        pedido = self.pedido("manual")
        self.client.force_login(self.gestor_b)
        self.assertNotIn(pedido.pk, [p["id"] for p in self.client.get(reverse("cozinha-api-fila")).json()["pedidos"]])


class FinalizarEEntregaTests(PedidoBase):
    def levar_ate_pronto(self):
        pedido = self.pedido("lancado")
        s.avancar(self.req_o, pedido)  # em preparo
        s.avancar(self.req_o, pedido)  # pronto
        return Pedido.objects.get(pk=pedido.pk)

    def test_finalizar_devolve_mensagem_e_vendas_mostra_entregue(self):
        pedido = self.levar_ate_pronto()
        self.assertEqual(Venda.objects.get(pk=pedido.venda_id).entrega, "em_andamento")
        self.client.force_login(self.operador)
        r = self.client.post(reverse("cozinha-api-mover", args=[pedido.pk]), json.dumps({"acao": "avancar"}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("finalizado", r.json()["mensagem"])
        venda = Venda.objects.get(pk=pedido.venda_id)
        self.assertEqual((venda.entrega, venda.entrega_rotulo), ("entregue", "Entregue"))
        self.assertContains(self.client.get(reverse("pdv-vendas")), "Entregue")
        self.assertContains(self.client.get(reverse("pdv-venda", args=[venda.pk])), "Entregue")

    def test_erro_ao_finalizar_pedido_ja_entregue_volta_como_json_422(self):
        pedido = self.levar_ate_pronto()
        s.avancar(self.req_o, pedido)
        self.client.force_login(self.operador)
        r = self.client.post(reverse("cozinha-api-mover", args=[pedido.pk]), json.dumps({"acao": "avancar"}), content_type="application/json")
        self.assertEqual(r.status_code, 422)
        self.assertIn("erro", r.json())

    def test_entrega_so_e_entregue_quando_todos_os_pedidos_foram(self):
        pedido = self.pedido("lancado")
        venda = Venda.objects.get(pk=pedido.venda_id)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        segundo, _ = s.criar_pedido(self.req_o, venda, tipo="mesa")
        for p in (pedido, segundo):
            for _ in range(3):
                if p.status != "entregue":
                    s.avancar(self.req_o, p)
                    p.refresh_from_db()
            if p is pedido:
                self.assertEqual(Venda.objects.get(pk=venda.pk).entrega, "em_andamento")
        self.assertEqual(Venda.objects.get(pk=venda.pk).entrega, "entregue")

    def test_pedido_cancelado_nao_conta_para_a_entrega(self):
        pedido = self.pedido("manual")
        venda = Venda.objects.get(pk=pedido.venda_id)
        s.cancelar(self.req_o, pedido, "cliente desistiu")
        self.assertEqual(Venda.objects.get(pk=venda.pk).entrega, "")


class EnvioDaComandaTests(PedidoBase):
    def test_mensagens_distinguem_comanda_vazia_e_tudo_enviado(self):
        self.regra("manual")
        venda = pdv.abrir_comanda(self.req_o, self.caixa, self.mesa)
        self.client.force_login(self.operador)
        url = reverse("pedido-enviar", args=[venda.pk])
        r = self.client.post(url, {"tipo": "mesa"}, follow=True)
        self.assertContains(r, "não tem itens")
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        self.client.post(url, {"tipo": "mesa"})
        r = self.client.post(url, {"tipo": "mesa"}, follow=True)
        self.assertContains(r, "já foram enviados")
        self.assertEqual(Pedido.objects.filter(venda=venda).count(), 1)  # sem duplicar

    def test_comanda_marca_itens_enviados_e_novos(self):
        self.regra("manual")
        venda = pdv.abrir_comanda(self.req_o, self.caixa, self.mesa)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        self.client.force_login(self.operador)
        pagina = self.client.get(reverse("pdv-comanda", args=[venda.pk]))
        self.assertContains(pagina, "Novo")
        self.assertEqual(pagina.context["novos"], 1)
        self.client.post(reverse("pedido-enviar", args=[venda.pk]), {"tipo": "mesa"})
        pagina = self.client.get(reverse("pdv-comanda", args=[venda.pk]))
        self.assertContains(pagina, "Enviado")
        self.assertEqual(pagina.context["novos"], 0)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)  # mesmo produto depois de enviado: linha NOVA, não soma na enviada
        pagina = self.client.get(reverse("pdv-comanda", args=[venda.pk]))
        self.assertEqual((pagina.context["novos"], len(pagina.context["itens"])), (1, 2))


class EventosSseTests(PedidoBase):
    def ler(self, resposta):
        return b"".join(resposta.streaming_content).decode()

    def test_stream_envia_marca_de_versao_sem_dados_de_pedido(self):
        pedido = self.pedido("manual")
        self.client.force_login(self.operador)
        with mock.patch.object(views, "DURACAO_SSE", 0):
            r = self.client.get(reverse("cozinha-api-eventos"))
            texto = self.ler(r)
        self.assertEqual(r["Content-Type"], "text/event-stream")
        self.assertEqual(r["X-Accel-Buffering"], "no")
        self.assertIn("event: fila", texto)
        self.assertNotIn(str(pedido.observacao or "x" * 5), "")  # (a marca não leva dado de pedido)
        self.assertNotIn("Bolo", texto)

    def test_marca_muda_quando_o_pedido_muda_e_so_para_a_propria_unidade(self):
        a, b = [self.cliente.pk], [self.outro.pk]
        antes_a, antes_b = s.versao_da_fila(a), s.versao_da_fila(b)
        pedido = self.pedido("manual")
        self.assertNotEqual(s.versao_da_fila(a), antes_a)
        self.assertEqual(s.versao_da_fila(b), antes_b)  # mexer na unidade A não "vaza" sinal para a B
        depois = s.versao_da_fila(a)
        s.confirmar(self.req_o, pedido)
        self.assertNotEqual(s.versao_da_fila(a), depois)

    def test_enviar_da_comanda_muda_a_marca_que_dispara_a_atualizacao_da_tela(self):
        self.regra("manual")
        venda = pdv.abrir_comanda(self.req_o, self.caixa, self.mesa)
        pdv.adicionar_item(self.req_o, venda, self.bolo.pk, 1)
        antes = s.versao_da_fila([self.cliente.pk])
        self.client.force_login(self.operador)
        self.client.post(reverse("pedido-enviar", args=[venda.pk]), {"tipo": "mesa"})
        self.assertNotEqual(s.versao_da_fila([self.cliente.pk]), antes)

    def test_exige_login_e_permissao_do_modulo(self):
        self.assertIn(self.client.get(reverse("cozinha-api-eventos")).status_code, (302, 401, 403))
        self.client.force_login(self.operador)
        habilitar = __import__("web_project.testing", fromlist=["habilitar_modulo"]).habilitar_modulo
        habilitar(self.cliente, "pedido", ativo=False)
        self.assertEqual(self.client.get(reverse("cozinha-api-eventos")).status_code, 403)
