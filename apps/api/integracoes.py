"""Integrações na API v1: conexões, mensagens (fila idempotente), templates do WhatsApp oficial (Meta) e compromissos da agenda.

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
from apps.integracao import templates_whatsapp as tw
from apps.integracao.adaptadores import disponiveis, obter
from apps.integracao.forms import CompromissoForm
from apps.integracao.models import Compromisso, Conexao, Mensagem, TemplateWhatsApp
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
            "config_opcional": [{"nome": n, "rotulo": r} for n, r in a.campos_config_opcionais],
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
        fields = ["id", "tipo", "titulo", "descricao", "inicio", "fim", "todo_o_dia", "lembrete_minutos", "pedido", "origem", "concluido", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CompromissoEntrada(serializers.Serializer):
    tipo = serializers.ChoiceField(choices=[c for c, _ in Compromisso.TIPO_CHOICES], required=False)
    titulo = serializers.CharField(max_length=150)
    descricao = serializers.CharField(max_length=500, required=False, allow_blank=True)
    inicio = serializers.DateTimeField()
    fim = serializers.DateTimeField()
    todo_o_dia = serializers.BooleanField(required=False)
    lembrete_minutos = serializers.ChoiceField(choices=[0, 15, 60, 1440], required=False, allow_null=True, help_text="Minutos antes do início (0 = na hora); vazio = sem lembrete.")
    concluido = serializers.BooleanField(required=False, help_text="Marca como concluído. Omitido na edição = mantém.")
    ativo = serializers.BooleanField(required=False)


@documentar("agenda", CompromissoSerializer, CompromissoEntrada, [
    OpenApiParameter("situacao", str, enum=["aberto", "atrasado", "concluido"], description="aberto = não concluídos (inclui atrasados); atrasado = abertos cujo fim já passou; concluido = marcados como concluídos. Sem o parâmetro: todos."),
    OpenApiParameter("tipo", str, enum=[t for t, _ in Compromisso.TIPO_CHOICES]),
    OpenApiParameter("inicio_de", str, description="AAAA-MM-DD: compromissos que terminam a partir desta data."),
    OpenApiParameter("inicio_ate", str, description="AAAA-MM-DD: compromissos que começam até esta data."),
])
class CompromissoViewSet(RecursoViewSet):
    """Compromissos/reservas/entregas da agenda (sincronizam com Google/iCloud). DELETE desativa (a sincronização remove o evento externo)."""

    modelo, serializer_class, entrada_class, form_class = Compromisso, CompromissoSerializer, CompromissoEntrada, CompromissoForm
    permissao_app, permissao_model, modulo_slug = "integracao", "compromisso", "agenda"
    campos_busca = ("titulo", "descricao")
    colunas_ordenaveis = {"inicio": "inicio", "titulo": "titulo"}
    ordenacao_padrao = ["inicio", "pk"]
    rotulo = "Compromisso"
    padroes = {"tipo": "compromisso", "ativo": True}

    def filtrar(self, queryset, params):
        """Além de texto/status: situação (aberto/atrasado/concluido), tipo e janela de datas. O escopo da empresa já vem do get_queryset."""
        from django.utils.dateparse import parse_date

        queryset = s.filtrar_por_situacao(super().filtrar(queryset, params), params.get("situacao"))
        if params.get("tipo") in dict(Compromisso.TIPO_CHOICES):
            queryset = queryset.filter(tipo=params["tipo"])
        de, ate = parse_date(params.get("inicio_de", "")), parse_date(params.get("inicio_ate", ""))
        if de:
            queryset = queryset.filter(fim__date__gte=de)
        if ate:
            queryset = queryset.filter(inicio__date__lte=ate)
        return queryset

    def dados_atuais(self, c):
        return {"tipo": c.tipo, "titulo": c.titulo, "descricao": c.descricao, "inicio": c.inicio, "fim": c.fim, "todo_o_dia": c.todo_o_dia, "lembrete_minutos": c.lembrete_minutos, "concluido": c.concluido, "ativo": c.ativo}

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


# --------------------------------------------------------------------------
# Templates do WhatsApp Business (API oficial da Meta)
# --------------------------------------------------------------------------

def _template_dict(t):
    """Os DOIS estados lado a lado: `situacao_local` (aqui) e `status_meta` (o que a Meta devolveu; vazio = sem retorno). Sem credenciais."""
    return {
        "id": t.pk, "conexao": t.conexao_id, "nome": t.nome, "idioma": t.idioma, "categoria": t.categoria, "cabecalho": t.cabecalho, "corpo": t.corpo, "rodape": t.rodape,
        "botoes": t.botoes, "exemplos": t.exemplos, "somente_leitura": t.somente_leitura, "situacao_local": t.situacao_local, "status_meta": t.status_meta,
        "motivo_rejeicao": t.motivo_rejeicao, "id_meta": t.id_meta, "ultimo_erro": t.ultimo_erro, "sincronizado_em": t.sincronizado_em, "criado_em": t.criado_em, "atualizado_em": t.atualizado_em,
    }


TemplateEntrada = inline_serializer("TemplateWhatsAppEntrada", {
    "conexao": serializers.IntegerField(help_text="ID de uma conexão `whatsapp_cloud` ativa da sua unidade (só na criação)."),
    "nome": serializers.CharField(help_text="Minúsculas, números e sublinhado. Ex.: pedido_confirmado"),
    "idioma": serializers.CharField(help_text="Ex.: pt_BR, en_US"),
    "categoria": serializers.ChoiceField(choices=["MARKETING", "UTILITY"]),
    "cabecalho": serializers.CharField(required=False, allow_blank=True, help_text="Texto até 60 caracteres, no máximo uma variável {{1}}."),
    "corpo": serializers.CharField(help_text="Até 1024 caracteres. Variáveis {{1}}, {{2}}... em sequência, nunca no início/fim."),
    "rodape": serializers.CharField(required=False, allow_blank=True),
    "botoes": serializers.ListField(child=serializers.DictField(), required=False, help_text="Até 10: {tipo: QUICK_REPLY|URL|PHONE_NUMBER, texto, url?, telefone?, exemplo?}"),
    "exemplos": serializers.DictField(required=False, help_text='{"cabecalho": "valor", "corpo": ["valor da {{1}}", ...]} — um exemplo por variável.'),
})


@extend_schema(tags=["integracoes"])
class TemplateWhatsAppViewSet(viewsets.ViewSet):
    """Templates do WhatsApp oficial (Meta) da sua unidade. Salvar cria/edita um RASCUNHO LOCAL; `enviar` é que submete à Meta e `sincronizar`
    traz o status dela. `status_meta` só tem o valor que a Meta devolveu (nunca é simulado). Excluir um template enviado o apaga também na Meta."""

    permission_classes = [PermissaoPorAcao]
    modulo_slug = "integracoes"
    permissoes = {
        "list": "integracao.view_templatewhatsapp", "retrieve": "integracao.view_templatewhatsapp", "create": "integracao.add_templatewhatsapp",
        "partial_update": "integracao.change_templatewhatsapp", "enviar": "integracao.change_templatewhatsapp", "sincronizar": "integracao.change_templatewhatsapp",
        "destroy": "integracao.delete_templatewhatsapp",
    }

    def _escopo(self, request, acao="view"):
        from web_project.acesso import clientes_dos_itens

        return TemplateWhatsApp.objects.filter(conexao__cliente__in=clientes_dos_itens(request.user, acao)).select_related("conexao")

    def _conexoes(self, request, acao):
        from web_project.acesso import clientes_dos_itens

        return Conexao.objects.filter(provedor="whatsapp_cloud", cliente__in=clientes_dos_itens(request.user, acao))

    def _obter(self, request, pk, acao="view"):
        try:
            return self._escopo(request, acao).get(pk=pk)
        except (TemplateWhatsApp.DoesNotExist, ValueError):
            raise NotFound from None

    @extend_schema(parameters=[OpenApiParameter("conexao", int), OpenApiParameter("status", str, description="`rascunho` ou um status da Meta (APPROVED, PENDING, REJECTED...)")], responses={200: OpenApiTypes.OBJECT}, operation_id="whatsapp_templates_list")
    def list(self, request):
        consulta = self._escopo(request)
        p = request.query_params
        if p.get("conexao", "").isdigit():
            consulta = consulta.filter(conexao_id=p["conexao"])
        if p.get("status") == "rascunho":
            consulta = consulta.filter(situacao_local=TemplateWhatsApp.RASCUNHO)
        elif p.get("status"):
            consulta = consulta.filter(status_meta=p["status"].upper())
        pagina = PaginacaoPadrao()
        return pagina.get_paginated_response([_template_dict(t) for t in pagina.paginate_queryset(consulta, request)])

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def retrieve(self, request, pk=None):
        return resposta(_template_dict(self._obter(request, pk)))

    @extend_schema(request=TemplateEntrada, responses={201: OpenApiTypes.OBJECT}, summary="Salva um rascunho local (não envia à Meta)")
    def create(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        try:
            conexao = self._conexoes(request, "add").get(pk=int(dados.get("conexao")))
        except (Conexao.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"conexao": ["Conexão inválida."]}) from None
        return resposta(_template_dict(tw.criar_local(request, conexao, dados)), "Rascunho salvo. Ainda não foi enviado à Meta.", 201)

    @extend_schema(request=TemplateEntrada, responses={200: OpenApiTypes.OBJECT}, summary="Altera o conteúdo localmente (envie à Meta depois)")
    def partial_update(self, request, pk=None):
        t = self._obter(request, pk, "change")
        dados = request.data if isinstance(request.data, dict) else {}
        # PATCH parcial: campos não enviados mantêm o valor atual (a validação recebe o conteúdo completo).
        atual = {"nome": t.nome, "idioma": t.idioma, "categoria": t.categoria, "cabecalho": t.cabecalho, "corpo": t.corpo, "rodape": t.rodape, "botoes": t.botoes, "exemplos": t.exemplos}
        return resposta(_template_dict(tw.editar_local(request, t, {**atual, **{k: v for k, v in dados.items() if k in atual}})), "Template atualizado aqui.")

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT}, summary="Envia à Meta (cria ou atualiza) e devolve o status que ela informou")
    @action(detail=True, methods=["post"], url_path="enviar")
    def enviar(self, request, pk=None):
        t = tw.enviar_para_meta(request, self._obter(request, pk, "change"))
        return resposta(_template_dict(t), "Enviado à Meta.")

    @extend_schema(request=inline_serializer("SincronizarTemplates", {"conexao": serializers.IntegerField()}), responses={200: OpenApiTypes.OBJECT}, summary="Traz da Meta os templates e seus status")
    @action(detail=False, methods=["post"], url_path="sincronizar")
    def sincronizar(self, request):
        dados = request.data if isinstance(request.data, dict) else {}
        try:
            conexao = self._conexoes(request, "change").get(pk=int(dados.get("conexao")))
        except (Conexao.DoesNotExist, TypeError, ValueError):
            raise serializers.ValidationError({"conexao": ["Conexão inválida."]}) from None
        return resposta(tw.sincronizar(request, conexao), "Sincronização com a Meta concluída.")

    @extend_schema(responses={204: None}, summary="Exclui (na Meta e aqui, se já tiver sido enviado)")
    def destroy(self, request, pk=None):
        from rest_framework.response import Response

        tw.excluir(request, self._obter(request, pk, "delete"))
        return Response(status=204)
