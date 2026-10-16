import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.consumidor.models import Consumidor
from apps.pdvconfig.models import ConfiguracaoPdv, FormaPagamento
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.regras import RegraNegocioError

from . import comprovante as comp
from .forms import AbrirCaixaForm, CancelarForm, ComandaForm, ConferenciaForm, DevolucaoForm, FecharCaixaForm, ItemComandaForm, MovimentoForm
from .models import Caixa, MovimentoCaixa, Venda, VendaItem
from .services import (
    abrir_caixa, abrir_comanda, adicionar_item, cancelar_venda, caixa_aberto_do, conferir_caixa, devolver_item, dividir_em_partes, fechar_caixa,
    pagar_comanda, produto_por_codigo, produtos_do_pdv, registrar_movimento, registrar_venda, remover_item, resumo_do_caixa,
)

MAX_CORPO_JSON = 64 * 1024


def _pagina(view, request, template, contexto):
    return render(request, template, TemplateLayout.init(view, contexto))


def _vendas_visiveis(user):
    """Vendas do escopo de unidades do usuário; quem não gerencia (sem cancelar_venda) só vê as que ele mesmo fez."""
    consulta = Venda.objects.filter(cliente__in=clientes_dos_itens(user, "view"))
    return consulta if user.has_perm("pdv.cancelar_venda") else consulta.filter(criado_por=user)


def _caixas_visiveis(user):
    consulta = Caixa.objects.filter(cliente__in=clientes_dos_itens(user, "view"))
    return consulta if user.has_perm("pdv.conferir_caixa") else consulta.filter(operador=user)


def _venda(request, pk):
    return get_object_or_404(_vendas_visiveis(request.user).select_related("cliente", "caixa", "consumidor", "mesa", "criado_por"), pk=pk)


def _erro(request, erro, destino):
    messages.error(request, str(erro))
    return redirect(destino)


def _config_visual(request):
    cliente = request.user.profile.cliente
    formas = list(FormaPagamento.objects.filter(cliente=cliente, ativo=True).order_by("nome")) if cliente else []
    return {
        "formas": formas, "formas_json": [{"id": f.pk, "nome": f.nome, "tipo": f.tipo} for f in formas],
        "pode_desconto": request.user.has_perm("pdv.conceder_desconto"),
    }


# --------------------------------------------------------------------------
# Vender (balcão)
# --------------------------------------------------------------------------

class VenderView(View):
    def get(self, request):
        caixa = caixa_aberto_do(request.user)
        contexto = {"caixa": caixa, "form_abrir": AbrirCaixaForm(), "ativa": "vender", **_config_visual(request)}
        if caixa is not None:
            contexto["config_pdv"] = ConfiguracaoPdv.da_unidade(caixa.cliente)
        return _pagina(self, request, "pdv.html", contexto)


class AbrirCaixaView(View):
    def post(self, request):
        form = AbrirCaixaForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Troco inicial inválido.")
            return redirect("pdv")
        try:
            abrir_caixa(request, form.cleaned_data["valor_abertura"])
            messages.success(request, "Caixa aberto. Boas vendas!")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv")


class ProdutosJsonView(View):
    """Produtos vendáveis (preço do PDV) para a busca da tela: só da unidade do caixa aberto do usuário."""

    def get(self, request):
        caixa = caixa_aberto_do(request.user)
        if caixa is None:
            return JsonResponse({"erro": "Abra o caixa."}, status=409)
        itens = [
            {"produto": p.produto_id, "nome": p.produto.nome, "codigo": p.produto.codigo_barras, "categoria": p.produto.categoria.nome, "preco": p.preco_venda, "preco_formatado": p.preco_venda_formatado}
            for p in produtos_do_pdv(caixa.cliente, request.GET.get("q", ""))
        ]
        return JsonResponse({"itens": itens})


def _json_do_corpo(request):
    if len(request.body) > MAX_CORPO_JSON:
        raise RegraNegocioError("Requisição grande demais.")
    try:
        dados = json.loads(request.body or b"{}")
    except ValueError:
        raise RegraNegocioError("JSON inválido.") from None
    if not isinstance(dados, dict):
        raise RegraNegocioError("JSON inválido.")
    return dados


def _consumidor(caixa, valor):
    if valor in (None, "", 0):
        return None
    try:
        return Consumidor.objects.get(pk=int(valor), cliente=caixa.cliente, ativo=True, anonimizado_em__isnull=True)
    except (Consumidor.DoesNotExist, TypeError, ValueError):
        raise RegraNegocioError("Consumidor inválido.") from None


def _resposta_venda(request, venda, criada):
    return JsonResponse({
        "ok": True, "criada": criada, "venda": venda.pk, "numero": venda.numero, "total": venda.total, "total_formatado": venda.total_formatado,
        "troco": venda.troco, "troco_formatado": venda.troco_formatado, "comprovante": request.build_absolute_uri(f"/pdv/vendas/{venda.pk}/comprovante"),
    }, status=201 if criada else 200)


class FinalizarJsonView(View):
    """POST JSON {itens:[{produto,quantidade}], pagamentos:[{forma,valor,valor_recebido?,pagador?}], desconto?, consumidor?, observacao?, chave}.
    Preço e ficha vêm do servidor. `chave` (uma por tentativa) torna o reenvio seguro."""

    def post(self, request):
        caixa = caixa_aberto_do(request.user)
        if caixa is None:
            return JsonResponse({"erro": "Abra o caixa."}, status=409)
        try:
            dados = _json_do_corpo(request)
            venda, criada = registrar_venda(
                request, caixa, dados.get("itens"), dados.get("pagamentos"), consumidor=_consumidor(caixa, dados.get("consumidor")), desconto=dados.get("desconto") or 0,
                observacao=str(dados.get("observacao") or ""), chave=(str(dados["chave"])[:64] if dados.get("chave") else None),
                pode_desconto=request.user.has_perm("pdv.conceder_desconto"),
            )
        except RegraNegocioError as erro:
            return JsonResponse({"erro": str(erro)}, status=422)
        return _resposta_venda(request, venda, criada)


# --------------------------------------------------------------------------
# Caixa
# --------------------------------------------------------------------------

class CaixaView(View):
    def get(self, request):
        caixa = caixa_aberto_do(request.user)
        contexto = {"ativa": "caixa", "caixa": caixa, "form_fechar": FecharCaixaForm(), "form_mov": MovimentoForm()}
        if caixa is not None:
            contexto.update(resumo=resumo_do_caixa(caixa), movimentos=caixa.movimentos.all()[:50])
        return _pagina(self, request, "pdv_caixa.html", contexto)


class FecharCaixaView(View):
    def post(self, request):
        caixa = caixa_aberto_do(request.user)
        form = FecharCaixaForm(request.POST)
        if caixa is None or not form.is_valid():
            messages.error(request, "Informe o dinheiro contado.")
            return redirect("pdv-caixa")
        try:
            caixa = fechar_caixa(request, caixa, form.cleaned_data["valor_contado"], form.cleaned_data["observacao"])
        except RegraNegocioError as erro:
            return _erro(request, erro, "pdv-caixa")
        messages.success(request, "Caixa fechado.")
        return redirect("pdv-caixa-detalhe", pk=caixa.pk)


class MovimentoView(View):
    tipo = None

    def post(self, request):
        caixa = caixa_aberto_do(request.user)
        form = MovimentoForm(request.POST)
        if caixa is None or not form.is_valid():
            messages.error(request, "Informe valor e motivo.")
            return redirect("pdv-caixa")
        try:
            registrar_movimento(request, caixa, self.tipo, form.cleaned_data["valor"], form.cleaned_data["motivo"])
            messages.success(request, f"{dict(MovimentoCaixa.TIPO_CHOICES)[self.tipo]} registrada.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv-caixa")


class CaixasView(View):
    def get(self, request):
        consulta = _caixas_visiveis(request.user).select_related("operador", "cliente")
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return _pagina(self, request, "pdv_caixas.html", {"ativa": "caixas", "caixas": pagina, "page_obj": pagina})


class CaixaDetalheView(View):
    def get(self, request, pk):
        caixa = get_object_or_404(_caixas_visiveis(request.user).select_related("operador", "cliente", "conferido_por"), pk=pk)
        return _pagina(self, request, "pdv_caixa_detalhe.html", {
            "ativa": "caixas", "caixa": caixa, "resumo": resumo_do_caixa(caixa), "movimentos": caixa.movimentos.all(), "form_conf": ConferenciaForm(),
            "vendas": caixa.vendas.exclude(status=Venda.ABERTA).select_related("consumidor")[:200],
        })


class ConferirView(View):
    def post(self, request, pk):
        caixa = get_object_or_404(_caixas_visiveis(request.user), pk=pk)
        form = ConferenciaForm(request.POST)
        try:
            conferir_caixa(request, caixa, form.cleaned_data.get("observacao", "") if form.is_valid() else "")
            messages.success(request, "Caixa conferido.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv-caixa-detalhe", pk=caixa.pk)


# --------------------------------------------------------------------------
# Comandas (mesas)
# --------------------------------------------------------------------------

class ComandasView(View):
    def get(self, request):
        caixa = caixa_aberto_do(request.user)
        abertas = _vendas_visiveis(request.user).filter(status=Venda.ABERTA).select_related("mesa", "consumidor").annotate(qtd=Count("itens"))
        return _pagina(self, request, "pdv_comandas.html", {
            "ativa": "comandas", "caixa": caixa, "comandas": abertas, "form": ComandaForm(cliente=caixa.cliente) if caixa else None,
        })


class AbrirComandaView(View):
    def post(self, request):
        caixa = caixa_aberto_do(request.user)
        if caixa is None:
            return _erro(request, "Abra o caixa para abrir comandas.", "pdv")
        form = ComandaForm(request.POST, cliente=caixa.cliente)
        if not form.is_valid():
            return _erro(request, "Escolha uma mesa livre.", "pdv-comandas")
        try:
            venda = abrir_comanda(request, caixa, form.cleaned_data["mesa"], observacao=form.cleaned_data["observacao"])
        except RegraNegocioError as erro:
            return _erro(request, erro, "pdv-comandas")
        return redirect("pdv-comanda", pk=venda.pk)


class ComandaView(View):
    def get(self, request, pk):
        venda = _venda(request, pk)
        caixa = caixa_aberto_do(request.user)
        return _pagina(self, request, "pdv_comanda.html", {
            "ativa": "comandas", "venda": venda, "itens": venda.itens.all(), "caixa": caixa, "form_item": ItemComandaForm(),
            "produtos": produtos_do_pdv(venda.cliente) if venda.status == Venda.ABERTA else [], **_config_visual(request),
            "dividir": [{"partes": n, "valor": dividir_em_partes(venda.total, n)[0]} for n in (2, 3, 4)] if venda.total >= 4 else [],
        })


class ComandaItemView(View):
    """POST: adiciona item (`produto`, `quantidade`, `observacao`) ou remove (`remover` = id do item)."""

    def post(self, request, pk):
        venda = _venda(request, pk)
        try:
            if request.POST.get("remover", "").isdigit():
                remover_item(request, venda, int(request.POST["remover"]))
            elif request.POST.get("codigo", "").strip():
                lido = produto_por_codigo(venda.cliente, request.POST["codigo"])
                if lido is None:
                    raise RegraNegocioError("Nenhum produto com este código (ou sem preço no PDV).")
                adicionar_item(request, venda, lido.produto_id, 1, "")
                messages.success(request, f"{lido.produto.nome} adicionado.")
            else:
                form = ItemComandaForm(request.POST)
                if not form.is_valid():
                    raise RegraNegocioError("Item inválido.")
                adicionar_item(request, venda, form.cleaned_data["produto"], form.cleaned_data["quantidade"], form.cleaned_data["observacao"])
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv-comanda", pk=venda.pk)


class ComandaPagarJsonView(View):
    """POST JSON {pagamentos:[...], desconto?, chave}: recebe a comanda (conta dividida = vários pagamentos)."""

    def post(self, request, pk):
        venda = _venda(request, pk)
        try:
            dados = _json_do_corpo(request)
            venda, criada = pagar_comanda(
                request, venda, dados.get("pagamentos"), dados.get("desconto") or 0, request.user.has_perm("pdv.conceder_desconto"),
                chave=(str(dados["chave"])[:64] if dados.get("chave") else None),
            )
        except RegraNegocioError as erro:
            return JsonResponse({"erro": str(erro)}, status=422)
        return _resposta_venda(request, venda, criada)


# --------------------------------------------------------------------------
# Vendas, cancelamento, devolução e comprovante
# --------------------------------------------------------------------------

class VendasView(View):
    def get(self, request):
        consulta = _vendas_visiveis(request.user).select_related("consumidor", "mesa", "caixa__operador").order_by("-criado_em", "-pk")
        status = request.GET.get("status")
        if status in dict(Venda.STATUS_CHOICES):
            consulta = consulta.filter(status=status)
        termo = request.GET.get("q", "").strip()
        if termo:
            filtro = Q(consumidor__nome__icontains=termo)
            if termo.lstrip("#").isdigit():
                filtro |= Q(numero=int(termo.lstrip("#")))
            consulta = consulta.filter(filtro)
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return _pagina(self, request, "pdv_vendas.html", {
            "ativa": "vendas", "vendas": pagina, "page_obj": pagina, "status": status or "", "termo": termo, "status_opcoes": Venda.STATUS_CHOICES,
        })


class VendaDetalheView(View):
    def get(self, request, pk):
        venda = _venda(request, pk)
        return _pagina(self, request, "pdv_venda.html", {
            "ativa": "vendas", "venda": venda, "itens": venda.itens.all(), "pagamentos": venda.pagamentos.all(), "devolucoes": venda.devolucoes.select_related("item", "forma_estorno"),
            "form_cancelar": CancelarForm(), "form_devolucao": DevolucaoForm(cliente=venda.cliente),
        })


class CancelarVendaView(View):
    def post(self, request, pk):
        venda = _venda(request, pk)
        form = CancelarForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Informe o motivo do cancelamento.")
            return redirect("pdv-venda", pk=venda.pk)
        try:
            cancelar_venda(request, venda, form.cleaned_data["motivo"])
            messages.success(request, "Venda cancelada.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv-venda", pk=venda.pk)


class DevolverView(View):
    def post(self, request, pk, item):
        venda = _venda(request, pk)
        linha = get_object_or_404(VendaItem, pk=item, venda=venda)
        form = DevolucaoForm(request.POST, cliente=venda.cliente)
        if not form.is_valid():
            messages.error(request, "Confira quantidade, forma de devolução e motivo.")
            return redirect("pdv-venda", pk=venda.pk)
        dados = form.cleaned_data
        try:
            devolver_item(request, linha, dados["quantidade"], dados["motivo"], dados["forma_estorno"], dados["repor_estoque"])
            messages.success(request, "Devolução registrada.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("pdv-venda", pk=venda.pk)


class ComprovanteView(View):
    """GET ?formato=html|pdf|escpos &tamanho=termica80|termica58|a4 (padrão da unidade)."""

    def get(self, request, pk):
        venda = _venda(request, pk)
        dados = comp.dados_comprovante(venda)
        tamanho = request.GET.get("tamanho") if request.GET.get("tamanho") in comp.FORMATOS else dados["formato_padrao"]
        formato = request.GET.get("formato", "html")
        if formato == "pdf":
            return comp.resposta_pdf(dados, tamanho)
        if formato == "escpos":
            return comp.resposta_escpos(dados, tamanho)
        if formato != "html":
            raise Http404("Formato inválido.")
        return _pagina(self, request, "pdv_comprovante.html", {"dados": dados, "venda": venda, "tamanho": tamanho, "tamanhos": [("termica80", "80 mm"), ("termica58", "58 mm"), ("a4", "A4")]})
