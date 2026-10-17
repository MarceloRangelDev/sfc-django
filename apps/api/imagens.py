"""Imagens na API v1 (Cloudflare R2).

- Autorização: a entidade (insumo, produto, usuario, cliente) é resolvida NO SERVIDOR com as regras de apps/imagens/registro.py; tenant e
  permissões nunca vêm do cliente. Entidade inexistente ou de outro tenant responde igual (404), sem confirmar existência.
- Upload: multipart/form-data, campo `imagem` (JPEG/PNG/WebP, até 5 MB por padrão, 8000 px por lado). PUT é idempotente: reenviar o mesmo arquivo
  devolve a imagem atual. Os bytes passam pelo servidor (validação do conteúdo real); não há URL de upload pré-assinada.
- Leitura: `url` aponta para o próprio aplicativo (exige o token); variantes fixas: avatar, miniatura, lista, detalhe. A resposta nunca contém
  URL pré-assinada, bucket, chave do R2 ou credenciais.
- Área do PROPRIETÁRIO da plataforma (superusuário): configuração, custos e tarifas. Exige o papel na rota E no serviço.
"""
from datetime import date

from django.conf import settings
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView

from apps.imagens import armazenamento, custos, processamento, registro, servico
from apps.imagens.models import TarifaImagens
from apps.imagens.views import DADOS_TARIFA_INVALIDOS, atualizar_dados_tarifa

from .views import resposta

VARIANTES = list(processamento.VARIANTES)


class ArmazenamentoFora(APIException):
    status_code = 503
    default_detail = "O armazenamento de imagens está indisponível no momento."
    default_code = "armazenamento_indisponivel"


class Proprietario(BasePermission):
    """Papel global do proprietário da plataforma (superusuário). Administrador de tenant não passa."""

    message = "Área exclusiva do proprietário da plataforma."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


def _dados(imagem):
    return {
        "id": str(imagem.uuid), "entidade": imagem.entidade_tipo, "entidade_id": imagem.entidade_id, "mime": imagem.mime, "tamanho": imagem.tamanho,
        "largura": imagem.largura, "altura": imagem.altura, "criado_em": imagem.criado_em, "atualizado_em": imagem.atualizado_em,
        "variantes": {v: servico.url(imagem, v) for v in VARIANTES},
    }


def _tipo_valido(tipo):
    if registro.entidade(tipo) is None:
        raise Http404


@extend_schema(tags=["imagens"])
class ImagemEntidadeView(APIView):
    """Imagem atual de uma entidade: `/imagens/<tipo>/<id>/` com tipo em insumo | produto | usuario | cliente."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "imagens"

    @extend_schema(summary="Dados e URLs das variantes da imagem atual", responses={200: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, tipo, pk):
        _tipo_valido(tipo)
        if not registro.pode_ver(request.user, tipo, pk):
            raise Http404
        imagem = servico.ativa(tipo, pk)
        if imagem is None:
            raise Http404
        return resposta(_dados(imagem))

    @extend_schema(
        summary="Envia ou substitui a imagem (idempotente)", request={"multipart/form-data": inline_serializer("EnvioImagem", {"imagem": serializers.ImageField()})},
        responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT},
    )
    def put(self, request, tipo, pk):
        _tipo_valido(tipo)
        arquivo = request.FILES.get("imagem")
        if arquivo is None:
            raise ValidationError({"imagem": "Envie o arquivo no campo 'imagem'."})
        if arquivo.size > settings.IMAGENS_MAX_BYTES:
            raise ValidationError({"imagem": "A imagem excede o tamanho máximo permitido."})
        if not servico.permitir(request.user, "envio", settings.IMAGENS_LIMITE_ENVIO_MIN):
            raise APIException("Muitos envios em pouco tempo.", code="limite")
        try:
            imagem = servico.enviar(request.user, tipo, pk, arquivo.read(), request=request._request)
        except processamento.ImagemInvalida as erro:
            raise ValidationError({"imagem": str(erro)}) from None
        except armazenamento.ArmazenamentoIndisponivel:
            raise ArmazenamentoFora from None
        return resposta(_dados(imagem), "Imagem salva.")

    @extend_schema(summary="Remove a imagem atual", responses={204: None, 404: OpenApiTypes.OBJECT})
    def delete(self, request, tipo, pk):
        _tipo_valido(tipo)
        servico.remover(request.user, tipo, pk, request=request._request)
        return resposta(None, "Imagem removida.", 204)


@extend_schema(tags=["imagens-plataforma"])
class ConfiguracaoImagensView(APIView):
    """Configuração do armazenamento (SEM segredos) — somente o proprietário da plataforma."""

    permission_classes = [Proprietario]

    @extend_schema(summary="Estado da configuração de imagens", responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta({
            "r2_configurado": armazenamento.configurado(), "entrega": settings.IMAGENS_ENTREGA, "max_bytes": settings.IMAGENS_MAX_BYTES,
            "max_lado_px": settings.IMAGENS_MAX_LADO, "retencao_dias": settings.IMAGENS_RETENCAO_DIAS, "variantes": VARIANTES,
            "formatos_entrada": ["image/jpeg", "image/png", "image/webp"],
        })

    @extend_schema(summary="Testa a conexão com o R2", responses={200: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT})
    def post(self, request):
        try:
            armazenamento.obter().verificar()
        except armazenamento.ArmazenamentoIndisponivel as erro:
            raise APIException(str(erro), code="armazenamento_indisponivel") from None
        return resposta({"conexao": "ok"})


@extend_schema(tags=["imagens-plataforma"])
class CustosImagensView(APIView):
    """Estimativa de custo mensal (NÃO é a fatura da Cloudflare) — somente o proprietário da plataforma."""

    permission_classes = [Proprietario]

    @extend_schema(parameters=[OpenApiParameter("mes", str, description="AAAA-MM (padrão: mês atual)")], summary="Uso e custo estimado do mês", responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        hoje = date.today()
        try:
            ano, mes = (int(p) for p in request.query_params.get("mes", f"{hoje.year}-{hoje.month}").split("-"))
            date(ano, mes, 1)
        except ValueError:
            raise ValidationError({"mes": "Use o formato AAAA-MM."}) from None
        return resposta({**custos.relatorio(ano, mes), "aviso": "Estimativa calculada pelo sistema; não substitui a fatura da Cloudflare."})


@extend_schema(tags=["imagens-plataforma"])
class TarifasImagensView(APIView):
    """Tarifas da estimativa (editáveis, auditadas) — somente o proprietário da plataforma."""

    permission_classes = [Proprietario]
    parser_classes = [JSONParser]

    @staticmethod
    def _tarifa(t):
        return {"chave": t.chave, "preco": str(t.preco), "franquia": str(t.franquia), "moeda": t.moeda, "fonte": t.fonte, "consultado_em": t.consultado_em, "atualizado_em": t.atualizado_em}

    @extend_schema(summary="Lista as tarifas", responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta([self._tarifa(t) for t in TarifaImagens.objects.order_by("chave")])

    @extend_schema(
        summary="Atualiza uma tarifa", responses={200: OpenApiTypes.OBJECT},
        request=inline_serializer("AtualizaTarifa", {"chave": serializers.CharField(), "preco": serializers.DecimalField(max_digits=12, decimal_places=6), "franquia": serializers.DecimalField(max_digits=14, decimal_places=2), "fonte": serializers.URLField(), "consultado_em": serializers.DateField()}),
    )
    def patch(self, request):
        if not request.user.is_superuser:  # defesa também no serviço, além da permissão da rota
            raise PermissionDenied("Área exclusiva do proprietário da plataforma.")
        tarifa = TarifaImagens.objects.filter(chave=request.data.get("chave")).first()
        if tarifa is None:
            raise Http404
        try:
            atualizar_dados_tarifa(request._request, request.user, tarifa, request.data)
        except DADOS_TARIFA_INVALIDOS:
            raise ValidationError({"detail": "Dados da tarifa inválidos."}) from None
        return resposta(self._tarifa(tarifa), "Tarifa atualizada.")
