"""Regras da assinatura: máquina de estados, webhooks, assinar/cancelar, verificação periódica e bloqueio.

Máquina de estados (única fonte; ver README.md):

    pendente --pagou--> ativa --venceu/falha--> em_atraso --carência esgotada--> bloqueada
        ^                 |  \\--anual venceu--> expirada          |
        |                 |                                       +--pagou--> ativa (desbloqueia sozinho)
        +-- (nova assinatura sobre cancelada/expirada)   qualquer --cancelou--> cancelada (acesso até vigente_ate)

Princípios: só o WEBHOOK AUTENTICADO + confirmação na API do provedor marca pagamento; evento repetido não repete efeito
(`EventoWebhook.hook_id` único); evento fora de ordem não rebaixa (fatura paga nunca volta a "falhou"); o tenant é sempre
o cliente RAIZ (matriz) derivado do usuário autenticado.
"""
import calendar
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao
from web_project.acesso import clientes_acessiveis
from web_project.regras import RegraNegocioError

from . import pagarme
from .models import Assinatura, Cobranca, EventoAssinatura, EventoWebhook, Plano

logger = logging.getLogger(__name__)

AVISO_DIAS = 30  # avisa a renovação com esta antecedência
ESTADOS_COM_ASSINATURA_VIVA = (Assinatura.PENDENTE, Assinatura.ATIVA, Assinatura.EM_ATRASO, Assinatura.BLOQUEADA)


# --------------------------------------------------------------------------
# Utilitários
# --------------------------------------------------------------------------

def somar_meses(data, meses):
    """Soma meses mantendo o dia (ou o último dia do mês, ex.: 31/01 + 1 mês = 28/02)."""
    indice = data.month - 1 + meses
    ano, mes = data.year + indice // 12, indice % 12 + 1
    return data.replace(year=ano, month=mes, day=min(data.day, calendar.monthrange(ano, mes)[1]))


def raiz_do_usuario(usuario):
    """Cliente RAIZ (matriz) do usuário: é quem assina pela estrutura inteira."""
    cliente = getattr(getattr(usuario, "profile", None), "cliente", None)
    if cliente is None:
        raise RegraNegocioError("Seu usuário não está vinculado a um cliente.")
    return cliente.matriz or cliente


def assinatura_do_tenant(cliente):
    """Assinatura da estrutura do cliente (a da matriz) ou None."""
    raiz_id = cliente.raiz_id
    return Assinatura.objects.select_related("plano").filter(cliente_id=raiz_id).first()


def _evento(assinatura, tipo, descricao):
    EventoAssinatura.objects.create(assinatura=assinatura, tipo=tipo, descricao=descricao[:200])


def _auditar(request, assinatura, campo, antes, depois):
    if request is not None:
        registrar_alteracoes(request, assinatura, [{"campo": campo, "rotulo": campo.capitalize(), "antes": antes, "depois": depois}], modulo="assinatura")


def _mudar_status(assinatura, novo, descricao, request=None, campos=()):
    antes = assinatura.status
    assinatura.status = novo
    assinatura.save(update_fields=["status", "atualizado_em", *campos])
    _evento(assinatura, "status", f"{descricao} ({assinatura.get_status_display()})")
    _auditar(request, assinatura, "status", antes, novo)


def _notificar(assinatura, titulo, mensagem):
    """Avisa (sino/push) quem administra a assinatura: usuários ativos da estrutura com `assinatura.view_assinatura`."""
    from django.db.models import Q

    from apps.notificacao.services import enviar_para_usuarios

    raiz = assinatura.cliente_id
    candidatos = User.objects.filter(is_active=True).filter(Q(profile__cliente_id=raiz) | Q(profile__cliente__matriz_id=raiz))
    destino = [u for u in candidatos if u.has_perm("assinatura.view_assinatura")]
    if destino:
        enviar_para_usuarios(titulo, mensagem, "/assinatura/", destino)


# --------------------------------------------------------------------------
# Bloqueio de acesso (consultado por web_project/acesso.py::modulos_habilitados — ÚNICO ponto)
# --------------------------------------------------------------------------

def acesso_bloqueado(cliente):
    """True se o tenant do cliente não pode usar os módulos operacionais.
    Sem assinatura: só bloqueia se ASSINATURA_OBRIGATORIA (padrão: clientes existentes continuam funcionando)."""
    assinatura = Assinatura.objects.filter(cliente_id=cliente.raiz_id).only("status", "vigente_ate").first()
    if assinatura is None:
        return bool(settings.ASSINATURA_OBRIGATORIA)
    if assinatura.status in (Assinatura.BLOQUEADA, Assinatura.EXPIRADA):
        return True
    if assinatura.status == Assinatura.CANCELADA:  # paga até o fim do período
        return assinatura.vigente_ate is None or assinatura.vigente_ate <= timezone.now()
    return False


# --------------------------------------------------------------------------
# Transições
# --------------------------------------------------------------------------

def registrar_pagamento(assinatura, agora=None):
    """Um pagamento novo foi confirmado: estende a vigência em um ciclo e (re)ativa/desbloqueia."""
    agora = agora or timezone.now()
    base = max(agora, assinatura.vigente_ate or agora)
    assinatura.vigente_ate = somar_meses(base, assinatura.plano.meses)
    assinatura.em_atraso_desde = assinatura.bloqueada_em = assinatura.aviso_vencimento_em = None
    if assinatura.status == Assinatura.CANCELADA:  # pagou depois de cancelar: só estende o período, segue cancelada
        assinatura.save(update_fields=["vigente_ate", "em_atraso_desde", "bloqueada_em", "aviso_vencimento_em", "atualizado_em"])
        return
    reativou = assinatura.status in (Assinatura.BLOQUEADA, Assinatura.EXPIRADA, Assinatura.EM_ATRASO)
    _mudar_status(assinatura, Assinatura.ATIVA, "Pagamento confirmado" + (" — acesso liberado" if reativou else ""),
                  campos=("vigente_ate", "em_atraso_desde", "bloqueada_em", "aviso_vencimento_em"))
    if reativou:
        _notificar(assinatura, "Assinatura regularizada", "Pagamento confirmado: o acesso está liberado.")


def registrar_falha(assinatura, agora=None):
    """Cobrança falhou: entra em atraso (carência). Já bloqueada/cancelada/expirada não muda."""
    if assinatura.status in (Assinatura.PENDENTE, Assinatura.ATIVA):
        assinatura.em_atraso_desde = assinatura.em_atraso_desde or agora or timezone.now()
        _mudar_status(assinatura, Assinatura.EM_ATRASO, "Cobrança não paga", campos=("em_atraso_desde",))
        _notificar(assinatura, "Pagamento da assinatura pendente",
                   f"Regularize em até {settings.ASSINATURA_CARENCIA_DIAS} dias para não perder o acesso.")


def cancelar_local(assinatura, descricao, request=None, agora=None):
    if assinatura.status == Assinatura.CANCELADA:
        return
    assinatura.cancelada_em = agora or timezone.now()
    _mudar_status(assinatura, Assinatura.CANCELADA, descricao, request, campos=("cancelada_em",))


# --------------------------------------------------------------------------
# Webhook
# --------------------------------------------------------------------------

def _texto(valor, limite=80):
    return str(valor)[:limite] if isinstance(valor, (str, int)) else ""


def _sub_id(dados):
    """Id da assinatura no payload: `data.subscription.id` (fatura/cobrança) ou o próprio `data.id` (assinatura).
    Formato do payload NÃO confirmado na documentação — validar na homologação (README_PAGARME.md)."""
    sub = dados.get("subscription")
    if isinstance(sub, dict) and sub.get("id"):
        return _texto(sub["id"])
    if isinstance(sub, str):
        return _texto(sub)
    identificador = _texto(dados.get("id"))
    return identificador if identificador.startswith("sub_") else ""


def _aplicar_fatura(assinatura, tipo, dados, agora):
    invoice_id = _texto(dados.get("id"))
    if not invoice_id:
        return "sem_id_da_fatura"
    charge = dados.get("charge") if isinstance(dados.get("charge"), dict) else {}
    cobranca, _ = Cobranca.objects.get_or_create(
        pagarme_invoice_id=invoice_id,
        defaults={"assinatura": assinatura, "valor": int(dados.get("amount") or 0) if str(dados.get("amount") or 0).isdigit() else 0,
                  "forma_pagamento": _texto(dados.get("payment_method"), 12), "url_pagamento": _texto(dados.get("url"), 500) if str(dados.get("url", "")).startswith("https://") else "",
                  "vencimento": parse_datetime(str(dados.get("due_at") or "")) or None},
    )
    if cobranca.assinatura_id != assinatura.pk:  # fatura de outra assinatura: nunca cruza tenants
        return "fatura_de_outra_assinatura"
    if charge.get("id"):
        cobranca.pagarme_charge_id = _texto(charge["id"])

    if tipo == "invoice.paid":
        if cobranca.status == Cobranca.PAGA:
            return "ja_paga"
        if settings.PAGARME_CONFIRMAR_PAGAMENTO:
            if not cobranca.pagarme_charge_id:
                return "nao_confirmado_sem_cobranca"
            try:
                confirmada = pagarme.obter_cliente().obter_cobranca(cobranca.pagarme_charge_id)
            except pagarme.ErroPagarme:
                raise  # o webhook responde 5xx e o provedor reenvia (o evento NÃO é marcado como processado)
            if confirmada.get("status") != "paid":
                return "nao_confirmado_pelo_provedor"
        cobranca.status, cobranca.pago_em = Cobranca.PAGA, agora
        cobranca.save()
        registrar_pagamento(assinatura, agora)
        return "pagamento_registrado"

    if tipo == "invoice.payment_failed":
        if cobranca.status == Cobranca.PAGA:
            return "ignorado_fora_de_ordem"
        cobranca.status = Cobranca.FALHOU
        cobranca.save()
        registrar_falha(assinatura, agora)
        return "falha_registrada"

    if tipo == "invoice.canceled":
        if cobranca.status != Cobranca.PAGA:
            cobranca.status = Cobranca.CANCELADA
        cobranca.save()
        return "fatura_cancelada"

    cobranca.save()  # created/updated: só atualiza dados
    return "fatura_atualizada"


def processar_webhook(payload, agora=None):
    """Processa UM webhook já autenticado pela view. Devolve o resultado (texto curto).

    Idempotente: `hook_id` repetido e já processado => "duplicado" sem efeito. Falhas transitórias (provedor fora do ar na
    confirmação) propagam ErroPagarme: nada é gravado e o provedor tenta de novo. Levanta ValueError se o payload é inválido."""
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido.")
    hook_id, tipo = _texto(payload.get("id")), _texto(payload.get("type"), 60)
    dados = payload.get("data")
    if not hook_id or not tipo or not isinstance(dados, dict):
        raise ValueError("Payload inválido.")
    agora = agora or timezone.now()

    with transaction.atomic():
        try:
            with transaction.atomic():
                evento = EventoWebhook.objects.create(hook_id=hook_id, tipo=tipo)
        except IntegrityError:
            evento = EventoWebhook.objects.select_for_update().get(hook_id=hook_id)
            if evento.processado_em:
                return "duplicado"
        sub_id = _sub_id(dados)
        assinatura = Assinatura.objects.select_for_update().select_related("plano").filter(pagarme_subscription_id=sub_id).first() if sub_id else None
        if tipo.startswith("invoice.") and assinatura:
            resultado = _aplicar_fatura(assinatura, tipo, dados, agora)
        elif tipo == "subscription.canceled" and assinatura:
            cancelar_local(assinatura, "Cancelada no provedor", agora=agora)
            resultado = "cancelada"
        elif assinatura is None and (tipo.startswith("invoice.") or tipo.startswith("subscription.")):
            resultado = "assinatura_desconhecida"  # não é nossa (ou ainda não gravamos o id): ignorado, sem erro
        else:
            resultado = "ignorado"
        evento.processado_em, evento.resultado = agora, resultado
        evento.save(update_fields=["processado_em", "resultado"])
    return resultado


# --------------------------------------------------------------------------
# Ações do usuário
# --------------------------------------------------------------------------

def _exigir(usuario, permissao="assinatura.gerenciar_assinatura"):
    if not usuario.has_perm(permissao):
        raise PermissionDenied("Sem permissão para gerenciar a assinatura.")


def sincronizar_plano(plano, cliente_api=None):
    """Cria o plano no provedor se ainda não existe (guarda `pagarme_plan_id`). Mudar o valor de um plano já sincronizado
    NÃO altera o do provedor: crie um plano novo (ver README_PAGARME.md)."""
    if plano.pagarme_plan_id:
        return plano
    resposta = (cliente_api or pagarme.obter_cliente()).criar_plano(plano)
    plano.pagarme_plan_id = _texto(resposta.get("id"), 60)
    if not plano.pagarme_plan_id:
        raise RegraNegocioError("O provedor não devolveu o código do plano.")
    plano.save(update_fields=["pagarme_plan_id", "atualizado_em"])
    return plano


def assinar(request, plano_slug, forma, card_token=""):
    """Assina (ou reassina, se a anterior foi cancelada/expirada) o plano para o tenant do usuário.
    Cartão: exige `card_token` gerado no navegador pela chave pública (o servidor nunca vê o número)."""
    usuario = request.user
    _exigir(usuario)
    cliente = raiz_do_usuario(usuario)
    if forma not in dict(Assinatura.FORMA_CHOICES):
        raise RegraNegocioError("Forma de pagamento inválida.")
    if forma == Assinatura.CARTAO and not (card_token or "").startswith("token_"):
        raise RegraNegocioError("Cartão não tokenizado. Recarregue a página e tente novamente.")
    plano = Plano.objects.filter(slug=plano_slug, ativo=True).first()
    if plano is None:
        raise RegraNegocioError("Plano indisponível.")
    existente = Assinatura.objects.filter(cliente=cliente).first()
    if existente and existente.status in ESTADOS_COM_ASSINATURA_VIVA:
        raise RegraNegocioError("Já existe uma assinatura em andamento. Cancele-a antes de assinar outro plano.")
    api = pagarme.obter_cliente()  # NaoConfigurado => a view mostra "indisponível"
    sincronizar_plano(plano, api)
    dono = {"name": cliente.nome_razao_social[:64], "email": usuario.email, "document": cliente.cpf_cnpj,
            "type": "company" if cliente.tipo_pessoa == "PJ" else "individual",
            "document_type": "CNPJ" if cliente.tipo_pessoa == "PJ" else "CPF"}
    if not usuario.email:
        raise RegraNegocioError("Cadastre um e-mail no seu usuário para receber as cobranças.")
    # Chama o provedor ANTES de gravar: se falhar nada fica pela metade. Código de idempotência estável por tenant+plano.
    resposta = api.criar_assinatura(plano.pagarme_plan_id, dono, forma, card_token or None, codigo=f"sfc-{cliente.pk}-{plano.slug}-{timezone.now():%Y%m%d%H%M%S}")
    sub_id = _texto(resposta.get("id"), 60)
    if not sub_id.startswith("sub_"):
        raise RegraNegocioError("O provedor não confirmou a assinatura.")
    with transaction.atomic():
        proxima = parse_datetime(str(resposta.get("next_billing_at") or ""))
        campos = dict(plano=plano, status=Assinatura.PENDENTE, forma_pagamento=forma, pagarme_subscription_id=sub_id, proxima_cobranca_em=proxima,
                      vigente_ate=None, em_atraso_desde=None, bloqueada_em=None, cancelada_em=None, aviso_vencimento_em=None)
        if existente:
            for k, v in campos.items():
                setattr(existente, k, v)
            existente.save()
            assinatura = existente
            _auditar(request, assinatura, "plano", "(anterior)", plano.nome)
        else:
            assinatura = Assinatura.objects.create(cliente=cliente, **campos)
            registrar_criacao(request, assinatura, modulo="assinatura")
        _evento(assinatura, "criada", f"Assinatura do plano {plano.nome} criada ({assinatura.get_forma_pagamento_display()})")
    return assinatura


def cancelar(request):
    """Cancela a assinatura do tenant no provedor e localmente (acesso até o fim do período pago)."""
    _exigir(request.user)
    cliente = raiz_do_usuario(request.user)
    with transaction.atomic():
        assinatura = Assinatura.objects.select_for_update().select_related("plano").filter(cliente=cliente).first()
        if assinatura is None or assinatura.status == Assinatura.CANCELADA:
            raise RegraNegocioError("Não há assinatura ativa para cancelar.")
        if assinatura.pagarme_subscription_id:
            pagarme.obter_cliente().cancelar_assinatura(assinatura.pagarme_subscription_id)
        cancelar_local(assinatura, "Cancelada pelo usuário", request)
    return assinatura


# --------------------------------------------------------------------------
# Verificação periódica (comando verificar_assinaturas)
# --------------------------------------------------------------------------

def verificar_assinaturas(agora=None, consultar_provedor=True):
    """Aplica carência/vencimento, avisa renovações e reconcilia com o provedor. Idempotente. Devolve contadores."""
    agora = agora or timezone.now()
    carencia = timedelta(days=settings.ASSINATURA_CARENCIA_DIAS)
    resumo = {"bloqueadas": 0, "em_atraso": 0, "expiradas": 0, "avisos": 0, "reconciliadas": 0, "erros": 0}
    try:
        api = pagarme.obter_cliente() if consultar_provedor else None
    except pagarme.NaoConfigurado:
        api = None

    for a in Assinatura.objects.select_related("plano", "cliente").exclude(status__in=(Assinatura.CANCELADA, Assinatura.EXPIRADA)):
        # 1) vencimento do período pago
        if a.status == Assinatura.ATIVA and a.vigente_ate and a.vigente_ate < agora:
            if a.plano.meses >= 12:  # anual não renova sozinho sem pagamento: expira
                _mudar_status(a, Assinatura.EXPIRADA, "Plano anual vencido")
                resumo["expiradas"] += 1
                _notificar(a, "Assinatura expirada", "O plano anual venceu. Renove para continuar usando o sistema.")
                continue
            a.em_atraso_desde = a.vigente_ate  # a carência conta do vencimento
            _mudar_status(a, Assinatura.EM_ATRASO, "Período vencido sem pagamento", campos=("em_atraso_desde",))
            resumo["em_atraso"] += 1
        # 2) carência esgotada => bloqueia
        if a.status == Assinatura.EM_ATRASO and a.em_atraso_desde and a.em_atraso_desde + carencia <= agora:
            a.bloqueada_em = agora
            _mudar_status(a, Assinatura.BLOQUEADA, "Carência esgotada — acesso bloqueado", campos=("bloqueada_em",))
            resumo["bloqueadas"] += 1
            _notificar(a, "Acesso bloqueado", "A assinatura não foi paga. Regularize em Assinatura para liberar o acesso.")
        # 3) aviso de renovação (uma vez por ciclo)
        if a.status == Assinatura.ATIVA and a.vigente_ate and agora >= a.vigente_ate - timedelta(days=AVISO_DIAS) and a.aviso_vencimento_em is None:
            a.aviso_vencimento_em = agora
            a.save(update_fields=["aviso_vencimento_em", "atualizado_em"])
            _notificar(a, "Assinatura perto de vencer", f"O período vigente termina em {a.vigente_ate:%d/%m/%Y}.")
            resumo["avisos"] += 1
        # 4) reconcilia com o provedor (cancelamento feito lá, próxima cobrança)
        if api and a.pagarme_subscription_id:
            try:
                remota = api.obter_assinatura(a.pagarme_subscription_id)
            except pagarme.ErroPagarme:
                resumo["erros"] += 1
                continue
            if remota.get("status") == "canceled" and a.status != Assinatura.CANCELADA:
                cancelar_local(a, "Cancelada no provedor (reconciliação)", agora=agora)
            proxima = parse_datetime(str(remota.get("next_billing_at") or ""))
            if proxima and proxima != a.proxima_cobranca_em:
                a.proxima_cobranca_em = proxima
                a.save(update_fields=["proxima_cobranca_em", "atualizado_em"])
            resumo["reconciliadas"] += 1
    return resumo


# --------------------------------------------------------------------------
# Leitura para telas/API (aba do cliente)
# --------------------------------------------------------------------------

def painel_do_cliente(usuario, cliente_id=None):
    """Dados da aba Pagamentos/Assinatura. Tenant: só a estrutura que o usuário alcança (`clientes_acessiveis`); fora disso
    => Http404 (igual a "não existe"). Sem `cliente_id`, usa o cliente do próprio usuário. Exige `assinatura.view_assinatura`."""
    if not usuario.has_perm("assinatura.view_assinatura"):
        raise PermissionDenied("Sem permissão para ver a assinatura.")
    if cliente_id is None:
        cliente = getattr(getattr(usuario, "profile", None), "cliente", None)
        if cliente is None:
            raise Http404
    else:
        cliente = clientes_acessiveis(usuario).filter(pk=cliente_id).first()
        if cliente is None:
            raise Http404
    assinatura = assinatura_do_tenant(cliente)
    return {
        "cliente": cliente, "assinatura": assinatura, "planos": Plano.objects.filter(ativo=True),
        "cobrancas": list(assinatura.cobrancas.all()[:24]) if assinatura else [],
        "eventos": list(assinatura.eventos.all()[:30]) if assinatura else [],
        "pode_gerenciar": usuario.has_perm("assinatura.gerenciar_assinatura") and cliente.raiz_id == (getattr(usuario.profile.cliente, "raiz_id", None)),
        "bloqueado": acesso_bloqueado(cliente), "chave_publica": settings.PAGARME_PUBLIC_KEY,
        "configurado": bool(settings.PAGARME_SECRET_KEY),
    }
