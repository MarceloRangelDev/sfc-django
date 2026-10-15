"""Integrações na API v1: conexões, mensagens (fila idempotente) e compromissos da agenda.

- Conexões: as credenciais são recebidas (HTTPS), cifradas e NUNCA devolvidas; a resposta traz só estado, escopos, validade e erro.
- Mensagens: POST enfileira (idempotente por `chave`/`Idempotency-Key`); o envio acontece pelo agendador (`enviar_mensagens_pendentes`),
  sem garantia de entrega instantânea. GET lista a caixa de saída/entrada (contato é dado pessoal: só quem tem acesso à conexão vê).
- Provedores NÃO estão homologados contra contas reais (ver /integracoes/provedores/ → `validado`).
- Tudo é da unidade do usuário (outra unidade = 404).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound

from apps.integracao import services as s
from apps.integracao.adaptadores import disponiveis, obter
from apps.integracao.forms import CompromissoForm
from apps.integracao.models import Compromisso, Conexao, Mensagem
from web_project.acesso import cliente_do_usuario

from .negocio import RecursoViewSet, documentar
from .pagination import PaginacaoPadrao
from .pdv import PermissaoPorAcao, _chave
from .views import resposta


def _conexao_dict(c):
    return {
        "id": c.pk, "provedor": c.provedor, "provedor_rotulo": c.get_provedor_display(), "nome": c.nome, "status": c.status, "escopos": c.escopos, "expira_em": c.expira_em,
        "ultimo_erro": c.ultimo_erro, "ultima_sincronizacao": c.ultima_sincronizacao, "config": {k: v for k, v in c.config.items() if k != "pausa_ate"},
        "validado": c.adaptador.validado, "tipo": c.adaptador.tipo, "webhook_token": c.webhook_token if c.adaptador.suporta_webhook else None,
    }


def _mensagem_dict(m):
    return {"id": m.pk, "conexao": m.conexao_id, "direcao": m.direcao, "contato": m.contato, "texto": m.texto, "status": m.status, "tentativas": m.tentativas, "erro": m.erro, "tipo": m.tipo, "criado_em": m.criado_em, "enviado_em": m.enviado_em}


@extend_schema(tags=["integracoes"])
class ConexaoViewSet(viewsets.ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "integracoes"
    permissoes = {
        "list": "integracao.view_conexao", "retrieve": "integracao.view_conexao", "provedores": "integracao.view_conexao", "create": "integracao.add_conexao",
        "testar": "integracao.change_conexao", "sincronizar": "integracao.change_conexao", "desconectar": "integracao.change_conexao", "credenciais": "integracao.change_conexao",
    }

    def _conexao(self, request, pk, acao="view"):
        from web_project.acesso import clientes_dos_itens

        try:
            return Conexao.objects.select_related("cliente").get(pk=pk, cliente__in=clientes_dos_itens(request.user, acao))
        except Conexao.DoesNotExist:
            raise NotFound from None

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        from web_project.acesso import clientes_dos_itens

        pagina = PaginacaoPadrao()
        consulta = Conexao.objects.filter(cliente__in=clientes_dos_itens(request.user, "view")).select_related("cliente")
        return pagina.get_paginated_response([_conexao_dict(c) for c in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_conexao_dict(self._conexao(request, pk)))

    @extend_schema(responses={200: OpenApiTypes.OBJECT}, summary="Provedores e os campos que cada um pede")
    @action(detail=False, methods=["get"], url_path="provedores")
    def provedores(self, request):
        return resposta([{
            "chave": a.chave, "rotulo": a.rotulo, "tipo": a.tipo, "descricao": a.descricao, "validado": a.validado, "usa_oauth": a.usa_oauth, "escopos": list(a.escopos),
            "credenciais": [{"nome": n, "rotulo": r, "secreto": sec} for n, r, sec in a.campos_credenciais], "config": [{"nome": n, "rotulo": r} for n, r in a.campos_config],
        } for a in disponiveis()])

    @extend_schema(request=inline_serializer("NovaConexao", {"provedor": serializers.CharField(), "nome": serializers.CharField(), "credenciais": serializers.DictField(child=serializers.CharField()), "config": serializers.DictField(child=serializers.CharField(), required=False)}),
                   responses={201: OpenApiTypes.OBJECT}, summary="Cria a conexão (provedores sem OAuth) e testa")
    def create(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        provedor = str(dados.get("provedor", ""))
        try:
            adaptador = obter(provedor)
        except Exception:
            raise serializers.ValidationError({"provedor": ["Provedor desconhecido."]}) from None
        if adaptador not in disponiveis() or adaptador.usa_oauth:
            raise serializers.ValidationError({"provedor": ["Este provedor exige autorização pelo navegador (OAuth) ou não está disponível."]})
        credenciais, config = dados.get("credenciais") or {}, dados.get("config") or {}
        if not isinstance(credenciais, dict) or not isinstance(config, dict):
            raise serializers.ValidationError({"credenciais": ["Envie objetos JSON."]})
        conexao = s.conectar(request, cliente_do_usuario(request.user), provedor, str(dados.get("nome", "")), {k: str(v) for k, v in credenciais.items()}, config)
        return resposta(_conexao_dict(conexao), "Conexão criada." if conexao.status == Conexao.CONECTADA else f"Conexão criada, mas o teste falhou: {conexao.ultimo_erro}", 201)

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="testar")
    def testar(self, request, pk=None):
        conexao = self._conexao(request, pk, "change")
        ok = s.testar_conexao(request, conexao)
        conexao.refresh_from_db()
        return resposta({**_conexao_dict(conexao), "ok": ok}, "Conexão verificada." if ok else "O teste falhou.")

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="sincronizar")
    def sincronizar(self, request, pk=None):
        conexao = self._conexao(request, pk, "change")
        if not conexao.adaptador.sincroniza_agenda:
            raise serializers.ValidationError({"conexao": ["Esta conexão não sincroniza agenda."]})
        return resposta(s.sincronizar_agenda(conexao), "Sincronização executada.")

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["post"], url_path="desconectar")
    def desconectar(self, request, pk=None):
        conexao = s.desconectar(request, self._conexao(request, pk, "change"), revogar=True)
        return resposta(_conexao_dict(conexao), "Conexão desconectada e credenciais apagadas.")

    @extend_schema(request=inline_serializer("TrocarCredenciais", {"credenciais": serializers.DictField(child=serializers.CharField(), required=False), "config": serializers.DictField(child=serializers.CharField(), required=False)}), responses={200: OpenApiTypes.OBJECT})
    @action(detail=True, methods=["patch"], url_path="credenciais")
    def credenciais(self, request, pk=None):
        conexao = self._conexao(request, pk, "change")
        if conexao.adaptador.usa_oauth:
            raise serializers.ValidationError({"conexao": ["Reconecte pelo navegador (OAuth)."]})
        dados = request.data if isinstance(request.data, dict) else {}
        ok = s.atualizar_credenciais(request, conexao, {k: str(v) for k, v in (dados.get("credenciais") or {}).items()}, dados.get("config") or {})
        conexao.refresh_from_db()
        return resposta({**_conexao_dict(conexao), "ok": ok}, "Credenciais atualizadas.")


@extend_schema(tags=["integracoes"])
class MensagemViewSet(viewsets.ViewSet):
    permission_classes = [PermissaoPorAcao]
    modulo_slug = "integracoes"
    permissoes = {"list": "integracao.view_conexao", "retrieve": "integracao.view_conexao", "create": "integracao.enviar_mensagem"}

    def _escopo(self, request):
        from web_project.acesso import clientes_dos_itens

        return Mensagem.objects.filter(conexao__cliente__in=clientes_dos_itens(request.user, "view")).select_related("conexao")

    @extend_schema(parameters=[OpenApiParameter("conexao", int), OpenApiParameter("direcao", str), OpenApiParameter("status", str)], responses={200: OpenApiTypes.OBJECT})
    def list(self, request):
        consulta = self._escopo(request)
        p = request.query_params
        if p.get("conexao", "").isdigit():
            consulta = consulta.filter(conexao_id=p["conexao"])
        if p.get("direcao") in ("entrada", "saida"):
            consulta = consulta.filter(direcao=p["direcao"])
        if p.get("status") in dict(Mensagem.STATUS_CHOICES):
            consulta = consulta.filter(status=p["status"])
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_mensagem_dict(m) for m in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        try:
            return resposta(_mensagem_dict(self._escopo(request).get(pk=pk)))
        except Mensagem.DoesNotExist:
            raise NotFound from None

    @extend_schema(request=inline_serializer("EnviarMensagem", {"conexao": serializers.IntegerField(), "contato": serializers.CharField(), "texto": serializers.CharField(), "chave": serializers.CharField(required=False)}),
                   responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT}, summary="Enfileira uma mensagem (idempotente)")
    def create(self, request):
        from web_project.acesso import clientes_dos_itens

        dados = request.data if isinstance(request.data, dict) else {}
        try:
            conexao = Conexao.objects.get(pk=int(dados.get("conexao")), cliente__in=clientes_dos_itens(request.user, "view"))
        except (Conexao.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"conexao": ["Conexão inválida."]}) from None
        mensagem, criada = s.enfileirar_mensagem(request, conexao, dados.get("contato"), dados.get("texto"), chave=_chave(request))
        return resposta({**_mensagem_dict(mensagem), "criada": criada}, "Mensagem na fila de envio." if criada else "Mensagem já registrada (chave repetida).", 201 if criada else 200)


class CompromissoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Compromisso
        fields = ["id", "tipo", "titulo", "descricao", "inicio", "fim", "todo_o_dia", "pedido", "origem", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CompromissoEntrada(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=[c for c, _ in Compromisso.TIPO_CHOICES], required=False)
    titulo = serializers.CharField(max_length=150)
    descricao = serializers.CharField(max_length=500, required=False, allow_blank=True)
    inicio = serializers.DateTimeField()
    fim = serializers.DateTimeField()
    todo_o_dia = serializers.BooleanField(required=False)
    ativo = serializers.BooleanField(required=False)


@documentar("agenda", CompromissoSerializer, CompromissoEntrada)
class CompromissoViewSet(RecursoViewSet):
    """Compromissos/reservas/entregas da agenda (sincronizam com Google/iCloud). DELETE desativa (a sincronização remove o evento externo)."""

    modelo, serializer_class, entrada_class, form_class = Compromisso, CompromissoSerializer, CompromissoEntrada, CompromissoForm
    permissao_app, permissao_model, modulo_slug = "integracao", "compromisso", "agenda"
    campos_busca = ("titulo", "descricao")
    colunas_ordenaveis = {"inicio": "inicio", "titulo": "titulo"}
    ordenacao_padrao = ["inicio", "pk"]
    rotulo = "Compromisso"
    padroes = {"tipo": "compromisso", "ativo": True}

    def dados_atuais(self, c):
        return {"tipo": c.tipo, "titulo": c.titulo, "descricao": c.descricao, "inicio": c.inicio, "fim": c.fim, "todo_o_dia": c.todo_o_dia, "ativo": c.ativo}

    def destroy(self, request, *args, **kwargs):
        from django.db import transaction
        from rest_framework.response import Response

        from apps.auditoria.utils import registrar_exclusao

        compromisso = self.get_object()
        with transaction.atomic():
            registrar_exclusao(request, compromisso, logica=True)
            compromisso.ativo = False
            compromisso.save(update_fields=["ativo", "atualizado_em"])
        return Response(status=204)
