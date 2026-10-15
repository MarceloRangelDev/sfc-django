"""Fiscal na API v1: configuração (sem segredos), prontidão e documentos fiscais das vendas.

- NÃO há emissão simulada: POST cria o documento PENDENTE com o motivo do que falta (ou aguardando o provedor). `autorizada` só existe com
  chave de acesso e protocolo reais devolvidos pela autoridade fiscal.
- O certificado digital NÃO trafega pela API (cadastro só pela tela, com envio cifrado em repouso).
- Idempotente: pedir de novo o mesmo tipo para a mesma venda devolve o documento ativo.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound

from apps.fiscal import services as s
from apps.fiscal.models import ConfiguracaoFiscal, DocumentoFiscal
from apps.fiscal.views import documentos_visiveis
from apps.pdv.models import Venda
from web_project.acesso import cliente_do_usuario, clientes_dos_itens

from .pagination import PaginacaoPadrao
from .pdv import PermissaoPorAcao
from .views import resposta


def _documento_dict(d):
    return {
        "id": d.pk, "venda": d.venda_id, "tipo": d.tipo, "status": d.status, "ambiente": d.ambiente, "motivo": d.motivo, "numero": d.numero, "serie": d.serie,
        "chave_acesso": d.chave_acesso, "protocolo": d.protocolo, "autorizado_em": d.autorizado_em, "tentativas": d.tentativas, "criado_em": d.criado_em,
    }


def _config_dict(cliente):
    c = ConfiguracaoFiscal.da_unidade(cliente)
    return {
        "emite_nota_fiscal": cliente.emite_nota_fiscal, "regime_tributario": cliente.regime_tributario, "ambiente": c.ambiente, "provedor": c.provedor,
        "tipos_habilitados": c.tipos_habilitados(), "certificado": {"enviado": c.tem_certificado, "titular": c.certificado_assunto, "validade": c.certificado_validade},
        "pendencias": s.prontidao(cliente),
    }


@extend_schema(tags=["fiscal"])
class FiscalConfiguracaoViewSet(viewsets.ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "fiscal-config"
    permissoes = {"list": "fiscal.view_configuracaofiscal"}

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Configuração e prontidão fiscal da unidade (sem segredos)")
    def list(self, request):
        return resposta(_config_dict(cliente_do_usuario(request.user)))


@extend_schema(tags=["fiscal"])
class DocumentoFiscalViewSet(viewsets.ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "fiscal"
    permissoes = {"list": "fiscal.view_documentofiscal", "retrieve": "fiscal.view_documentofiscal", "create": "fiscal.emitir_nota", "cancelar": "fiscal.emitir_nota"}

    def _documento(self, request, pk):
        try:
            return documentos_visiveis(request.user).get(pk=pk)
        except DocumentoFiscal.DoesNotExist:
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("status", str), OpenApiParameter("venda", int)], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        consulta = documentos_visiveis(request.user)
        if request.query_params.get("status") in dict(DocumentoFiscal.STATUS_CHOICES):
            consulta = consulta.filter(status=request.query_params["status"])
        if request.query_params.get("venda", "").isdigit():
            consulta = consulta.filter(venda_id=int(request.query_params["venda"]))
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_documento_dict(d) for d in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_documento_dict(self._documento(request, pk)))

    @extend_schema(
        request=inline_serializer("SolicitarDocumentoFiscal", {"venda": serializers.IntegerField(), "tipo": serializers.ChoiceField(choices=["nfce", "nfe", "nfse"])}),
        responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT}, summary="Pede o documento fiscal da venda paga (fica PENDENTE; idempotente)",
    )
    def create(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        try:
            venda = Venda.objects.select_related("cliente").get(pk=int(dados.get("venda")), cliente__in=clientes_dos_itens(request.user, "view"))
        except (Venda.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"venda": ["Venda inválida."]}) from None
        documento, criado = s.solicitar_emissao(request, venda, str(dados.get("tipo") or ""))
        return resposta({**_documento_dict(documento), "criado": criado}, "Documento registrado." if criado else "Já existe documento ativo.", 201 if criado else 200)

    @extend_schema(request=inline_serializer("CancelarDocumentoFiscal", {"motivo": serializers.CharField()}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        documento = s.cancelar_documento(request, self._documento(request, pk), str(request.data.get("motivo", "")))
        return resposta(_documento_dict(documento), "Documento cancelado.")
