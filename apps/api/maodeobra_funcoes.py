"""Funções de mão de obra, itens de custo, simulações de promoção e dashboard na API v1 (módulo Mão de obra).

Mesmas regras das telas: a API só adapta JSON aos formulários e serviços de apps/maodeobra (forms_funcao.py, composicao.py,
custos.py). Dinheiro em CENTAVOS; percentuais como texto decimal ("8.3333"). O cliente (tenant) vem SEMPRE do usuário
autenticado. Custos de pessoas são dados sensíveis: só Administrador e Gestor (o Operador e o Visualizador não acessam).
Os valores calculados são informativos e não alteram o valor da hora/dia usado nos apontamentos.
"""
from decimal import Decimal

from django.db.models import Count, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.views import APIView

from apps.auditoria.utils import registrar_exclusao
from apps.maodeobra import composicao, custos
from apps.maodeobra.forms_funcao import ComponenteForm, DashboardForm, FuncaoForm, SimulacaoForm
from apps.maodeobra.models import ComponenteFuncao, FuncaoMaoDeObra, SimulacaoPromocao
from apps.maodeobra.views_funcao import dados_do_dashboard
from web_project.acesso import cliente_do_usuario, clientes_dos_itens

from .negocio import RecursoViewSet, documentar, erros_de
from .pdv import PermissaoPorAcao
from .views import resposta

MODULO = "mao-de-obra"
TAG = "mao-de-obra"


# --------------------------------------------------------------------------
# Funções
# --------------------------------------------------------------------------

class FuncaoSerializer(serializers.ModelSerializer):
    colaboradores_vinculados = serializers.IntegerField(read_only=True, help_text="Colaboradores com esta função hoje (vínculo aberto).")

    class Meta:
        model = FuncaoMaoDeObra
        fields = ["id", "nome", "descricao", "cbo_codigo", "cbo_descricao", "observacao", "ativo", "colaboradores_vinculados", "criado_em", "atualizado_em"]
        read_only_fields = fields


class FuncaoEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=80)
    descricao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    cbo_codigo = serializers.RegexField(r"^\d{6}$", required=False, allow_blank=True, help_text="CBO: 6 dígitos.")
    cbo_descricao = serializers.CharField(max_length=150, required=False, allow_blank=True)
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ativo = serializers.BooleanField(required=False)


@documentar(TAG, FuncaoSerializer, FuncaoEntrada)
class FuncaoViewSet(RecursoViewSet):
    """Funções de mão de obra da empresa. DELETE: com colaboradores vinculados (ou histórico) só desativa; sem uso apaga junto com os itens de custo."""

    modelo, serializer_class, entrada_class, form_class = FuncaoMaoDeObra, FuncaoSerializer, FuncaoEntrada, FuncaoForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "funcaomaodeobra", MODULO
    campos_busca = ("nome", "cbo_codigo", "cbo_descricao")
    colunas_ordenaveis = {"nome": "nome", "cbo": "cbo_codigo", "criado_em": "criado_em"}
    rotulo = "Função"

    def consulta_base(self):
        return FuncaoMaoDeObra.objects.annotate(colaboradores_vinculados=Count("vinculos", filter=Q(vinculos__fim__isnull=True), distinct=True))

    def dados_atuais(self, f):
        return {"nome": f.nome, "descricao": f.descricao, "cbo_codigo": f.cbo_codigo, "cbo_descricao": f.cbo_descricao, "observacao": f.observacao, "ativo": f.ativo}


# --------------------------------------------------------------------------
# Itens de custo da função
# --------------------------------------------------------------------------

class ComponenteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComponenteFuncao
        fields = ["id", "funcao", "categoria", "nome", "tipo_valor", "valor", "percentual", "base", "periodicidade", "personalizavel", "condicao", "inicio", "fim", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class ComponenteEntrada(serializers.Serializer):
    funcao = serializers.IntegerField(required=False, help_text="Id da função (obrigatório ao criar; não muda depois).")
    categoria = serializers.ChoiceField(choices=[c for c, _ in custos.CATEGORIAS])
    nome = serializers.CharField(max_length=100)
    tipo_valor = serializers.ChoiceField(choices=[c for c, _ in custos.TIPOS_VALOR], required=False)
    valor = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos (tipo fixo).")
    percentual = serializers.DecimalField(max_digits=8, decimal_places=4, min_value=Decimal("0"), required=False, allow_null=True, help_text="Taxa mensal em % (tipo percentual).")
    base = serializers.ChoiceField(choices=[c for c, _ in custos.BASES], required=False, help_text="Base do percentual; cada categoria aceita só as bases anteriores a ela.")
    periodicidade = serializers.ChoiceField(choices=[c for c, _ in custos.PERIODICIDADES], required=False, help_text="mensal ou anual (valor anual ÷ 12).")
    personalizavel = serializers.BooleanField(required=False)
    condicao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    inicio = serializers.DateField(help_text="Início da vigência.")
    fim = serializers.DateField(required=False, allow_null=True)
    ativo = serializers.BooleanField(required=False)


@documentar(TAG, ComponenteSerializer, ComponenteEntrada, [OpenApiParameter("funcao", int, description="Filtra pelos itens de uma função.")])
class ComponenteViewSet(RecursoViewSet):
    """Itens de custo das funções (salário, adicionais, DSR, provisões, encargos, benefícios, indiretos) com vigência.
    DELETE: item já vigente é ENCERRADO (histórico preservado); item futuro sem personalização é apagado."""

    modelo, serializer_class, entrada_class, form_class = ComponenteFuncao, ComponenteSerializer, ComponenteEntrada, ComponenteForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "componentefuncao", MODULO
    campos_busca = ("nome", "condicao")
    colunas_ordenaveis = {"nome": "nome", "inicio": "inicio", "categoria": "categoria"}
    ordenacao_padrao = ["funcao", "categoria", "inicio"]
    rotulo = "Item de custo"
    padroes = {"ativo": True, "tipo_valor": custos.FIXO, "base": custos.B_SALARIO, "periodicidade": custos.MENSAL, "personalizavel": False}

    def get_queryset(self):  # o item não tem coluna de unidade: o escopo é o da função
        if getattr(self, "swagger_fake_view", False):
            return ComponenteFuncao.objects.none()
        cliente = self.cliente()
        queryset = ComponenteFuncao.objects.filter(funcao__cliente=cliente) if cliente else ComponenteFuncao.objects.none()
        if self.action == "list":
            queryset = self.filtrar(queryset, self.request.query_params)
            if self.request.query_params.get("funcao", "").isdigit():
                queryset = queryset.filter(funcao_id=self.request.query_params["funcao"])
        from .views import _ordenar

        return _ordenar(queryset, self.request.query_params, self.colunas_ordenaveis, self.ordenacao_padrao)

    def consulta_base(self):
        return ComponenteFuncao.objects.select_related("funcao")

    def dados_atuais(self, c):
        return {
            "categoria": c.categoria, "nome": c.nome, "tipo_valor": c.tipo_valor, "valor": c.valor, "percentual": c.percentual, "base": c.base, "periodicidade": c.periodicidade,
            "personalizavel": c.personalizavel, "condicao": c.condicao, "inicio": c.inicio, "fim": c.fim, "ativo": c.ativo,
        }

    campos_moeda = ("valor",)

    def _funcao(self, instancia):
        if instancia is not None:
            return instancia.funcao
        identificador = self.request.data.get("funcao")
        try:
            return FuncaoMaoDeObra.objects.get(pk=int(identificador), cliente=self.cliente())
        except (TypeError, ValueError, FuncaoMaoDeObra.DoesNotExist):
            raise serializers.ValidationError({"funcao": ["Informe uma função da sua empresa."]}) from None

    def kwargs_do_formulario(self, instancia):
        return {"funcao": self._funcao(instancia)}

    def gravar(self, request, contexto, dados, instancia, foto):
        contexto["form"].instance.funcao = self._funcao(instancia)
        return super().gravar(request, contexto, dados, instancia, foto)

    def destroy(self, request, *args, **kwargs):
        from django.db import transaction
        from rest_framework.response import Response

        with transaction.atomic():
            composicao.encerrar_ou_excluir_componente(request, self.get_object())
        return Response(status=204)


# --------------------------------------------------------------------------
# Simulações de promoção
# --------------------------------------------------------------------------

class SimulacaoSerializer(serializers.ModelSerializer):
    class Meta:
        model = SimulacaoPromocao
        fields = ["id", "nome", "tipo", "colaborador", "funcao_origem", "funcao_destino", "novo_salario", "ajuste_salario_percentual", "data_referencia", "observacao", "criado_em", "atualizado_em"]
        read_only_fields = fields


class SimulacaoEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=100)
    tipo = serializers.ChoiceField(choices=["pessoa", "funcao"], required=False)
    colaborador = serializers.IntegerField(required=False, allow_null=True, help_text="Tipo pessoa: colaborador da sua empresa.")
    funcao_origem = serializers.IntegerField(required=False, allow_null=True, help_text="Tipo funcao: todos os colaboradores ativos desta função entram.")
    funcao_destino = serializers.IntegerField(required=False, allow_null=True, help_text="Vazio = mesma função (só ajusta o salário).")
    novo_salario = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos por mês. Não combine com ajuste_salario_percentual.")
    ajuste_salario_percentual = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True, help_text="Sobre o salário atual (ex.: 10 ou -5).")
    data_referencia = serializers.DateField()
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


@documentar(TAG, SimulacaoSerializer, SimulacaoEntrada, [OpenApiParameter("tipo", str, enum=["pessoa", "funcao"])])
class SimulacaoViewSet(RecursoViewSet):
    """Cenários de PLANEJAMENTO de promoção. Guardam só parâmetros; nunca alteram colaboradores ou funções.
    O resultado (atual × simulado por período) sai em `GET /{id}/resultado/`."""

    modelo, serializer_class, entrada_class, form_class = SimulacaoPromocao, SimulacaoSerializer, SimulacaoEntrada, SimulacaoForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "simulacaopromocao", MODULO
    permissoes_por_acao = {"resultado": "view"}
    campos_busca = ("nome",)
    campos_moeda = ("novo_salario",)
    colunas_ordenaveis = {"nome": "nome", "data": "data_referencia", "criado_em": "criado_em"}
    ordenacao_padrao = ["-criado_em"]
    rotulo = "Simulação"
    padroes = {"tipo": "pessoa"}

    def filtrar(self, queryset, params):  # sem "ativo"
        termo = (params.get("q") or "").strip()
        if termo:
            queryset = queryset.filter(nome__icontains=termo)
        if params.get("tipo") in ("pessoa", "funcao"):
            queryset = queryset.filter(tipo=params["tipo"])
        return queryset

    def dados_atuais(self, s):
        return {
            "nome": s.nome, "tipo": s.tipo, "colaborador": s.colaborador_id, "funcao_origem": s.funcao_origem_id, "funcao_destino": s.funcao_destino_id,
            "novo_salario": s.novo_salario, "ajuste_salario_percentual": s.ajuste_salario_percentual, "data_referencia": s.data_referencia, "observacao": s.observacao,
        }

    def gravar(self, request, contexto, dados, instancia, foto):
        if instancia is None:
            contexto["form"].instance.criado_por = request.user
        return super().gravar(request, contexto, dados, instancia, foto)

    def destroy(self, request, *args, **kwargs):
        from django.db import transaction
        from rest_framework.response import Response

        simulacao = self.get_object()
        with transaction.atomic():
            registrar_exclusao(request, simulacao)
            simulacao.delete()
        return Response(status=204)

    @action(detail=True, methods=["get"])
    @extend_schema(responses={200: OpenApiTypes.OBJECT}, description="Resultado atual × simulado: totais por período e, por colaborador, a composição atual e a simulada. Somente leitura.")
    def resultado(self, request, *args, **kwargs):
        r = composicao.simular(self.get_object())
        return resposta({
            "quantidade": r["quantidade"], "incompletos": r["incompletos"], "total_mensal_atual": r["total_mensal_atual"], "total_mensal_simulado": r["total_mensal_simulado"],
            "diferenca_mensal": r["diferenca_mensal"], "percentual": None if r["percentual"] is None else str(r["percentual"]),
            "periodos": [{**p, "percentual": None if p["percentual"] is None else str(p["percentual"])} for p in r["periodos"]],
            "colaboradores": [
                {
                    "colaborador": l["colaborador"].pk, "nome": l["colaborador"].nome, "atual": composicao.como_dict(l["atual"]),
                    "simulado": {"total_mensal": l["simulado"].total_mensal, "por_categoria": l["simulado"].por_categoria, "periodos": l["simulado"].periodos, "custo_hora": l["simulado"].custo_hora},
                    "periodos": [{**p, "percentual": None if p["percentual"] is None else str(p["percentual"])} for p in l["periodos"]],
                }
                for l in r["linhas"]
            ],
        })


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@extend_schema(tags=[TAG])
class DashboardMaoDeObraView(APIView):
    """Indicadores de custo de mão de obra da empresa na data (`data`, padrão hoje): total, colaboradores, médio, por função, composição e
    períodos; compara com `comparar_com` e traz os últimos cenários. `incompletos` conta colaboradores sem função/salário (totais parciais)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = MODULO
    permissoes = {"get": "maodeobra.view_colaborador"}

    @extend_schema(parameters=[OpenApiParameter("data", str), OpenApiParameter("comparar_com", str), OpenApiParameter("funcao", int), OpenApiParameter("periodo", str, enum=[p for p, _ in custos.PERIODOS])], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        escopo = clientes_dos_itens(request.user, "view").filter(pk=cliente_do_usuario(request.user).pk)
        form = DashboardForm({k: request.query_params.get(k) for k in ("data", "comparar_com", "funcao", "periodo") if request.query_params.get(k)}, escopo=escopo)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        i = dados_do_dashboard(escopo, form.cleaned_data)
        texto = lambda v: None if v is None else str(v)  # noqa: E731
        comparacao = i.get("comparacao")
        return resposta({
            "data": i["data"].isoformat(), "periodo": form.cleaned_data["periodo"], "quantidade": i["quantidade"], "total_mensal": i["total_mensal"], "medio_mensal": i["medio_mensal"],
            "incompletos": i["incompletos"], "por_funcao": i["por_funcao"], "categorias": i["categorias"], "periodos": i["periodos_lista"],
            "comparacao": None if not comparacao else {
                "data": comparacao["data"].isoformat(), "total_mensal": comparacao["total_mensal"], "quantidade": comparacao["quantidade"], "diferenca": comparacao["diferenca"], "percentual": texto(comparacao["percentual"]),
            },
            "cenarios": [{"id": c["simulacao"].pk, "nome": c["simulacao"].nome, "quantidade": c["quantidade"], "diferenca_mensal": c["diferenca_mensal"], "percentual": texto(c["percentual"])} for c in i["cenarios"]],
        })
