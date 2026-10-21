"""Regras de negócio das notificações: quem recebe, envio, histórico e leitura."""
from django.db import transaction
from django.utils import timezone

from apps.auditoria.utils import registrar_criacao
from web_project.acesso import usuarios_acessiveis

from . import canais
from .models import DestinatarioNotificacao, Notificacao


def resolver_destinatarios(solicitante, tipo, segmentos=(), usuarios=()):
    """Usuários ativos que receberiam a notificação, SEMPRE dentro do que o
    solicitante pode enxergar (a própria estrutura matriz/filiais; Admin:
    todos). Escolher um usuário ou segmento de fora do escopo não tem efeito."""
    base = usuarios_acessiveis(solicitante).filter(is_active=True, profile__cliente__isnull=False)
    if tipo == Notificacao.DESTINO_SEGMENTO:
        return base.filter(profile__cliente__segmento__in=segmentos)
    if tipo == Notificacao.DESTINO_USUARIOS:
        return base.filter(pk__in=[u.pk for u in usuarios])
    return base


def enviar_notificacao(request, dados):
    """Cria a notificação, registra os destinatários (com auditoria, numa
    transação) e só então tenta entregar pelos canais.

    `dados`: cleaned_data de NotificacaoForm. Devolve (notificacao, entregues, falhas).
    """
    solicitante = request.user
    segmentos = dados.get("segmentos") or []
    usuarios = dados.get("usuarios") or []
    with transaction.atomic():
        notificacao = Notificacao.objects.create(
            titulo=dados["titulo"], mensagem=dados["mensagem"], link=dados.get("link", ""),
            tipo_destinatario=dados["tipo_destinatario"], criado_por=solicitante,
        )
        if dados["tipo_destinatario"] == Notificacao.DESTINO_SEGMENTO:
            notificacao.segmentos.set(segmentos)
        if dados["tipo_destinatario"] == Notificacao.DESTINO_USUARIOS:
            notificacao.usuarios_alvo.set(usuarios)
        destinatarios = resolver_destinatarios(solicitante, dados["tipo_destinatario"], segmentos, usuarios)
        DestinatarioNotificacao.objects.bulk_create(
            [DestinatarioNotificacao(notificacao=notificacao, usuario=u) for u in destinatarios]
        )
        registrar_criacao(request, notificacao, modulo="notificacao")

    entregues, falhas = _entregar(notificacao)
    return notificacao, entregues, falhas


def _entregar(notificacao):
    """Entrega aos destinatários já gravados, pelos canais, registrando o
    resultado de cada um. Devolve (entregues, falhas)."""
    entregues = falhas = 0
    for item in notificacao.destinatarios.select_related("usuario"):
        erro = canais.entregar(item.usuario, notificacao.titulo, notificacao.mensagem, notificacao.link)
        if erro is None:
            item.status_entrega, item.entregue_em = DestinatarioNotificacao.ENTREGA_ENTREGUE, timezone.now()
            entregues += 1
        else:
            item.status_entrega, item.erro_entrega = DestinatarioNotificacao.ENTREGA_FALHA, erro[:255]
            falhas += 1
        item.save(update_fields=["status_entrega", "entregue_em", "erro_entrega"])
    return entregues, falhas


def enviar_para_usuarios(titulo, mensagem, link, usuarios):
    """Notificação GERADA PELO SISTEMA (alertas de estoque, vencimentos...) para
    usuários já definidos pelo chamador, que é quem garante o escopo (ex.: só
    usuários da própria unidade). Sem solicitante (`criado_por` vazio) e sem
    auditoria de criação — não é uma ação de usuário. Devolve (notificacao, entregues, falhas)."""
    with transaction.atomic():
        notificacao = Notificacao.objects.create(
            titulo=titulo[:120], mensagem=mensagem[:500], link=link, tipo_destinatario=Notificacao.DESTINO_USUARIOS,
        )
        notificacao.usuarios_alvo.set(usuarios)
        DestinatarioNotificacao.objects.bulk_create([DestinatarioNotificacao(notificacao=notificacao, usuario=u) for u in usuarios])
    entregues, falhas = _entregar(notificacao)
    return notificacao, entregues, falhas


def resumo_do_usuario(usuario, limite=10):
    """Dados do sino da navbar: (quantidade de não lidas, últimas notificações).
    Sempre restrito ao PRÓPRIO usuário (usado pela página e pela atualização automática)."""
    itens = DestinatarioNotificacao.objects.vigentes().filter(usuario=usuario)
    return itens.filter(lida_em__isnull=True).count(), list(itens.select_related("notificacao")[:limite])


def marcar_como_lida(item):
    """Registra a leitura. Ler no sistema também conta como entregue."""
    agora = timezone.now()
    campos = []
    if item.lida_em is None:
        item.lida_em = agora
        campos.append("lida_em")
    if item.status_entrega != DestinatarioNotificacao.ENTREGA_ENTREGUE:
        item.status_entrega, item.entregue_em, item.erro_entrega = DestinatarioNotificacao.ENTREGA_ENTREGUE, agora, ""
        campos += ["status_entrega", "entregue_em", "erro_entrega"]
    if campos:
        item.save(update_fields=campos)
    return item


def marcar_todas_como_lidas(usuario):
    agora = timezone.now()
    pendentes = DestinatarioNotificacao.objects.filter(usuario=usuario, lida_em__isnull=True)
    pendentes.exclude(status_entrega=DestinatarioNotificacao.ENTREGA_ENTREGUE).update(
        status_entrega=DestinatarioNotificacao.ENTREGA_ENTREGUE, entregue_em=agora, erro_entrega=""
    )
    pendentes.update(lida_em=agora)


def limpar_lidas_expiradas():
    """Apaga as notificações lidas há 48 h ou mais (rodada pelo comando agendado
    `limpar_notificacoes_lidas`, não depende de tela aberta). Só atinge linhas com
    `lida_em` preenchido; não lidas ficam intactas. Remove também a mensagem
    (`Notificacao`) que ficou sem nenhum destinatário. Devolve quantos destinatários saíram."""
    with transaction.atomic():
        expiradas = DestinatarioNotificacao.objects.expiradas()
        mensagens = list(expiradas.values_list("notificacao_id", flat=True).distinct())
        removidos, _ = expiradas.delete()
        Notificacao.objects.filter(pk__in=mensagens, destinatarios__isnull=True).delete()
    return removidos
