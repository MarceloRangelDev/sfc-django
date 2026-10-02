"""Views da API v1 (ver README, seção "API").

Padrão: cada recurso é um ViewSet fino. Regras de negócio, validações e
auditoria moram nos mesmos serviços usados pelas telas web — aqui só há
autenticação, permissão, escopo por cliente e o formato HTTP.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ErrorDetail, Throttled, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.acl.permissions_matrix import montar_matriz_permissoes
from apps.acl.services import excluir_papel
from apps.auditoria.filtros import filtrar_registros
from apps.auditoria.utils import registrar, registrar_criacao, registrar_edicao, registrar_exclusao, tirar_foto
from apps.auditoria.views import registros_visiveis
from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.medida.models import Medida
from apps.modulo.models import ClienteModulo, Modulo
from apps.modulo.services import atualizar_modulos_do_cliente
from apps.usuario.services import foto_usuario
from apps.usuario.views import usuarios_gerenciaveis
from auth import ratelimit
from auth.services import (
    ErroDeAutenticacao, cadastrar_usuario_publico, iniciar_redefinicao_de_senha,
    redefinir_senha, validar_forca_da_senha,
)
from web_project.acesso import cliente_do_usuario

from .permissions import PermissaoDoSistema
from .serializers import (
    AlterarSenhaSerializer, AuditoriaSerializer, CadastroSerializer, CategoriaSerializer, ClienteSerializer,
    EsqueciSenhaSerializer, InsumoSerializer, LoginSerializer, MedidaSerializer, ModuloSerializer,
    ModulosDoClienteSerializer, PerfilSerializer, PermissaoModuloSerializer, RedefinirSenhaSerializer,
    UsuarioSerializer,
)


def resposta(dados=None, mensagem=None, codigo=status.HTTP_200_OK):
    """Response com mensagem personalizada no envelope (ver renderers.py)."""
    r = Response(dados, status=codigo)
    if mensagem:
        r.message = mensagem
    return r


class CredenciaisInvalidas(APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Usuário ou senha inválidos."
    default_code = "credenciais_invalidas"


# --------------------------------------------------------------------------
# Autenticação
# --------------------------------------------------------------------------

def dados_do_usuario(usuario):
    """O que o app mobile precisa saber logo após o login: quem é, a qual
    cliente pertence, quais permissões tem e quais módulos estão habilitados
    (para montar o menu)."""
    cliente = cliente_do_usuario(usuario)
    modulos = []
    if cliente:
        modulos = sorted(
            ClienteModulo.objects.filter(cliente=cliente, ativo=True, modulo__ativo=True).values_list("modulo__slug", flat=True)
        )
    return {
        "id": usuario.pk,
        "username": usuario.username,
        "email": usuario.email,
        "nome": usuario.get_full_name(),
        "cliente": {"id": cliente.pk, "nome": cliente.nome_razao_social} if cliente else None,
        "perfis": sorted(usuario.groups.values_list("name", flat=True)),
        "permissoes": sorted(usuario.get_all_permissions()),
        "modulos": modulos,
        "segmento_trabalho": usuario.profile.segmento_trabalho,
    }


def _emitir_token(usuario):
    """Um token por usuário: cada login invalida o anterior."""
    Token.objects.filter(user=usuario).delete()
    token = Token.objects.create(user=usuario)
    expira_em = token.created + timedelta(days=settings.API_TOKEN_TTL_DIAS)
    return {"token": token.key, "tipo": "Bearer", "expira_em": expira_em}


RESPOSTA_TOKEN = inline_serializer("RespostaToken", {
    "token": serializers.CharField(), "tipo": serializers.CharField(), "expira_em": serializers.DateTimeField(),
})
RESPOSTA_LOGIN = inline_serializer("RespostaLogin", {
    "token": serializers.CharField(), "tipo": serializers.CharField(), "expira_em": serializers.DateTimeField(),
    "usuario": serializers.DictField(),
})
SEM_CORPO = inline_serializer("SemDados", {})


class LoginView(APIView):
    """Login: devolve o token Bearer. Aceita usuário OU e-mail.

    Proteção contra força bruta: mesmo limite do login web (por IP + login
    informado) — excedido, responde 429."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=LoginSerializer, responses={200: RESPOSTA_LOGIN}, tags=["auth"], summary="Login (obtém o token)")
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login_informado = serializer.validated_data["login"].strip()
        senha = serializer.validated_data["password"]

        chave = f"{ratelimit.get_client_ip(request)}:{login_informado.lower()}"
        bloqueio = ratelimit.check_rate_limit("api_login", chave, settings.LOGIN_RATE_LIMIT_ATTEMPTS, settings.LOGIN_RATE_LIMIT_WINDOW)
        if bloqueio.blocked:
            raise Throttled(wait=bloqueio.retry_after)

        username = login_informado
        if "@" in login_informado:
            por_email = User.objects.filter(email__iexact=login_informado).first()
            username = por_email.username if por_email else login_informado

        usuario = authenticate(request=request._request, username=username, password=senha)
        if usuario is None:
            ratelimit.hit("api_login", chave, settings.LOGIN_RATE_LIMIT_WINDOW)
            # Mesma mensagem para usuário inexistente, senha errada ou conta inativa.
            raise CredenciaisInvalidas()

        ratelimit.reset("api_login", chave)
        registrar(request, acao="login", modulo="api", usuario=usuario, descricao=f'Login de "{usuario.username}" via API.')
        return resposta({**_emitir_token(usuario), "usuario": dados_do_usuario(usuario)}, "Login realizado com sucesso.")


class LogoutView(APIView):
    @extend_schema(request=None, responses={204: None}, tags=["auth"], summary="Logout (invalida o token)")
    def post(self, request):
        registrar(request, acao="logout", modulo="api", descricao=f'Logout de "{request.user.username}" via API.')
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    @extend_schema(responses={200: inline_serializer("Eu", {"id": serializers.IntegerField()})}, tags=["auth"], summary="Dados do usuário logado")
    def get(self, request):
        return resposta(dados_do_usuario(request.user))


class CadastroView(APIView):
    """Autocadastro público. A conta nasce SEM cliente (sem acesso a módulos)
    e precisa verificar o e-mail; um administrador vincula cliente e perfil."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "cadastro"

    @extend_schema(request=CadastroSerializer, responses={201: inline_serializer("Cadastro", {"id": serializers.IntegerField(), "username": serializers.CharField()})},
                   tags=["auth"], summary="Cadastro")
    def post(self, request):
        serializer = CadastroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            usuario = cadastrar_usuario_publico(**serializer.validated_data)
        except ErroDeAutenticacao as erro:
            codigo = "unique" if erro.conflito else "invalid"  # duplicidade => 409
            raise ValidationError({erro.campo or "non_field_errors": [ErrorDetail(erro.mensagem, code=codigo)]}) from None
        registrar(request, acao="criar", modulo="usuario", usuario=usuario, tabela="auth_user", registro_id=usuario.pk,
                  registro_nome=usuario.username, descricao=f"Criado usuário ID {usuario.pk} - {usuario.username} (autocadastro via API).")
        return resposta({"id": usuario.pk, "username": usuario.username}, "Cadastro realizado. Verifique seu e-mail.", status.HTTP_201_CREATED)


class AlterarSenhaView(APIView):
    throttle_scope = "senha"

    @extend_schema(request=AlterarSenhaSerializer, responses={200: RESPOSTA_TOKEN}, tags=["auth"], summary="Alterar a própria senha")
    def post(self, request):
        serializer = AlterarSenhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario = request.user
        if not usuario.check_password(serializer.validated_data["senha_atual"]):
            raise ValidationError({"senha_atual": ["Senha atual incorreta."]})
        try:
            validar_forca_da_senha(serializer.validated_data["nova_senha"], user=usuario)
        except ErroDeAutenticacao as erro:
            raise ValidationError({"nova_senha": [erro.mensagem]}) from None
        with transaction.atomic():
            usuario.set_password(serializer.validated_data["nova_senha"])
            usuario.save()
            registrar(request, acao="editar", modulo="usuario", tabela="auth_user", registro_id=usuario.pk, registro_nome=usuario.username,
                      descricao=f"Senha do usuário ID {usuario.pk} - {usuario.username} alterada pelo próprio usuário.", silencioso=False)
        # A troca de senha derruba os tokens antigos; devolve um novo para o app seguir logado.
        return resposta(_emitir_token(usuario), "Senha alterada com sucesso.")


class EsqueciSenhaView(APIView):
    """Pede o e-mail de redefinição. A resposta é SEMPRE a mesma, exista ou
    não o e-mail (evita descobrir quais contas existem)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "senha"

    @extend_schema(request=EsqueciSenhaSerializer, responses={200: SEM_CORPO}, tags=["auth"], summary="Esqueci minha senha")
    def post(self, request):
        serializer = EsqueciSenhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()

        chave = f"{ratelimit.get_client_ip(request)}:{email}"
        if ratelimit.check_rate_limit("api_password_reset", chave, settings.PASSWORD_RESET_RATE_LIMIT_ATTEMPTS, settings.PASSWORD_RESET_RATE_LIMIT_WINDOW).blocked:
            raise Throttled()
        # Conta a tentativa antes de saber se o e-mail existe (não vaza existência).
        ratelimit.hit("api_password_reset", chave, settings.PASSWORD_RESET_RATE_LIMIT_WINDOW)

        iniciar_redefinicao_de_senha(email)
        return resposta(None, "Se o e-mail estiver cadastrado, enviaremos as instruções para redefinir a senha.")


class RedefinirSenhaView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "senha"

    @extend_schema(request=RedefinirSenhaSerializer, responses={200: SEM_CORPO}, tags=["auth"], summary="Redefinir a senha com o token recebido por e-mail")
    def post(self, request):
        serializer = RedefinirSenhaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            usuario = redefinir_senha(serializer.validated_data["token"], serializer.validated_data["nova_senha"])
        except ErroDeAutenticacao as erro:
            raise ValidationError({erro.campo or "non_field_errors": [erro.mensagem]}) from None
        Token.objects.filter(user=usuario).delete()  # sessões antigas deixam de valer
        registrar(request, acao="editar", modulo="usuario", usuario=usuario, tabela="auth_user", registro_id=usuario.pk,
                  registro_nome=usuario.username, descricao=f"Senha do usuário ID {usuario.pk} - {usuario.username} redefinida por e-mail.")
        return resposta(None, "Senha redefinida com sucesso. Faça login com a nova senha.")


# --------------------------------------------------------------------------
# Mixins dos ViewSets
# --------------------------------------------------------------------------

class AuditoriaViewSetMixin:
    """Audita CREATE/UPDATE da API com o mesmo serviço das telas web, na
    mesma transação da gravação."""

    def perform_create(self, serializer):
        with transaction.atomic():
            instancia = serializer.save(**self.dados_de_criacao())
            registrar_criacao(self.request, instancia)

    def perform_update(self, serializer):
        with transaction.atomic():
            foto = tirar_foto(serializer.instance)  # antes de salvar
            instancia = serializer.save()
            registrar_edicao(self.request, instancia, foto)

    def dados_de_criacao(self):
        return {}


class AtivarInativarMixin:
    """Ações POST /<recurso>/{id}/ativar/ e /inativar/ (alteram só o status)."""

    campo_ativo = "ativo"

    def foto_para_auditoria(self, objeto):
        return tirar_foto(objeto)

    def validar_mudanca_de_status(self, objeto, ativo):
        """Gancho para regras extras (ex.: não inativar a própria conta)."""

    def _definir_status(self, request, ativo):
        objeto = self.get_object()
        self.validar_mudanca_de_status(objeto, ativo)
        if getattr(objeto, self.campo_ativo) != ativo:
            with transaction.atomic():
                foto = self.foto_para_auditoria(objeto)
                setattr(objeto, self.campo_ativo, ativo)
                campos = [self.campo_ativo] + (["atualizado_em"] if hasattr(objeto, "atualizado_em") else [])
                objeto.save(update_fields=campos)
                registrar_edicao(request, objeto, foto, **self.opcoes_de_auditoria())
        return resposta(self.get_serializer(objeto).data, "Registro ativado." if ativo else "Registro inativado.")

    def opcoes_de_auditoria(self):
        return {}

    @extend_schema(request=None, summary="Ativar")
    @action(detail=True, methods=["post"])
    def ativar(self, request, pk=None):
        return self._definir_status(request, True)

    @extend_schema(request=None, summary="Inativar")
    @action(detail=True, methods=["post"])
    def inativar(self, request, pk=None):
        return self._definir_status(request, False)


def _filtro_ativo(queryset, params, campo="ativo", padrao="true"):
    """?ativo=true|false|todos (padrão: só ativos)."""
    valor = (params.get("ativo") or padrao).lower()
    if valor == "true":
        return queryset.filter(**{campo: True})
    if valor == "false":
        return queryset.filter(**{campo: False})
    return queryset


def _ordenar(queryset, params, permitidos, padrao):
    """?ordenar=campo ou ?ordenar=-campo, só para campos da whitelist."""
    pedido = params.get("ordenar", "")
    campo = pedido.lstrip("-")
    if campo in permitidos:
        return queryset.order_by(f"-{permitidos[campo]}" if pedido.startswith("-") else permitidos[campo])
    return queryset.order_by(*padrao)


PARAMETROS_BUSCA = [OpenApiParameter("q", str, description="Pesquisa por texto.")]
PARAMETRO_ATIVO = OpenApiParameter("ativo", str, enum=["true", "false", "todos"], description="Filtro de status (padrão: true).")


# --------------------------------------------------------------------------
# Recursos
# --------------------------------------------------------------------------

@extend_schema(tags=["insumos"])
class InsumoViewSet(AuditoriaViewSetMixin, viewsets.ModelViewSet):
    """Insumos do cliente do usuário (isolados por cliente). DELETE é exclusão
    lógica: o insumo é apenas inativado."""

    serializer_class = InsumoSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "insumo", "insumo", "insumo"
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Insumo.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        queryset = Insumo.objects.select_related("medida").filter(cliente=cliente) if cliente else Insumo.objects.none()
        params = self.request.query_params
        if self.action == "list":
            queryset = _filtro_ativo(queryset, params)
            if params.get("q"):
                queryset = queryset.filter(Q(nome__icontains=params["q"].strip()) | Q(medida__medida__icontains=params["q"].strip()))
            if params.get("medida", "").isdigit():
                queryset = queryset.filter(medida_id=params["medida"])
        return _ordenar(queryset, params, {
            "nome": "nome", "quantidade": "quantidade", "preco_pago": "preco_pago",
            "preco_unidade": "preco_unidade", "criado_em": "criado_em",
        }, ["nome"])

    def dados_de_criacao(self):
        return {"cliente": cliente_do_usuario(self.request.user)}  # nunca vem do corpo da requisição

    def perform_destroy(self, insumo):
        with transaction.atomic():
            registrar_exclusao(self.request, insumo, logica=True)
            insumo.ativo = False
            insumo.save(update_fields=["ativo", "atualizado_em"])

    @extend_schema(parameters=[*PARAMETROS_BUSCA, PARAMETRO_ATIVO, OpenApiParameter("medida", int), OpenApiParameter("ordenar", str, description="nome, quantidade, preco_pago, preco_unidade, criado_em (prefixo - = decrescente)")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["medidas"])
class MedidaViewSet(AuditoriaViewSetMixin, AtivarInativarMixin, viewsets.ModelViewSet):
    """Unidades de medida (cadastro global). Qualquer usuário autenticado lê;
    só administradores criam/editam. DELETE: medida em uso é apenas inativada."""

    serializer_class = MedidaSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "medida", "medida"
    leitura_livre = True
    permissoes_por_acao = {"ativar": "change", "inativar": "change"}
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = Medida.objects.all()
        params = self.request.query_params
        if self.action == "list":
            queryset = _filtro_ativo(queryset, params, padrao="todos")
            if params.get("q"):
                termo = params["q"].strip()
                queryset = queryset.filter(Q(medida__icontains=termo) | Q(sigla__icontains=termo) | Q(descricao__icontains=termo))
            if params.get("dimensao"):
                queryset = queryset.filter(dimensao=params["dimensao"])
        return queryset.order_by("id")

    def dados_de_criacao(self):
        return {"usuario": self.request.user}

    def destroy(self, request, *args, **kwargs):
        medida = self.get_object()
        with transaction.atomic():
            if medida.em_uso():
                # Sem CASCADE: medida em uso só é inativada (nada se perde).
                registrar_exclusao(request, medida, logica=True)
                medida.ativo = False
                medida.save(update_fields=["ativo", "atualizado_em"])
                return resposta(self.get_serializer(medida).data, "A medida está em uso e não pode ser apagada; foi apenas inativada.")
            registrar_exclusao(request, medida)
            medida.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(parameters=[*PARAMETROS_BUSCA, PARAMETRO_ATIVO, OpenApiParameter("dimensao", str)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["categorias"])
class CategoriaViewSet(
    AuditoriaViewSetMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """Categorias (usadas pelos produtos). Sem exclusão, como na tela web."""

    serializer_class = CategoriaSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model, modulo_slug = "categoria", "categoria", "categoria"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Categoria.objects.none()
        cliente = cliente_do_usuario(self.request.user)
        queryset = Categoria.objects.filter(cliente=cliente) if cliente else Categoria.objects.none()
        termo = self.request.query_params.get("q", "").strip()
        return queryset.filter(nome__icontains=termo) if termo else queryset

    def dados_de_criacao(self):
        return {"cliente": cliente_do_usuario(self.request.user)}

    @extend_schema(parameters=PARAMETROS_BUSCA)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["usuarios"])
class UsuarioViewSet(
    AtivarInativarMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """Usuários (auth_user) vinculados a cliente e perfil. Sem exclusão
    física: use /inativar/. Administrador global vê todos; os demais só o
    próprio cliente."""

    serializer_class = UsuarioSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "auth", "user"
    permissoes_por_acao = {"ativar": "change", "inativar": "change"}
    campo_ativo = "is_active"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return User.objects.none()
        queryset = usuarios_gerenciaveis(self.request.user)
        params = self.request.query_params
        if self.action == "list":
            queryset = _filtro_ativo(queryset, params, campo="is_active", padrao="todos")
            if params.get("q"):
                termo = params["q"].strip()
                queryset = queryset.filter(Q(username__icontains=termo) | Q(email__icontains=termo) | Q(first_name__icontains=termo) | Q(last_name__icontains=termo))
            if params.get("cliente", "").isdigit():
                queryset = queryset.filter(profile__cliente_id=params["cliente"])
            if params.get("perfil", "").isdigit():
                queryset = queryset.filter(groups__pk=params["perfil"])
        return queryset.order_by("username")

    def foto_para_auditoria(self, usuario):
        return foto_usuario(usuario)

    def opcoes_de_auditoria(self):
        return {"modulo": "usuario"}

    def validar_mudanca_de_status(self, usuario, ativo):
        if usuario.pk == self.request.user.pk and not ativo:
            raise ValidationError({"detail": ["Você não pode inativar a sua própria conta."]})

    @extend_schema(parameters=[*PARAMETROS_BUSCA, PARAMETRO_ATIVO, OpenApiParameter("cliente", int), OpenApiParameter("perfil", int)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["clientes"])
class ClienteViewSet(
    AuditoriaViewSetMixin, AtivarInativarMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """Clientes (tenants identificados por CPF/CNPJ). Administração do sistema."""

    serializer_class = ClienteSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "cliente", "cliente"
    permissoes_por_acao = {"ativar": "change", "inativar": "change", "modulos": "change"}

    def get_queryset(self):
        queryset = Cliente.objects.prefetch_related("perfis")
        params = self.request.query_params
        if self.action == "list":
            queryset = _filtro_ativo(queryset, params, padrao="todos")
            if params.get("q"):
                termo = params["q"].strip()
                queryset = queryset.filter(Q(nome_razao_social__icontains=termo) | Q(nome_fantasia__icontains=termo) | Q(cpf_cnpj__icontains=termo))
        return queryset.order_by("nome_razao_social")

    @extend_schema(
        methods=["GET"], responses={200: inline_serializer("ModulosDoCliente", {"modulos": ModuloSerializer(many=True)})},
        summary="Módulos e se estão habilitados para o cliente",
    )
    @extend_schema(methods=["PUT"], request=ModulosDoClienteSerializer, responses={200: SEM_CORPO}, summary="Define os módulos habilitados do cliente")
    @action(detail=True, methods=["get", "put"])
    def modulos(self, request, pk=None):
        cliente = self.get_object()
        if request.method == "PUT":
            serializer = ModulosDoClienteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            atualizar_modulos_do_cliente(request, cliente, [m.pk for m in serializer.validated_data["modulos"]])
        habilitados = set(ClienteModulo.objects.filter(cliente=cliente, ativo=True).values_list("modulo_id", flat=True))
        itens = [{**ModuloSerializer(m).data, "habilitado": m.pk in habilitados} for m in Modulo.objects.order_by("nome")]
        return resposta({"modulos": itens}, "Módulos atualizados." if request.method == "PUT" else None)

    @extend_schema(parameters=[*PARAMETROS_BUSCA, PARAMETRO_ATIVO])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)


@extend_schema(tags=["modulos"])
class ModuloViewSet(viewsets.ReadOnlyModelViewSet):
    """Catálogo de módulos do sistema (somente leitura)."""

    queryset = Modulo.objects.order_by("nome")
    serializer_class = ModuloSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "modulo", "modulo"


@extend_schema(tags=["perfis"])
class PerfilViewSet(viewsets.ModelViewSet):
    """Perfis (papéis) do ACL e suas permissões. Administração do sistema."""

    serializer_class = PerfilSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "auth", "group"
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Group.objects.prefetch_related("permissions__content_type").order_by("name")

    def perform_destroy(self, grupo):
        excluir_papel(self.request, grupo)


@extend_schema(tags=["perfis"])
class PermissaoViewSet(viewsets.ViewSet):
    """Catálogo (somente leitura) das permissões atribuíveis, por módulo e ação."""

    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "auth", "permission"

    @extend_schema(responses={200: PermissaoModuloSerializer(many=True)}, summary="Catálogo de permissões")
    def list(self, request):
        return Response(montar_matriz_permissoes())


@extend_schema(tags=["auditoria"])
class AuditoriaViewSet(viewsets.ReadOnlyModelViewSet):
    """Trilha de auditoria (somente leitura). Administrador global vê tudo;
    os demais só o próprio cliente."""

    serializer_class = AuditoriaSerializer
    permission_classes = [PermissaoDoSistema]
    permissao_app, permissao_model = "auditoria", "registroauditoria"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from apps.auditoria.models import RegistroAuditoria

            return RegistroAuditoria.objects.none()
        queryset = registros_visiveis(self.request.user)
        return filtrar_registros(queryset, self.request.query_params) if self.action == "list" else queryset

    @extend_schema(parameters=[
        OpenApiParameter("q", str), OpenApiParameter("usuario", int), OpenApiParameter("modulo", str),
        OpenApiParameter("acao", str), OpenApiParameter("de", str, description="Data inicial (AAAA-MM-DD)."),
        OpenApiParameter("ate", str, description="Data final (AAAA-MM-DD), inclusiva."),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
