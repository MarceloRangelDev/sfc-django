"""Núcleo das integrações: conexão (credenciais cifradas), fila de mensagens com reenvio, webhook idempotente e sincronização de agenda.

PRINCÍPIOS
- Segredo nunca em texto claro, log, auditoria ou mensagem de erro (cripto.py). Escopos mínimos por provedor.
- Cada conexão é de UMA unidade; tudo é filtrado pela unidade do usuário (clientes_dos_itens).
- Falha temporária (rede, 429/5xx) = nova tentativa com espera crescente (mensagens) ou pausa da conexão (agenda); falha definitiva
  marca a conexão/mensagem com erro e avisa a equipe. Limites de volume por execução protegem os provedores.
- Webhook: assinatura obrigatória + evento processado uma única vez (idempotência por id do provedor).
- Nada aqui promete entrega instantânea: mensagens vão por fila (comando agendado) e a sincronização roda por agendador.
"""
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone as tz

from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.auditoria.utils import registrar, registrar_criacao, registrar_edicao, tirar_foto
from apps.auditoria.models import ACAO_EXCLUIR
from web_project.regras import RegraNegocioError

from . import cripto
from .adaptadores import ErroIntegracao, obter
from .models import Compromisso, Conexao, EventoWebhook, ItemSincronizado, LogIntegracao, Mensagem

logger = logging.getLogger(__name__)

MAX_TENTATIVAS = 5
LIMITE_ENVIOS_POR_MINUTO = 60
LIMITE_OPERACOES_AGENDA = 200
JANELA_DIAS = 60
MAX_CORPO_WEBHOOK = 256 * 1024
RENOVAR_ANTES = timedelta(minutes=5)


def log(conexao, nivel, evento, detalhe=""):
    LogIntegracao.objects.create(conexao=conexao, nivel=nivel, evento=evento[:40], detalhe=(detalhe or "")[:300])


def credenciais_de(conexao):
    return cripto.decifrar(conexao.credenciais_cifradas)


def _mascarar(texto):
    """Mensagem de erro sem qualquer trecho que lembre segredo (limpeza defensiva)."""
    return re.sub(r"(?i)(token|secret|senha|password|bearer)\S*", "[oculto]", str(texto))[:255]


# --------------------------------------------------------------------------
# Conexão
# --------------------------------------------------------------------------

def conectar(request, cliente, provedor, nome, credenciais, config=None, escopos="", testar=True):
    """Cria a conexão (manual ou pós-OAuth): valida, cifra, grava (auditado) e, por padrão, TESTA no provedor. Sem teste bem-sucedido
    a conexão fica `erro` (editável); com sucesso, `conectada`."""
    adaptador = obter(provedor)
    nome = " ".join((nome or "").split())
    if not nome:
        raise RegraNegocioError("Informe um nome para a conexão.")
    config = {k: str(v).strip() for k, v in (config or {}).items() if v not in (None, "")}
    try:
        adaptador.validar(credenciais, config)
        cifradas = cripto.cifrar(credenciais) if credenciais else ""
    except ErroIntegracao as erro:
        raise RegraNegocioError(str(erro)) from None
    except cripto.CriptoIndisponivel as erro:
        raise RegraNegocioError(str(erro)) from None
    try:
        with transaction.atomic():
            conexao = Conexao.objects.create(
                cliente=cliente, provedor=provedor, nome=nome[:80], config=config, credenciais_cifradas=cifradas, escopos=escopos or " ".join(adaptador.escopos),
                criado_por=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            )
            registrar_criacao(request, conexao, modulo="integracao")
            log(conexao, LogIntegracao.INFO, "conectada", "Conexão criada.")
    except IntegrityError:
        raise RegraNegocioError("Já existe uma conexão com este nome nesta unidade.") from None
    if testar:
        testar_conexao(request, conexao)
    else:
        _marcar(conexao, Conexao.CONECTADA)
    return conexao


def _marcar(conexao, status, erro=""):
    conexao.status, conexao.ultimo_erro = status, _mascarar(erro)
    conexao.save(update_fields=["status", "ultimo_erro", "atualizado_em"])


def testar_conexao(request, conexao):
    """Chama o provedor para conferir a conexão e atualiza o estado."""
    try:
        conexao.adaptador.testar(conexao, credenciais_de(conexao))
    except ErroIntegracao as erro:
        _marcar(conexao, Conexao.ERRO, str(erro))
        log(conexao, LogIntegracao.ERRO, "teste", _mascarar(erro))
        return False
    except ValueError as erro:
        _marcar(conexao, Conexao.ERRO, str(erro))
        log(conexao, LogIntegracao.ERRO, "teste", "Credenciais ilegíveis (chave de cifra diferente?).")
        return False
    _marcar(conexao, Conexao.CONECTADA)
    log(conexao, LogIntegracao.INFO, "teste", "Conexão verificada.")
    return True


def atualizar_credenciais(request, conexao, credenciais, config=None):
    """Troca credenciais (ex.: token novo) sem recriar a conexão. Os campos não informados são mantidos."""
    atuais = credenciais_de(conexao)
    novas = {**atuais, **{k: v for k, v in credenciais.items() if v}}
    nova_config = {**conexao.config, **{k: str(v).strip() for k, v in (config or {}).items() if v not in (None, "")}}
    try:
        conexao.adaptador.validar(novas, nova_config)
        cifradas = cripto.cifrar(novas)
    except (ErroIntegracao, cripto.CriptoIndisponivel) as erro:
        raise RegraNegocioError(str(erro)) from None
    with transaction.atomic():
        foto = tirar_foto(conexao)
        conexao.credenciais_cifradas, conexao.config = cifradas, nova_config
        conexao.save(update_fields=["credenciais_cifradas", "config", "atualizado_em"])
        registrar_edicao(request, conexao, foto, modulo="integracao")
        registrar(request, acao="editar", modulo="integracao", tabela=conexao._meta.db_table, registro_id=conexao.pk, registro_nome=conexao.nome,
                  descricao=f"Credenciais da conexão ID {conexao.pk} - {conexao.nome} foram substituídas (valores não registrados).", silencioso=False)
        log(conexao, LogIntegracao.INFO, "credenciais", "Credenciais substituídas.")
    return testar_conexao(request, conexao)


def renovar_credenciais(conexao, agora=None):
    """Renova tokens que expiram (provedores OAuth). Devolve True se renovou. Falha definitiva marca erro e avisa a equipe."""
    agora = agora or timezone.now()
    try:
        resultado = conexao.adaptador.renovar(conexao, credenciais_de(conexao))
    except ErroIntegracao as erro:
        if erro.temporario:
            log(conexao, LogIntegracao.AVISO, "renovacao", f"Falha temporária: {_mascarar(erro)}")
            return False
        _marcar(conexao, Conexao.ERRO, str(erro))
        log(conexao, LogIntegracao.ERRO, "renovacao", _mascarar(erro))
        notificar_equipe(conexao, f"Reconecte {conexao.nome}", "A integração perdeu o acesso e precisa ser reconectada.")
        return False
    if resultado is None:
        return False
    credenciais, expira_em = resultado
    conexao.credenciais_cifradas, conexao.expira_em = cripto.cifrar(credenciais), expira_em
    if conexao.status == Conexao.ERRO:
        conexao.status, conexao.ultimo_erro = Conexao.CONECTADA, ""
    conexao.save(update_fields=["credenciais_cifradas", "expira_em", "status", "ultimo_erro", "atualizado_em"])
    log(conexao, LogIntegracao.INFO, "renovacao", "Credencial renovada.")
    return True


def desconectar(request, conexao, revogar=True):
    """Revoga no provedor (melhor esforço), APAGA as credenciais locais e marca a conexão como desconectada/revogada. Auditado."""
    if conexao.status in (Conexao.DESCONECTADA, Conexao.REVOGADA):
        raise RegraNegocioError("A conexão já está desconectada.")
    with transaction.atomic():
        if revogar and conexao.credenciais_cifradas:
            try:
                conexao.adaptador.revogar(conexao, credenciais_de(conexao))
            except (ErroIntegracao, ValueError):
                log(conexao, LogIntegracao.AVISO, "revogacao", "Não foi possível revogar no provedor; credenciais locais apagadas.")
        foto = tirar_foto(conexao)
        conexao.credenciais_cifradas, conexao.expira_em = "", None
        conexao.status = Conexao.REVOGADA if revogar else Conexao.DESCONECTADA
        conexao.ultimo_erro = ""
        conexao.save(update_fields=["credenciais_cifradas", "expira_em", "status", "ultimo_erro", "atualizado_em"])
        conexao.mensagens.filter(direcao=Mensagem.SAIDA, status=Mensagem.PENDENTE).update(status=Mensagem.CANCELADA, erro="Conexão desconectada")
        registrar_edicao(request, conexao, foto, modulo="integracao")
        log(conexao, LogIntegracao.INFO, "desconectada", "Acesso revogado e credenciais apagadas." if revogar else "Credenciais apagadas.")
    return conexao


def excluir_conexao(request, conexao):
    """Só conexão desconectada e sem histórico de mensagens pode ser excluída (senão fica como histórico)."""
    if conexao.ativa:
        raise RegraNegocioError("Desconecte antes de excluir.")
    if conexao.mensagens.exists():
        raise RegraNegocioError("A conexão tem mensagens no histórico e não pode ser excluída.")
    with transaction.atomic():
        registrar(request, acao=ACAO_EXCLUIR, modulo="integracao", tabela=conexao._meta.db_table, registro_id=conexao.pk, registro_nome=conexao.nome,
                  descricao=f"Excluída conexão ID {conexao.pk} - {conexao.nome}.", silencioso=False)
        conexao.delete()


def notificar_equipe(conexao, titulo, mensagem):
    """Web Push do sistema para os usuários da unidade que podem gerir integrações (melhor esforço; não garante entrega instantânea)."""
    from django.contrib.auth import get_user_model

    from apps.notificacao.services import enviar_para_usuarios

    usuarios = [u for u in get_user_model().objects.filter(profile__cliente=conexao.cliente, is_active=True) if u.has_perm("integracao.enviar_mensagem")]
    if usuarios:
        try:
            enviar_para_usuarios(titulo, mensagem, "/integracoes", usuarios)
        except Exception:  # noqa: BLE001 (notificação nunca derruba o fluxo)
            logger.exception("Falha ao notificar a equipe")


# --------------------------------------------------------------------------
# Mensagens (fila com reenvio)
# --------------------------------------------------------------------------

def enfileirar_mensagem(request, conexao, contato, texto, chave=None):
    """Coloca uma mensagem na fila de saída. IDEMPOTENTE por `chave` (reenvio do app não duplica). Devolve (mensagem, criada)."""
    if conexao.status != Conexao.CONECTADA:
        raise RegraNegocioError("A conexão não está ativa.")
    if not conexao.adaptador.envia_mensagens:
        raise RegraNegocioError("Esta conexão não envia mensagens.")
    contato = re.sub(r"[^\d+]", "", str(contato or ""))[:30]
    texto = (texto or "").strip()
    if len(contato) < 5:
        raise RegraNegocioError("Contato inválido.")
    if not texto or len(texto) > 2000:
        raise RegraNegocioError("A mensagem deve ter de 1 a 2000 caracteres.")
    if chave:
        existente = Mensagem.objects.filter(conexao=conexao, chave_idempotencia=chave).first()
        if existente:
            return existente, False
    try:
        with transaction.atomic():
            mensagem = Mensagem.objects.create(
                conexao=conexao, direcao=Mensagem.SAIDA, contato=contato, texto=texto, chave_idempotencia=chave or None, proxima_tentativa=timezone.now(),
                criado_por=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            )
            registrar(request, acao="criar", modulo="integracao", tabela=mensagem._meta.db_table, registro_id=mensagem.pk, registro_nome=f"Mensagem #{mensagem.pk}",
                      descricao=f"Mensagem ID {mensagem.pk} enfileirada na conexão {conexao.nome} (contato e texto não registrados).", silencioso=False)
    except IntegrityError:
        existente = Mensagem.objects.filter(conexao=conexao, chave_idempotencia=chave).first()
        if existente:
            return existente, False
        raise
    return mensagem, True


def _espera(tentativa):
    return min(30 * (2 ** (tentativa - 1)), 3600)  # 30 s, 1 min, 2 min, 4 min, 8 min... até 1 h


def processar_fila(limite=50, agora=None):
    """Envia mensagens pendentes cujo horário chegou (chamado pelo comando agendado). Devolve {"enviadas", "falhas", "adiadas"}."""
    agora = agora or timezone.now()
    resultado = {"enviadas": 0, "falhas": 0, "adiadas": 0}
    consulta = Mensagem.objects.filter(direcao=Mensagem.SAIDA, status=Mensagem.PENDENTE, proxima_tentativa__lte=agora, conexao__status=Conexao.CONECTADA).order_by("proxima_tentativa", "pk")
    for pk in list(consulta.values_list("pk", flat=True)[:limite]):
        with transaction.atomic():
            fila = Mensagem.objects.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else Mensagem.objects
            mensagem = fila.select_related("conexao").filter(pk=pk, status=Mensagem.PENDENTE).first()
            if mensagem is None:
                continue
            conexao = mensagem.conexao
            recentes = Mensagem.objects.filter(conexao=conexao, direcao=Mensagem.SAIDA, status=Mensagem.ENVIADA, enviado_em__gte=agora - timedelta(minutes=1)).count()
            if recentes >= LIMITE_ENVIOS_POR_MINUTO:  # limite de volume por conexão
                Mensagem.objects.filter(pk=pk).update(proxima_tentativa=agora + timedelta(seconds=60))
                resultado["adiadas"] += 1
                continue
            mensagem.tentativas += 1
            try:
                id_externo = conexao.adaptador.enviar_mensagem(conexao, credenciais_de(conexao), mensagem.contato, mensagem.texto)
            except ErroIntegracao as erro:
                definitivo = (not erro.temporario) or mensagem.tentativas >= MAX_TENTATIVAS
                mensagem.erro = _mascarar(erro)
                if definitivo:
                    mensagem.status = Mensagem.ERRO
                    resultado["falhas"] += 1
                    log(conexao, LogIntegracao.ERRO, "envio", f"Mensagem {mensagem.pk}: {mensagem.erro}")
                else:
                    mensagem.proxima_tentativa = agora + timedelta(seconds=erro.espera or _espera(mensagem.tentativas))
                    resultado["adiadas"] += 1
                    log(conexao, LogIntegracao.AVISO, "envio", f"Mensagem {mensagem.pk}: nova tentativa {mensagem.tentativas}/{MAX_TENTATIVAS}.")
                mensagem.save(update_fields=["tentativas", "erro", "status", "proxima_tentativa"])
                continue
            except ValueError:
                mensagem.status, mensagem.erro = Mensagem.ERRO, "Credenciais ilegíveis."
                mensagem.save(update_fields=["tentativas", "erro", "status"])
                resultado["falhas"] += 1
                continue
            mensagem.status, mensagem.id_externo, mensagem.enviado_em, mensagem.erro = Mensagem.ENVIADA, str(id_externo)[:120], agora, ""
            mensagem.save(update_fields=["tentativas", "status", "id_externo", "enviado_em", "erro"])
            resultado["enviadas"] += 1
    return resultado


# --------------------------------------------------------------------------
# Webhook
# --------------------------------------------------------------------------

def tratar_webhook(conexao, cabecalhos, corpo):
    """Processa um webhook POST. Devolve (status_http, resposta_texto). Assinatura inválida = 403 e NADA é processado. Cada evento é
    processado uma só vez (idempotência pelo id do provedor)."""
    if len(corpo) > MAX_CORPO_WEBHOOK:
        return 413, "grande demais"
    if conexao.status != Conexao.CONECTADA:
        return 404, "inativa"
    try:
        credenciais = credenciais_de(conexao)
        assinatura_ok = conexao.adaptador.assinatura_valida(conexao, credenciais, cabecalhos, corpo)
    except (ErroIntegracao, ValueError):
        assinatura_ok = False
    if not assinatura_ok:
        log(conexao, LogIntegracao.AVISO, "webhook", "Assinatura inválida: requisição ignorada.")
        return 403, "assinatura inválida"
    try:
        payload = json.loads(corpo or b"{}")
        eventos = conexao.adaptador.interpretar_webhook(conexao, payload if isinstance(payload, dict) else {})
    except ValueError:
        return 400, "json inválido"
    novas = 0
    for evento in eventos[:100]:
        id_externo = str(evento.get("id") or "")[:120]
        if not id_externo:
            continue
        try:
            with transaction.atomic():
                EventoWebhook.objects.create(conexao=conexao, id_externo=id_externo, tipo=str(evento.get("tipo", ""))[:20])
                Mensagem.objects.create(
                    conexao=conexao, direcao=Mensagem.ENTRADA, contato=str(evento.get("de", ""))[:60], texto=str(evento.get("texto", ""))[:2000], status=Mensagem.RECEBIDA,
                    id_externo=id_externo, tipo=str(evento.get("tipo", "mensagem"))[:20],
                )
            novas += 1
        except IntegrityError:
            continue  # já processado (reenvio do provedor): ignora sem erro
    if novas:
        log(conexao, LogIntegracao.INFO, "webhook", f"{novas} evento(s) recebido(s).")
        if conexao.notificar_usuarios:
            notificar_equipe(conexao, f"Nova mensagem em {conexao.nome}", f"{novas} nova(s) mensagem(ns) recebida(s). Abra as integrações para ver.")
    return 200, "ok"


# --------------------------------------------------------------------------
# Agenda (sincronização)
# --------------------------------------------------------------------------

def _hash(evento):
    base = {k: str(evento.get(k)) for k in ("titulo", "descricao", "inicio", "fim", "todo_o_dia")}
    return hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()


def _dia(d, fim=False):
    inicio = datetime(d.year, d.month, d.day, tzinfo=tz.utc) if not fim else datetime(d.year, d.month, d.day, 23, 59, tzinfo=tz.utc)
    return inicio


def eventos_locais(conexao, hoje=None):
    """Eventos que o sistema publica na agenda: vencimentos de contas (SÓ de sistema → calendário) e compromissos (nos dois sentidos).
    Valores monetários só aparecem se `incluir_valores` estiver ligado na conexão (privacidade)."""
    from apps.financeiro.models import ContaPagar, ContaReceber

    hoje = hoje or timezone.localdate()
    inicio, fim = hoje - timedelta(days=1), hoje + timedelta(days=JANELA_DIAS)
    mostrar_valor = conexao.config.get("incluir_valores") == "1"
    eventos = {}
    for tipo, modelo, rotulo in (("conta_pagar", ContaPagar, "Pagar"), ("conta_receber", ContaReceber, "Receber")):
        for conta in modelo.objects.filter(cliente=conexao.cliente, ativo=True, vencimento__range=(inicio, fim), valor_baixado__lt=F("valor")):
            titulo = f"{rotulo}: {conta.descricao}" + (f" ({conta.saldo_formatado})" if mostrar_valor else "")
            eventos[(tipo, conta.pk)] = {"titulo": titulo[:200], "descricao": f"Vencimento (sistema SFC). {conta.contraparte}".strip()[:500], "inicio": _dia(conta.vencimento), "fim": _dia(conta.vencimento, True), "todo_o_dia": True}
    janela_inicio, janela_fim = _dia(inicio), _dia(fim, True)
    for c in Compromisso.objects.filter(cliente=conexao.cliente, ativo=True, inicio__lte=janela_fim, fim__gte=janela_inicio):
        eventos[("compromisso", c.pk)] = {"titulo": c.titulo, "descricao": c.descricao, "inicio": c.inicio, "fim": c.fim, "todo_o_dia": c.todo_o_dia}
    return eventos


def _interpretar_data(valor, todo_o_dia):
    """ISO (com ou sem fuso), 'AAAA-MM-DD', 'AAAAMMDD' e 'AAAAMMDDTHHMMSSZ' -> datetime aware."""
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=tz.utc)
    texto = str(valor or "").strip()
    for formato in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d", "%Y-%m-%d"):
        try:
            d = datetime.strptime(texto, formato)
            return d.replace(tzinfo=tz.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=tz.utc)
    except ValueError:
        return None


def sincronizar_automatico(cliente, minutos=5):
    """Sincronização "oportunista": chamada ao abrir o sistema e a Agenda. No máximo uma vez a cada `minutos` por conexão (o cache também
    impede duas execuções simultâneas) e NUNCA levanta erro: falha de rede/provedor só fica no log da conexão. Devolve quantas rodaram."""
    from django.core.cache import cache

    rodadas = 0
    for conexao in Conexao.objects.filter(cliente=cliente, status=Conexao.CONECTADA):
        if not conexao.adaptador.sincroniza_agenda:
            continue
        if not cache.add(f"integracao:sync-auto:{conexao.pk}", 1, minutos * 60):
            continue
        try:
            sincronizar_agenda(conexao)
            rodadas += 1
        except Exception:
            logger.exception("Sincronização automática da conexão %s falhou", conexao.pk)
    return rodadas


def sincronizar_agenda(conexao, agora=None, limite=LIMITE_OPERACOES_AGENDA):
    """Sincroniza UMA conexão de agenda. Ordem: (1) renova credencial se preciso, (2) PUXA alterações de compromissos, (3) EMPURRA o que
    mudou no sistema. Regras de conflito: vencimentos são do sistema (edição externa é sobrescrita); em compromissos vale a alteração
    MAIS RECENTE e, em empate, a do sistema. Devolve um resumo. Respeita pausa por limite do provedor e o teto de operações."""
    agora = agora or timezone.now()
    resumo = {"criados": 0, "atualizados": 0, "removidos": 0, "importados": 0, "puxados": 0, "erros": 0}
    if conexao.status != Conexao.CONECTADA or not conexao.adaptador.sincroniza_agenda:
        return resumo
    pausa = conexao.config.get("pausa_ate")
    if pausa and agora < datetime.fromisoformat(pausa):
        return resumo
    if conexao.expira_em and conexao.expira_em - agora < RENOVAR_ANTES:
        if not renovar_credenciais(conexao, agora) and conexao.status != Conexao.CONECTADA:
            return resumo
        conexao.refresh_from_db()
    adaptador = conexao.adaptador
    try:
        credenciais = credenciais_de(conexao)
        operacoes = 0
        hoje = timezone.localdate()
        desde, ate = _dia(hoje - timedelta(days=1)), _dia(hoje + timedelta(days=JANELA_DIAS), True)
        mapa = {(i.tipo_local, i.id_local): i for i in conexao.itens_sincronizados.all()}
        locais = eventos_locais(conexao, hoje)

        # (2) puxar: só compromissos (vencimentos pertencem ao sistema)
        externos = adaptador.listar_eventos(conexao, credenciais, desde, ate)
        por_externo = {i.id_externo: i for i in mapa.values()}
        ids_vistos = set()
        for externo in externos:
            if operacoes >= limite:
                break
            ids_vistos.add(str(externo["id"]))
            item = por_externo.get(str(externo["id"]))
            inicio, fim = _interpretar_data(externo["inicio"], externo["todo_o_dia"]), _interpretar_data(externo["fim"], externo["todo_o_dia"])
            if inicio is None or fim is None:
                continue
            if externo["todo_o_dia"] and externo["fim"] and fim > inicio:
                fim = fim - timedelta(days=1)  # o fim externo de dia inteiro é exclusivo
            conteudo = {"titulo": externo["titulo"], "descricao": externo.get("descricao", ""), "inicio": inicio, "fim": fim, "todo_o_dia": externo["todo_o_dia"]}
            if item is None:
                c = Compromisso.objects.create(cliente=conexao.cliente, titulo=conteudo["titulo"][:150], descricao=conteudo["descricao"][:500], inicio=inicio, fim=fim, todo_o_dia=conteudo["todo_o_dia"], origem=Compromisso.EXTERNA)
                ItemSincronizado.objects.create(conexao=conexao, tipo_local="compromisso", id_local=c.pk, id_externo=str(externo["id"]), hash_conteudo=_hash(conteudo), etag=externo.get("etag", ""))
                resumo["importados"] += 1
                operacoes += 1
            elif item.tipo_local == "compromisso" and item.hash_conteudo != _hash(conteudo):
                c = Compromisso.objects.filter(pk=item.id_local, cliente=conexao.cliente).first()
                alterado_externo = _interpretar_data(externo.get("atualizado_em"), False) if externo.get("atualizado_em") else None
                if c is not None and alterado_externo and alterado_externo > c.atualizado_em:  # externo mais recente vence
                    Compromisso.objects.filter(pk=c.pk).update(titulo=conteudo["titulo"][:150], descricao=conteudo["descricao"][:500], inicio=inicio, fim=fim, todo_o_dia=conteudo["todo_o_dia"], atualizado_em=alterado_externo)
                    ItemSincronizado.objects.filter(pk=item.pk).update(hash_conteudo=_hash(conteudo), etag=externo.get("etag", ""))
                    resumo["puxados"] += 1
                    operacoes += 1
        # evento de compromisso removido NO calendário (e dentro da janela): desativa o compromisso local
        if operacoes < limite:
            for (tipo, id_local), item in list(mapa.items()):
                if tipo == "compromisso" and item.id_externo not in ids_vistos:
                    c = Compromisso.objects.filter(pk=id_local, cliente=conexao.cliente, ativo=True, inicio__gte=desde, fim__lte=ate).first()
                    if c is not None:
                        Compromisso.objects.filter(pk=c.pk).update(ativo=False)
                        item.delete()
                        mapa.pop((tipo, id_local), None)
                        locais.pop((tipo, id_local), None)
                        resumo["removidos"] += 1
                        operacoes += 1

        # (3) empurrar
        for chave, evento in locais.items():
            if operacoes >= limite:
                break
            item, h = mapa.get(chave), _hash(evento)
            if item is None:
                id_externo, etag = adaptador.criar_evento(conexao, credenciais, evento)
                ItemSincronizado.objects.create(conexao=conexao, tipo_local=chave[0], id_local=chave[1], id_externo=str(id_externo), hash_conteudo=h, etag=etag or "")
                resumo["criados"] += 1
                operacoes += 1
            elif item.hash_conteudo != h:
                etag = adaptador.atualizar_evento(conexao, credenciais, item.id_externo, evento)
                ItemSincronizado.objects.filter(pk=item.pk).update(hash_conteudo=h, etag=etag or "", sincronizado_em=agora)
                resumo["atualizados"] += 1
                operacoes += 1
        for chave, item in mapa.items():
            if operacoes >= limite:
                break
            if chave not in locais:  # conta paga/cancelada/fora da janela ou compromisso desativado: sai da agenda
                adaptador.remover_evento(conexao, credenciais, item.id_externo)
                item.delete()
                resumo["removidos"] += 1
                operacoes += 1
        conexao.ultima_sincronizacao = agora
        conexao.config = {k: v for k, v in conexao.config.items() if k != "pausa_ate"}
        conexao.save(update_fields=["ultima_sincronizacao", "config", "atualizado_em"])
        log(conexao, LogIntegracao.INFO, "sincronizacao", ", ".join(f"{k}={v}" for k, v in resumo.items()))
    except ErroIntegracao as erro:
        resumo["erros"] += 1
        if erro.temporario:  # limite do provedor/rede: pausa a conexão e tenta mais tarde
            conexao.config = {**conexao.config, "pausa_ate": (agora + timedelta(seconds=erro.espera or 300)).isoformat()}
            conexao.save(update_fields=["config", "atualizado_em"])
            log(conexao, LogIntegracao.AVISO, "sincronizacao", f"Pausada: {_mascarar(erro)}")
        else:
            _marcar(conexao, Conexao.ERRO, str(erro))
            log(conexao, LogIntegracao.ERRO, "sincronizacao", _mascarar(erro))
            notificar_equipe(conexao, f"Erro em {conexao.nome}", "A sincronização da agenda falhou. Verifique a conexão.")
    except ValueError:
        resumo["erros"] += 1
        _marcar(conexao, Conexao.ERRO, "Credenciais ilegíveis.")
    return resumo
