"""Lista de compras na API v1: itens (CRUD) e sugestões do estoque.

- Itens: validação/gravação pelo mesmo formulário da tela (um item por insumo; avulsos/serviços livres), auditados.
- Sugestões: GET calcula a quantidade (fórmula em apps/compras/services.py); POST /sugestoes/adicionar/ inclui na
  lista e é IDEMPOTENTE (repetir não duplica). O cliente informa só QUAIS insumos; a quantidade é sempre do servidor.
- A lista é sempre a da unidade do usuário autenticado.
"""
from decimal import Decimal

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action

from apps.compras.forms import ItemCompraForm
from apps.compras.models import ItemCompra
from apps.compras.services import adicionar_sugestoes, sugestoes_de_compra
from web_project.acesso import cliente_do_usuario

from .negocio import RecursoViewSet, documentar
from .permissions import PermissaoDoSistema
from .views import resposta


class ItemCompraSerializer(serializers.ModelSerializer):
    medida_sigla = serializers.CharField(source="medida.sigla", read_only=True)
    insumo_nome = serializers.CharField(source="insumo.nome", read_only=True, default=None)

    class Meta:
        model = ItemCompra
        fields = ["id", "insumo", "insumo_nome", "descricao", "quantidade", "medida", "medida_sigla", "observacao", "origem", "criado_em", "atualizado_em"]
        read_only_fields = fields


class ItemCompraEntrada(serializers.Serializer):
    insumo = serializers.IntegerField(required=False, allow_null=True, help_text="Id de um insumo ATIVO da sua empresa. Vazio = item avulso/serviço (informe `descricao`).")
    descricao = serializers.CharField(max_length=150, required=False, allow_blank=True, help_text="Texto do item avulso/serviço; ignorado quando há insumo.")
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"), help_text='Com ponto decimal (ex.: "2.5").')
    medida = serializers.IntegerField(help_text="Id da unidade (insumo: mesma dimensão da unidade do insumo).")
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


@documentar("compras", ItemCompraSerializer, ItemCompraEntrada)
class ItemCompraViewSet(RecursoViewSet):
    """Itens da lista de compras da empresa do usuário. DELETE remove o item (auditado)."""

    modelo, serializer_class, entrada_class, form_class = ItemCompra, ItemCompraSerializer, ItemCompraEntrada, ItemCompraForm
    permissao_app, permissao_model, modulo_slug = "compras", "itemcompra", "compras"
    campos_busca = ("descricao", "observacao")
    colunas_ordenaveis = {"nome": "descricao", "criado_em": "criado_em"}
    ordenacao_padrao = ["descricao", "pk"]
    rotulo = "Item"
    padroes = {}

    def consulta_base(self):
        return ItemCompra.objects.select_related("medida", "insumo")

    def filtrar(self, queryset, params):  # a lista não tem "ativo"
        termo = (params.get("q") or "").strip()
        if termo:
            from django.db.models import Q

            queryset = queryset.filter(Q(descricao__icontains=termo) | Q(observacao__icontains=termo))
        if params.get("origem") in dict(ItemCompra.ORIGEM_CHOICES):
            queryset = queryset.filter(origem=params["origem"])
        return queryset

    def dados_atuais(self, i):
        return {"insumo": i.insumo_id, "descricao": i.descricao, "quantidade": i.quantidade, "medida": i.medida_id, "observacao": i.observacao}

    def gravar(self, request, contexto, dados, instancia, foto):
        if instancia is None:
            contexto["form"].instance.origem = ItemCompra.ORIGEM_MANUAL
            contexto["form"].instance.criado_por = request.user
        return super().gravar(request, contexto, dados, instancia, foto)

    def destroy(self, request, *args, **kwargs):
        from django.db import transaction
        from rest_framework.response import Response

        from apps.auditoria.utils import registrar_exclusao

        item = self.get_object()
        with transaction.atomic():
            registrar_exclusao(request, item)
            item.delete()
        return Response(status=204)


class SugestaoSerializer(serializers.Serializer):
    insumo = serializers.IntegerField(source="insumo.pk")
    nome = serializers.CharField(source="insumo.nome")
    unidade = serializers.CharField(source="insumo.medida.sigla")
    saldo = serializers.DecimalField(max_digits=15, decimal_places=4)
    minimo = serializers.DecimalField(max_digits=15, decimal_places=4)
    fator_reposicao = serializers.DecimalField(source="fator", max_digits=6, decimal_places=2)
    multiplo_compra = serializers.DecimalField(source="multiplo", max_digits=15, decimal_places=4, allow_null=True)
    quantidade_sugerida = serializers.DecimalField(source="quantidade", max_digits=15, decimal_places=4)
    calculo = serializers.CharField()


@extend_schema(tags=["compras"])
class SugestaoCompraViewSet(viewsets.GenericViewSet):
    """Sugestões de compra do estoque (insumos abaixo do mínimo e fora da lista) e inclusão na lista."""

    serializer_class = SugestaoSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "compras", "itemcompra", "compras"
    permissoes_por_acao = {"adicionar": "add"}
    pagination_class = None

    def get_queryset(self):  # pragma: no cover - requerido pelo DRF/schema; os dados vêm de services
        return ItemCompra.objects.none()

    def list(self, request):
        cliente = cliente_do_usuario(request.user)
        return resposta(SugestaoSerializer(sugestoes_de_compra(cliente) if cliente else [], many=True).data)

    @extend_schema(
        request=inline_serializer("AdicionarSugestoes", {"insumos": serializers.ListField(child=serializers.IntegerField(), required=False, help_text="Ids dos insumos; omitido = todas as sugestões.")}),
        responses={201: ItemCompraSerializer(many=True)}, summary="Inclui sugestões na lista (idempotente)",
    )
    @action(detail=False, methods=["post"], url_path="adicionar")
    def adicionar(self, request):
        ids = request.data.get("insumos") if isinstance(request.data, dict) else None
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise serializers.ValidationError({"insumos": ["Informe uma lista de ids de insumos."]})
        cliente = cliente_do_usuario(request.user)
        criados = adicionar_sugestoes(request, cliente, ids)
        saida = ItemCompraSerializer(ItemCompra.objects.select_related("medida", "insumo").filter(pk__in=[i.pk for i in criados]), many=True).data
        return resposta(saida, f"{len(criados)} item(ns) incluído(s) na lista.", 201 if criados else 200)
