"""Telas do orçamento. Escopo SEMPRE da unidade do usuário (`clientes_dos_itens`): id de outra unidade = 404. A permissão de cada ação é conferida
aqui (view/add/change/delete e `enviar_orcamento`) além da rota; nada que o cliente manda (totais, status, unidade) é confiado."""
import json
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from apps.auditoria.utils import tirar_foto
from auth import ratelimit
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ListaPaginadaMixin, ListaPorUnidadeMixin
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService, ler_numero_brasileiro

from . import services as s
from .forms import LinhasFormSet, OrcamentoForm, linhas_iniciais
from .models import Orcamento, OrcamentoEnvio, OrcamentoLink
from .pdf import gerar_pdf

logger = logging.getLogger(__name__)
LIMITE_LINK_PUBLICO = (60, 60)  # 60 downloads por minuto por IP


def _orcamento(request, pk, acao="view"):
    return get_object_or_404(Orcamento.objects.select_related("cliente", "consumidor"), pk=pk, cliente__in=clientes_dos_itens(request.user, acao))


def _pagina(view, request, template, contexto):
    from django.shortcuts import render

    return render(request, template, TemplateLayout.init(view, contexto))


class OrcamentoListView(ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    model = Orcamento
    template_name = "orcamento.html"
    context_object_name = "orcamentos"
    colunas_ordenaveis = {"numero": "numero", "destinatario": "destinatario_nome", "total": "total", "criado_em": "criado_em"}
    ordenacao_padrao = "numero"
    direcao_padrao = "desc"
    status_padrao = "todos"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            filtro = Q(destinatario_nome__icontains=termo)
            if termo.isdigit():
                filtro |= Q(numero=int(termo))
            queryset = queryset.filter(filtro)
        situacao = self.request.GET.get("situacao")
        if situacao in dict(Orcamento.STATUS_CHOICES):
            queryset = queryset.filter(status=situacao)
        return self.ordenar(queryset)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(situacoes=Orcamento.STATUS_CHOICES, situacao=self.request.GET.get("situacao", ""))
        return context


class OrcamentoFormView(View):
    """Criar (pk=None) e editar. Só rascunho/enviado é editável."""

    def _render(self, request, form, formset, orcamento=None):
        return _pagina(self, request, "orcamento_form.html", {"form": form, "formset": formset, "orcamento": orcamento})

    def _formset(self, request, cliente, orcamento=None):
        usados = list(orcamento.itens.exclude(produto=None).values_list("produto_id", flat=True)) if orcamento else []
        inicial = linhas_iniciais(orcamento) if orcamento and request.method != "POST" else None
        return LinhasFormSet(request.POST if request.method == "POST" else None, initial=inicial, cliente=cliente, produto_atual_ids=usados)

    def get(self, request, pk=None):
        if pk is None:
            cliente = request.user.profile.cliente
            if cliente is None:
                raise PermissionDenied
            return self._render(request, OrcamentoForm(cliente=cliente), self._formset(request, cliente))
        orcamento = _orcamento(request, pk, "change")
        if not orcamento.editavel:
            messages.error(request, "Este orçamento está fechado e não pode ser alterado.")
            return redirect("orcamento-detalhe", pk=orcamento.pk)
        return self._render(request, OrcamentoForm(instance=orcamento, cliente=orcamento.cliente), self._formset(request, orcamento.cliente, orcamento), orcamento)

    def post(self, request, pk=None):
        orcamento = _orcamento(request, pk, "change") if pk else None
        if orcamento is not None and not orcamento.editavel:
            messages.error(request, "Este orçamento está fechado e não pode ser alterado.")
            return redirect("orcamento-detalhe", pk=orcamento.pk)
        cliente = orcamento.cliente if orcamento else request.user.profile.cliente
        if cliente is None:
            raise PermissionDenied
        foto = tirar_foto(orcamento) if orcamento else None  # ANTES do formulário alterar a instância
        form = OrcamentoForm(request.POST, instance=orcamento, cliente=cliente)
        formset = self._formset(request, cliente, orcamento)
        if form.is_valid() and formset.is_valid():
            novo = form.save(commit=False)
            if orcamento is None:
                novo.cliente, novo.criado_por = cliente, request.user  # dono e autor vêm do usuário autenticado, nunca do corpo
            try:
                itens = s.validar_linhas(cliente, formset.linhas(), orcamento)
                s.salvar_orcamento(request, novo, itens, foto_antes=foto)
            except RegraNegocioError as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, "Orçamento salvo.")
                return redirect("orcamento-detalhe", pk=novo.pk)
        return self._render(request, form, formset, orcamento)


class OrcamentoDetalheView(View):
    def get(self, request, pk):
        orcamento = _orcamento(request, pk)
        return _pagina(self, request, "orcamento_detalhe.html", {
            "orcamento": orcamento, "itens": orcamento.itens.select_related("produto"), "envios": orcamento.envios.select_related("criado_por")[:10],
            "pode_alterar": request.user.has_perm("orcamento.change_orcamento") and orcamento.editavel and orcamento.cliente_id in set(clientes_dos_itens(request.user, "change").values_list("pk", flat=True)),
            "pode_excluir": request.user.has_perm("orcamento.delete_orcamento") and orcamento.status == Orcamento.RASCUNHO,
            "pode_enviar": request.user.has_perm("orcamento.enviar_orcamento"),
        })


class OrcamentoAcaoView(View):
    """POST: aprovar | recusar | cancelar (permissão de alterar) | excluir (permissão de excluir; só rascunho nunca enviado)."""

    NOVAS = {"aprovar": Orcamento.APROVADO, "recusar": Orcamento.RECUSADO, "cancelar": Orcamento.CANCELADO}

    def post(self, request, pk, acao):
        if acao not in (*self.NOVAS, "excluir"):
            raise Http404
        permissao = "delete" if acao == "excluir" else "change"
        if not request.user.has_perm(f"orcamento.{permissao}_orcamento"):
            raise PermissionDenied
        orcamento = _orcamento(request, pk, permissao)
        try:
            if acao == "excluir":
                s.excluir_orcamento(request, orcamento)
                messages.success(request, "Orçamento excluído.")
                return redirect("orcamento-list")
            s.mudar_situacao(request, orcamento, self.NOVAS[acao])
            messages.success(request, f"Orçamento {orcamento.get_status_display().lower()}.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("orcamento-detalhe", pk=orcamento.pk)


def _resposta_pdf(orcamento, versao, usuario, baixar=False):
    nome = f"orcamento-{orcamento.numero}.pdf"
    resposta = HttpResponse(gerar_pdf(versao, usuario), content_type="application/pdf")
    resposta["Content-Disposition"] = f'{"attachment" if baixar else "inline"}; filename="{nome}"'
    resposta["Cache-Control"] = "private, no-store"
    return resposta


class OrcamentoPdfView(View):
    """GET: PDF do orçamento COMPLETO (para imprimir/baixar). Para tirar itens da versão impressa use a tela "Enviar / imprimir"."""

    def get(self, request, pk):
        orcamento = _orcamento(request, pk)
        versao = s.versao_para_envio(orcamento)
        try:
            resposta = _resposta_pdf(orcamento, versao, request.user, baixar=request.GET.get("baixar") == "1")
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gerar o PDF do orçamento %s", orcamento.pk)
            s.registrar_envio(request, orcamento, OrcamentoEnvio.PDF, OrcamentoEnvio.FALHA, detalhe="Falha ao gerar o PDF.")
            messages.error(request, "Não foi possível gerar o PDF agora. O orçamento continua salvo.")
            return redirect("orcamento-detalhe", pk=orcamento.pk)
        s.registrar_envio(request, orcamento, OrcamentoEnvio.PDF, OrcamentoEnvio.OK, detalhe="PDF gerado.")
        return resposta


class OrcamentoEnviarView(View):
    """Tela "Enviar / imprimir": o usuário desmarca itens que NÃO devem ir nesta versão e escolhe PDF, e-mail ou WhatsApp.
    Omitir vale só para a versão; remover do orçamento salvo exige marcar `persistir` E confirmar (`confirmar_persistir`)."""

    def get(self, request, pk):
        orcamento = _orcamento(request, pk)
        return self._pagina(request, orcamento)

    def _pagina(self, request, orcamento):
        return _pagina(self, request, "orcamento_enviar.html", {
            "orcamento": orcamento, "itens": orcamento.itens.all(), "canais": s.canais_disponiveis(request.user, orcamento),
            "pode_persistir": request.user.has_perm("orcamento.change_orcamento") and orcamento.editavel,
        })

    def post(self, request, pk):
        orcamento = _orcamento(request, pk)
        mantidos = {int(i) for i in request.POST.getlist("incluir") if i.isdigit()}
        omitir = [i.pk for i in orcamento.itens.all() if i.pk not in mantidos]
        acao = request.POST.get("acao", "pdf")
        if acao not in ("pdf", "email", "whatsapp"):
            raise Http404
        try:
            if omitir and request.POST.get("persistir") == "on":
                # Alteração PERMANENTE do orçamento salvo: exige a permissão de alterar (escopo de alteração) e a confirmação explícita.
                if not request.user.has_perm("orcamento.change_orcamento"):
                    raise PermissionDenied
                if request.POST.get("confirmar_persistir") != "on":
                    raise RegraNegocioError("Para remover os itens do orçamento salvo, marque também a confirmação.")
                orcamento = s.aplicar_omissao_persistente(request, _orcamento(request, pk, "change"), omitir)
                omitir = []
            if acao == "pdf":
                versao = s.versao_para_envio(orcamento, omitir)
                resposta = _resposta_pdf(orcamento, versao, request.user)
                s.registrar_envio(request, orcamento, OrcamentoEnvio.PDF, OrcamentoEnvio.OK, detalhe="PDF gerado.", omitidos=versao.omitidos)
                return resposta
            if acao == "email":
                s.enviar_por_email(request, orcamento, omitir, request.POST.get("email") or None)
                messages.success(request, "Orçamento enviado por e-mail.")
            else:
                s.enviar_por_whatsapp(request, orcamento, request.POST.get("conexao"), omitir, request.POST.get("whatsapp") or None)
                messages.success(request, "Mensagem do orçamento na fila de envio do WhatsApp (acompanhe em Integrações > Mensagens).")
            return redirect("orcamento-detalhe", pk=orcamento.pk)
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        except PermissionDenied:
            raise
        except Exception:  # noqa: BLE001 (geração do PDF)
            logger.exception("Falha ao gerar/enviar o orçamento %s", orcamento.pk)
            messages.error(request, "Não foi possível gerar o documento agora. O orçamento continua salvo.")
        return self._pagina(request, orcamento)


class OrcamentoCalcularView(View):
    """Prévia (sem gravar) dos totais enquanto o formulário é preenchido: o navegador só exibe o que o servidor devolve.
    POST JSON {"itens": [{"produto": 1, "quantidade": "2", "preco_unitario": "10,00", "desconto": "1,00"}], "desconto": "0"}."""

    def post(self, request):
        try:
            payload = json.loads(request.body or b"{}")
            linhas = payload.get("itens", [])
        except (ValueError, AttributeError):
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        if not isinstance(linhas, list) or len(linhas) > s.MAX_ITENS:
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        from apps.produto.models import Produto

        ids = {int(l["produto"]) for l in linhas if isinstance(l, dict) and str(l.get("produto", "")).isdigit()}
        produtos = {p.pk: p for p in Produto.objects.filter(pk__in=ids, cliente__in=clientes_dos_itens(request.user, "view"))}
        cliente = request.user.profile.cliente
        resultado, subtotal, desconto_itens = [], 0, 0
        for linha in linhas:
            saida = {"total": "—", "preco": "", "erro": None}
            try:
                produto = produtos.get(int(linha.get("produto") or 0))
                preco = DinheiroService.de_texto(linha["preco_unitario"]) if str(linha.get("preco_unitario", "")).strip() else (s.preco_sugerido(cliente, produto) if produto else None)
                if preco is not None and str(linha.get("quantidade", "")).strip():
                    desconto = DinheiroService.de_texto(linha["desconto"]) if str(linha.get("desconto", "")).strip() else 0
                    bruto, total = s.total_da_linha(ler_numero_brasileiro(linha["quantidade"]), preco, desconto)
                    subtotal, desconto_itens = subtotal + bruto, desconto_itens + desconto
                    saida.update(total=DinheiroService.formatar(total), preco=DinheiroService.formatar(preco))
            except (RegraNegocioError, ValueError, TypeError, AttributeError) as erro:
                saida["erro"] = str(erro) if isinstance(erro, RegraNegocioError) else "Valores inválidos."
            resultado.append(saida)
        try:
            geral = DinheiroService.de_texto(payload.get("desconto")) if str(payload.get("desconto", "")).strip() else 0
        except RegraNegocioError:
            geral = 0
        return JsonResponse({"itens": resultado, "subtotal": DinheiroService.formatar(subtotal), "total": DinheiroService.formatar(max(subtotal - desconto_itens - geral, 0))})


class PdfPublicoView(View):
    """Link do PDF enviado por WhatsApp (SEM login). A autorização é o token longo e imprevisível; vale por 7 dias, só para aquele orçamento e com
    os mesmos itens omitidos da versão enviada. Alterar/cancelar/excluir o orçamento apaga o link. Limite de downloads por IP."""

    def get(self, request, token):
        ip = ratelimit.get_client_ip(request)
        if ratelimit.check_rate_limit("orcamento_pdf", ip, *LIMITE_LINK_PUBLICO).blocked:
            return HttpResponse("Muitas requisições. Tente em instantes.", status=429)
        ratelimit.hit("orcamento_pdf", ip, LIMITE_LINK_PUBLICO[1])
        link = OrcamentoLink.objects.select_related("orcamento", "orcamento__cliente").filter(token=token, expira_em__gt=timezone.now()).first()
        if link is None or link.orcamento.status in (Orcamento.CANCELADO, Orcamento.RECUSADO):
            raise Http404
        versao = s.versao_para_envio(link.orcamento, link.itens_omitidos)
        resposta = _resposta_pdf(link.orcamento, versao, None)
        resposta["X-Robots-Tag"] = "noindex, noarchive"
        return resposta
