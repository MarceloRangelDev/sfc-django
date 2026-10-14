"""Consumidores (clientes finais) na API v1.

- Validação e gravação reaproveitam o formulário/model da tela (Consumidor.clean) e a auditoria.
- LISTA: devolve só o necessário (documento MASCARADO, sem contatos nem endereço completo). Contatos e o
  documento completo só no DETALHE (GET /consumidores/{id}/), que exige a permissão de visualizar.
- DELETE = anonimização (LGPD), irreversível; devolve 204. Anonimizado não pode ser editado.
- Consultas de CEP (ViaCEP) e CNPJ (BrasilAPI) são apenas sugestões; nada é gravado por elas.
"""
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, Throttled
from rest_framework.response import Response

from apps.consumidor.forms import ConsumidorForm
from apps.consumidor.models import Consumidor
from apps.consumidor.services import (
    ConsultaIndisponivel, ConsultaInvalida, ConsultaLimite, ConsultaNaoEncontrada, anonimizar_consumidor,
    consultar_cep, consultar_cnpj,
)

from .negocio import RecursoViewSet, documentar
from .views import _filtro_ativo, resposta

CAMPOS_DETALHE = [
    "id", "nome", "tipo_pessoa", "documento", "documento_mascarado", "email", "telefone", "whatsapp", "instagram",
    "data_nascimento", "cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf",
    "consentimento_lgpd", "consentimento_em", "ativo", "anonimizado_em", "criado_em", "atualizado_em",
]
# Campos que a LISTA nunca devolve (dado pessoal sem necessidade em listagens).
SOMENTE_NO_DETALHE = {"documento", "email", "telefone", "whatsapp", "instagram", "data_nascimento", "cep", "logradouro", "numero", "complemento", "bairro"}


class ConsumidorSerializer(serializers.ModelSerializer):
    documento_mascarado = serializers.CharField(read_only=True)

    class Meta:
        model = Consumidor
        fields = CAMPOS_DETALHE
        read_only_fields = fields

    def to_representation(self, instancia):
        dados = super().to_representation(instancia)
        view = self.context.get("view")
        if view is not None and getattr(view, "action", None) == "list":
            for campo in SOMENTE_NO_DETALHE:
                dados.pop(campo, None)
        return dados


class ConsumidorEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    tipo_pessoa = serializers.ChoiceField(choices=["PF", "PJ"], required=False)
    documento = serializers.CharField(max_length=18, required=False, allow_blank=True, help_text="CPF (PF) ou CNPJ (PJ), com ou sem máscara; os dígitos verificadores são conferidos.")
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    telefone = serializers.CharField(max_length=20, required=False, allow_blank=True, help_text="DDD + número (10 ou 11 dígitos).")
    whatsapp = serializers.CharField(max_length=20, required=False, allow_blank=True, help_text="DDD + número, com DDI 55 opcional (10 a 13 dígitos).")
    instagram = serializers.CharField(max_length=40, required=False, allow_blank=True, help_text="Usuário, com ou sem @.")
    data_nascimento = serializers.DateField(required=False, allow_null=True)
    cep = serializers.CharField(max_length=9, required=False, allow_blank=True)
    logradouro = serializers.CharField(max_length=150, required=False, allow_blank=True)
    numero = serializers.CharField(max_length=20, required=False, allow_blank=True)
    complemento = serializers.CharField(max_length=100, required=False, allow_blank=True)
    bairro = serializers.CharField(max_length=100, required=False, allow_blank=True)
    cidade = serializers.CharField(max_length=100, required=False, allow_blank=True)
    uf = serializers.CharField(max_length=2, required=False, allow_blank=True)
    consentimento_lgpd = serializers.BooleanField(required=False)
    ativo = serializers.BooleanField(required=False)


class IndisponivelAPI(APIException):
    status_code = 503
    default_detail = "O serviço de consulta está indisponível no momento."
    default_code = "indisponivel"


@documentar("consumidores", ConsumidorSerializer, ConsumidorEntrada)
class ConsumidorViewSet(RecursoViewSet):
    """Consumidores (clientes finais) da empresa do usuário. A lista traz o documento mascarado e não traz contatos;
    o detalhe traz tudo. DELETE anonimiza (apaga os dados pessoais, irreversível)."""

    modelo, serializer_class, entrada_class, form_class = Consumidor, ConsumidorSerializer, ConsumidorEntrada, ConsumidorForm
    permissao_app, permissao_model, modulo_slug = "consumidor", "consumidor", "consumidor"
    campos_busca = ("nome", "cidade")
    colunas_ordenaveis = {"nome": "nome", "cidade": "cidade", "criado_em": "criado_em"}
    rotulo = "Consumidor"
    permissoes_por_acao = {"consulta_cep": "add", "consulta_cnpj": "add"}
    padroes = {"ativo": True, "tipo_pessoa": "PF"}

    def get_queryset(self):
        queryset = super().get_queryset()
        # Anonimizado não se edita nem se consulta como dado pessoal; segue visível só na lista/detalhe de leitura.
        if self.action in ("update", "partial_update", "destroy"):
            queryset = queryset.filter(anonimizado_em__isnull=True)
        return queryset

    def filtrar(self, queryset, params):
        digitos = "".join(c for c in (params.get("q") or "") if c.isdigit())
        if len(digitos) < 3:
            return super().filtrar(queryset, params)
        # Também pesquisa por trecho do documento (sem devolvê-lo na lista). O queryset já vem do escopo do tenant.
        termo = params["q"].strip()
        return _filtro_ativo(queryset, params).filter(
            Q(nome__icontains=termo) | Q(cidade__icontains=termo) | Q(documento__contains=digitos)
        )

    def dados_atuais(self, c):
        return {campo: getattr(c, campo) for campo in (
            "nome", "tipo_pessoa", "documento", "email", "telefone", "whatsapp", "instagram", "data_nascimento", "cep",
            "logradouro", "numero", "complemento", "bairro", "cidade", "uf", "consentimento_lgpd", "ativo")}

    def destroy(self, request, *args, **kwargs):
        anonimizar_consumidor(request, self.get_object())
        return Response(status=204)

    @extend_schema(responses={200: None}, description="Sugestão de endereço pelo CEP (ViaCEP). Não grava nada; confira os dados antes de salvar. Limite: 30 consultas/minuto por usuário.")
    @action(detail=False, methods=["get"], url_path=r"consulta-cep/(?P<cep>[0-9\-\.]+)")
    def consulta_cep(self, request, cep=None):
        return self._consultar(consultar_cep, cep)

    @extend_schema(responses={200: None}, description="Sugestão de dados cadastrais pelo CNPJ (BrasilAPI). Não grava nada; confira os dados antes de salvar. Limite: 30 consultas/minuto por usuário.")
    @action(detail=False, methods=["get"], url_path=r"consulta-cnpj/(?P<cnpj>[0-9\-\./]+)")
    def consulta_cnpj(self, request, cnpj=None):
        return self._consultar(consultar_cnpj, cnpj)

    def _consultar(self, funcao, valor):
        try:
            return resposta(funcao(self.request.user, valor))
        except ConsultaInvalida as erro:
            raise serializers.ValidationError({"consulta": [str(erro)]}) from None
        except ConsultaNaoEncontrada:
            from django.http import Http404

            raise Http404 from None
        except ConsultaLimite as erro:
            raise Throttled(wait=erro.espera) from None
        except ConsultaIndisponivel as erro:
            raise IndisponivelAPI(str(erro)) from None
