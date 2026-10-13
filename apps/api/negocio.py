"""Base dos recursos de negócio da API v1 (produtos, canais, custos, processados, fichas, precificações...).

Princípio: a API NÃO reimplementa regra de negócio. Cada recurso valida e grava com os MESMOS
formulários e serviços das telas web (que já aplicam escopo por unidade, unicidade, regras de
negócio e auditoria). Aqui só há o "adaptador" entre JSON e esses formulários:

- o serializer de ENTRADA descreve e converte os tipos (dinheiro sempre em CENTAVOS inteiros);
- `para_formulario` transforma o JSON no formato que o formulário espera (ex.: 1250 -> "12,50");
- o formulário valida; erros viram 400 no formato padrão da API;
- o cliente (tenant) vem SEMPRE do usuário autenticado, nunca do corpo da requisição.

PATCH funciona mesclando o que foi enviado com os valores atuais do registro, porque os formulários
exigem o conjunto completo de campos.
"""
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, serializers, viewsets
from rest_framework.response import Response

from apps.auditoria.utils import registrar_criacao, registrar_edicao, registrar_exclusao, tirar_foto
from web_project.acesso import cliente_do_usuario
from web_project.mixins import em_uso

from .permissions import PermissaoDoSistema
from .views import PARAMETROS_BUSCA, PARAMETRO_ATIVO, _filtro_ativo, _ordenar, resposta


def reais(centavos):
    """1250 -> "12,50": o formato que os campos de dinheiro dos formulários aceitam."""
    centavos = int(centavos)
    return f"{centavos // 100},{centavos % 100:02d}"


def para_formulario(dados, campos_moeda=()):
    """JSON validado -> dados de formulário. Booleano falso some (como a caixa desmarcada do HTML), None vira
    vazio, dinheiro em centavos vira texto em reais, listas (itens, taxas) ficam de fora (tratadas à parte)."""
    saida = {}
    for chave, valor in dados.items():
        if isinstance(valor, (list, tuple)):
            continue
        if isinstance(valor, bool):
            if valor:
                saida[chave] = "on"
        elif valor is None:
            saida[chave] = ""
        elif chave in campos_moeda:
            saida[chave] = reais(valor)
        elif isinstance(valor, (date, datetime)):
            saida[chave] = valor.isoformat()
        elif isinstance(valor, (Decimal, float)):
            saida[chave] = str(valor)
        else:
            saida[chave] = valor
    return saida


def dados_do_formset(itens, campos, prefixo="itens"):
    """Lista de itens (dicts) -> dados POST de um formset (management form + um conjunto de campos por linha).
    INITIAL_FORMS fica 0: a gravação SUBSTITUI todos os itens, como nas telas."""
    dados = {f"{prefixo}-TOTAL_FORMS": str(len(itens)), f"{prefixo}-INITIAL_FORMS": "0", f"{prefixo}-MIN_NUM_FORMS": "0", f"{prefixo}-MAX_NUM_FORMS": "1000"}
    for i, item in enumerate(itens):
        for campo in campos:
            dados[f"{prefixo}-{i}-{campo}"] = str(item[campo])
    return dados


def erros_de(*formularios):
    """Erros de formulários/formsets -> {campo: [mensagens]} (o tratador da API monta o envelope)."""
    erros = {}
    for formulario in formularios:
        if hasattr(formulario, "forms"):  # formset: erros gerais e de cada linha (formset.errors é uma lista, não um dict)
            for erro in formulario.non_form_errors():
                erros.setdefault("itens", []).append(str(erro))
            for numero, form_item in enumerate(formulario.forms):
                for campo, lista in form_item.errors.items():
                    erros.setdefault("itens", []).extend(f"Item {numero + 1} — {campo}: {m}" for m in lista)
            continue
        for campo, lista in formulario.errors.items():
            erros.setdefault("non_field_errors" if campo == "__all__" else campo, []).extend(str(m) for m in lista)
    return erros


class RecursoViewSet(mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """list / retrieve / create / update / partial_update / destroy sobre formulários e serviços das telas.

    Cada recurso define: `modelo`, `serializer_class` (saída), `entrada_class` (entrada), `form_class`,
    permissões (`permissao_app`, `permissao_model`, `modulo_slug`), `padroes` (valores de criação),
    `dados_atuais(instancia)`, e, se precisar, `campos_busca`, `colunas_ordenaveis`, `filtrar`, `gravar`."""

    permission_classes = [PermissaoDoSistema]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]
    modelo = None
    entrada_class = None
    form_class = None
    campos_moeda = ()
    campos_busca = ("nome",)
    colunas_ordenaveis = {"nome": "nome", "criado_em": "criado_em"}
    ordenacao_padrao = ["nome"]
    padroes = {"ativo": True}
    rotulo = "Registro"

    # --- consulta ---------------------------------------------------------------------------
    def cliente(self):
        return cliente_do_usuario(self.request.user)

    def consulta_base(self):
        return self.modelo.objects.all()

    def filtrar(self, queryset, params):
        """Filtros de listagem: status e texto (campos de `campos_busca`). Subclasses acrescentam os seus."""
        queryset = _filtro_ativo(queryset, params)
        termo = (params.get("q") or "").strip()
        if termo:
            from django.db.models import Q

            filtro = Q()
            for campo in self.campos_busca:
                filtro |= Q(**{f"{campo}__icontains": termo})
            queryset = queryset.filter(filtro)
        return queryset

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.modelo.objects.none()
        cliente = self.cliente()
        queryset = self.consulta_base().filter(cliente=cliente) if cliente else self.modelo.objects.none()
        if self.action == "list":
            queryset = self.filtrar(queryset, self.request.query_params)
        return _ordenar(queryset, self.request.query_params, self.colunas_ordenaveis, self.ordenacao_padrao)

    def get_serializer_class(self):
        return self.entrada_class if self.action in ("create", "update", "partial_update") else self.serializer_class

    def list(self, request, *args, **kwargs):
        pagina = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(pagina, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        return resposta(self.get_serializer(self.get_object()).data)

    # --- escrita ----------------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        return self._gravar(request, None, partial=False)

    def update(self, request, *args, **kwargs):
        return self._gravar(request, self.get_object(), partial=kwargs.get("partial", False))

    def dados_atuais(self, instancia):
        raise NotImplementedError

    def preparar(self, dados, instancia):
        """Valida com o formulário da tela; devolve {"form": ...} (e, se houver, o formset). Levanta 400 se inválido."""
        form = self.form_class(para_formulario(dados, self.campos_moeda), instance=instancia, **self.kwargs_do_formulario(instancia))
        if not form.is_valid():
            raise serializers.ValidationError(erros_de(form))
        return {"form": form}

    def kwargs_do_formulario(self, instancia):
        return {"cliente": instancia.cliente if instancia is not None else self.cliente()}

    def gravar(self, request, contexto, dados, instancia, foto):
        """Gravação padrão (modelos simples): salva o formulário e audita criação/edição."""
        objeto = contexto["form"].save(commit=False)
        if instancia is None:
            objeto.cliente = self.cliente()  # o dono vem do usuário, nunca do corpo
        objeto.save()
        if instancia is None:
            registrar_criacao(request, objeto)
        else:
            registrar_edicao(request, objeto, foto)
        return objeto

    def _gravar(self, request, instancia, partial):
        entrada = self.entrada_class(data=request.data, partial=partial)
        entrada.is_valid(raise_exception=True)
        foto = tirar_foto(instancia) if instancia is not None else None  # antes do formulário alterar a instância
        dados = {**(self.dados_atuais(instancia) if instancia is not None else self.padroes), **entrada.validated_data}
        with transaction.atomic():
            contexto = self.preparar(dados, instancia)
            objeto = self.gravar(request, contexto, dados, instancia, foto)
        objeto = self.consulta_base().get(pk=objeto.pk)
        saida = self.serializer_class(objeto, context=self.get_serializer_context()).data
        if instancia is None:
            return resposta(saida, f"{self.rotulo} cadastrado com sucesso.", 201)
        return resposta(saida, f"{self.rotulo} atualizado com sucesso.")

    def destroy(self, request, *args, **kwargs):
        """Exclusão segura: sem uso é apagado; em uso (referenciado por outros cadastros) é só DESATIVADO,
        preservando histórico e vínculos. Auditado."""
        objeto = self.get_object()
        with transaction.atomic():
            if em_uso(objeto):
                registrar_exclusao(request, objeto, logica=True)
                objeto.ativo = False
                objeto.save(update_fields=["ativo", "atualizado_em"])
            else:
                registrar_exclusao(request, objeto)
                objeto.delete()
        return Response(status=204)


def documentar(tag, saida, entrada, filtros=(), exemplo=None):
    """Decorador de documentação OpenAPI comum aos recursos: lista paginada com filtros, consulta, criação e
    edição com os serializers de entrada/saída certos (a saída nunca é o serializer de entrada)."""

    def aplicar(cls):
        cls = extend_schema_view(
            list=extend_schema(parameters=[*PARAMETROS_BUSCA, PARAMETRO_ATIVO, *filtros], responses={200: saida(many=True)}),
            retrieve=extend_schema(responses={200: saida}),
            create=extend_schema(request=entrada, responses={201: saida}),
            update=extend_schema(request=entrada, responses={200: saida}),
            partial_update=extend_schema(request=entrada, responses={200: saida}),
            destroy=extend_schema(responses={204: None}, description="Sem uso é apagado; em uso é apenas desativado (ativo=false)."),
        )(cls)
        return extend_schema(tags=[tag])(cls)

    return aplicar
