"""PDV na API v1 (aplicativo móvel): formas de pagamento, mesas, caixa, vendas, comandas e comprovante.

A API usa os MESMOS serviços das telas (apps/pdv/services.py): preço e ficha vêm do cadastro (o app só envia produto e
quantidade), estoque/financeiro/auditoria são transacionais e a venda é IDEMPOTENTE: envie a chave em `chave` (corpo) ou
no cabeçalho `Idempotency-Key`; repetir a mesma chave devolve a MESMA venda (HTTP 200, `criada: false`) sem duplicar.
Dinheiro em CENTAVOS inteiros; quantidades como número/texto decimal com ponto. Tudo é da unidade do usuário autenticado
(outra empresa = 404). Quem só tem o perfil Operador vende e vê apenas as próprias vendas. Atenção ao limite geral de
requisições por usuário (`API_THROTTLE_USER`, padrão 2000/hora): ajuste para o volume de um caixa movimentado.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.consumidor.models import Consumidor
from apps.pdv import comprovante as comp
from apps.pdv import services as s
from apps.pdv.forms import DevolucaoForm
from apps.pdv.models import Caixa, MovimentoCaixa, Venda, VendaItem
from apps.pdv.views import _caixas_visiveis, _vendas_visiveis
from apps.pdvconfig.forms import ConfiguracaoPdvForm, FormaPagamentoForm, MesaForm
from apps.pdvconfig.models import ConfiguracaoPdv, FormaPagamento, Mesa
from web_project.acesso import cliente_do_usuario
from web_project.decorators import modulo_habilitado_para
from web_project.regras.dinheiro import DinheiroService

from .negocio import RecursoViewSet, documentar, erros_de
from .pagination import PaginacaoPadrao
from .views import resposta


class PermissaoPorAcao(BasePermission):
    """Cada ação do ViewSet declara a permissão exigida em `permissoes = {"acao": "app.codename"}`; o módulo
    (`modulo_slug`) precisa estar habilitado para o cliente do usuário. Ação sem permissão declarada é negada."""

    message = "Você não tem permissão para esta operação."

    def has_permission(self, request, view):
        usuario = request.user
        if not (usuario and usuario.is_authenticated):
            return False
        if not modulo_habilitado_para(usuario, view.modulo_slug):
            self.message = "Este módulo não está habilitado para o seu cliente."
            return False
        chave = getattr(view, "action", None) or request.method.lower()  # ViewSet: ação; APIView: método HTTP
        permissao = getattr(view, "permissoes", {}).get(chave)
        return bool(permissao) and usuario.has_perm(permissao)


def _reais(c):
    return DinheiroService.formatar(c)


def _caixa_dict(caixa, com_resumo=False):
    dados = {
        "id": caixa.pk, "status": caixa.status, "operador": caixa.operador.get_username(), "unidade": caixa.cliente_id, "aberto_em": caixa.aberto_em,
        "fechado_em": caixa.fechado_em, "valor_abertura": caixa.valor_abertura, "valor_esperado": caixa.valor_esperado, "valor_contado": caixa.valor_contado,
        "diferenca": caixa.diferenca, "conferido": caixa.conferido_em is not None, "conferido_em": caixa.conferido_em,
    }
    if com_resumo:
        r = s.resumo_do_caixa(caixa)
        dados["resumo"] = {"dinheiro": r["dinheiro"], "qtd_vendas": r["qtd_vendas"], "total_vendas": r["total_vendas"], "canceladas": r["canceladas"],
                           "comandas_abertas": r["comandas_abertas"], "por_forma": r["por_forma"]}
    return dados


def _venda_dict(venda, detalhe=False):
    dados = {
        "id": venda.pk, "numero": venda.numero, "status": venda.status, "origem": venda.origem, "mesa": venda.mesa.nome if venda.mesa_id else None,
        "consumidor": venda.consumidor_id, "caixa": venda.caixa_id, "subtotal": venda.subtotal, "desconto": venda.desconto, "total": venda.total, "total_formatado": _reais(venda.total),
        "troco": venda.troco, "criado_em": venda.criado_em, "finalizada_em": venda.finalizada_em, "cancelada_em": venda.cancelada_em,
    }
    if detalhe:
        dados["itens"] = [
            {"id": i.pk, "produto": i.produto_id, "descricao": i.descricao, "quantidade": f"{i.quantidade:.3f}", "preco_unitario": i.preco_unitario, "total": i.total,
             "quantidade_devolvida": f"{i.quantidade_devolvida:.3f}", "preparado": i.preparado, "observacao": i.observacao}
            for i in venda.itens.all()
        ]
        dados["pagamentos"] = [
            {"forma": p.forma_id, "forma_nome": p.forma_nome, "valor": p.valor, "valor_recebido": p.valor_recebido, "taxa": p.taxa_valor, "liquido": p.liquido, "pagador": p.pagador}
            for p in venda.pagamentos.all()
        ]
        dados["devolucoes"] = [{"id": d.pk, "item": d.item_id, "quantidade": f"{d.quantidade:.3f}", "valor": d.valor, "estoque_reposto": d.repor_estoque, "motivo": d.motivo} for d in venda.devolucoes.all()]
        dados["motivo_cancelamento"] = venda.motivo_cancelamento
    return dados


class PdvItemEntrada(serializers.Serializer):
    produto = serializers.IntegerField(help_text="Id do produto (ver /pdv/produtos/).")
    quantidade = serializers.DecimalField(max_digits=12, decimal_places=3)
    observacao = serializers.CharField(required=False)


class PdvPagamentoEntrada(serializers.Serializer):
    forma = serializers.IntegerField(help_text="Id da forma de pagamento (ver /formas-pagamento/).")
    valor = serializers.IntegerField(help_text="Centavos que abatem a venda.")
    valor_recebido = serializers.IntegerField(required=False, help_text="Só dinheiro: quanto o cliente entregou (troco = recebido − valor).")
    pagador = serializers.CharField(required=False)


class PdvVendaEntrada(serializers.Serializer):
    itens = PdvItemEntrada(many=True)
    pagamentos = PdvPagamentoEntrada(many=True)
    desconto = serializers.IntegerField(required=False, help_text="Centavos; exige a permissão conceder_desconto.")
    consumidor = serializers.IntegerField(required=False, allow_null=True)
    observacao = serializers.CharField(required=False)
    chave = serializers.CharField(required=False, help_text="Chave de idempotência (ou cabeçalho Idempotency-Key).")


class PdvPagarComandaEntrada(serializers.Serializer):
    pagamentos = PdvPagamentoEntrada(many=True)
    desconto = serializers.IntegerField(required=False)
    chave = serializers.CharField(required=False)


def _chave(request):
    chave = request.data.get("chave") if isinstance(request.data, dict) else None
    return (str(chave or request.headers.get("Idempotency-Key") or "")[:64]) or None


def _consumidor(caixa, valor):
    if valor in (None, "", 0):
        return None
    try:
        return Consumidor.objects.get(pk=int(valor), cliente=caixa.cliente, ativo=True, anonimizado_em__isnull=True)
    except (Consumidor.DoesNotExist, TypeError, ValueError):
        raise serializers.ValidationError({"consumidor": ["Consumidor inválido."]}) from None


def _caixa_do_usuario(request, exigir=True):
    caixa = s.caixa_aberto_do(request.user)
    if caixa is None and exigir:
        raise serializers.ValidationError({"caixa": ["Abra o caixa antes de vender."]})
    return caixa


def _inteiro(request, campo, minimo=0):
    valor = request.data.get(campo) if isinstance(request.data, dict) else None
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise serializers.ValidationError({campo: ["Informe um valor inteiro em centavos."]})
    return valor


# --------------------------------------------------------------------------
# Configurações: formas de pagamento, mesas, ajustes
# --------------------------------------------------------------------------

class FormaPagamentoSerializer(serializers.ModelSerializer):
    descricao_da_taxa = serializers.CharField(read_only=True)

    class Meta:
        model = FormaPagamento
        fields = ["id", "nome", "tipo", "taxa_modo", "taxa_percentual", "taxa_fixa", "descricao_da_taxa", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class FormaPagamentoEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=60)
    tipo = serializers.ChoiceField(choices=[c for c, _ in FormaPagamento.TIPO_CHOICES])
    taxa_modo = serializers.ChoiceField(choices=[c for c, _ in FormaPagamento.MODO_CHOICES], required=False, help_text="sem_taxa, percentual, fixa (valor fixo por transação) ou ambas.")
    taxa_percentual = serializers.DecimalField(max_digits=7, decimal_places=4, required=False, allow_null=True, help_text="Em %, usado nos modos percentual e ambas.")
    taxa_fixa = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos por transação, usado nos modos fixa e ambas.")
    ativo = serializers.BooleanField(required=False)


@documentar("pdv-config", FormaPagamentoSerializer, FormaPagamentoEntrada)
class FormaPagamentoViewSet(RecursoViewSet):
    """Formas de pagamento do PDV e como cada uma cobra taxa (percentual, valor fixo por transação ou ambos). DELETE: em uso só desativa."""

    modelo, serializer_class, entrada_class, form_class = FormaPagamento, FormaPagamentoSerializer, FormaPagamentoEntrada, FormaPagamentoForm
    permissao_app, permissao_model, modulo_slug = "pdvconfig", "formapagamento", "forma-pagamento"
    campos_moeda = ("taxa_fixa",)
    rotulo = "Forma de pagamento"
    padroes = {"ativo": True, "taxa_modo": "sem_taxa"}

    def dados_atuais(self, f):
        return {"nome": f.nome, "tipo": f.tipo, "taxa_modo": f.taxa_modo, "taxa_percentual": f.taxa_percentual, "taxa_fixa": f.taxa_fixa, "ativo": f.ativo}


class MesaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mesa
        fields = ["id", "nome", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class MesaEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=40)
    ativo = serializers.BooleanField(required=False)


@documentar("pdv-config", MesaSerializer, MesaEntrada)
class MesaViewSet(RecursoViewSet):
    """Mesas/comandas do PDV. DELETE: em uso só desativa."""

    modelo, serializer_class, entrada_class, form_class = Mesa, MesaSerializer, MesaEntrada, MesaForm
    permissao_app, permissao_model, modulo_slug = "pdvconfig", "mesa", "mesa"
    rotulo = "Mesa"

    def dados_atuais(self, m):
        return {"nome": m.nome, "ativo": m.ativo}


@extend_schema(tags=["pdv-config"])
class ConfiguracaoPdvView(APIView):
    """Ajustes do PDV da unidade do usuário: GET e PATCH em /pdv/configuracao/."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pdv-config"
    permissoes = {"get": "pdvconfig.view_configuracaopdv", "patch": "pdvconfig.change_configuracaopdv"}

    def _dados(self, c):
        return {"controla_estoque": c.controla_estoque, "formato_comprovante": c.formato_comprovante, "rodape_comprovante": c.rodape_comprovante}

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta(self._dados(ConfiguracaoPdv.da_unidade(cliente_do_usuario(request.user))))

    @extend_schema(
        request=inline_serializer("ConfiguracaoPdvEntrada", {
            "controla_estoque": serializers.BooleanField(required=False), "formato_comprovante": serializers.ChoiceField(choices=["termica80", "termica58", "a4"], required=False),
            "rodape_comprovante": serializers.CharField(required=False, max_length=120)}),
        responses={200: OpenApiTypes.OBJECT},
    )
    def patch(self, request):
        from django.db import transaction

        from apps.auditoria.utils import registrar_edicao, tirar_foto

        config = ConfiguracaoPdv.da_unidade(cliente_do_usuario(request.user))
        foto = tirar_foto(config)
        atuais = self._dados(config)
        dados = {**atuais, **{k: v for k, v in request.data.items() if k in atuais}}
        form = ConfiguracaoPdvForm({**dados, "controla_estoque": "on" if dados["controla_estoque"] else ""}, instance=config)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        with transaction.atomic():
            config = form.save()
            registrar_edicao(request, config, foto)
        return resposta(self._dados(config), "Configurações salvas.")


# --------------------------------------------------------------------------
# Catálogo, caixa, vendas e comandas
# --------------------------------------------------------------------------

@extend_schema(tags=["pdv"])
class ProdutoPdvViewSet(viewsets.ViewSet):
    """Produtos vendáveis no PDV (precificação marcada para o PDV) da unidade do caixa aberto."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pdv"
    permissoes = {"list": "pdv.add_venda"}

    @extend_schema(parameters=[OpenApiParameter("q", str)], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        caixa = _caixa_do_usuario(request)
        itens = [{"produto": p.produto_id, "nome": p.produto.nome, "categoria": p.produto.categoria.nome, "preco": p.preco_venda, "preco_formatado": p.preco_venda_formatado}
                 for p in s.produtos_do_pdv(caixa.cliente, request.query_params.get("q", ""))]
        return resposta({"itens": itens})


@extend_schema(tags=["pdv"])
class CaixaViewSet(viewsets.ViewSet):
    """Caixa do operador: abrir, ver o atual, sangria/suprimento, fechar; histórico e conferência (quem tem a permissão)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pdv"
    permissoes = {
        "list": "pdv.view_caixa", "retrieve": "pdv.view_caixa", "atual": "pdv.view_caixa", "abrir": "pdv.abrir_caixa", "fechar": "pdv.abrir_caixa",
        "sangria": "pdv.sangria", "suprimento": "pdv.suprimento", "conferir": "pdv.conferir_caixa",
    }

    def _caixa(self, request, pk):
        try:
            return _caixas_visiveis(request.user).select_related("operador", "cliente").get(pk=pk)
        except Caixa.DoesNotExist:
            raise NotFound from None

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        pagina = PaginacaoPadrao()
        itens = pagina.paginate_queryset(_caixas_visiveis(request.user).select_related("operador", "cliente"), request)
        return pagina.get_paginated_response([_caixa_dict(c) for c in itens])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_caixa_dict(self._caixa(request, pk), com_resumo=True))

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Caixa aberto do usuário (404 se não houver)")
    @action(detail=False, methods=["get"], url_path="atual")
    def atual(self, request):
        caixa = s.caixa_aberto_do(request.user)
        if caixa is None:
            raise NotFound("Você não tem caixa aberto.")
        return resposta(_caixa_dict(caixa, com_resumo=True))

    @extend_schema(request=inline_serializer("AbrirCaixa", {"valor_abertura": serializers.IntegerField(required=False)}), responses={201: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="abrir")
    def abrir(self, request):
        valor = request.data.get("valor_abertura", 0) if isinstance(request.data, dict) else 0
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise serializers.ValidationError({"valor_abertura": ["Informe o troco inicial em centavos."]})
        return resposta(_caixa_dict(s.abrir_caixa(request, valor)), "Caixa aberto.", 201)

    @extend_schema(request=inline_serializer("FecharCaixa", {"valor_contado": serializers.IntegerField(), "observacao": serializers.CharField(required=False)}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="fechar")
    def fechar(self, request):
        caixa = _caixa_do_usuario(request)
        fechado = s.fechar_caixa(request, caixa, _inteiro(request, "valor_contado"), str(request.data.get("observacao", "")))
        return resposta(_caixa_dict(fechado, com_resumo=True), "Caixa fechado.")

    def _movimento(self, request, tipo):
        movimento = s.registrar_movimento(request, _caixa_do_usuario(request), tipo, _inteiro(request, "valor", 1), str(request.data.get("motivo", "")))
        return resposta({"id": movimento.pk, "tipo": movimento.tipo, "valor": movimento.valor, "motivo": movimento.motivo}, "Movimento registrado.", 201)

    @extend_schema(request=inline_serializer("Movimento", {"valor": serializers.IntegerField(), "motivo": serializers.CharField()}), responses={201: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="sangria")
    def sangria(self, request):
        return self._movimento(request, MovimentoCaixa.SANGRIA)

    @extend_schema(request=inline_serializer("MovimentoSuprimento", {"valor": serializers.IntegerField(), "motivo": serializers.CharField()}), responses={201: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="suprimento")
    def suprimento(self, request):
        return self._movimento(request, MovimentoCaixa.SUPRIMENTO)

    @extend_schema(request=inline_serializer("ConferirCaixa", {"observacao": serializers.CharField(required=False)}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="conferir")
    def conferir(self, request, pk=None):
        caixa = s.conferir_caixa(request, self._caixa(request, pk), str(request.data.get("observacao", "")))
        return resposta(_caixa_dict(caixa), "Caixa conferido.")


@extend_schema(tags=["pdv"])
class VendaViewSet(viewsets.ViewSet):
    """Vendas do PDV: criar (balcão, idempotente), listar, consultar, cancelar, devolver itens e comprovante."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pdv"
    permissoes = {
        "list": "pdv.view_venda", "retrieve": "pdv.view_venda", "comprovante": "pdv.view_venda", "create": "pdv.add_venda",
        "cancelar": "pdv.cancelar_venda", "devolver": "pdv.devolver_item",
    }

    def _venda(self, request, pk):
        try:
            return _vendas_visiveis(request.user).select_related("cliente", "caixa", "consumidor", "mesa", "criado_por").get(pk=pk)
        except Venda.DoesNotExist:
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("status", str), OpenApiParameter("q", str, description="número ou nome do consumidor")], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        from django.db.models import Q

        consulta = _vendas_visiveis(request.user).select_related("mesa").order_by("-criado_em", "-pk")
        if request.query_params.get("status") in dict(Venda.STATUS_CHOICES):
            consulta = consulta.filter(status=request.query_params["status"])
        termo = request.query_params.get("q", "").strip()
        if termo:
            filtro = Q(consumidor__nome__icontains=termo)
            if termo.lstrip("#").isdigit():
                filtro |= Q(numero=int(termo.lstrip("#")))
            consulta = consulta.filter(filtro)
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_venda_dict(v) for v in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_venda_dict(self._venda(request, pk), detalhe=True))

    @extend_schema(request=PdvVendaEntrada, responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT}, summary="Registra uma venda de balcão (idempotente)")
    def create(self, request):
        caixa = _caixa_do_usuario(request)
        dados = request.data if isinstance(request.data, dict) else {}
        desconto = dados.get("desconto") or 0
        venda, criada = s.registrar_venda(
            request, caixa, dados.get("itens"), dados.get("pagamentos"), consumidor=_consumidor(caixa, dados.get("consumidor")), desconto=desconto,
            observacao=str(dados.get("observacao") or ""), chave=_chave(request), pode_desconto=request.user.has_perm("pdv.conceder_desconto"),
        )
        saida = {**_venda_dict(venda, detalhe=True), "criada": criada}
        return resposta(saida, "Venda registrada." if criada else "Venda já registrada (chave repetida).", 201 if criada else 200)

    @extend_schema(request=inline_serializer("CancelarVenda", {"motivo": serializers.CharField()}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="cancelar")
    def cancelar(self, request, pk=None):
        venda = s.cancelar_venda(request, self._venda(request, pk), str(request.data.get("motivo", "")))
        return resposta(_venda_dict(venda, detalhe=True), "Venda cancelada.")

    @extend_schema(request=inline_serializer("DevolverItem", {"quantidade": serializers.DecimalField(max_digits=12, decimal_places=3), "forma_estorno": serializers.IntegerField(), "motivo": serializers.CharField(), "repor_estoque": serializers.BooleanField(required=False)}),
                   responses={201: OpenApiTypes.OBJECT}, summary="Devolve itens (repõe o estoque só se o item não foi preparado)")
    @action(detail=True, methods=["post"], url_path=r"itens/(?P<item_id>[0-9]+)/devolver")
    def devolver(self, request, pk=None, item_id=None):
        venda = self._venda(request, pk)
        try:
            item = VendaItem.objects.get(pk=item_id, venda=venda)
        except VendaItem.DoesNotExist:
            raise NotFound from None
        dados = request.data if isinstance(request.data, dict) else {}
        form = DevolucaoForm({**dados, "repor_estoque": "on" if dados.get("repor_estoque") else ""}, cliente=venda.cliente)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        c = form.cleaned_data
        devolucao = s.devolver_item(request, item, c["quantidade"], c["motivo"], c["forma_estorno"], c["repor_estoque"])
        return resposta({"id": devolucao.pk, "valor": devolucao.valor, "estoque_reposto": devolucao.repor_estoque}, "Devolução registrada.", 201)

    @extend_schema(parameters=[OpenApiParameter("formato", str, description="json (padrão), pdf ou escpos"), OpenApiParameter("tamanho", str, description="termica80, termica58 ou a4")], responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="comprovante")
    def comprovante(self, request, pk=None):
        venda = self._venda(request, pk)
        dados = comp.dados_comprovante(venda)
        tamanho = request.query_params.get("tamanho") if request.query_params.get("tamanho") in comp.FORMATOS else dados["formato_padrao"]
        formato = request.query_params.get("formato", "json")
        if formato == "pdf":
            return comp.resposta_pdf(dados, tamanho)
        if formato == "escpos":
            return comp.resposta_escpos(dados, tamanho)
        if formato != "json":
            raise serializers.ValidationError({"formato": ["Use json, pdf ou escpos."]})
        return resposta({**dados, "linhas": comp.linhas_texto(dados, comp.FORMATOS[tamanho])})


@extend_schema(tags=["pdv"])
class ComandaViewSet(viewsets.ViewSet):
    """Comandas de mesa: abrir, lançar/remover itens, ver e receber (conta dividida = vários pagamentos)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "pdv"
    permissoes = {"list": "pdv.view_venda", "retrieve": "pdv.view_venda", "create": "pdv.add_venda", "itens": "pdv.add_venda", "item": "pdv.add_venda", "pagar": "pdv.add_venda"}

    def _comanda(self, request, pk):
        try:
            return _vendas_visiveis(request.user).select_related("cliente", "caixa", "mesa").get(pk=pk, origem=Venda.ORIGEM_MESA)
        except Venda.DoesNotExist:
            raise NotFound from None

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Comandas abertas")
    def list(self, request):
        return resposta([_venda_dict(v, detalhe=True) for v in _vendas_visiveis(request.user).filter(status=Venda.ABERTA, origem=Venda.ORIGEM_MESA).select_related("mesa")])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_venda_dict(self._comanda(request, pk), detalhe=True))

    @extend_schema(request=inline_serializer("AbrirComanda", {"mesa": serializers.IntegerField(), "observacao": serializers.CharField(required=False)}), responses={201: OpenApiTypes.OBJECT})
    def create(self, request):
        caixa = _caixa_do_usuario(request)
        try:
            mesa = Mesa.objects.get(pk=int(request.data.get("mesa")), cliente=caixa.cliente)
        except (Mesa.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"mesa": ["Mesa inválida."]}) from None
        venda = s.abrir_comanda(request, caixa, mesa, observacao=str(request.data.get("observacao", "")))
        return resposta(_venda_dict(venda, detalhe=True), "Comanda aberta.", 201)

    @extend_schema(request=inline_serializer("ItemComanda", {"produto": serializers.IntegerField(), "quantidade": serializers.DecimalField(max_digits=12, decimal_places=3), "observacao": serializers.CharField(required=False)}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="itens")
    def itens(self, request, pk=None):
        dados = request.data if isinstance(request.data, dict) else {}
        venda = s.adicionar_item(request, self._comanda(request, pk), dados.get("produto"), dados.get("quantidade", 1), str(dados.get("observacao", "")))
        return resposta(_venda_dict(venda, detalhe=True), "Item lançado.")

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Remove um item da comanda aberta")
    @action(detail=True, methods=["delete"], url_path=r"itens/(?P<item_id>[0-9]+)")
    def item(self, request, pk=None, item_id=None):
        venda = s.remover_item(request, self._comanda(request, pk), int(item_id))
        return resposta(_venda_dict(venda, detalhe=True), "Item removido.")

    @extend_schema(request=PdvPagarComandaEntrada, responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="pagar")
    def pagar(self, request, pk=None):
        dados = request.data if isinstance(request.data, dict) else {}
        venda, criada = s.pagar_comanda(request, self._comanda(request, pk), dados.get("pagamentos"), dados.get("desconto") or 0, request.user.has_perm("pdv.conceder_desconto"), chave=_chave(request))
        return resposta({**_venda_dict(venda, detalhe=True), "criada": criada}, "Comanda recebida." if criada else "Comanda já recebida (chave repetida).", 201 if criada else 200)
