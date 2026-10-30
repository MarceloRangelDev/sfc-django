"""Mão de obra por hora/dia na API v1 (segmentos Serviço e Produto e Serviço).

Cálculo SEMPRE no servidor (regras em apps/maodeobra/calculo.py): o app envia colaborador, data e horários (ou dias) e recebe
minutos, horas extras e valores. Dinheiro em centavos. O apontamento guarda os parâmetros usados (snapshot): mudar o valor da
hora ou as regras depois não altera lançamentos antigos; PUT/PATCH em um apontamento o RECALCULA com os valores de hoje.
Valores de hora e diária são dados sensíveis: só Administrador e Gestor acessam (o Operador não).
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.views import APIView

from apps.auditoria.utils import registrar_edicao, tirar_foto
from apps.maodeobra import composicao
from apps.maodeobra.forms_funcao import PersonalizacaoForm
from apps.maodeobra.forms import ApontamentoForm, ColaboradorForm, ConfiguracaoForm, PeriodoForm
from apps.maodeobra.models import Apontamento, Colaborador, ConfiguracaoMaoDeObra
from apps.maodeobra.views import linhas_do_relatorio
from web_project.acesso import cliente_do_usuario, clientes_dos_itens

from .negocio import RecursoViewSet, documentar, erros_de, reais
from .pdv import PermissaoPorAcao
from .views import resposta


class ColaboradorSerializer(serializers.ModelSerializer):
    funcao_vinculada = serializers.SerializerMethodField(help_text="Id da função cadastrada vigente (vínculo aberto) ou null.")
    funcao_vinculada_nome = serializers.SerializerMethodField()

    class Meta:
        model = Colaborador
        fields = ["id", "nome", "funcao", "funcao_vinculada", "funcao_vinculada_nome", "valor_mes", "valor_hora", "valor_dia", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields

    def _vinculo(self, c):
        abertos = getattr(c, "vinculo_aberto_lista", None)
        if abertos is None:
            return c.vinculos_funcao.filter(fim__isnull=True).select_related("funcao").first()
        return abertos[0] if abertos else None

    def get_funcao_vinculada(self, c) -> int | None:
        v = self._vinculo(c)
        return v.funcao_id if v else None

    def get_funcao_vinculada_nome(self, c) -> str | None:
        v = self._vinculo(c)
        return v.funcao.nome if v else None


class ColaboradorEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=120)
    funcao = serializers.CharField(max_length=80, required=False, allow_blank=True)
    valor_mes = serializers.IntegerField(min_value=1, required=False, allow_null=True, help_text="Centavos por mês (opcional): calcula hora e dia pelas regras da unidade (horas e dias por mês).")
    valor_hora = serializers.IntegerField(min_value=1, required=False, help_text="Centavos por hora (obrigatório se não houver valor por mês).")
    valor_dia = serializers.IntegerField(min_value=0, required=False, allow_null=True, help_text="Centavos por dia; vazio = valor da hora × jornada.")
    funcao_vinculada = serializers.IntegerField(required=False, allow_null=True, help_text="Id de uma função cadastrada da sua empresa. Traz o salário da função para valor_mes (a menos que o item de salário seja personalizável e valor_mes seja informado) e registra o histórico.")
    funcao_inicio = serializers.DateField(required=False, help_text="Vigência da nova função (padrão hoje). A função anterior é encerrada na véspera.")
    ativo = serializers.BooleanField(required=False)


@documentar("mao-de-obra", ColaboradorSerializer, ColaboradorEntrada)
class ColaboradorViewSet(RecursoViewSet):
    """Colaboradores (valor da hora e da diária). DELETE: com apontamentos só desativa."""

    modelo, serializer_class, entrada_class, form_class = Colaborador, ColaboradorSerializer, ColaboradorEntrada, ColaboradorForm
    permissao_app, permissao_model, modulo_slug = "maodeobra", "colaborador", "mao-de-obra"
    campos_busca = ("nome", "funcao")
    campos_moeda = ("valor_mes", "valor_hora", "valor_dia")
    rotulo = "Colaborador"
    permissoes_por_acao = {"composicao": "view", "personalizacoes": "change"}

    def consulta_base(self):
        from django.db.models import Prefetch

        from apps.maodeobra.models import VinculoFuncao

        return Colaborador.objects.prefetch_related(Prefetch("vinculos_funcao", queryset=VinculoFuncao.objects.filter(fim__isnull=True).select_related("funcao"), to_attr="vinculo_aberto_lista"))

    def dados_atuais(self, c):
        dados = {"nome": c.nome, "funcao": c.funcao, "valor_mes": c.valor_mes, "valor_hora": c.valor_hora, "valor_dia": c.valor_dia, "ativo": c.ativo}
        vinculo = c.vinculos_funcao.filter(fim__isnull=True).first()
        if vinculo:  # mantém o salário coerente com a função mesmo num PATCH que só mexe em outros campos
            dados["funcao_vinculada"] = vinculo.funcao_id
        return dados

    def gravar(self, request, contexto, dados, instancia, foto):
        colaborador = super().gravar(request, contexto, dados, instancia, foto)
        form = contexto["form"]
        funcao = form.cleaned_data.get("funcao_vinculada")
        if funcao is not None:
            composicao.trocar_funcao(colaborador, funcao, form.cleaned_data["funcao_inicio"], request.user)
        return colaborador

    @extend_schema(parameters=[OpenApiParameter("data", str, description="AAAA-MM-DD (padrão hoje).")], responses={200: OpenApiTypes.OBJECT},
                   description="Composição de custo na data: itens (herdado × personalizado), total mensal, custo por dia/semana/quinzena/mês/bimestre/trimestre/semestre/ano e por hora. Informativo.")
    @action(detail=True, methods=["get"])
    def composicao(self, request, *args, **kwargs):
        from django.utils import timezone

        data = timezone.localdate()
        if request.query_params.get("data"):
            try:
                data = date.fromisoformat(request.query_params["data"])
            except ValueError:
                raise serializers.ValidationError({"data": ["Use o formato AAAA-MM-DD."]}) from None
        return resposta(composicao.como_dict(composicao.composicao_do_colaborador(self.get_object(), data)))

    @extend_schema(request=inline_serializer("PersonalizacoesEntrada", {"itens": serializers.ListField(child=serializers.DictField(), help_text="[{componente, valor (centavos) | percentual (texto)}]. Item omitido ou com valor nulo volta a herdar a função.")}),
                   responses={200: OpenApiTypes.OBJECT}, description="Substitui TODAS as personalizações do vínculo atual. Só vale para itens marcados como personalizáveis (o salário se altera no próprio colaborador).")
    @action(detail=True, methods=["put"])
    def personalizacoes(self, request, *args, **kwargs):
        from django.db import transaction
        from django.utils import timezone

        from apps.maodeobra.models import ComponenteFuncao

        colaborador = self.get_object()
        resultado = composicao.composicao_do_colaborador(colaborador, timezone.localdate())
        if not resultado.vinculo:
            raise serializers.ValidationError({"itens": ["Vincule uma função ao colaborador antes de personalizar itens."]})
        itens = request.data.get("itens")
        if not isinstance(itens, list):
            raise serializers.ValidationError({"itens": ["Envie uma lista de itens."]})
        componentes = list(ComponenteFuncao.objects.filter(funcao=resultado.vinculo.funcao, funcao__cliente=colaborador.cliente_id, ativo=True))
        dados = {}
        for item in itens:
            if not isinstance(item, dict) or not str(item.get("componente", "")).isdigit():
                raise serializers.ValidationError({"itens": ["Cada item precisa de 'componente' (id)."]})
            componente = next((c for c in componentes if c.pk == int(item["componente"])), None)
            if componente is None or not componente.personalizavel or componente.categoria == "salario":
                raise serializers.ValidationError({"itens": [f"O item {item['componente']} não pode ser personalizado."]})
            valor = item.get("valor") if componente.tipo_valor == "fixo" else item.get("percentual")
            dados[f"item_{componente.pk}"] = "" if valor is None else (reais(valor) if componente.tipo_valor == "fixo" else str(valor))
        form = PersonalizacaoForm(dados, vinculo=resultado.vinculo, componentes=componentes)
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        try:
            with transaction.atomic():
                form.salvar()
        except DjangoValidationError as erro:
            raise serializers.ValidationError({"itens": erro.messages}) from None
        return resposta(composicao.como_dict(composicao.composicao_do_colaborador(colaborador, timezone.localdate())), "Personalizações salvas.")


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
        return {"arredondamento": c.arredondamento, "jornada_horas": str(c.jornada_horas), "horas_mes": str(c.horas_mes), "dias_mes": str(c.dias_mes), "extra_percentual": str(c.extra_percentual), "adicional_domingo": str(c.adicional_domingo)}

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return resposta(self._dados(ConfiguracaoMaoDeObra.da_unidade(cliente_do_usuario(request.user))))

    @extend_schema(request=inline_serializer("RegrasMaoDeObra", {
        "arredondamento": serializers.ChoiceField(choices=["minuto", "quarto", "hora"], required=False), "jornada_horas": serializers.DecimalField(max_digits=4, decimal_places=2, required=False),
        "horas_mes": serializers.DecimalField(max_digits=6, decimal_places=2, required=False), "dias_mes": serializers.DecimalField(max_digits=4, decimal_places=1, required=False),
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
