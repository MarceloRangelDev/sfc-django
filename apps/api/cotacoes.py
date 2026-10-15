"""Cotações na API v1.

A API NÃO reimplementa regra: usa os mesmos formulários, serviços (apps/cotacao/services.py) e auditoria das telas.
Estrutura (tudo sob /cotacoes/{id}/):
  itens/, itens/{item}/        lojas/, lojas/{loja}/        precos/, precos/{preco}/
  importar-lista/              escolher/ (vencedor)         limpar-escolha/
  status/ (enviar|fechar|reabrir|cancelar)                   gerar-contas/ (contas a pagar dos vencedores)
  comparativo/                 exportar/?formato=pdf|xlsx

Dinheiro em CENTAVOS inteiros; quantidades como texto decimal com ponto. A cotação é sempre da unidade do usuário
autenticado (outra empresa = 404). Leitura exige `view_cotacao`; qualquer escrita nas sub-rotas exige `change_cotacao`
(gerar contas exige ainda `financeiro.add_contapagar` e o módulo de contas a pagar).
"""
from decimal import Decimal

from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.cotacao.exports import exportar_cotacao
from apps.cotacao.forms import CotacaoForm, ItemForm, LojaForm, PrecoForm
from apps.cotacao.models import Cotacao, CotacaoItem, CotacaoLoja, CotacaoPreco
from apps.cotacao.services import (
    comparativo, escolher_preco, excluir_cotacao, excluir_filho, exigir_editavel, gerar_contas_a_pagar, importar_da_lista, limpar_escolha,
    mudar_status, resumo_escolhidos, salvar_filho,
)
from web_project.decorators import modulo_habilitado_para

from .negocio import RecursoViewSet, documentar, erros_de, para_formulario
from .views import resposta

MOEDAS = ("preco_unitario", "preco_atacado")
QUANTIDADE = dict(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"))


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------

class CotacaoListaSerializer(serializers.ModelSerializer):
    status_rotulo = serializers.CharField(source="get_status_display", read_only=True)
    qtd_itens = serializers.IntegerField(read_only=True, default=None)
    qtd_lojas = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Cotacao
        fields = ["id", "titulo", "status", "status_rotulo", "qtd_itens", "qtd_lojas", "criado_em", "atualizado_em"]
        read_only_fields = fields


def _oferta(o):
    preco = o["preco"]
    return {
        "id": preco.pk, "loja": preco.loja_id, "loja_nome": preco.loja.nome, "marca": preco.marca, "medida": preco.medida_id,
        "medida_sigla": preco.medida.sigla, "quantidade": f"{preco.quantidade:.4f}", "preco_unitario": preco.preco_unitario,
        "preco_atacado": preco.preco_atacado, "preco_e_atacado": preco.preco_e_atacado,
        "qtd_minima_atacado": None if preco.qtd_minima_atacado is None else f"{preco.qtd_minima_atacado:.4f}",
        "observacao": preco.observacao, "escolhido": preco.escolhido,
        # avaliação (regra em apps/cotacao/services.py)
        "tipo_aplicado": o["tipo"], "preco_aplicado": int(o["valor_unitario"]), "total": o["total"], "total_formatado": o["total_formatado"],
        "preco_por_unidade_padrao": o["por_base_centavos"], "unidade_padrao": o["unidade_base"], "atende": o["atende"], "motivo": o["motivo"],
        "menor_preco": o["melhor"],
    }


def serializar_comparativo(cotacao):
    dados = comparativo(cotacao)
    itens = []
    for bloco in dados:
        item = bloco["item"]
        itens.append({
            "id": item.pk, "insumo": item.insumo_id, "descricao": item.descricao, "quantidade": f"{item.quantidade:.4f}", "medida": item.medida_id,
            "medida_sigla": item.medida.sigla, "servico": item.servico, "observacao": item.observacao,
            "ofertas": [_oferta(o) for o in bloco["ofertas"]],
        })
    blocos, total = resumo_escolhidos(dados)
    resumo = [{"loja": b["loja"].pk, "loja_nome": b["loja"].nome, "itens": [l["item"].pk for l in b["linhas"]], "total": b["total"], "total_formatado": b["total_formatado"],
               "conta_pagar": b["loja"].conta_pagar_id} for b in blocos]
    return {"itens": itens, "vencedores_por_loja": resumo, "total_vencedores": total}


class CotacaoLojaSerializer(serializers.ModelSerializer):
    conta_pagar = serializers.IntegerField(source="conta_pagar_id", read_only=True, help_text="Id da conta a pagar gerada para esta loja (se houver).")

    class Meta:
        model = CotacaoLoja
        fields = ["id", "nome", "endereco", "telefone", "email", "conta_pagar", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CotacaoItemSerializer(serializers.ModelSerializer):
    medida_sigla = serializers.CharField(source="medida.sigla", read_only=True)

    class Meta:
        model = CotacaoItem
        fields = ["id", "insumo", "descricao", "quantidade", "medida", "medida_sigla", "servico", "observacao"]
        read_only_fields = fields


class CotacaoDetalheSerializer(serializers.ModelSerializer):
    status_rotulo = serializers.CharField(source="get_status_display", read_only=True)
    lojas = CotacaoLojaSerializer(many=True, read_only=True)
    comparativo = serializers.SerializerMethodField()

    class Meta:
        model = Cotacao
        fields = ["id", "titulo", "observacoes", "status", "status_rotulo", "enviada_em", "fechada_em", "lojas", "comparativo", "criado_em", "atualizado_em"]
        read_only_fields = fields

    def get_comparativo(self, cotacao) -> dict:
        return serializar_comparativo(cotacao)


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------

class CotacaoEntrada(serializers.Serializer):
    titulo = serializers.CharField(max_length=150)
    observacoes = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    importar_lista = serializers.BooleanField(required=False, help_text="Só na criação: copia todos os itens da lista de compras.")


class CotacaoLojaEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    endereco = serializers.CharField(max_length=200, required=False, allow_blank=True)
    telefone = serializers.CharField(max_length=20, required=False, allow_blank=True, help_text="DDD + número (10 ou 11 dígitos).")
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)


class CotacaoItemEntrada(serializers.Serializer):
    insumo = serializers.IntegerField(required=False, allow_null=True, help_text="Id de um insumo ATIVO da sua empresa; vazio = item avulso/serviço.")
    descricao = serializers.CharField(max_length=150, required=False, allow_blank=True)
    quantidade = serializers.DecimalField(**QUANTIDADE)
    medida = serializers.IntegerField()
    servico = serializers.BooleanField(required=False)
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


class CotacaoPrecoEntrada(serializers.Serializer):
    item = serializers.IntegerField(required=False, help_text="Id do item (obrigatório na criação).")
    loja = serializers.IntegerField(help_text="Id de uma loja DESTA cotação.")
    marca = serializers.CharField(max_length=60, required=False, allow_blank=True, help_text="Obrigatória, exceto para itens de serviço.")
    medida = serializers.IntegerField(help_text="Mesma dimensão da unidade do item.")
    quantidade = serializers.DecimalField(**QUANTIDADE)
    preco_unitario = serializers.IntegerField(min_value=1, help_text="Centavos por unidade de medida.")
    preco_atacado = serializers.IntegerField(min_value=1, required=False, allow_null=True, help_text="Centavos por unidade, válido a partir de qtd_minima_atacado.")
    preco_e_atacado = serializers.BooleanField(required=False, help_text="O preço unitário informado já é de atacado (deixe preco_atacado vazio).")
    qtd_minima_atacado = serializers.DecimalField(required=False, allow_null=True, **QUANTIDADE)
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


def inline_ids(nome, campo, tipo=None, extra=None):
    """Serializer de entrada mínimo só para documentar o Swagger."""
    from drf_spectacular.utils import inline_serializer

    return inline_serializer(nome, {campo: tipo or serializers.IntegerField(), **(extra or {})})


# --------------------------------------------------------------------------
# ViewSet
# --------------------------------------------------------------------------

@documentar("cotacoes", CotacaoDetalheSerializer, CotacaoEntrada, [OpenApiParameter("status", str)])
class CotacaoViewSet(RecursoViewSet):
    """Cotações da empresa do usuário. A lista traz resumo; o detalhe traz lojas, itens, ofertas avaliadas e vencedores.
    DELETE exclui a cotação (recusado se houver conta a pagar gerada)."""

    modelo, serializer_class, entrada_class, form_class = Cotacao, CotacaoDetalheSerializer, CotacaoEntrada, CotacaoForm
    permissao_app, permissao_model, modulo_slug = "cotacao", "cotacao", "cotacao"
    campos_busca = ("titulo",)
    colunas_ordenaveis = {"titulo": "titulo", "criado_em": "criado_em", "status": "status"}
    ordenacao_padrao = ["-criado_em", "-pk"]
    rotulo = "Cotação"
    padroes = {}
    permissoes_por_acao = {
        "itens": "view", "item": "view", "lojas": "view", "loja": "view", "precos": "view", "preco": "view", "comparativo": "view", "exportar": "view",
        "importar_lista": "change", "escolher": "change", "limpar_escolha": "change", "mudar_status": "change", "gerar_contas": "change",
    }

    # --- listagem / detalhe ------------------------------------------------------------
    def consulta_base(self):
        return Cotacao.objects.select_related("cliente").prefetch_related("lojas")

    def filtrar(self, queryset, params):
        termo = (params.get("q") or "").strip()
        if termo:
            queryset = queryset.filter(titulo__icontains=termo)
        if params.get("status") in dict(Cotacao.STATUS_CHOICES):
            queryset = queryset.filter(status=params["status"])
        return queryset

    def get_serializer_class(self):
        if self.action == "list":
            return CotacaoListaSerializer
        return super().get_serializer_class()

    def list(self, request, *args, **kwargs):
        from django.db.models import Count

        queryset = self.get_queryset().annotate(qtd_itens=Count("itens", distinct=True), qtd_lojas=Count("lojas", distinct=True))
        pagina = self.paginate_queryset(queryset)
        return self.get_paginated_response(CotacaoListaSerializer(pagina, many=True).data)

    # --- escrita da cotação --------------------------------------------------------------
    def dados_atuais(self, c):
        return {"titulo": c.titulo, "observacoes": c.observacoes}

    def preparar(self, dados, instancia):
        if instancia is not None:
            exigir_editavel(instancia)
        return super().preparar(dados, instancia)

    def gravar(self, request, contexto, dados, instancia, foto):
        from django.db import transaction

        with transaction.atomic():
            if instancia is None:
                contexto["form"].instance.criado_por = request.user
            cotacao = super().gravar(request, contexto, dados, instancia, foto)
            if instancia is None and dados.get("importar_lista"):
                importar_da_lista(request, cotacao)
        return cotacao

    def destroy(self, request, *args, **kwargs):
        excluir_cotacao(request, self.get_object())
        return Response(status=204)

    # --- auxiliares ------------------------------------------------------------------------
    def _exigir(self, tipo):
        if not self.request.user.has_perm(f"cotacao.{tipo}_cotacao"):
            raise PermissionDenied("Você não tem permissão para esta operação.")

    def _filho(self, modelo, cotacao, pk, campo="cotacao"):
        try:
            return modelo.objects.get(**{"pk": pk, campo: cotacao})
        except modelo.DoesNotExist:
            raise Http404 from None

    def _gravar_filho(self, form_class, kwargs_form, instancia, entrada_class, dados_atuais, partial, moedas=()):
        entrada = entrada_class(data=self.request.data, partial=partial)
        entrada.is_valid(raise_exception=True)
        dados = {**(dados_atuais or {}), **entrada.validated_data}
        form = form_class(para_formulario(dados, moedas), instance=instancia, **kwargs_form)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        return salvar_filho(self.request, form)

    # --- itens -------------------------------------------------------------------------------
    @extend_schema(request=CotacaoItemEntrada, responses={200: CotacaoItemSerializer(many=True), 201: CotacaoItemSerializer}, summary="Lista (GET) ou cria (POST) itens da cotação")
    @action(detail=True, methods=["get", "post"], url_path="itens")
    def itens(self, request, pk=None):
        cotacao = self.get_object()
        if request.method == "GET":
            return resposta(CotacaoItemSerializer(cotacao.itens.select_related("medida"), many=True).data)
        self._exigir("change")
        item = self._gravar_filho(ItemForm, {"cotacao": cotacao}, None, CotacaoItemEntrada, None, False)
        return resposta(CotacaoItemSerializer(item).data, "Item incluído.", 201)

    @extend_schema(request=CotacaoItemEntrada, responses={200: CotacaoItemSerializer, 204: None}, summary="Atualiza (PATCH) ou remove (DELETE) um item")
    @action(detail=True, methods=["patch", "delete"], url_path=r"itens/(?P<item_id>[0-9]+)")
    def item(self, request, pk=None, item_id=None):
        self._exigir("change")
        cotacao = self.get_object()
        item = self._filho(CotacaoItem, cotacao, item_id)
        if request.method == "DELETE":
            excluir_filho(request, item)
            return Response(status=204)
        atuais = {"insumo": item.insumo_id, "descricao": item.descricao, "quantidade": item.quantidade, "medida": item.medida_id, "servico": item.servico, "observacao": item.observacao}
        item = self._gravar_filho(ItemForm, {"cotacao": cotacao}, item, CotacaoItemEntrada, atuais, True)
        return resposta(CotacaoItemSerializer(item).data, "Item atualizado.")

    @extend_schema(request=None, responses={201: CotacaoItemSerializer(many=True)}, summary="Copia itens da lista de compras (idempotente)")
    @action(detail=True, methods=["post"], url_path="importar-lista")
    def importar_lista(self, request, pk=None):
        cotacao = self.get_object()
        ids = request.data.get("itens") if isinstance(request.data, dict) else None
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
            raise serializers.ValidationError({"itens": ["Informe uma lista de ids de itens da lista de compras."]})
        criados = importar_da_lista(request, cotacao, ids)
        return resposta(CotacaoItemSerializer(criados, many=True).data, f"{len(criados)} item(ns) copiado(s).", 201 if criados else 200)

    # --- lojas -------------------------------------------------------------------------------
    @extend_schema(request=CotacaoLojaEntrada, responses={200: CotacaoLojaSerializer(many=True), 201: CotacaoLojaSerializer}, summary="Lista (GET) ou cria (POST) lojas da cotação")
    @action(detail=True, methods=["get", "post"], url_path="lojas")
    def lojas(self, request, pk=None):
        cotacao = self.get_object()
        if request.method == "GET":
            return resposta(CotacaoLojaSerializer(cotacao.lojas.all(), many=True).data)
        self._exigir("change")
        loja = self._gravar_filho(LojaForm, {"cotacao": cotacao}, None, CotacaoLojaEntrada, None, False)
        return resposta(CotacaoLojaSerializer(loja).data, "Loja incluída.", 201)

    @extend_schema(request=CotacaoLojaEntrada, responses={200: CotacaoLojaSerializer, 204: None}, summary="Atualiza (PATCH) ou remove (DELETE) uma loja")
    @action(detail=True, methods=["patch", "delete"], url_path=r"lojas/(?P<loja_id>[0-9]+)")
    def loja(self, request, pk=None, loja_id=None):
        self._exigir("change")
        cotacao = self.get_object()
        loja = self._filho(CotacaoLoja, cotacao, loja_id)
        if request.method == "DELETE":
            excluir_filho(request, loja)
            return Response(status=204)
        atuais = {"nome": loja.nome, "endereco": loja.endereco, "telefone": loja.telefone, "email": loja.email}
        loja = self._gravar_filho(LojaForm, {"cotacao": cotacao}, loja, CotacaoLojaEntrada, atuais, True)
        return resposta(CotacaoLojaSerializer(loja).data, "Loja atualizada.")

    # --- preços ------------------------------------------------------------------------------
    @extend_schema(request=CotacaoPrecoEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Lista (GET) ou cria (POST) preços/ofertas das lojas")
    @action(detail=True, methods=["get", "post"], url_path="precos")
    def precos(self, request, pk=None):
        cotacao = self.get_object()
        if request.method == "GET":
            return resposta([o for item in serializar_comparativo(cotacao)["itens"] for o in item["ofertas"]])
        self._exigir("change")
        item_id = request.data.get("item") if isinstance(request.data, dict) else None
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise serializers.ValidationError({"item": ["Informe o id do item."]})
        item = self._filho(CotacaoItem, cotacao, item_id)
        preco = self._gravar_filho(PrecoForm, {"item": item}, None, CotacaoPrecoEntrada, None, False, MOEDAS)
        return resposta(self._oferta_por_id(cotacao, preco.pk), "Preço incluído.", 201)

    def _oferta_por_id(self, cotacao, preco_id):
        for item in serializar_comparativo(cotacao)["itens"]:
            for oferta in item["ofertas"]:
                if oferta["id"] == preco_id:
                    return oferta
        raise Http404

    @extend_schema(request=CotacaoPrecoEntrada, responses={200: OpenApiTypes.OBJECT, 204: None}, summary="Atualiza (PATCH) ou remove (DELETE) um preço")
    @action(detail=True, methods=["patch", "delete"], url_path=r"precos/(?P<preco_id>[0-9]+)")
    def preco(self, request, pk=None, preco_id=None):
        self._exigir("change")
        cotacao = self.get_object()
        try:
            preco = CotacaoPreco.objects.select_related("item", "loja", "medida").get(pk=preco_id, item__cotacao=cotacao)
        except CotacaoPreco.DoesNotExist:
            raise Http404 from None
        if request.method == "DELETE":
            excluir_filho(request, preco)
            return Response(status=204)
        atuais = {
            "loja": preco.loja_id, "marca": preco.marca, "medida": preco.medida_id, "quantidade": preco.quantidade, "preco_unitario": preco.preco_unitario,
            "preco_atacado": preco.preco_atacado, "preco_e_atacado": preco.preco_e_atacado, "qtd_minima_atacado": preco.qtd_minima_atacado, "observacao": preco.observacao,
        }
        preco = self._gravar_filho(PrecoForm, {"item": preco.item}, preco, CotacaoPrecoEntrada, atuais, True, MOEDAS)
        return resposta(self._oferta_por_id(cotacao, preco.pk), "Preço atualizado.")

    # --- vencedor, status, contas, comparativo, exportação ----------------------------------
    @extend_schema(request=inline_ids("EscolherPreco", "preco"), responses={200: OpenApiTypes.OBJECT}, summary="Escolhe o vencedor do item")
    @action(detail=True, methods=["post"], url_path="escolher")
    def escolher(self, request, pk=None):
        cotacao = self.get_object()
        preco_id = request.data.get("preco") if isinstance(request.data, dict) else None
        if not isinstance(preco_id, int) or isinstance(preco_id, bool):
            raise serializers.ValidationError({"preco": ["Informe o id do preço."]})
        try:
            preco = CotacaoPreco.objects.select_related("item", "loja", "medida").get(pk=preco_id, item__cotacao=cotacao)
        except CotacaoPreco.DoesNotExist:
            raise Http404 from None
        escolher_preco(request, preco)
        return resposta(self._oferta_por_id(cotacao, preco.pk), "Vencedor escolhido.")

    @extend_schema(request=inline_ids("LimparEscolha", "item"), responses={200: OpenApiTypes.OBJECT}, summary="Remove o vencedor do item")
    @action(detail=True, methods=["post"], url_path="limpar-escolha")
    def limpar_escolha(self, request, pk=None):
        cotacao = self.get_object()
        item_id = request.data.get("item") if isinstance(request.data, dict) else None
        if not isinstance(item_id, int) or isinstance(item_id, bool):
            raise serializers.ValidationError({"item": ["Informe o id do item."]})
        limpar_escolha(request, self._filho(CotacaoItem.objects.select_related("cotacao"), cotacao, item_id))
        return resposta(serializar_comparativo(cotacao), "Vencedor removido.")

    @extend_schema(request=inline_ids("MudarStatus", "acao", serializers.ChoiceField(choices=["enviar", "fechar", "reabrir", "cancelar"])), responses={200: CotacaoDetalheSerializer}, summary="Muda o status da cotação")
    @action(detail=True, methods=["post"], url_path="status")
    def mudar_status(self, request, pk=None):
        cotacao = mudar_status(request, self.get_object(), (request.data.get("acao") if isinstance(request.data, dict) else "") or "")
        return resposta(CotacaoDetalheSerializer(cotacao).data, "Status atualizado.")

    @extend_schema(request=inline_ids("GerarContas", "categoria", serializers.IntegerField(), extra={"vencimento": serializers.DateField()}), responses={201: OpenApiTypes.OBJECT},
                   summary="Gera as contas a pagar dos vencedores (uma por loja; idempotente)")
    @action(detail=True, methods=["post"], url_path="gerar-contas")
    def gerar_contas(self, request, pk=None):
        if not (request.user.has_perm("financeiro.add_contapagar") and modulo_habilitado_para(request.user, "financeiro-pagar")):
            raise PermissionDenied("Você não tem permissão para gerar contas a pagar.")
        cotacao = self.get_object()
        categoria, vencimento = request.data.get("categoria"), request.data.get("vencimento")
        erros = {}
        if not isinstance(categoria, int) or isinstance(categoria, bool):
            erros["categoria"] = ["Informe o id da categoria de despesa."]
        data = serializers.DateField().to_internal_value(vencimento) if vencimento else None
        if data is None:
            erros["vencimento"] = ["Informe o vencimento (AAAA-MM-DD)."]
        if erros:
            raise serializers.ValidationError(erros)
        contas = gerar_contas_a_pagar(request, cotacao, categoria, data)
        saida = [{"id": c.pk, "contraparte": c.contraparte, "valor": c.valor, "valor_formatado": c.valor_formatado, "vencimento": c.vencimento} for c in contas]
        return resposta(saida, f"{len(contas)} conta(s) a pagar gerada(s).", 201 if contas else 200)

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Comparativo: ofertas avaliadas por item e vencedores por loja")
    @action(detail=True, methods=["get"], url_path="comparativo")
    def comparativo(self, request, pk=None):
        return resposta(serializar_comparativo(self.get_object()))

    @extend_schema(responses={200: OpenApiTypes.BINARY}, parameters=[OpenApiParameter("formato", str, description="pdf (padrão) ou xlsx")], summary="Exporta a cotação em PDF ou Excel")
    @action(detail=True, methods=["get"], url_path="exportar")
    def exportar(self, request, pk=None):
        formato = request.query_params.get("formato", "pdf")
        if formato not in ("pdf", "xlsx"):
            raise serializers.ValidationError({"formato": ["Use pdf ou xlsx."]})
        return exportar_cotacao(request._request, self.get_object(), formato)

