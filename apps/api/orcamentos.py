"""Orçamentos na API v1 (aplicativo móvel): CRUD, situação, PDF, canais de envio e envio por e-mail/WhatsApp.

As mesmas regras das telas (serviços de `apps/orcamento/services.py`): totais recalculados no servidor; escopo da unidade do usuário (outra
unidade = 404, produto de outra unidade = 422); fechado (aprovado/recusado/cancelado) não se altera; enviar exige `orcamento.enviar_orcamento`
(e, no WhatsApp, `integracao.enviar_mensagem` + conexão ativa da unidade). Omitir itens vale só para a versão enviada; alterar o orçamento
salvo exige `persistir_alteracao` E `confirmar_persistencia` explícitos. O PDF vem como `application/pdf` (fora do envelope JSON).
"""
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from apps.auditoria.utils import tirar_foto
from apps.consumidor.models import Consumidor
from apps.orcamento import services as s
from apps.orcamento.models import Orcamento, OrcamentoEnvio
from apps.orcamento.pdf import gerar_pdf
from web_project.acesso import clientes_dos_itens, cliente_do_usuario

from .pagination import PaginacaoPadrao
from .pdv import PermissaoPorAcao
from .views import resposta


def _item_dict(i):
    return {"id": i.pk, "produto": i.produto_id, "tipo": i.tipo, "descricao": i.descricao, "quantidade": str(i.quantidade), "preco_unitario": i.preco_unitario, "desconto": i.desconto, "total": i.total}


def _orcamento_dict(o, com_itens=True):
    dados = {
        "id": o.pk, "numero": o.numero, "status": o.status, "consumidor": o.consumidor_id, "destinatario_nome": o.destinatario_nome, "destinatario_email": o.destinatario_email,
        "destinatario_whatsapp": o.destinatario_whatsapp, "validade": o.validade, "observacoes": o.observacoes, "desconto": o.desconto, "subtotal": o.subtotal,
        "desconto_itens": o.desconto_itens, "total": o.total, "enviado_em": o.enviado_em, "criado_em": o.criado_em, "atualizado_em": o.atualizado_em,
    }
    if com_itens:
        dados["itens"] = [_item_dict(i) for i in o.itens.all()]
    return dados


LinhaEntrada = inline_serializer("OrcamentoLinha", {
    "produto": serializers.IntegerField(required=False, allow_null=True, help_text="Id de produto/serviço da sua unidade (omitido = linha avulsa)."),
    "tipo": serializers.ChoiceField(choices=["produto", "servico"], required=False, help_text="Só em linha avulsa."),
    "descricao": serializers.CharField(required=False, allow_blank=True, help_text="Obrigatória em linha avulsa; padrão = nome do produto."),
    "quantidade": serializers.DecimalField(max_digits=10, decimal_places=3),
    "preco_unitario": serializers.IntegerField(required=False, allow_null=True, help_text="Centavos. Omitido = preço da precificação marcada para o PDV."),
    "desconto": serializers.IntegerField(required=False, help_text="Centavos, no item."),
})
OrcamentoEntrada = inline_serializer("OrcamentoEntrada", {
    "consumidor": serializers.IntegerField(required=False, allow_null=True, help_text="Cliente final do cadastro (copia nome/contatos quando não informados)."),
    "destinatario_nome": serializers.CharField(required=False, help_text="Obrigatório se não houver `consumidor`."),
    "destinatario_email": serializers.EmailField(required=False, allow_blank=True),
    "destinatario_whatsapp": serializers.CharField(required=False, allow_blank=True),
    "validade": serializers.DateField(required=False, allow_null=True),
    "observacoes": serializers.CharField(required=False, allow_blank=True, max_length=1000),
    "desconto": serializers.IntegerField(required=False, help_text="Desconto geral, em centavos."),
    "itens": serializers.ListField(child=LinhaEntrada, required=False, help_text="Substitui TODOS os itens (obrigatório ao criar; omitido na edição = mantém)."),
})
EnvioEntrada = inline_serializer("OrcamentoEnvioEntrada", {
    "canal": serializers.ChoiceField(choices=["email", "whatsapp"]),
    "omitir_itens": serializers.ListField(child=serializers.IntegerField(), required=False, help_text="Ids de itens que NÃO vão nesta versão (o orçamento salvo não muda)."),
    "persistir_alteracao": serializers.BooleanField(required=False, help_text="Remove também do orçamento salvo os itens omitidos. Exige `confirmar_persistencia`."),
    "confirmar_persistencia": serializers.BooleanField(required=False),
    "destinatario": serializers.CharField(required=False, help_text="E-mail ou WhatsApp a usar no lugar do cadastrado no orçamento."),
    "conexao": serializers.IntegerField(required=False, help_text="WhatsApp: id da conexão ativa da unidade (ver /canais/)."),
})


@extend_schema(tags=["orcamentos"])
class OrcamentoViewSet(viewsets.ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "orcamento"
    permissoes = {
        "list": "orcamento.view_orcamento", "retrieve": "orcamento.view_orcamento", "pdf": "orcamento.view_orcamento", "canais": "orcamento.view_orcamento",
        "create": "orcamento.add_orcamento", "partial_update": "orcamento.change_orcamento", "update": "orcamento.change_orcamento",
        "aprovar": "orcamento.change_orcamento", "recusar": "orcamento.change_orcamento", "cancelar": "orcamento.change_orcamento",
        "destroy": "orcamento.delete_orcamento", "enviar": ["orcamento.view_orcamento", "orcamento.enviar_orcamento"],
    }

    def _escopo(self, request, acao="view"):
        return Orcamento.objects.filter(cliente__in=clientes_dos_itens(request.user, acao)).select_related("cliente", "consumidor")

    def _obter(self, request, pk, acao="view"):
        try:
            return self._escopo(request, acao).get(pk=pk)
        except (Orcamento.DoesNotExist, ValueError):
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("q", str, description="Número ou destinatário"), OpenApiParameter("situacao", str, enum=[c for c, _ in Orcamento.STATUS_CHOICES]),
                               OpenApiParameter("pagina", int), OpenApiParameter("por_pagina", int)], responses={200: OpenApiTypes.OBJECT}, operation_id="orcamentos_list")
    def list(self, request):
        consulta = self._escopo(request)
        termo = request.query_params.get("q", "").strip()
        if termo:
            from django.db.models import Q

            consulta = consulta.filter(Q(destinatario_nome__icontains=termo) | (Q(numero=int(termo)) if termo.isdigit() else Q(pk__in=[])))
        if request.query_params.get("situacao") in dict(Orcamento.STATUS_CHOICES):
            consulta = consulta.filter(status=request.query_params["situacao"])
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_orcamento_dict(o, com_itens=False) for o in pagina.paginate_queryset(consulta.order_by("-numero"), request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_orcamento_dict(self._obter(request, pk)))

    def _consumidor(self, cliente, valor):
        if valor in (None, ""):
            return None
        try:
            return Consumidor.objects.get(pk=int(valor), cliente=cliente, ativo=True, anonimizado_em__isnull=True)
        except (Consumidor.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"consumidor": ["Cliente final inválido."]}) from None

    def _preencher(self, orcamento, dados, cliente):
        """Copia do corpo SÓ os campos permitidos (unidade, número, status e totais nunca vêm do cliente)."""
        if "consumidor" in dados:
            orcamento.consumidor = self._consumidor(cliente, dados["consumidor"])
        for campo in ("destinatario_nome", "destinatario_email", "destinatario_whatsapp", "observacoes"):
            if campo in dados:
                setattr(orcamento, campo, str(dados[campo] or "").strip())
        if "validade" in dados:
            orcamento.validade = dados["validade"]
        if "desconto" in dados:
            try:
                orcamento.desconto = max(int(dados["desconto"] or 0), 0)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"desconto": ["Informe o desconto em centavos."]}) from None
        consumidor = orcamento.consumidor
        if consumidor is not None:
            orcamento.destinatario_nome = orcamento.destinatario_nome or consumidor.nome
            orcamento.destinatario_email = orcamento.destinatario_email or consumidor.email
            orcamento.destinatario_whatsapp = orcamento.destinatario_whatsapp or consumidor.whatsapp or consumidor.telefone
        if not orcamento.destinatario_nome:
            raise serializers.ValidationError({"destinatario_nome": ["Informe o destinatário ou o cliente final."]})

    @extend_schema(request=OrcamentoEntrada, responses={201: OpenApiTypes.OBJECT}, summary="Cria um orçamento (rascunho); os totais são calculados pelo servidor")
    def create(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        cliente = cliente_do_usuario(request.user)
        if cliente is None:
            raise PermissionDenied("Seu usuário não está vinculado a uma unidade.")
        orcamento = Orcamento(cliente=cliente, criado_por=request.user)
        self._preencher(orcamento, dados, cliente)
        itens = s.validar_linhas(cliente, dados.get("itens") or [])
        return resposta(_orcamento_dict(s.salvar_orcamento(request, orcamento, itens)), "Orçamento criado.", 201)

    @extend_schema(request=OrcamentoEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Altera (parcial): só o que for enviado; `itens` substitui todos")
    def partial_update(self, request, pk=None):
        orcamento = self._obter(request, pk, "change")
        dados = request.data if isinstance(request.data, dict) else {}
        foto = tirar_foto(orcamento)
        self._preencher(orcamento, dados, orcamento.cliente)
        if "itens" in dados:
            itens = s.validar_linhas(orcamento.cliente, dados["itens"] or [], orcamento)
        else:  # sem itens no corpo: mantém os atuais (revalida para recalcular totais com o desconto novo)
            itens = list(orcamento.itens.all())
            for item in itens:
                item.pk = None  # o serviço recria as linhas; objetos novos com os mesmos valores
        return resposta(_orcamento_dict(s.salvar_orcamento(request, orcamento, itens, foto_antes=foto)), "Orçamento atualizado.")

    update = partial_update

    @extend_schema(request=None, responses={204: None}, summary="Exclui um rascunho nunca enviado (os demais devem ser cancelados)")
    def destroy(self, request, pk=None):
        s.excluir_orcamento(request, self._obter(request, pk, "delete"))
        return Response(status=204)

    def _situacao(self, request, pk, nova):
        return resposta(_orcamento_dict(s.mudar_situacao(request, self._obter(request, pk, "change"), nova)))

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Marca como aprovado")
    @action(detail=True, methods=["post"], url_path="aprovar")
    def aprovar(self, request, pk=None):
        return self._situacao(request, pk, Orcamento.APROVADO)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Marca como recusado")
    @action(detail=True, methods=["post"], url_path="recusar")
    def recusar(self, request, pk=None):
        return self._situacao(request, pk, Orcamento.RECUSADO)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Cancela")
    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        return self._situacao(request, pk, Orcamento.CANCELADO)

    @extend_schema(parameters=[OpenApiParameter("omitir", str, description="Ids de itens a omitir desta versão, separados por vírgula (não altera o orçamento)")],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY}, summary="PDF do orçamento (application/pdf)")
    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        orcamento = self._obter(request, pk)
        omitir = [i for i in request.query_params.get("omitir", "").split(",") if i.strip().isdigit()]
        versao = s.versao_para_envio(orcamento, omitir)
        resposta_pdf = HttpResponse(gerar_pdf(versao, request.user), content_type="application/pdf")
        resposta_pdf["Content-Disposition"] = f'inline; filename="orcamento-{orcamento.numero}.pdf"'
        resposta_pdf["Cache-Control"] = "private, no-store"
        s.registrar_envio(request, orcamento, OrcamentoEnvio.PDF, OrcamentoEnvio.OK, detalhe="PDF gerado (API).", omitidos=versao.omitidos)
        return resposta_pdf

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Canais de envio disponíveis para você neste orçamento (e-mail configurado; conexões de WhatsApp ativas)")
    @action(detail=True, methods=["get"], url_path="canais")
    def canais(self, request, pk=None):
        canais = s.canais_disponiveis(request.user, self._obter(request, pk))
        return resposta({"email": canais["email"], "whatsapp": [{"id": c.pk, "nome": c.nome} for c in canais["whatsapp"]]})

    @extend_schema(request=EnvioEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Envia por e-mail ou WhatsApp (versão sem os itens omitidos)")
    @action(detail=True, methods=["post"], url_path="enviar")
    def enviar(self, request, pk=None):
        orcamento = self._obter(request, pk)
        dados = request.data if isinstance(request.data, dict) else {}
        canal = dados.get("canal")
        if canal not in ("email", "whatsapp"):
            raise s.RegraNegocioError("Escolha o canal: email ou whatsapp.")
        omitir = dados.get("omitir_itens") or []
        if not isinstance(omitir, list):
            raise s.RegraNegocioError("`omitir_itens` deve ser uma lista de ids.")
        with transaction.atomic():
            if omitir and dados.get("persistir_alteracao"):
                # Alteração PERMANENTE: exige a permissão de alterar (no escopo de alteração) e a confirmação explícita.
                if not request.user.has_perm("orcamento.change_orcamento"):
                    raise PermissionDenied
                if dados.get("confirmar_persistencia") is not True:
                    raise s.RegraNegocioError("Para remover os itens do orçamento salvo, envie também `confirmar_persistencia: true`.")
                orcamento = s.aplicar_omissao_persistente(request, self._obter(request, pk, "change"), omitir)
                omitir = []
        if canal == "email":
            s.enviar_por_email(request, orcamento, omitir, dados.get("destinatario") or None)
        else:
            s.enviar_por_whatsapp(request, orcamento, dados.get("conexao"), omitir, dados.get("destinatario") or None)
        orcamento.refresh_from_db()
        return resposta(_orcamento_dict(orcamento), "Orçamento enviado." if canal == "email" else "Mensagem na fila de envio do WhatsApp.")
