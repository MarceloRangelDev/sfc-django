import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.pdv.models import Venda
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.regras import RegraNegocioError

from . import services as s
from .models import Pedido

MAX_CORPO_JSON = 16 * 1024


def pedidos_visiveis(user):
    """Pedidos das unidades do escopo do usuário (a cozinha precisa ver todos os da unidade, não só os que ele criou)."""
    return Pedido.objects.filter(cliente__in=clientes_dos_itens(user, "view"))


def _pedido(request, pk):
    return get_object_or_404(pedidos_visiveis(request.user).select_related("venda", "venda__mesa", "venda__consumidor", "cliente"), pk=pk)


def pedido_dict(pedido):
    minutos = int((timezone.now() - (pedido.confirmado_em or pedido.criado_em)).total_seconds() // 60)
    venda = pedido.venda
    return {
        "id": pedido.pk, "numero": pedido.numero, "tipo": pedido.tipo, "tipo_rotulo": pedido.get_tipo_display(), "status": pedido.status, "status_rotulo": pedido.get_status_display(),
        "mesa": venda.mesa.nome if venda.mesa_id else "", "cliente": venda.consumidor.nome if venda.consumidor_id else "", "observacao": pedido.observacao,
        "minutos": minutos, "criado_em": pedido.criado_em.isoformat(),
        "itens": [{"descricao": i.descricao, "quantidade": f"{i.quantidade.normalize():f}", "observacao": i.observacao} for i in pedido.itens.all()],
        "pode_avancar": pedido.status in s.AVANCO, "pode_voltar": pedido.status in s.RETROCESSO, "pode_cancelar": pedido.ativo,
    }


class CozinhaView(View):
    def get(self, request):
        return render(request, "cozinha.html", TemplateLayout.init(self, {"ativa": "cozinha", "pode_mover": request.user.has_perm("pedido.mover_pedido")}))


class FilaJsonView(View):
    """JSON da fila de produção (a tela consulta a cada poucos segundos)."""

    def get(self, request):
        pedidos = s.fila(clientes_dos_itens(request.user, "view"))
        return JsonResponse({"pedidos": [pedido_dict(p) for p in pedidos], "agora": timezone.now().isoformat()})


class MoverJsonView(View):
    """POST JSON {"acao": "avancar" | "voltar"}: move o pedido na fila (perm `mover_pedido`). Escopo: unidades do usuário."""

    def post(self, request, pk):
        pedido = _pedido(request, pk)
        try:
            if len(request.body) > MAX_CORPO_JSON:
                raise RegraNegocioError("Requisição grande demais.")
            acao = (json.loads(request.body or b"{}") or {}).get("acao")
            if acao == "avancar":
                s.avancar(request, pedido)
            elif acao == "voltar":
                s.voltar(request, pedido)
            else:
                raise RegraNegocioError("Ação inválida.")
        except ValueError:
            return JsonResponse({"erro": "JSON inválido."}, status=422)
        except RegraNegocioError as erro:
            return JsonResponse({"erro": str(erro)}, status=422)
        pedido.refresh_from_db()
        return JsonResponse({"ok": True, "pedido": pedido_dict(pedido)})


class PedidosView(View):
    def get(self, request):
        consulta = pedidos_visiveis(request.user).select_related("venda", "venda__mesa", "venda__consumidor")
        status = request.GET.get("status")
        if status in dict(Pedido.STATUS_CHOICES):
            consulta = consulta.filter(status=status)
        termo = request.GET.get("q", "").strip().lstrip("#")
        if termo.isdigit():
            consulta = consulta.filter(numero=int(termo))
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return render(request, "pedidos.html", TemplateLayout.init(self, {"ativa": "pedidos", "pedidos": pagina, "page_obj": pagina, "status": status or "", "termo": request.GET.get("q", ""), "status_opcoes": Pedido.STATUS_CHOICES}))


class PedidoView(View):
    def get(self, request, pk):
        pedido = _pedido(request, pk)
        return render(request, "pedido.html", TemplateLayout.init(self, {
            "ativa": "pedidos", "pedido": pedido, "itens": pedido.itens.all(), "eventos": pedido.eventos.all(),
            "pode_mover": request.user.has_perm("pedido.mover_pedido"), "pode_cancelar": request.user.has_perm("pedido.cancelar_pedido"),
            "pode_avancar": pedido.status in s.AVANCO, "pode_voltar": pedido.status in s.RETROCESSO,
        }))


class PedidoAcaoView(View):
    """POST `acao` = avancar | voltar | confirmar | cancelar (+ `motivo`). Cancelar exige `cancelar_pedido`."""

    def post(self, request, pk):
        pedido = _pedido(request, pk)
        acao = request.POST.get("acao")
        try:
            if acao == "cancelar":
                if not request.user.has_perm("pedido.cancelar_pedido"):
                    raise Http404  # sem permissão: a view já exige mover_pedido; cancelar é restrito a Gestor/Administrador
                s.cancelar(request, pedido, request.POST.get("motivo", ""))
            elif acao == "avancar":
                s.avancar(request, pedido)
            elif acao == "voltar":
                s.voltar(request, pedido)
            elif acao == "confirmar":
                s.confirmar(request, pedido)
            else:
                raise RegraNegocioError("Ação inválida.")
            messages.success(request, "Pedido atualizado.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pedido-detalhe", pk=pedido.pk)


class EnviarCozinhaView(View):
    """POST (a partir da comanda): envia à cozinha os itens ainda não enviados da venda aberta/paga. Idempotente: sem itens
    pendentes, nada acontece."""

    def post(self, request, venda_id):
        venda = get_object_or_404(Venda.objects.select_related("cliente"), pk=venda_id, cliente__in=clientes_dos_itens(request.user, "view"))
        if not request.user.has_perm("pdv.cancelar_venda") and venda.criado_por_id != request.user.pk:
            raise Http404  # operador só mexe nas próprias vendas
        tipo = request.POST.get("tipo") if request.POST.get("tipo") in dict(Pedido.TIPO_CHOICES) else (Pedido.MESA if venda.mesa_id else Pedido.BALCAO)
        try:
            pedido, criado = s.criar_pedido(request, venda, tipo=tipo, observacao=request.POST.get("observacao", ""), chave=(request.POST.get("chave") or None))
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
            return redirect("pdv-comanda", pk=venda.pk)
        if pedido is None:
            messages.info(request, "Nenhum item novo para enviar à cozinha.")
        else:
            messages.success(request, f"Pedido #{pedido.numero} enviado à cozinha.")
        return redirect("pdv-comanda", pk=venda.pk)
