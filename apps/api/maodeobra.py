"""Mão de obra por hora/dia na API v1 (segmentos Serviço e Produto e Serviço).

Cálculo SEMPRE no servidor (regras em apps/maodeobra/calculo.py): o app envia colaborador, data e horários (ou dias) e recebe
minutos, horas extras e valores. Dinheiro em centavos. O apontamento guarda os parâmetros usados (snapshot): mudar o valor da
hora ou as regras depois não altera lançamentos antigos; PUT/PATCH em um apontamento o RECALCULA com os valores de hoje.
Valores de hora e diária são dados sensíveis: só Administrador e Gestor acessam (o Operador não).
"""
from decimal import Decimal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.views import APIView

from apps.auditoria.utils import registrar_edicao, tirar_foto
from apps.maodeobra.forms import ApontamentoForm, ColaboradorForm, ConfiguracaoForm, PeriodoForm
from apps.maodeobra.models import Apontamento, Colaborador, ConfiguracaoMaoDeObra
from apps.maodeobra.views import linhas_do_relatorio
from web_project.acesso import cliente_do_usuario, clientes_dos_itens

from .negocio import RecursoViewSet, documentar, erros_de
from .pdv import PermissaoPorAcao
from .views import resposta


class ColaboradorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colaborador
        fields = ["id", "nome", "funcao", "valor_hora", "valor_dia", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class ColaboradorEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=120)
    funcao = serializers.CharField(max_length=80, required=False, allow_blank=True)
    valor_hora = serializers.IntegerField(min_value=1, help_text="Centavos por hora.")
    valor_dia = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos por dia; vazio = valor da hora × jornada.")
    ativo = serializers.BooleanField(required=False)


@documentar("mao-de-obra", ColaboradorSerializer, ColaboradorEntrada)
class ColaboradorViewSet(RecursoViewSet):
    """Colaboradores (valor da hora e da diária). DELETE: com apontamentos só desativa."""

    modelo, serializer_class, entrada_class, form_class = Colaborador, ColaboradorSerializer, ColaboradorEntrada, ColaboradorForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "colaborador", "mao-de-obra"
    campos_busca = ("nome", "funcao")
    campos_moeda = ("valor_hora", "valor_dia")
    rotulo = "Colaborador"

    def dados_atuais(self, c):
        return {"nome": c.nome, "funcao": c.funcao, "valor_hora": c.valor_hora, "valor_dia": c.valor_dia, "ativo": c.ativo}


class ApontamentoSerializer(serializers.ModelSerializer):
    colaborador_nome = serializers.CharField(source="colaborador.nome", read_only=True)
    servico_nome = serializers.CharField(source="servico.nome", read_only=True, default=None)

    class Meta:
        model = Apontamento
        fields = [
            "id", "colaborador", "colaborador_nome", "servico", "servico_nome", "descricao", "data", "tipo", "inicio", "fim", "pausa_minutos", "dias", "observacao",
            "valor_hora_usado", "valor_dia_usado", "arredondamento_usado", "jornada_usada", "extra_percentual_usado", "adicional_domingo_usado",
            "minutos_trabalhados", "minutos_normais", "minutos_extra", "valor_normal", "valor_extra", "valor_total", "criado_em", "atualizado_em",
        ]
        read_only_fields = fields


class ApontamentoEntrada(serializers.Serializer):
    colaborador = serializers.IntegerField(help_text="Id de um colaborador ATIVO da sua empresa.")
    servico = serializers.IntegerField(required=False, allow_null=True, help_text="Id de um produto (serviço) da empresa; opcional.")
    descricao = serializers.CharField(max_length=150, required=False, allow_blank=True)
    data = serializers.DateField()
    tipo = serializers.ChoiceField(choices=["hora", "dia"], required=False)
    inicio = serializers.TimeField(required=False, allow_null=True, help_text="HH:MM (tipo hora). Fim menor que o início = passou da meia-noite.")
    fim = serializers.TimeField(required=False, allow_null=True)
    pausa_minutos = serializers.IntegerField(min_value=0, required=False, help_text="Descontada do período.")
    dias = serializers.DecimalField(max_digits=4, decimal_places=1, min_value=Decimal("0.5"), max_value=Decimal("31"), required=False, allow_null=True, help_text="Dias trabalhados (tipo dia).")
    observacao = serializers.CharField(max_length=255, required=False, allow_blank=True)


@documentar("mao-de-obra", ApontamentoSerializer, ApontamentoEntrada, [OpenApiParameter("colaborador", int), OpenApiParameter("de", str), OpenApiParameter("ate", str)])
class ApontamentoViewSet(RecursoViewSet):
    """Apontamentos por hora ou por dia, calculados no servidor. DELETE exclui (auditado)."""

    modelo, serializer_class, entrada_class, form_class = Apontamento, ApontamentoSerializer, ApontamentoEntrada, ApontamentoForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "apontamento", "mao-de-obra"
    campos_busca = ("colaborador__nome", "descricao")
    colunas_ordenaveis = {"data": "data", "total": "valor_total", "criado_em": "criado_em"}
    ordenacao_padrao = ["-data", "-pk"]
    rotulo = "Apontamento"
    padroes = {"tipo": "hora", "pausa_minutos": 0}

    def consulta_base(self):
        return Apontamento.objects.select_related("colaborador", "servico")

    def filtrar(self, queryset, params):  # sem "ativo"
        termo = (params.get("q") or "").strip()
        if termo:
            from django.db.models import Q

            queryset = queryset.filter(Q(colaborador__nome__icontains=termo) | Q(descricao__icontains=termo))
        if params.get("colaborador", "").isdigit():
            queryset = queryset.filter(colaborador_id=params["colaborador"])
        if params.get("de"):
            queryset = queryset.filter(data__gte=params["de"])
        if params.get("ate"):
            queryset = queryset.filter(data__lte=params["ate"])
        return queryset

    def dados_atuais(self, a):
        return {
            "colaborador": a.colaborador_id, "servico": a.servico_id, "descricao": a.descricao, "data": a.data, "tipo": a.tipo, "inicio": a.inicio, "fim": a.fim,
            "pausa_minutos": a.pausa_minutos, "dias": a.dias, "observacao": a.observacao,
        }

    def kwargs_do_formulario(self, instancia):
        cliente = instancia.cliente if instancia is not None else self.cliente()
        return {"cliente": cliente}

    def gravar(self, request, contexto, dados, instancia, foto):
        apontamento = contexto["form"].instance
        if instancia is None:
            apontamento.criado_por = request.user
        return super().gravar(request, contexto, dados, instancia, foto)

    def destroy(self, request, *args, **kwargs):
        from django.db import transaction
        from rest_framework.response import Response

        from apps.auditoria.utils import registrar_exclusao

        apontamento = self.get_object()
        with transaction.atomic():
            registrar_exclusao(request, apontamento)
            apontamento.delete()
        return Response(status=204)


@extend_schema(tags=["mao-de-obra"])
class ConfiguracaoMaoDeObraView(APIView):
    """Regras de cálculo da unidade: GET e PATCH em /mao-de-obra/regras/ (valem para os próximos apontamentos)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "mao-de-obra"
    permissoes = {"get": "maodeobra.view_apontamento", "patch": "maodeobra.change_configuracaomaodeobra"}

    def _dados(self, c):
        return {"arredondamento": c.arredondamento, "jornada_horas": str(c.jornada_horas), "extra_percentual": str(c.extra_percentual), "adicional_domingo": str(c.adicional_domingo)}

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta(self._dados(ConfiguracaoMaoDeObra.da_unidade(cliente_do_usuario(request.user))))

    @extend_schema(request=inline_serializer("RegrasMaoDeObra", {
        "arredondamento": serializers.ChoiceField(choices=["minuto", "quarto", "hora"], required=False), "jornada_horas": serializers.DecimalField(max_digits=4, decimal_places=2, required=False),
        "extra_percentual": serializers.DecimalField(max_digits=6, decimal_places=2, required=False), "adicional_domingo": serializers.DecimalField(max_digits=6, decimal_places=2, required=False)}),
        responses={200: OpenApiTypes.OBJECT})
    def patch(self, request):
        from django.db import transaction

        config = ConfiguracaoMaoDeObra.da_unidade(cliente_do_usuario(request.user))
        foto = tirar_foto(config)
        atuais = self._dados(config)
        form = ConfiguracaoForm({**atuais, **{k: v for k, v in request.data.items() if k in atuais}}, instance=config)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        with transaction.atomic():
            config = form.save()
            registrar_edicao(request, config, foto)
        return resposta(self._dados(config), "Regras salvas.")


@extend_schema(tags=["mao-de-obra"])
class RelatorioMaoDeObraView(APIView):
    """Resumo por colaborador no período (`de`, `ate`, `colaborador`)."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "mao-de-obra"
    permissoes = {"get": "maodeobra.view_apontamento"}

    @extend_schema(parameters=[OpenApiParameter("de", str), OpenApiParameter("ate", str), OpenApiParameter("colaborador", int)], responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        escopo = clientes_dos_itens(request.user, "view").filter(pk=cliente_do_usuario(request.user).pk)
        consulta = Apontamento.objects.filter(cliente__in=escopo)
        form = PeriodoForm({"inicio": request.query_params.get("de"), "fim": request.query_params.get("ate"), "colaborador": request.query_params.get("colaborador")}, escopo=escopo)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        d = form.cleaned_data
        if d.get("inicio"):
            consulta = consulta.filter(data__gte=d["inicio"])
        if d.get("fim"):
            consulta = consulta.filter(data__lte=d["fim"])
        if d.get("colaborador"):
            consulta = consulta.filter(colaborador=d["colaborador"])
        linhas = linhas_do_relatorio(consulta)
        itens = [{"colaborador": l["colaborador_id"], "nome": l["colaborador__nome"], "apontamentos": l["qtd"], "minutos_trabalhados": l["minutos"] or 0, "minutos_extra": l["extra"] or 0,
                  "dias": str(l["dias"]), "valor_normal": l["normal"] or 0, "valor_extra": l["valor_extra"] or 0, "total": l["total"] or 0} for l in linhas]
        return resposta({"itens": itens, "total": sum(i["total"] for i in itens)})
