"""Precificação de serviços na API v1 (segmentos Serviço e Serviço e Produto; módulo `precificacao-servico`).

Mesmas regras das telas (`apps/precificacaoservico/services.py` e `calculos.py`): NADA é calculado pelo cliente. Dinheiro em centavos inteiros;
percentuais em pontos percentuais (10 = 10%). A unidade (tenant) vem sempre do usuário autenticado; id de outra unidade = 404. Dados sensíveis
(remuneração, custos, margem): só Administrador e Gestor têm as permissões. Propostas guardam o retrato das premissas do momento do cálculo.
"""
from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from apps.auditoria.utils import tirar_foto
from apps.consumidor.models import Consumidor
from apps.maodeobra.models import segmento_permite
from apps.precificacaoservico import services as s
from apps.precificacaoservico.forms import ConfiguracaoForm, DespesaForm, ModeloForm
from apps.precificacaoservico.models import ConfiguracaoServico, DespesaFixa, Lancamento, ModeloServico, Pagamento, Proposta, PropostaCusto
from web_project.acesso import cliente_do_usuario, clientes_dos_itens

from .negocio import RecursoViewSet, documentar, erros_de, para_formulario
from .pagination import PaginacaoPadrao
from .pdv import PermissaoPorAcao
from .views import resposta

TAG = "precificacao-servico"
MODULO = "precificacao-servico"
APP = "precificacaoservico"


def _unidade(request):
    """Unidade do usuário; segmento de produto puro não usa o módulo (403 mesmo que alguém habilite o módulo por engano)."""
    cliente = cliente_do_usuario(request.user)
    if cliente is None:
        raise PermissionDenied("Seu usuário não está vinculado a uma unidade.")
    if not segmento_permite(cliente):
        raise PermissionDenied("O segmento da sua empresa não usa a precificação de serviços.")
    return cliente


# ---------------------------------------------------------------------------------------------------------------------------
# Configuração e custo-hora
# ---------------------------------------------------------------------------------------------------------------------------

CAMPOS_CONFIG = ("moeda", "remuneracao_mensal", "dias_mes", "horas_dia", "horas_nao_faturaveis", "horas_faturaveis_informadas", "custo_variavel_hora", "impostos_pct", "taxas_pct", "margem_pct")
MOEDA_CONFIG = ("remuneracao_mensal", "custo_variavel_hora")


class ConfiguracaoEntrada(serializers.Serializer):
    moeda = serializers.ChoiceField(choices=["BRL"], required=False)
    remuneracao_mensal = serializers.IntegerField(min_value=0, required=False, help_text="Centavos por mês. É custo, não lucro.")
    dias_mes = serializers.DecimalField(max_digits=4, decimal_places=1, required=False, help_text="Dias de trabalho no mês (já descontadas férias e folgas).")
    horas_dia = serializers.DecimalField(max_digits=4, decimal_places=2, required=False)
    horas_nao_faturaveis = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)
    horas_faturaveis_informadas = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True, help_text="Opcional; vazio = disponíveis − não faturáveis.")
    custo_variavel_hora = serializers.IntegerField(min_value=0, required=False, help_text="Centavos por hora.")
    impostos_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, help_text="Estimativa informada pelo usuário (não é orientação tributária).")
    taxas_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    margem_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, help_text="% do preço de venda. Impostos + taxas + margem < 100.")


def _config_dict(c):
    return {
        "moeda": c.moeda, "remuneracao_mensal": c.remuneracao_mensal, "dias_mes": str(c.dias_mes), "horas_dia": str(c.horas_dia), "horas_nao_faturaveis": str(c.horas_nao_faturaveis),
        "horas_faturaveis_informadas": None if c.horas_faturaveis_informadas is None else str(c.horas_faturaveis_informadas), "custo_variavel_hora": c.custo_variavel_hora,
        "impostos_pct": str(c.impostos_pct), "taxas_pct": str(c.taxas_pct), "margem_pct": str(c.margem_pct), "versao": c.versao, "atualizado_em": c.atualizado_em,
    }


@extend_schema(tags=[TAG])
class ConfiguracaoView(APIView):
    """Premissas da unidade: GET e PATCH em /precificacao-servico/configuracao/ (valem para as próximas propostas; as antigas não mudam)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = MODULO
    permissoes = {"get": f"{APP}.view_configuracaoservico", "patch": f"{APP}.change_configuracaoservico"}

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta(_config_dict(ConfiguracaoServico.da_unidade(_unidade(request))))

    @extend_schema(request=ConfiguracaoEntrada, responses={200: OpenApiTypes.OBJECT})
    def patch(self, request):
        cfg = ConfiguracaoServico.da_unidade(_unidade(request))
        entrada = ConfiguracaoEntrada(data=request.data, partial=True)
        entrada.is_valid(raise_exception=True)
        foto = tirar_foto(cfg)
        dados = {**{k: v for k, v in _config_dict(cfg).items() if k in CAMPOS_CONFIG}, **entrada.validated_data}
        form = ConfiguracaoForm(para_formulario(dados, MOEDA_CONFIG), instance=cfg)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        s.salvar_configuracao(request, form.save(commit=False), foto)
        return resposta(_config_dict(cfg), "Configuração salva.")


def _custo_hora_dict(cap, ch):
    dinheiro = {k: str(v) for k, v in ch.items() if k not in ("horas_faturaveis",)}
    return {"horas": {k: str(v) for k, v in cap.items()}, "centavos": dinheiro,
            "custo_hora_total": round(ch["custo_hora_total"]), "custo_fixo_hora": round(ch["custo_fixo_hora"]), "remuneracao_hora": round(ch["remuneracao_hora"])}


@extend_schema(tags=[TAG])
class CustoHoraView(APIView):
    """Custo-hora calculado com a configuração e as despesas de hoje, com a composição (remuneração em linha própria)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = MODULO
    permissoes = {"get": f"{APP}.view_configuracaoservico"}

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Custo-hora total e sua composição")
    def get(self, request):
        cap, ch = s.calcular_custo_hora(_unidade(request))
        return resposta(_custo_hora_dict(cap, ch))


# ---------------------------------------------------------------------------------------------------------------------------
# Despesas e modelos (CRUD sobre os formulários das telas)
# ---------------------------------------------------------------------------------------------------------------------------

class DespesaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DespesaFixa
        fields = ["id", "natureza", "descricao", "categoria", "valor", "periodicidade", "vigencia_inicio", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class DespesaEntrada(serializers.Serializer):
    natureza = serializers.ChoiceField(choices=[c for c, _ in DespesaFixa.NATUREZA_CHOICES], required=False)
    descricao = serializers.CharField(max_length=150)
    categoria = serializers.ChoiceField(choices=[c for c, _ in DespesaFixa.CATEGORIA_CHOICES], required=False)
    valor = serializers.IntegerField(min_value=1, help_text="Centavos, na periodicidade informada.")
    periodicidade = serializers.ChoiceField(choices=[c for c, _ in DespesaFixa.PERIODICIDADE_CHOICES], required=False)
    vigencia_inicio = serializers.DateField(required=False, allow_null=True)
    ativo = serializers.BooleanField(required=False)


@documentar(TAG, DespesaSerializer, DespesaEntrada)
class DespesaViewSet(RecursoViewSet):
    """Despesas fixas/indiretas e provisões (convertidas para base mensal no custo-hora)."""

    modelo, serializer_class, entrada_class, form_class = DespesaFixa, DespesaSerializer, DespesaEntrada, DespesaForm
    permissao_app, permissao_model, modulo_slug = APP, "despesafixa", MODULO
    campos_busca = ("descricao",)
    colunas_ordenaveis = {"descricao": "descricao", "valor": "valor", "criado_em": "criado_em"}
    ordenacao_padrao = ["descricao"]
    campos_moeda = ("valor",)
    padroes = {"ativo": True, "natureza": "despesa", "categoria": "outras", "periodicidade": "mensal"}
    rotulo = "Despesa"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        _unidade(request)

    def dados_atuais(self, d):
        return {"natureza": d.natureza, "descricao": d.descricao, "categoria": d.categoria, "valor": d.valor, "periodicidade": d.periodicidade, "vigencia_inicio": d.vigencia_inicio, "ativo": d.ativo}


class ModeloSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModeloServico
        fields = ["id", "nome", "categoria", "modo", "unidade", "preco_base", "horas_por_unidade", "margem_pct", "descricao", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class ModeloEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    categoria = serializers.CharField(max_length=80, required=False, allow_blank=True)
    modo = serializers.ChoiceField(choices=[c for c, _ in ModeloServico._meta.get_field("modo").choices], required=False)
    unidade = serializers.CharField(max_length=20, required=False, allow_blank=True)
    preco_base = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos por unidade.")
    horas_por_unidade = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)
    margem_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True, help_text="Vazio = margem da configuração.")
    descricao = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    ativo = serializers.BooleanField(required=False)


@documentar(TAG, ModeloSerializer, ModeloEntrada)
class ModeloViewSet(RecursoViewSet):
    """Modelos de serviço. Propostas criadas a partir deles recebem uma CÓPIA dos valores (o modelo pode mudar depois)."""

    modelo, serializer_class, entrada_class, form_class = ModeloServico, ModeloSerializer, ModeloEntrada, ModeloForm
    permissao_app, permissao_model, modulo_slug = APP, "modeloservico", MODULO
    campos_busca = ("nome", "categoria")
    campos_moeda = ("preco_base",)
    padroes = {"ativo": True, "modo": "hora"}
    rotulo = "Modelo"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        _unidade(request)

    def dados_atuais(self, m):
        return {"nome": m.nome, "categoria": m.categoria, "modo": m.modo, "unidade": m.unidade, "preco_base": m.preco_base, "horas_por_unidade": m.horas_por_unidade,
                "margem_pct": m.margem_pct, "descricao": m.descricao, "ativo": m.ativo}


# ---------------------------------------------------------------------------------------------------------------------------
# Propostas
# ---------------------------------------------------------------------------------------------------------------------------

class PropostaItemEntrada(serializers.Serializer):
    descricao = serializers.CharField(max_length=150)
    modo = serializers.ChoiceField(choices=[c for c, _ in ModeloServico._meta.get_field("modo").choices], help_text="hora, dia, semana, quinzena, mes, unidade ou pacote.")
    quantidade = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    unidade = serializers.CharField(max_length=20, required=False, allow_blank=True)
    horas_por_unidade = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True, help_text="Vazio = padrão da forma de cobrança (dia = jornada; semana = 5 dias; quinzena = 10 dias; mês = horas faturáveis).")
    preco_unitario = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos. Vazio = o preço sai do custo e da margem/markup.")


class PropostaCustoEntrada(serializers.Serializer):
    categoria = serializers.ChoiceField(choices=[c for c, _ in PropostaCusto.CATEGORIA_CHOICES], required=False)
    descricao = serializers.CharField(max_length=150)
    quantidade = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    unidade = serializers.CharField(max_length=20, required=False, allow_blank=True)
    custo_unitario = serializers.IntegerField(min_value=0, help_text="Centavos.")
    repasse = serializers.ChoiceField(choices=[c for c, _ in PropostaCusto.REPASSE_CHOICES], help_text="discriminado (cobrado à parte), incluido (no preço) ou absorvido (custo seu).")


CAMPOS_TEXTO = ("destinatario_nome", "destinatario_email", "destinatario_whatsapp", "descricao", "prazo", "condicoes_pagamento", "escopo_incluido", "escopo_excluido", "etapas",
                "materiais_incluidos", "materiais_excluidos", "limites", "regras_extras", "observacoes")
CAMPOS_PARAMETROS = ("modo", "metodo_preco", "margem_pct", "markup_pct", "preco_fechado", "minimo_cobranca", "horas_minimas", "arredondamento_minutos", "horas_deslocamento",
                     "cobra_deslocamento", "desconto_tipo", "desconto", "acrescimo_pct", "acrescimo_valor", "custo_financeiro_pct", "detalhar_custos", "validade")


class PropostaEntrada(serializers.ModelSerializer):
    """Entrada de proposta (criar, alterar e simular). Totais, status, número, unidade e premissas NUNCA vêm do cliente."""

    consumidor = serializers.IntegerField(required=False, allow_null=True, help_text="Cliente final do cadastro (copia nome e contatos quando não informados).")
    itens = PropostaItemEntrada(many=True, required=False, help_text="Substitui TODOS os itens de trabalho (obrigatório ao criar/simular; omitido na alteração = mantém).")
    custos = PropostaCustoEntrada(many=True, required=False, help_text="Substitui TODOS os custos diretos (omitido na alteração = mantém).")
    atualizar_premissas = serializers.BooleanField(required=False, help_text="Só na alteração: refaz o retrato das premissas com os valores de hoje.")
    desconto = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, help_text="Centavos (desconto_tipo=valor) ou percentual (desconto_tipo=percentual).")

    class Meta:
        model = Proposta
        fields = [*CAMPOS_TEXTO, *CAMPOS_PARAMETROS, "consumidor", "itens", "custos", "atualizar_premissas"]
        extra_kwargs = {"destinatario_nome": {"required": False}, "descricao": {"required": False}, "arredondamento_minutos": {"required": False}}

    def validate_arredondamento_minutos(self, valor):
        if valor not in (0, 15, 30, 60):
            raise serializers.ValidationError("Use 0, 15, 30 ou 60 minutos.")
        return valor


def _item_dict(i):
    return {"id": i.pk, "descricao": i.descricao, "modo": i.modo, "quantidade": str(i.quantidade), "unidade": i.unidade,
            "horas_por_unidade": None if i.horas_por_unidade is None else str(i.horas_por_unidade), "preco_unitario": i.preco_unitario}


def _custo_dict(k):
    return {"id": k.pk, "categoria": k.categoria, "descricao": k.descricao, "quantidade": str(k.quantidade), "unidade": k.unidade, "custo_unitario": k.custo_unitario, "repasse": k.repasse}


def _proposta_dict(p, completo=True):
    dados = {
        "id": p.pk, "numero": p.numero, "status": p.status, "consumidor": p.consumidor_id, "destinatario_nome": p.destinatario_nome, "descricao": p.descricao, "modo": p.modo,
        "validade": p.validade, "custo_total": p.custo_total, "total": p.total, "lucro": p.lucro, "criado_em": p.criado_em, "atualizado_em": p.atualizado_em,
    }
    if completo:
        dados.update({campo: getattr(p, campo) for campo in (*CAMPOS_TEXTO[1:3], *CAMPOS_TEXTO[4:])})
        dados.update({campo: (str(v) if hasattr(v, "quantize") else v) for campo in CAMPOS_PARAMETROS[1:-1] for v in [getattr(p, campo)]})
        dados.update({"resultado": p.resultado, "premissas": p.premissas, "aceite_em": p.aceite_em, "aceite_nome": p.aceite_nome, "data_inicio": p.data_inicio, "data_conclusao": p.data_conclusao,
                      "itens": [_item_dict(i) for i in p.itens.all()], "custos": [_custo_dict(k) for k in p.custos.all()]})
    return dados


def _lancamento_dict(l):
    return {"id": l.pk, "tipo": l.tipo, "data": l.data, "descricao": l.descricao, "horas": str(l.horas), "custo": l.custo, "receita": l.receita, "criado_em": l.criado_em}


def _pagamento_dict(g):
    return {"id": g.pk, "valor": g.valor, "data": g.data, "meio": g.meio, "taxa": g.taxa, "status": g.status, "observacao": g.observacao, "criado_em": g.criado_em}


LancamentoEntrada = inline_serializer("LancamentoEntrada", {
    "tipo": serializers.ChoiceField(choices=[c for c, _ in Lancamento.TIPO_CHOICES]), "data": serializers.DateField(), "descricao": serializers.CharField(max_length=150, required=False, allow_blank=True),
    "horas": serializers.DecimalField(max_digits=7, decimal_places=2, required=False), "custo": serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos. Vazio = horas × custo-hora do retrato da proposta."),
    "receita": serializers.IntegerField(min_value=0, required=False, help_text="Centavos cobrados; só para tipo=extra."),
})
PagamentoEntrada = inline_serializer("PagamentoEntrada", {
    "valor": serializers.IntegerField(min_value=1, help_text="Centavos."), "data": serializers.DateField(), "meio": serializers.ChoiceField(choices=[c for c, _ in Pagamento.MEIO_CHOICES]),
    "taxa": serializers.IntegerField(min_value=0, required=False, help_text="Centavos de taxa do meio (cartão/plataforma); não pode passar do valor."),
    "status": serializers.ChoiceField(choices=["recebido", "pendente"], required=False), "observacao": serializers.CharField(max_length=255, required=False, allow_blank=True),
})
DatasEntrada = inline_serializer("DatasEntrada", {"data_inicio": serializers.DateField(required=False, allow_null=True), "data_conclusao": serializers.DateField(required=False, allow_null=True)})
StatusEntrada = inline_serializer("StatusEntrada", {
    "status": serializers.ChoiceField(choices=["enviada", "aprovada", "recusada", "cancelada", "concluida"]), "aceite_nome": serializers.CharField(max_length=150, required=False, help_text="Quem deu o aceite (só ao aprovar)."),
})


def _consumidor(cliente, valor):
    if valor in (None, ""):
        return None
    try:
        return Consumidor.objects.get(pk=int(valor), cliente=cliente, ativo=True, anonimizado_em__isnull=True)
    except (Consumidor.DoesNotExist, TypeError, ValueError):
        raise serializers.ValidationError({"consumidor": ["Cliente final inválido."]}) from None


def _preencher(proposta, entrada, exigir_textos):
    """Copia do corpo SÓ os campos permitidos para a proposta (objeto em memória); devolve (itens, custos) se vieram no corpo, senão (None, None).
    Unidade, número, status, totais e premissas nunca vêm do cliente. `exigir_textos`: cliente e descrição obrigatórios (criação)."""
    dados = dict(entrada.validated_data)
    itens, custos = dados.pop("itens", None), dados.pop("custos", None)
    dados.pop("atualizar_premissas", None)
    if "consumidor" in dados:
        proposta.consumidor = _consumidor(proposta.cliente, dados.pop("consumidor"))
    for campo, valor in dados.items():
        setattr(proposta, campo, valor)
    if proposta.consumidor_id:
        c = proposta.consumidor
        proposta.destinatario_nome = proposta.destinatario_nome or c.nome
        proposta.destinatario_email = proposta.destinatario_email or c.email
        proposta.destinatario_whatsapp = proposta.destinatario_whatsapp or c.whatsapp or c.telefone
    if exigir_textos:
        if not proposta.destinatario_nome:
            raise serializers.ValidationError({"destinatario_nome": ["Informe o cliente ou o cliente final."]})
        if not proposta.descricao:
            raise serializers.ValidationError({"descricao": ["Informe a descrição do serviço."]})
    if proposta.desconto_tipo == "valor":
        proposta.desconto = Decimal(proposta.desconto).quantize(Decimal(1))  # centavos inteiros
    return ([dict(i) for i in itens] if itens is not None else None), ([dict(k) for k in custos] if custos is not None else None)


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH)])
class PropostaViewSet(ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = MODULO
    permissoes = {
        "list": f"{APP}.view_proposta", "retrieve": f"{APP}.view_proposta", "relatorio": f"{APP}.view_proposta", "lancamentos": f"{APP}.view_proposta", "pagamentos": f"{APP}.view_proposta",
        "create": f"{APP}.add_proposta", "partial_update": f"{APP}.change_proposta", "update": f"{APP}.change_proposta", "mudar_status": f"{APP}.change_proposta",
        "criar_lancamento": f"{APP}.change_proposta", "excluir_lancamento": f"{APP}.change_proposta", "criar_pagamento": f"{APP}.change_proposta",
        "excluir_pagamento": f"{APP}.change_proposta", "datas": f"{APP}.change_proposta", "destroy": f"{APP}.delete_proposta",
    }

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        _unidade(request)  # segmento: produto puro nunca usa o módulo

    def _escopo(self, request, acao="view"):
        return Proposta.objects.filter(cliente__in=clientes_dos_itens(request.user, acao)).select_related("cliente", "consumidor")

    def _obter(self, request, pk, acao="view"):
        try:
            return self._escopo(request, acao).get(pk=pk)
        except (Proposta.DoesNotExist, ValueError):
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("q", str, description="Número, cliente ou serviço"), OpenApiParameter("situacao", str, enum=[c for c, _ in Proposta.STATUS_CHOICES]),
                               OpenApiParameter("ordenar", str, enum=["numero", "-numero", "total", "-total", "criado_em", "-criado_em"]), OpenApiParameter("pagina", int), OpenApiParameter("por_pagina", int)],
                   responses={200: OpenApiTypes.OBJECT}, operation_id="propostas_servico_list")
    def list(self, request):
        from django.db.models import Q

        consulta = self._escopo(request)
        termo = request.query_params.get("q", "").strip()
        if termo:
            filtro = Q(destinatario_nome__icontains=termo) | Q(descricao__icontains=termo)
            consulta = consulta.filter(filtro | Q(numero=int(termo)) if termo.isdigit() else filtro)
        if request.query_params.get("situacao") in dict(Proposta.STATUS_CHOICES):
            consulta = consulta.filter(status=request.query_params["situacao"])
        ordem = request.query_params.get("ordenar", "-numero")
        ordem = ordem if ordem.lstrip("-") in ("numero", "total", "criado_em") else "-numero"  # lista fechada: nunca a querystring direto no banco
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_proposta_dict(p, completo=False) for p in pagina.paginate_queryset(consulta.order_by(ordem, "-pk"), request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_proposta_dict(self._obter(request, pk)))

    # --- entrada ---------------------------------------------------------------------------------------------------
    def _consumidor(self, cliente, valor):
        if valor in (None, ""):
            return None
        try:
            return Consumidor.objects.get(pk=int(valor), cliente=cliente, ativo=True, anonimizado_em__isnull=True)
        except (Consumidor.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"consumidor": ["Cliente final inválido."]}) from None

    @extend_schema(request=PropostaEntrada, responses={201: OpenApiTypes.OBJECT}, summary="Cria a proposta (rascunho): custo, preços e lucro são calculados pelo servidor, com as premissas de hoje")
    def create(self, request):
        cliente = _unidade(request)
        entrada = PropostaEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        proposta = Proposta(cliente=cliente, criado_por=request.user)
        itens, custos = _preencher(proposta, entrada, exigir_textos=True)
        proposta = s.salvar_proposta(request, proposta, itens or [], custos or [])
        return resposta(_proposta_dict(proposta), "Proposta criada.", 201)

    @extend_schema(request=PropostaEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Altera (parcial). Usa o retrato das premissas gravado; `atualizar_premissas: true` refaz com os valores de hoje")
    def partial_update(self, request, pk=None):
        proposta = self._obter(request, pk, "change")
        entrada = PropostaEntrada(data=request.data, partial=True)
        entrada.is_valid(raise_exception=True)
        foto = tirar_foto(proposta)
        itens, custos = _preencher(proposta, entrada, exigir_textos=False)
        salvos_i, salvos_c = s.linhas_salvas(proposta)
        proposta = s.salvar_proposta(request, proposta, salvos_i if itens is None else itens, salvos_c if custos is None else custos, foto_antes=foto,
                                     atualizar_premissas=bool(entrada.validated_data.get("atualizar_premissas")))
        return resposta(_proposta_dict(proposta), "Proposta atualizada.")

    update = partial_update

    @extend_schema(request=None, responses={204: None}, summary="Exclui um rascunho (as demais propostas devem ser canceladas)")
    def destroy(self, request, pk=None):
        s.excluir_proposta(request, self._obter(request, pk, "delete"))
        return Response(status=204)

    @extend_schema(request=StatusEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Muda a situação (enviada, aprovada, recusada, cancelada, concluida) pelas transições permitidas")
    @action(detail=True, methods=["post"], url_path="status")
    def mudar_status(self, request, pk=None):
        proposta = self._obter(request, pk, "change")
        dados = request.data if isinstance(request.data, dict) else {}
        s.mudar_status(request, proposta, dados.get("status", ""), str(dados.get("aceite_nome") or "").strip())
        return resposta(_proposta_dict(proposta))

    # --- execução --------------------------------------------------------------------------------------------------
    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Previsto x realizado (horas, custo, valor, recebido e resultado)")
    @action(detail=True, methods=["get"], url_path="relatorio")
    def relatorio(self, request, pk=None):
        return resposta(s.relatorio(self._obter(request, pk)))

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Lista os lançamentos reais (horas, custos, deslocamentos, extras)")
    @action(detail=True, methods=["get"], url_path="lancamentos")
    def lancamentos(self, request, pk=None):
        return resposta([_lancamento_dict(l) for l in self._obter(request, pk).lancamentos.all()])

    @extend_schema(request=LancamentoEntrada, responses={201: OpenApiTypes.OBJECT}, summary="Registra horas/custos reais (só propostas aprovadas ou concluídas)")
    @lancamentos.mapping.post
    def criar_lancamento(self, request, pk=None):
        proposta = self._obter(request, pk, "change")
        entrada = inline_serializer_validado(LancamentoEntrada, request.data)
        return resposta(_lancamento_dict(s.registrar_lancamento(request, proposta, entrada)), "Lançamento registrado.", 201)

    @extend_schema(request=None, responses={204: None}, summary="Exclui um lançamento")
    @action(detail=True, methods=["delete"], url_path=r"lancamentos/(?P<registro_id>\d+)")
    def excluir_lancamento(self, request, pk=None, registro_id=None):
        proposta = self._obter(request, pk, "change")
        try:
            registro = proposta.lancamentos.get(pk=registro_id)
        except Lancamento.DoesNotExist:
            raise NotFound from None
        s.excluir_registro(request, registro)
        return Response(status=204)

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Lista os pagamentos")
    @action(detail=True, methods=["get"], url_path="pagamentos")
    def pagamentos(self, request, pk=None):
        return resposta([_pagamento_dict(g) for g in self._obter(request, pk).pagamentos.all()])

    @extend_schema(request=PagamentoEntrada, responses={201: OpenApiTypes.OBJECT}, summary="Registra um pagamento (só propostas aprovadas ou concluídas)")
    @pagamentos.mapping.post
    def criar_pagamento(self, request, pk=None):
        proposta = self._obter(request, pk, "change")
        entrada = inline_serializer_validado(PagamentoEntrada, request.data)
        return resposta(_pagamento_dict(s.registrar_pagamento(request, proposta, entrada)), "Pagamento registrado.", 201)

    @extend_schema(request=None, responses={204: None}, summary="Exclui um pagamento")
    @action(detail=True, methods=["delete"], url_path=r"pagamentos/(?P<registro_id>\d+)")
    def excluir_pagamento(self, request, pk=None, registro_id=None):
        proposta = self._obter(request, pk, "change")
        try:
            registro = proposta.pagamentos.get(pk=registro_id)
        except Pagamento.DoesNotExist:
            raise NotFound from None
        s.excluir_registro(request, registro)
        return Response(status=204)

    @extend_schema(request=DatasEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Define as datas de início e conclusão do serviço")
    @action(detail=True, methods=["patch"], url_path="datas")
    def datas(self, request, pk=None):
        proposta = self._obter(request, pk, "change")
        entrada = inline_serializer_validado(DatasEntrada, request.data)
        s.atualizar_datas(request, proposta, entrada.get("data_inicio", proposta.data_inicio), entrada.get("data_conclusao", proposta.data_conclusao))
        return resposta(_proposta_dict(proposta))


def inline_serializer_validado(serializer_class, dados):
    """Valida o corpo com o serializer (tipos e limites) e devolve o dict validado."""
    classe = type(serializer_class) if isinstance(serializer_class, serializers.BaseSerializer) else serializer_class  # inline_serializer devolve instância
    entrada = classe(data=dados)
    entrada.is_valid(raise_exception=True)
    return dict(entrada.validated_data)


@extend_schema(tags=[TAG])
class SimularView(APIView):
    """Simulador: calcula custo, preço mínimo, preço-alvo, impostos, lucro e margem SEM gravar nada, com as premissas de hoje."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = MODULO
    permissoes = {"post": f"{APP}.view_proposta"}

    @extend_schema(request=PropostaEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Simula o preço de uma proposta (não grava)")
    def post(self, request):
        cliente = _unidade(request)
        entrada = PropostaEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        proposta = Proposta(cliente=cliente)
        itens, custos = _preencher(proposta, entrada, exigir_textos=False)
        itens, custos = s.normalizar_linhas(itens or [], custos or [])
        resultado, premissas = s.simular(cliente, proposta, itens, custos)
        return resposta({"resultado": resultado, "premissas": premissas})
