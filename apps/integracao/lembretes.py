"""Notificações da agenda: lembrete no horário escolhido e resumo do dia ao entrar no sistema.

Reaproveita `apps.notificacao` (sino + push web). O escopo é sempre a UNIDADE do compromisso: só usuários ativos daquela
unidade, com `integracao.view_compromisso` e o módulo "agenda" habilitado, são avisados (nunca outras unidades).
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from apps.notificacao.models import DestinatarioNotificacao
from apps.notificacao.services import enviar_para_usuarios
from web_project.acesso import modulos_habilitados

from .models import Compromisso

JANELA_ATRASO = timedelta(days=1)  # lembrete que ficou para trás por mais que isso não é mais enviado (evita enxurrada após parada)
TITULO_RESUMO = "Resumo da agenda"


def _usuarios_da_unidade(cliente):
    """Usuários ativos da unidade que podem ver a agenda (permissão + módulo habilitado)."""
    candidatos = User.objects.filter(is_active=True, profile__cliente=cliente)
    return [u for u in candidatos if u.has_perm("integracao.view_compromisso") and "agenda" in modulos_habilitados(u)]


def enviar_lembretes(agora=None):
    """Envia os lembretes vencidos e devolve quantos compromissos foram avisados.

    Idempotente: o compromisso é "reservado" com um UPDATE condicional (`lembrete_enviado_em` ainda nulo) antes do envio,
    então duas execuções simultâneas ou repetidas nunca avisam duas vezes. Se não houver a quem avisar, a marca é mantida
    (o compromisso não fica tentando para sempre)."""
    agora = agora or timezone.now()
    # Vence quando início - antecedência <= agora; ignora o que começou há mais de JANELA_ATRASO.
    candidatos = Compromisso.objects.filter(
        ativo=True, concluido=False, lembrete_minutos__isnull=False, lembrete_enviado_em__isnull=True, inicio__gte=agora - JANELA_ATRASO,
    ).select_related("cliente")
    avisados = 0
    for c in candidatos:
        if c.inicio - timedelta(minutes=c.lembrete_minutos) > agora:
            continue
        reservado = Compromisso.objects.filter(pk=c.pk, lembrete_enviado_em__isnull=True).update(lembrete_enviado_em=agora)
        if not reservado:
            continue
        usuarios = _usuarios_da_unidade(c.cliente)
        if usuarios:
            hora = timezone.localtime(c.inicio).strftime("%d/%m %H:%M")
            enviar_para_usuarios(f"Lembrete: {c.titulo}", f"{c.get_tipo_display()} em {hora}.", reverse("compromisso-list"), usuarios)
            avisados += 1
    return avisados


def resumo_do_dia(usuario, agora=None):
    """Notificação única por dia ao entrar no sistema: compromissos de hoje e atrasados da unidade do usuário.
    Devolve a notificação criada ou None (sem permissão/agenda, nada a mostrar ou já avisado hoje)."""
    cliente = getattr(getattr(usuario, "profile", None), "cliente", None)
    if cliente is None or not usuario.has_perm("integracao.view_compromisso") or "agenda" not in modulos_habilitados(usuario):
        return None
    agora = agora or timezone.now()
    hoje = timezone.localtime(agora).date()
    if DestinatarioNotificacao.objects.filter(usuario=usuario, notificacao__titulo=TITULO_RESUMO, criado_em__date=hoje).exists():
        return None
    abertos = Compromisso.objects.filter(cliente=cliente, ativo=True, concluido=False)
    de_hoje = abertos.filter(inicio__date=hoje, fim__gte=agora).count()
    atrasados = abertos.filter(fim__lt=agora).count()
    if not (de_hoje or atrasados):
        return None
    partes = []
    if de_hoje:
        partes.append(f"{de_hoje} para hoje")
    if atrasados:
        partes.append(f"{atrasados} em atraso")
    notificacao, _, _ = enviar_para_usuarios(TITULO_RESUMO, "Você tem " + " e ".join(partes) + ".", reverse("compromisso-list"), [usuario])
    return notificacao
