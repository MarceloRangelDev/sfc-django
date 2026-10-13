"""Regras de estoque: entradas, saídas (PEPS por validade), ajustes,
transferências entre unidades, mínimos, alertas e conferência de consistência.

Princípios:
- Todo movimento acontece numa transação, com o(s) lote(s) bloqueado(s)
  (select_for_update) e saldo nunca negativo (também garantido por CheckConstraint
  no banco): operações simultâneas não deixam o estoque inconsistente.
- O histórico (MovimentoEstoque/TransferenciaEstoque) é imutável: correções são
  ajustes de sinal oposto.
- Quantidades ficam na unidade de medida do INSUMO (Insumo.medida); o usuário pode
  informar outra unidade da mesma dimensão (convertida pela tabela `medidas`).
- Saída consome primeiro o lote que vence antes (PEPS/FEFO); sem validade, o mais antigo.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, F, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from apps.insumo.models import Insumo
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import converter

from web_project.historico import corrigir_em_lote, corrigir_registro

from .models import AlertaEnviado, EstoqueMinimo, Lote, MovimentoEstoque, TransferenciaEstoque, codigo_do_lote_por_validade

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
MAX_ITENS_NO_ALERTA = 10


def dias_alerta_vencimento():
    return getattr(settings, "ESTOQUE_DIAS_ALERTA_VENCIMENTO", 7)


# --------------------------------------------------------------------------
# Quantidades e saldos
# --------------------------------------------------------------------------

def para_unidade_do_insumo(insumo, quantidade, medida):
    """Quantidade informada em `medida` convertida para a unidade do insumo
    (4 casas). Unidade de outra dimensão ou quantidade que arredonda para 0 → erro."""
    convertida = converter(quantidade, medida, insumo.medida, casas=4)
    if convertida <= 0:
        raise RegraNegocioError("A quantidade é pequena demais para a unidade do insumo.")
    return convertida


def saldo_do_insumo(insumo):
    """Saldo total (soma dos lotes) na unidade do insumo."""
    return Lote.objects.filter(cliente_id=insumo.cliente_id, insumo=insumo).aggregate(
        total=Coalesce(Sum("saldo"), Value(ZERO), output_field=DecimalField())
    )["total"]


def insumo_tem_estoque(insumo):
    """True se o insumo já tem lote/movimento: sua unidade não pode mais mudar
    (os saldos guardados estão na unidade atual)."""
    return Lote.objects.filter(insumo=insumo).exists()


def anotar_saldo(queryset):
    """Anota num queryset de Insumo: `saldo` (soma dos lotes), `minimo` e
    `proximo_vencimento` (menor validade entre os lotes com saldo)."""
    return queryset.annotate(
        saldo=Coalesce(Sum("lotes__saldo"), Value(ZERO), output_field=DecimalField()),
        minimo=Coalesce(F("estoque_minimo__minimo"), Value(ZERO), output_field=DecimalField()),
        proximo_vencimento=Min("lotes__validade", filter=Q(lotes__saldo__gt=0)),
    )


def insumos_com_saldo(clientes):
    """Insumos ATIVOS dos clientes com saldo e mínimo anotados."""
    return anotar_saldo(Insumo.objects.filter(cliente__in=clientes, ativo=True))


def mensagem_saldo_em_estoque(insumo):
    """Texto de bloqueio se excluir/inativar o insumo deixaria saldo órfão; senão None."""
    saldo = saldo_do_insumo(insumo)
    if saldo > 0:
        return f'O insumo "{insumo.nome}" tem {saldo.normalize():f} {insumo.medida.sigla} em estoque. Zere o estoque antes de excluir.'
    return None


# --------------------------------------------------------------------------
# Movimentos
# --------------------------------------------------------------------------

def _nome_do_responsavel(usuario):
    if usuario is None:
        return ""
    return (usuario.get_full_name() or usuario.get_username())[:150]


def _lote_para_entrada(cliente_id, insumo, codigo, validade):
    """Lote (já BLOQUEADO) onde a entrada será lançada: cria se não existe.
    Lote existente com outra validade é recusado (o lote tem uma validade só)."""
    codigo = (codigo or "").strip()
    if validade and not codigo:
        # Validade sem lote: a validade vale por si só (lote automático "VAL-AAAAMMDD").
        codigo = codigo_do_lote_por_validade(validade)
    Lote.objects.get_or_create(cliente_id=cliente_id, insumo=insumo, codigo=codigo, defaults={"validade": validade})
    lote = Lote.objects.select_for_update().get(cliente_id=cliente_id, insumo=insumo, codigo=codigo)
    if validade and lote.validade != validade:
        if lote.validade is None and lote.saldo == 0:
            lote.validade = validade
            lote.save(update_fields=["validade"])
        elif lote.validade is not None:
            raise RegraNegocioError(
                f'O lote "{codigo}" já existe com validade {lote.validade.strftime("%d/%m/%Y")}; use outro código de lote.'
            )
    return lote


def _lancar(usuario, lote, tipo, quantidade, observacao="", data=None, transferencia=None):
    """Aplica o movimento ao lote (já bloqueado) e grava o histórico."""
    sinal = 1 if tipo in MovimentoEstoque.TIPOS_DE_ENTRADA else -1
    novo_saldo = lote.saldo + sinal * quantidade
    if novo_saldo < 0:
        raise RegraNegocioError(f'Saldo insuficiente no lote "{lote}" de "{lote.insumo.nome}".')
    lote.saldo = novo_saldo
    lote.save(update_fields=["saldo"])
    return MovimentoEstoque.objects.create(
        cliente_id=lote.cliente_id, insumo=lote.insumo, lote=lote, tipo=tipo, quantidade=quantidade, saldo_lote_apos=novo_saldo,
        responsavel=usuario, responsavel_nome=_nome_do_responsavel(usuario), observacao=(observacao or "")[:255],
        data=data or timezone.now(), transferencia=transferencia,
    )


def _consumir_peps(usuario, insumo, quantidade, tipo, observacao="", data=None, transferencia=None):
    """Saída `quantidade` (unidade do insumo) dos lotes que vencem primeiro. Devolve
    a lista de movimentos (um por lote tocado). Chamar DENTRO de transaction.atomic()."""
    lotes = list(
        Lote.objects.select_for_update().select_related("insumo").filter(cliente_id=insumo.cliente_id, insumo=insumo, saldo__gt=0)
        .order_by(F("validade").asc(nulls_last=True), "criado_em", "pk")
    )
    disponivel = sum((l.saldo for l in lotes), ZERO)
    if quantidade > disponivel:
        raise RegraNegocioError(
            f'Saldo insuficiente de "{insumo.nome}": disponível {disponivel.normalize():f} {insumo.medida.sigla}, '
            f'solicitado {quantidade.normalize():f}.'
        )
    restante, movimentos = quantidade, []
    for lote in lotes:
        if restante <= 0:
            break
        retirar = min(lote.saldo, restante)
        movimentos.append(_lancar(usuario, lote, tipo, retirar, observacao, data, transferencia))
        restante -= retirar
    return movimentos


def registrar_entrada(usuario, insumo, quantidade, medida, codigo="", validade=None, observacao="", tipo=MovimentoEstoque.ENTRADA):
    """Entrada (ou ajuste de acréscimo, `tipo=AJUSTE_ENTRADA`) no lote informado
    (vazio = sem lote). Devolve o movimento."""
    if tipo not in (MovimentoEstoque.ENTRADA, MovimentoEstoque.AJUSTE_ENTRADA):
        raise RegraNegocioError("Tipo de entrada inválido.")
    qtd = para_unidade_do_insumo(insumo, quantidade, medida)
    with transaction.atomic():
        lote = _lote_para_entrada(insumo.cliente_id, insumo, codigo, validade)
        return _lancar(usuario, lote, tipo, qtd, observacao)


def registrar_saida(usuario, insumo, quantidade, medida, observacao="", tipo=MovimentoEstoque.SAIDA):
    """Saída (ou ajuste de redução, `tipo=AJUSTE_SAIDA`) pelo PEPS. Devolve a lista
    de movimentos. Depois de gravar, avisa se o saldo ficou abaixo do mínimo."""
    if tipo not in (MovimentoEstoque.SAIDA, MovimentoEstoque.AJUSTE_SAIDA):
        raise RegraNegocioError("Tipo de saída inválido.")
    qtd = para_unidade_do_insumo(insumo, quantidade, medida)
    with transaction.atomic():
        movimentos = _consumir_peps(usuario, insumo, qtd, tipo, observacao)
        _agendar_alerta_de_baixo(insumo)
    return movimentos


MANTER_VALIDADE = object()  # sentinela: "não mexer na validade" (None significa REMOVER a validade)


def editar_movimento(request, movimento_id, escopo, quantidade, medida, data, observacao="", validade=MANTER_VALIDADE):
    """Corrige um movimento de estoque já gravado e REAJUSTA os saldos.

    Pode mudar: quantidade (e a unidade em que foi digitada), data, observação e — só nas
    entradas — a validade (omitida = mantém; None = remove). Não muda: insumo, tipo, lote e unidade do insumo.
    `escopo`: clientes que o usuário pode alterar (tenant); movimento fora dele = inexistente.

    Cálculo: delta = sinal × (nova quantidade − antiga). O saldo do lote muda `delta` e o
    "saldo após" de cada movimento POSTERIOR do mesmo lote também (a história reflete a
    correção). A edição é recusada se o saldo atual ou o saldo após de QUALQUER movimento
    posterior ficaria negativo, ou se for um movimento de transferência. Roda numa transação
    com movimento e lote bloqueados (select_for_update), então edições/saídas simultâneas
    não deixam o estoque inconsistente; grava auditoria com antes/depois."""
    from apps.auditoria.utils import comparar, registrar_alteracoes

    with transaction.atomic():
        try:
            movimento = (
                MovimentoEstoque.objects.select_for_update().select_related("insumo", "insumo__medida", "lote")
                .get(pk=movimento_id, cliente__in=escopo)
            )
        except MovimentoEstoque.DoesNotExist:
            raise RegraNegocioError("Movimentação não encontrada.") from None
        if not movimento.editavel:
            raise RegraNegocioError("Movimentações de transferência não podem ser editadas; registre uma nova transferência.")
        insumo = movimento.insumo
        lote = Lote.objects.select_for_update().get(pk=movimento.lote_id)
        antes = _foto_do_movimento(movimento, lote)

        nova_quantidade = para_unidade_do_insumo(insumo, quantidade, medida)
        if data is not None and data > timezone.now() + timedelta(days=1):
            raise RegraNegocioError("A data da movimentação não pode estar no futuro.")
        delta = movimento.sinal * (nova_quantidade - movimento.quantidade)

        if delta != 0:
            posteriores = list(MovimentoEstoque.objects.select_for_update().filter(lote=lote, pk__gt=movimento.pk))
            menor = min([lote.saldo + delta, movimento.saldo_lote_apos + delta] + [p.saldo_lote_apos + delta for p in posteriores])
            if menor < 0:
                raise RegraNegocioError(
                    "Esta alteração deixaria o saldo do lote negativo em algum ponto do histórico; "
                    "ajuste primeiro as saídas posteriores ou reduza menos a quantidade."
                )
            if posteriores:
                corrigir_em_lote(MovimentoEstoque.objects.filter(pk__in=[p.pk for p in posteriores]), saldo_lote_apos=F("saldo_lote_apos") + delta)
            lote.saldo = lote.saldo + delta
            lote.save(update_fields=["saldo"])

        if validade is not MANTER_VALIDADE and movimento.tipo in MovimentoEstoque.TIPOS_DE_ENTRADA and validade != lote.validade:
            _trocar_validade_do_lote(lote, movimento, validade)

        corrigir_registro(
            movimento,
            quantidade=nova_quantidade, saldo_lote_apos=movimento.saldo_lote_apos + delta,
            data=data or movimento.data, observacao=(observacao or "")[:255], editado_em=timezone.now(),
        )
        alteracoes = comparar(antes, _foto_do_movimento(movimento, lote))
        if alteracoes:
            registrar_alteracoes(request, movimento, alteracoes, modulo="estoque")
        if delta < 0:
            _agendar_alerta_de_baixo(insumo)
    return movimento


def _foto_do_movimento(movimento, lote):
    """Foto (formato da auditoria) dos campos que a edição pode mudar."""
    from apps.auditoria.utils import tirar_foto

    foto = tirar_foto(movimento, ignorar=("cliente", "insumo", "lote", "tipo", "saldo_lote_apos", "responsavel", "responsavel_nome", "transferencia"))
    foto["validade"] = {
        "rotulo": "Validade", "valor": lote.validade, "texto": lote.validade.strftime("%d/%m/%Y") if lote.validade else None,
    }
    return foto


def _trocar_validade_do_lote(lote, movimento, validade):
    """Muda a validade do lote da entrada. Só quando este é o único movimento do lote (senão
    a validade de outras entradas mudaria junto). Lote automático ("VAL-...") é renomeado."""
    if lote.movimentos.exclude(pk=movimento.pk).exists():
        raise RegraNegocioError("A validade não pode ser alterada: o lote tem outras movimentações. Registre um novo lote.")
    novo_codigo = lote.codigo
    if lote.lote_automatico or (not lote.codigo and validade):
        novo_codigo = codigo_do_lote_por_validade(validade) if validade else ""
    if novo_codigo != lote.codigo and Lote.objects.filter(cliente_id=lote.cliente_id, insumo_id=lote.insumo_id, codigo=novo_codigo).exclude(pk=lote.pk).exists():
        raise RegraNegocioError("Já existe um lote com essa validade; use a movimentação desse lote.")
    lote.codigo, lote.validade = novo_codigo, validade
    lote.save(update_fields=["codigo", "validade"])


def transferir(usuario, origem, destino, itens, observacao=""):
    """Transfere insumos da unidade `origem` para `destino` (mesma estrutura
    matriz/filiais). `itens`: [(insumo da origem, quantidade, medida)]. O insumo de
    destino é o de MESMO NOME na unidade de destino (e mesma dimensão); lote e
    validade acompanham a mercadoria. Tudo ou nada. Quem chama já checou a permissão."""
    if origem.pk == destino.pk:
        raise RegraNegocioError("Origem e destino devem ser unidades diferentes.")
    if origem.raiz_id != destino.raiz_id:
        raise RegraNegocioError("A transferência só é possível entre unidades da mesma matriz.")
    if not destino.ativo:
        raise RegraNegocioError("A unidade de destino está inativa.")
    if not itens:
        raise RegraNegocioError("Informe ao menos um item para transferir.")

    with transaction.atomic():
        transferencia = TransferenciaEstoque.objects.create(
            origem=origem, destino=destino, responsavel=usuario, responsavel_nome=_nome_do_responsavel(usuario), observacao=(observacao or "")[:255],
        )
        origem_insumos = []
        for insumo, quantidade, medida in itens:
            if insumo.cliente_id != origem.pk:
                raise RegraNegocioError("Insumo não pertence à unidade de origem.")
            insumo_destino = Insumo.objects.select_related("medida").filter(cliente=destino, ativo=True, nome__iexact=insumo.nome).first()
            if insumo_destino is None:
                raise RegraNegocioError(f'A unidade de destino não tem o insumo "{insumo.nome}" cadastrado.')
            if insumo_destino.medida.dimensao != insumo.medida.dimensao:
                raise RegraNegocioError(f'"{insumo.nome}" tem unidades incompatíveis entre origem e destino.')
            qtd = para_unidade_do_insumo(insumo, quantidade, medida)
            saidas = _consumir_peps(usuario, insumo, qtd, MovimentoEstoque.TRANSFERENCIA_SAIDA, observacao, transferencia=transferencia)
            for saida in saidas:
                qtd_destino = converter(saida.quantidade, insumo.medida, insumo_destino.medida, casas=4)
                if qtd_destino <= 0:
                    raise RegraNegocioError("A quantidade é pequena demais para a unidade de destino.")
                lote_destino = _lote_para_entrada(destino.pk, insumo_destino, saida.lote.codigo, saida.lote.validade)
                _lancar(usuario, lote_destino, MovimentoEstoque.TRANSFERENCIA_ENTRADA, qtd_destino, observacao, transferencia=transferencia)
            origem_insumos.append(insumo)
        for insumo in origem_insumos:
            _agendar_alerta_de_baixo(insumo)
    return transferencia


# --------------------------------------------------------------------------
# Mínimos
# --------------------------------------------------------------------------

def definir_minimo(request, insumo, minimo):
    """Grava a quantidade mínima do insumo (0 = sem alerta), com auditoria."""
    from apps.auditoria.utils import registrar_alteracoes

    if minimo < 0:
        raise RegraNegocioError("O mínimo não pode ser negativo.")
    with transaction.atomic():
        atual = EstoqueMinimo.objects.filter(insumo=insumo).first()
        antes = atual.minimo if atual else ZERO
        EstoqueMinimo.objects.update_or_create(insumo=insumo, defaults={"minimo": minimo})
        if antes != minimo:
            sigla = insumo.medida.sigla
            registrar_alteracoes(
                request, insumo,
                [{"campo": "estoque_minimo", "rotulo": "Estoque mínimo", "antes": f"{antes.normalize():f} {sigla}", "depois": f"{minimo.normalize():f} {sigla}"}],
                modulo="estoque",
            )


# --------------------------------------------------------------------------
# Alertas
# --------------------------------------------------------------------------

def alertas_de_estoque_baixo(clientes):
    """Insumos ativos com mínimo definido e saldo abaixo dele."""
    return insumos_com_saldo(clientes).filter(minimo__gt=0, saldo__lt=F("minimo")).select_related("medida", "cliente").order_by("nome")


def alertas_de_vencimento(clientes, hoje=None, dias=None):
    """Lotes COM saldo vencidos ou que vencem em até `dias` dias (padrão: configuração)."""
    hoje = hoje or timezone.localdate()
    limite = hoje + timedelta(days=dias_alerta_vencimento() if dias is None else dias)
    return (
        Lote.objects.filter(cliente__in=clientes, saldo__gt=0, validade__isnull=False, validade__lte=limite, insumo__ativo=True)
        .select_related("insumo", "insumo__medida", "cliente").order_by("validade", "insumo__nome")
    )


def _destinatarios(cliente):
    """Usuários ativos da unidade que podem ver o estoque."""
    from django.contrib.auth.models import User

    candidatos = User.objects.filter(is_active=True, profile__cliente=cliente).select_related("profile")
    return [u for u in candidatos if u.has_perm("estoque.view_movimentoestoque")]


def _notificar(cliente, tipo, referencias, titulo, mensagem, link):
    """Notifica os usuários da unidade sobre os itens ainda não avisados HOJE
    (AlertaEnviado garante que a mesma notificação não se repete no dia)."""
    from apps.notificacao.services import enviar_para_usuarios

    hoje = timezone.localdate()
    novas = []
    for referencia in referencias:
        _, criado = AlertaEnviado.objects.get_or_create(cliente=cliente, tipo=tipo, referencia=referencia, data=hoje)
        if criado:
            novas.append(referencia)
    if novas:
        usuarios = _destinatarios(cliente)
        if usuarios:
            enviar_para_usuarios(titulo, mensagem, link, usuarios)
    return novas


def _agendar_alerta_de_baixo(insumo):
    """Depois da transação: se o saldo do insumo ficou abaixo do mínimo, avisa
    (uma vez por dia). Falha ao notificar nunca desfaz o movimento."""

    def enviar():
        try:
            minimo = EstoqueMinimo.objects.filter(insumo=insumo).values_list("minimo", flat=True).first() or ZERO
            saldo = saldo_do_insumo(insumo)
            if minimo > 0 and saldo < minimo:
                sigla = insumo.medida.sigla
                _notificar(
                    insumo.cliente, AlertaEnviado.TIPO_BAIXO, [insumo.pk], "Estoque baixo",
                    f'"{insumo.nome}": {saldo.normalize():f} {sigla} em estoque (mínimo {minimo.normalize():f} {sigla}).', reverse("estoque-alertas"),
                )
        except Exception:  # noqa: BLE001 — alerta é acessório
            logger.exception("Falha ao enviar alerta de estoque baixo (insumo=%s)", insumo.pk)

    transaction.on_commit(enviar)


def gerar_alertas(clientes):
    """Envia os alertas ainda não avisados hoje (estoque baixo e vencimentos) para
    cada unidade, UMA notificação-resumo por unidade e tipo. Idempotente: rodar de
    novo no mesmo dia não repete. Devolve {'baixo': n, 'vencimento': n} (itens novos)."""
    total = {"baixo": 0, "vencimento": 0}
    for cliente in clientes:
        baixos = list(alertas_de_estoque_baixo([cliente]))
        if baixos:
            avisados = _notificar(
                cliente, AlertaEnviado.TIPO_BAIXO, [i.pk for i in baixos], "Estoque baixo",
                _resumo([i.nome for i in baixos], "abaixo do mínimo"), reverse("estoque-alertas"),
            )
            total["baixo"] += len(avisados)
        vencendo = list(alertas_de_vencimento([cliente]))
        if vencendo:
            avisados = _notificar(
                cliente, AlertaEnviado.TIPO_VENCIMENTO, [l.pk for l in vencendo], "Itens vencendo",
                _resumo([f"{l.insumo.nome} (lote {l.codigo or '—'}, {l.validade.strftime('%d/%m')})" for l in vencendo], "vencidos ou próximos do vencimento"),
                reverse("estoque-alertas"),
            )
            total["vencimento"] += len(avisados)
    return total


def _resumo(nomes, situacao):
    amostra = ", ".join(nomes[:MAX_ITENS_NO_ALERTA])
    extra = f" e mais {len(nomes) - MAX_ITENS_NO_ALERTA}" if len(nomes) > MAX_ITENS_NO_ALERTA else ""
    return f"{len(nomes)} item(ns) {situacao}: {amostra}{extra}."


# --------------------------------------------------------------------------
# Consistência
# --------------------------------------------------------------------------

def verificar_consistencia(clientes=None, corrigir=False):
    """Compara o saldo de cada lote com a soma do histórico. Devolve a lista de
    divergências [(lote, saldo gravado, saldo pelo histórico)]; com `corrigir`,
    ajusta o saldo gravado (o histórico manda). Idempotente."""
    lotes = Lote.objects.select_related("insumo")
    if clientes is not None:
        lotes = lotes.filter(cliente__in=clientes)
    divergencias = []
    for lote in lotes:
        pelo_historico = ZERO
        for tipo, quantidade in lote.movimentos.values_list("tipo", "quantidade"):
            pelo_historico += quantidade if tipo in MovimentoEstoque.TIPOS_DE_ENTRADA else -quantidade
        if pelo_historico != lote.saldo:
            divergencias.append((lote, lote.saldo, pelo_historico))
            if corrigir:
                Lote.objects.filter(pk=lote.pk).update(saldo=pelo_historico)
    return divergencias


def valor_em_estoque(cliente, ate=None):
    """Valor do estoque da unidade a CUSTO (centavos): para cada insumo ativo,
    saldo × preço pago ÷ quantidade comprada (o preço da própria unidade do insumo).
    Com `ate`, o saldo é reconstruído pelo histórico até aquela data (movimentos com
    data ≤ `ate`), mas o custo é o ATUAL do insumo — não existe histórico de preços."""
    from django.db.models import Case, When

    movimentos = MovimentoEstoque.objects.filter(cliente=cliente)
    if ate is not None:
        movimentos = movimentos.filter(data__date__lte=ate)
    sinal = Case(
        When(tipo__in=MovimentoEstoque.TIPOS_DE_ENTRADA, then=F("quantidade")), default=-F("quantidade"),
        output_field=DecimalField(max_digits=15, decimal_places=4),
    )
    total = Decimal(0)
    saldos = movimentos.values("insumo_id").annotate(saldo=Sum(sinal))
    insumos = {i.pk: i for i in Insumo.objects.filter(cliente=cliente, ativo=True, pk__in=[s["insumo_id"] for s in saldos])}
    for linha in saldos:
        insumo = insumos.get(linha["insumo_id"])
        if insumo and insumo.quantidade and linha["saldo"] and linha["saldo"] > 0:
            total += linha["saldo"] * Decimal(insumo.preco_pago) / insumo.quantidade
    return int(total.quantize(Decimal(1), rounding="ROUND_HALF_UP"))
