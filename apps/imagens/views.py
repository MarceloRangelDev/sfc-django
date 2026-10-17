"""Envio, remoção e ENTREGA autorizada das imagens + painel de custos do PROPRIETÁRIO da plataforma.

A permissão é sempre decidida no servidor (registro.py / servico.py); esconder botões no HTML não é controle de acesso.
Respostas de "não pode" e "não existe" são idênticas (404) para não revelar imagens de outros tenants.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from apps.auditoria.models import ACAO_EDITAR
from apps.auditoria.utils import registrar
from web_project import TemplateLayout

from . import armazenamento, custos, processamento, registro, servico
from .models import Imagem, TarifaImagens


def somente_proprietario(view):
    """Papel GLOBAL do proprietário da plataforma = superusuário. Administrador de tenant/grupo "Administrador" NÃO passa."""

    @login_required
    def protegida(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Área exclusiva do proprietário da plataforma.")
        return view(request, *args, **kwargs)

    return protegida


def _erro(texto, status):
    return JsonResponse({"ok": False, "erro": texto}, status=status)


@login_required
@require_POST
def enviar(request, tipo, pk):
    if not servico.permitir(request.user, "envio", settings.IMAGENS_LIMITE_ENVIO_MIN):
        return _erro("Muitos envios em pouco tempo. Aguarde um minuto.", 429)
    arquivo = request.FILES.get("imagem")
    if arquivo is None:
        return _erro("Selecione uma imagem.", 400)
    if arquivo.size > settings.IMAGENS_MAX_BYTES:  # barra antes de ler tudo para a memória
        return _erro(f"A imagem excede o limite de {settings.IMAGENS_MAX_BYTES // (1024 * 1024)} MB.", 400)
    try:
        imagem = servico.enviar(request.user, tipo, pk, arquivo.read(), request=request)
    except processamento.ImagemInvalida as erro:
        return _erro(str(erro), 400)
    except armazenamento.ArmazenamentoIndisponivel as erro:
        return _erro(str(erro), 503)
    except Http404:
        return _erro("Registro não encontrado.", 404)
    return JsonResponse({"ok": True, "url": servico.url(imagem, "lista"), "largura": imagem.largura, "altura": imagem.altura})


@login_required
@require_POST
def excluir(request, tipo, pk):
    try:
        servico.remover(request.user, tipo, pk, request=request)
    except Http404:
        return _erro("Imagem não encontrada.", 404)
    return JsonResponse({"ok": True})


@login_required
@require_GET
def entrega(request, uuid, variante):
    """Lê a imagem: autoriza (tenant + permissão) e entrega a variante pedida (somente variantes fixas)."""
    if variante not in processamento.VARIANTES:
        raise Http404
    if not servico.permitir(request.user, "leitura", settings.IMAGENS_LIMITE_LEITURA_MIN):
        return HttpResponse("Muitas requisições.", status=429)
    imagem = Imagem.objects.filter(uuid=uuid, estado=Imagem.ATIVA).first()
    if imagem is None or not registro.pode_ver(request.user, imagem.entidade_tipo, imagem.entidade_id):
        raise Http404
    try:
        if servico.entrega_cloudflare(imagem):
            base = request.build_absolute_uri("/").rstrip("/")
            resposta = HttpResponseRedirect(servico.url_cloudflare(imagem, variante, base))
            resposta["Cache-Control"] = "private, max-age=300"
            return resposta
        dados, mime = servico.variante_bytes(imagem, variante, request.headers.get("Accept", ""))
    except armazenamento.ArmazenamentoIndisponivel:
        return HttpResponse("Imagem temporariamente indisponível.", status=503)
    resposta = HttpResponse(dados, content_type=mime)
    # Privado: só o navegador do usuário guarda. A URL muda (uuid novo) quando a imagem é trocada; excluída, passa a dar 404.
    resposta["Cache-Control"] = "private, max-age=86400"
    resposta["Vary"] = "Accept, Cookie"
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta


@require_GET
def origem(request, token):
    """Origem das Image Transformations da Cloudflare: devolve o ORIGINAL de uma imagem de CATÁLOGO, só com token assinado válido.
    Não é um proxy aberto: o token identifica uma única imagem e não aceita URLs externas."""
    try:
        imagem, dados = servico.original_por_token(token)
    except armazenamento.ArmazenamentoIndisponivel:
        return HttpResponse(status=503)
    resposta = HttpResponse(dados, content_type=imagem.mime)
    resposta["Cache-Control"] = "public, max-age=86400"
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta


# --------------------------------------------------------------------------
# Painel do proprietário
# --------------------------------------------------------------------------

class PainelView(TemplateView):
    template_name = "imagens/painel.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        hoje = date.today()
        try:
            ano, mes = (int(p) for p in self.request.GET.get("mes", f"{hoje.year}-{hoje.month}").split("-"))
            date(ano, mes, 1)
        except ValueError:
            ano, mes = hoje.year, hoje.month
        context.update({
            "r": custos.relatorio(ano, mes), "tarifas": TarifaImagens.objects.order_by("chave"), "mes": f"{ano:04d}-{mes:02d}",
            "entrega": settings.IMAGENS_ENTREGA, "configurado": armazenamento.configurado(),
        })
        return context


painel = somente_proprietario(PainelView.as_view())


DADOS_TARIFA_INVALIDOS = (KeyError, ValueError, TypeError, InvalidOperation, ValidationError)


def atualizar_dados_tarifa(request, usuario, tarifa, dados):
    """Altera a tarifa e audita (antes/depois, autor). Compartilhado pela tela e pela API; só o proprietário chega aqui."""
    antes = f"preço {tarifa.preco}, franquia {tarifa.franquia}, fonte {tarifa.fonte}, consulta {tarifa.consultado_em}"
    tarifa.preco = Decimal(str(dados["preco"]))
    tarifa.franquia = Decimal(str(dados["franquia"]))
    tarifa.fonte = str(dados["fonte"]).strip()
    tarifa.consultado_em = date.fromisoformat(str(dados["consultado_em"]))
    tarifa.atualizado_por = usuario
    tarifa.full_clean()
    if tarifa.preco < 0 or tarifa.franquia < 0:
        raise ValueError("valores negativos")
    with transaction.atomic():
        tarifa.save()
        registrar(
            request, ACAO_EDITAR, modulo="imagens", registro_id=tarifa.pk, tabela="imagens_tarifaimagens", usuario=usuario, registro_nome=tarifa.get_chave_display(),
            descricao=f"Tarifa atualizada. Antes: {antes}. Depois: preço {tarifa.preco}, franquia {tarifa.franquia}, fonte {tarifa.fonte}, consulta {tarifa.consultado_em}.",
            silencioso=False,
        )


@somente_proprietario
@require_POST
def atualizar_tarifa(request, chave):
    tarifa = TarifaImagens.objects.filter(chave=chave).first()
    if tarifa is None:
        raise Http404
    try:
        atualizar_dados_tarifa(request, request.user, tarifa, request.POST)
        messages.success(request, "Tarifa atualizada.")
    except DADOS_TARIFA_INVALIDOS:
        messages.error(request, "Dados da tarifa inválidos. Confira preço, franquia, fonte (URL) e data.")
    return redirect("imagens-painel")


@somente_proprietario
@require_POST
def verificar(request):
    """Teste administrativo da conexão com o R2 (HeadBucket). As mensagens nunca incluem credenciais."""
    try:
        armazenamento.obter().verificar()
        messages.success(request, "Conexão com o R2 verificada (bucket acessível).")
    except armazenamento.ArmazenamentoIndisponivel as erro:
        messages.error(request, str(erro))
    return redirect("imagens-painel")
