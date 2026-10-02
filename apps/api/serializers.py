"""Serializers da API v1.

Regras de negócio NÃO são reimplementadas aqui: os serializers reaproveitam
as mesmas funções/formulários das telas web (web_project/regras, UsuarioForm,
Insumo.calcular_preco_unidade, Cliente.clean...). Eles só descrevem os
campos, cuidam do formato (dinheiro nunca é float) e traduzem erros.

Proteção contra mass assignment: todo serializer lista os campos
EXPLICITAMENTE; `cliente`, `usuario`, `preco_unidade`, `is_superuser` etc.
nunca são aceitos na entrada.
"""
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.acl.permissions_matrix import ids_validos_da_matriz
from apps.acl.services import salvar_papel
from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from apps.cliente.validators import normalizar_documento
from apps.categoria.models import Categoria
from apps.insumo.models import LIMITE_CENTAVOS, Insumo
from apps.medida.models import Medida
from apps.modulo.models import Modulo
from apps.usuario.forms import UsuarioForm
from apps.usuario.services import foto_usuario, salvar_usuario
from auth.models import Profile
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .exceptions import RegraDeNegocioViolada


def _erro_unico(mensagem):
    """ValidationError com code "unique" => a API responde 409 (Conflict)."""
    return serializers.ValidationError(mensagem, code="unique")


def _cliente_do_request(serializer):
    return getattr(getattr(serializer.context["request"].user, "profile", None), "cliente", None)


# --------------------------------------------------------------------------
# Autenticação
# --------------------------------------------------------------------------

class LoginSerializer(serializers.Serializer):
    login = serializers.CharField(help_text="Nome de usuário ou e-mail.")
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class CadastroSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class AlterarSenhaSerializer(serializers.Serializer):
    senha_atual = serializers.CharField(write_only=True, trim_whitespace=False)
    nova_senha = serializers.CharField(write_only=True, trim_whitespace=False)


class EsqueciSenhaSerializer(serializers.Serializer):
    email = serializers.EmailField()


class RedefinirSenhaSerializer(serializers.Serializer):
    token = serializers.CharField()
    nova_senha = serializers.CharField(write_only=True, trim_whitespace=False)


class ModuloResumoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Modulo
        fields = ["id", "slug", "nome"]


# --------------------------------------------------------------------------
# Medidas
# --------------------------------------------------------------------------

class MedidaSerializer(serializers.ModelSerializer):
    fator_base = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal("0.000001"))
    sigla_preco = serializers.CharField(read_only=True)
    em_uso = serializers.SerializerMethodField()

    class Meta:
        model = Medida
        fields = [
            "id", "medida", "sigla", "descricao", "equivale", "dimensao", "fator_base",
            "nome_singular", "nome_plural", "sigla_preco", "ativo", "em_uso", "criado_em", "atualizado_em",
        ]
        read_only_fields = ["criado_em", "atualizado_em"]
        # A unicidade é checada sem diferenciar maiúsculas (abaixo); o
        # validador padrão (exato) sairia duplicado e com outra mensagem.
        extra_kwargs = {"medida": {"validators": []}, "sigla": {"validators": []}}

    @extend_schema_field(serializers.BooleanField())
    def get_em_uso(self, medida):
        # Calculado uma vez por requisição (o contexto é compartilhado pelos
        # itens de uma lista), em vez de uma consulta por medida.
        if "ids_em_uso" not in self.context:
            self.context["ids_em_uso"] = Medida.ids_em_uso()
        return medida.pk in self.context["ids_em_uso"]

    def _unico(self, campo, valor, mensagem):
        duplicadas = Medida.objects.filter(**{f"{campo}__iexact": valor})
        if self.instance:
            duplicadas = duplicadas.exclude(pk=self.instance.pk)
        if duplicadas.exists():
            raise _erro_unico(mensagem)
        return valor

    def validate_medida(self, valor):
        return self._unico("medida", valor.strip(), "Já existe uma medida com este nome.")

    def validate_sigla(self, valor):
        return self._unico("sigla", valor.strip(), "Já existe uma medida com esta sigla.")

    def validate(self, dados):
        # Medida em uso: mudar dimensão/fator alteraria o significado dos
        # preços já gravados (mesma regra da tela web).
        if self.instance and self.instance.em_uso():
            for campo in ("dimensao", "fator_base"):
                if campo in dados and dados[campo] != getattr(self.instance, campo):
                    raise RegraDeNegocioViolada(
                        "Esta medida já está em uso; não é possível alterar a dimensão nem o fator de conversão. "
                        "Cadastre uma nova medida e inative esta."
                    )
        return dados


# --------------------------------------------------------------------------
# Categorias
# --------------------------------------------------------------------------

class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = ["id", "nome", "criado_em", "atualizado_em"]
        read_only_fields = ["criado_em", "atualizado_em"]

    def validate_nome(self, nome):
        nome = nome.strip()
        duplicadas = Categoria.objects.filter(cliente=_cliente_do_request(self), nome__iexact=nome)
        if self.instance:
            duplicadas = duplicadas.exclude(pk=self.instance.pk)
        if duplicadas.exists():
            raise _erro_unico("Esta categoria já está cadastrada.")
        return nome


# --------------------------------------------------------------------------
# Insumos
# --------------------------------------------------------------------------

class MedidaResumoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Medida
        fields = ["id", "medida", "sigla", "dimensao"]


class InsumoSerializer(serializers.ModelSerializer):
    medida = serializers.PrimaryKeyRelatedField(queryset=Medida.objects.all(), help_text="Id da unidade comprada (ver /medidas/).")
    medida_detalhe = MedidaResumoSerializer(source="medida", read_only=True)
    quantidade = serializers.DecimalField(
        max_digits=15, decimal_places=4, min_value=Decimal("0.0001"),
        help_text="Quantidade comprada, com ponto decimal (ex.: \"100\" ou \"1.5\").",
    )
    # Dinheiro: inteiro em CENTAVOS (R$ 4,00 -> 400). Nunca float.
    preco_pago = serializers.IntegerField(min_value=0, max_value=LIMITE_CENTAVOS, help_text="Valor pago em centavos (R$ 4,00 = 400).")
    preco_pago_decimal = serializers.SerializerMethodField()
    preco_pago_formatado = serializers.SerializerMethodField()
    preco_unidade = serializers.IntegerField(read_only=True, help_text="Preço de 1 KG/L/UN em centavos (calculado).")
    preco_unidade_decimal = serializers.SerializerMethodField()
    preco_unidade_formatado = serializers.SerializerMethodField()
    rotulo_preco = serializers.CharField(read_only=True)
    quantidade_por_extenso = serializers.CharField(read_only=True)
    codigo = serializers.CharField(read_only=True)

    class Meta:
        model = Insumo
        fields = [
            "id", "codigo", "nome", "medida", "medida_detalhe", "quantidade", "quantidade_por_extenso",
            "preco_pago", "preco_pago_decimal", "preco_pago_formatado",
            "preco_unidade", "preco_unidade_decimal", "preco_unidade_formatado", "rotulo_preco",
            "ativo", "criado_em", "atualizado_em",
        ]
        read_only_fields = ["criado_em", "atualizado_em"]
        validators = []  # unicidade (cliente, nome) tratada em validate_nome

    @extend_schema_field(serializers.CharField())
    def get_preco_pago_decimal(self, insumo):
        return str(DinheiroService.para_decimal(insumo.preco_pago))

    @extend_schema_field(serializers.CharField())
    def get_preco_pago_formatado(self, insumo):
        return DinheiroService.formatar(insumo.preco_pago)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_preco_unidade_decimal(self, insumo):
        return None if insumo.preco_unidade is None else str(DinheiroService.para_decimal(insumo.preco_unidade))

    @extend_schema_field(serializers.CharField())
    def get_preco_unidade_formatado(self, insumo):
        return DinheiroService.formatar(insumo.preco_unidade)

    def validate_nome(self, nome):
        nome = nome.strip()
        if not nome:
            raise serializers.ValidationError("O nome do insumo é obrigatório.")
        duplicados = Insumo.objects.filter(cliente=_cliente_do_request(self), nome__iexact=nome)
        if self.instance:
            duplicados = duplicados.exclude(pk=self.instance.pk)
        if duplicados.exists():
            raise _erro_unico("Este insumo já está cadastrado.")
        return nome

    def validate_medida(self, medida):
        # Só medidas ativas em novos usos; a medida atual de um insumo
        # existente continua válida mesmo que tenha sido inativada depois.
        if not medida.ativo and not (self.instance and self.instance.medida_id == medida.pk):
            raise serializers.ValidationError("Esta medida está inativa.")
        return medida

    def validate(self, dados):
        base = self.instance
        tentativa = Insumo(
            medida=dados.get("medida", getattr(base, "medida", None)),
            quantidade=dados.get("quantidade", getattr(base, "quantidade", None)),
            preco_pago=dados.get("preco_pago", getattr(base, "preco_pago", 0)),
        )
        if tentativa.medida is not None and tentativa.quantidade is not None:
            try:
                tentativa.calcular_preco_unidade()  # mesma regra de gravação (ex.: limite de valor)
            except RegraNegocioError as erro:
                raise serializers.ValidationError(str(erro)) from None
        return dados


# --------------------------------------------------------------------------
# Clientes, módulos, perfis e permissões
# --------------------------------------------------------------------------

class ClienteSerializer(serializers.ModelSerializer):
    perfis = serializers.PrimaryKeyRelatedField(many=True, queryset=Group.objects.all(), required=False)
    cpf_cnpj_formatado = serializers.CharField(read_only=True)

    class Meta:
        model = Cliente
        fields = [
            "id", "tipo_pessoa", "cpf_cnpj", "cpf_cnpj_formatado", "nome_razao_social", "nome_fantasia",
            "ativo", "perfis", "criado_em", "atualizado_em",
        ]
        read_only_fields = ["criado_em", "atualizado_em"]
        extra_kwargs = {"cpf_cnpj": {"validators": []}}  # unicidade checada no documento normalizado (abaixo)

    def validate(self, dados):
        base = self.instance
        documento = normalizar_documento(dados.get("cpf_cnpj", getattr(base, "cpf_cnpj", "")))
        tentativa = Cliente(
            tipo_pessoa=dados.get("tipo_pessoa", getattr(base, "tipo_pessoa", "")),
            cpf_cnpj=documento,
            nome_razao_social=dados.get("nome_razao_social", getattr(base, "nome_razao_social", "")),
        )
        try:
            tentativa.clean()  # valida CPF/CNPJ de verdade (dígitos verificadores)
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict) from None
        duplicados = Cliente.objects.filter(cpf_cnpj=tentativa.cpf_cnpj)
        if base:
            duplicados = duplicados.exclude(pk=base.pk)
        if duplicados.exists():
            raise serializers.ValidationError({"cpf_cnpj": [serializers.ErrorDetail("Já existe um cliente com este CPF/CNPJ.", code="unique")]})
        dados["cpf_cnpj"] = tentativa.cpf_cnpj  # sempre gravado só com dígitos
        return dados


class ModuloSerializer(serializers.ModelSerializer):
    class Meta:
        model = Modulo
        fields = ["id", "slug", "nome", "descricao", "ativo"]


class ModulosDoClienteSerializer(serializers.Serializer):
    modulos = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Modulo.objects.all(), help_text="Ids dos módulos que ficam habilitados; os demais são desabilitados."
    )


class PerfilSerializer(serializers.Serializer):
    """Perfil (papel/Group do ACL) com a lista de ids de permissão."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    permissoes = serializers.ListField(child=serializers.IntegerField(), required=False, default=list, help_text="Ids de /permissoes/.")
    permissoes_codigos = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissoes_codigos(self, grupo):
        return sorted(f"{p.content_type.app_label}.{p.codename}" for p in grupo.permissions.all())

    def to_representation(self, grupo):
        dados = super().to_representation(grupo)
        dados["permissoes"] = sorted(p.pk for p in grupo.permissions.all())
        return dados

    def validate_name(self, nome):
        nome = nome.strip()
        duplicados = Group.objects.filter(name__iexact=nome)
        if self.instance:
            duplicados = duplicados.exclude(pk=self.instance.pk)
        if duplicados.exists():
            raise _erro_unico(f'Já existe um perfil chamado "{nome}".')
        return nome

    def validate_permissoes(self, ids):
        invalidos = set(ids) - ids_validos_da_matriz()
        if invalidos:
            raise serializers.ValidationError(f"Permissões inválidas: {sorted(invalidos)}.")
        return ids

    def save(self, **kwargs):
        request = self.context["request"]
        dados = self.validated_data
        if self.instance is None:
            self.instance = salvar_papel(request, None, dados["name"], dados.get("permissoes", []))
        else:
            # PATCH sem "permissoes" mantém as atuais.
            ids = dados.get("permissoes", [p.pk for p in self.instance.permissions.all()])
            self.instance = salvar_papel(request, self.instance, dados.get("name", self.instance.name), ids)
        return self.instance


class PermissaoModuloSerializer(serializers.Serializer):
    app_label = serializers.CharField()
    nome = serializers.CharField()
    acoes = serializers.ListField(child=serializers.DictField())


# --------------------------------------------------------------------------
# Usuários (reaproveita o UsuarioForm da tela web: mesmas validações)
# --------------------------------------------------------------------------

class UsuarioSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(max_length=150)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.CharField()
    is_active = serializers.BooleanField(required=False, default=True)
    cliente = serializers.IntegerField(help_text="Id do cliente (ver /clientes/).")
    perfil = serializers.IntegerField(help_text="Id do perfil, que deve estar liberado para o cliente (ver /perfis/).")
    segmento_trabalho = serializers.ChoiceField(choices=Profile.SEGMENTO_CHOICES, required=False, default=Profile.SEGMENTO_PRODUTO)
    senha = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    confirmar_senha = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)

    def to_representation(self, usuario):
        profile = usuario.profile
        grupos = list(usuario.groups.all())  # usa o prefetch (.first() faria uma consulta por usuário)
        grupo = grupos[0] if grupos else None
        return {
            "id": usuario.pk,
            "username": usuario.username,
            "first_name": usuario.first_name,
            "last_name": usuario.last_name,
            "email": usuario.email,
            "is_active": usuario.is_active,
            "cliente": {"id": profile.cliente_id, "nome": profile.cliente.nome_razao_social} if profile.cliente_id else None,
            "perfil": {"id": grupo.pk, "nome": grupo.name} if grupo else None,
            "segmento_trabalho": profile.segmento_trabalho,
        }

    def validate(self, dados):
        usuario = self.instance
        # Foto "antes" ANTES de o formulário alterar o objeto (auditoria).
        self._foto_antes = foto_usuario(usuario) if usuario else None

        # PATCH: completa com os valores atuais (o form valida o conjunto inteiro).
        base = {}
        if usuario:
            grupo = usuario.groups.first()
            base = {
                "username": usuario.username, "first_name": usuario.first_name, "last_name": usuario.last_name,
                "email": usuario.email, "is_active": usuario.is_active, "cliente": usuario.profile.cliente_id,
                "perfil": grupo.pk if grupo else None, "segmento_trabalho": usuario.profile.segmento_trabalho,
            }
        base.update(dados)

        form = UsuarioForm(data=base, instance=usuario, solicitante=self.context["request"].user)
        if not form.is_valid():
            erros = {}
            for campo, lista in form.errors.as_data().items():
                chave = campo if campo != "__all__" else "non_field_errors"
                erros[chave] = [serializers.ErrorDetail(" ".join(e.messages), code=e.code or "invalid") for e in lista]
            raise serializers.ValidationError(erros)
        self._form = form
        return dados

    def save(self, **kwargs):
        self.instance = salvar_usuario(self.context["request"], self._form, self._foto_antes)
        return self.instance


# --------------------------------------------------------------------------
# Auditoria (somente leitura)
# --------------------------------------------------------------------------

class AuditoriaSerializer(serializers.ModelSerializer):
    usuario = serializers.CharField(source="usuario.username", default=None, read_only=True)
    cliente = serializers.CharField(source="cliente.nome_razao_social", default=None, read_only=True)
    acao_rotulo = serializers.CharField(read_only=True)

    class Meta:
        model = RegistroAuditoria
        fields = [
            "id", "criado_em", "usuario", "cliente", "acao", "acao_rotulo", "modulo", "tabela",
            "registro_id", "registro_nome", "descricao", "alteracoes", "ip", "resultado",
        ]
        read_only_fields = fields
