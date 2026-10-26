"""Pedidos e cozinha: criação a partir da venda, confirmação (baixa de estoque), fila de produção e cancelamento.

REGRA DE CONFIRMAÇÃO (por unidade, `ConfiguracaoPdv.confirmacao_pedido`):
- `lancado`: o pedido nasce CONFIRMADO (entra direto na fila);
- `pagamento`: confirma quando a venda é paga (se já está paga, na criação);
- `manual`: alguém confirma na tela/API.

ESTOQUE: a baixa dos insumos dos itens do pedido acontece na CONFIRMAÇÃO e nunca se repete (cada item da venda guarda
`baixa_estoque_em`; o recebimento da venda só baixa o que ainda não foi baixado). Ao entrar EM PREPARO os itens ficam
`preparado` (o insumo foi consumido: devolução/cancelamento não repõem estoque desses itens).

TRANSIÇÕES válidas e quem pode: qualquer perfil com `mover_pedido` move a fila; cancelar exige `cancelar_pedido` (checado nas
views/API). Cada mudança grava um PedidoEvento imutável e auditoria.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao
from apps.cliente.models import Cliente
from apps.pdv import services as pdv
from apps.pdv.models import Venda, VendaItem
from apps.pdvconfig.models import ConfiguracaoPdv
from web_project.regras import RegraNegocioError

from .models import Pedido, PedidoEvento, PedidoItem

AVANCO = {Pedido.NOVO: Pedido.CONFIRMADO, Pedido.CONFIRMADO: Pedido.EM_PREPARO, Pedido.EM_PREPARO: Pedido.PRONTO, Pedido.PRONTO: Pedido.ENTREGUE}
RETROCESSO = {Pedido.PRONTO: Pedido.EM_PREPARO, Pedido.EM_PREPARO: Pedido.CONFIRMADO}
CAMPO_DATA = {Pedido.CONFIRMADO: "confirmado_em", Pedido.EM_PREPARO: "preparo_em", Pedido.PRONTO: "pronto_em", Pedido.ENTREGUE: "entregue_em"}
ROTULOS = dict(Pedido.STATUS_CHOICES)


def _nome(usuario):
    return (usuario.get_full_name() or usuario.get_username())[:150] if usuario else ""


def _evento(pedido, de, para, usuario, nota=""):
    PedidoEvento.objects.create(pedido=pedido, de_status=de, para_status=para, usuario=usuario, usuario_nome=_nome(usuario), nota=nota[:255])


def _proximo_numero(cliente_id):
    Cliente.objects.select_for_update().get(pk=cliente_id)  # bloqueia a unidade: dois pedidos simultâneos não repetem o número
    return (Pedido.objects.filter(cliente_id=cliente_id).aggregate(m=Max("numero"))["m"] or 0) + 1


def _mudar(request, pedido, novo, nota=""):
    antes = pedido.status
    pedido.status = novo
    campos = ["status", "atualizado_em"]
    if novo in CAMPO_DATA:
        setattr(pedido, CAMPO_DATA[novo], timezone.now())
        campos.append(CAMPO_DATA[novo])
    pedido.save(update_fields=campos)
    _evento(pedido, antes, novo, request.user, nota)
    registrar_alteracoes(request, pedido, [{"campo": "status", "rotulo": "Status", "antes": ROTULOS[antes], "depois": ROTULOS[novo]}], modulo="pedido")


def _travar(pedido):
    return Pedido.objects.select_for_update().select_related("venda", "cliente").get(pk=pedido.pk)


# --------------------------------------------------------------------------
# Criação
# --------------------------------------------------------------------------

def criar_pedido(request, venda, itens=None, tipo=Pedido.BALCAO, observacao="", chave=None):
    """Cria um pedido (status NOVO, depois aplica a regra de confirmação) com os itens da venda que ainda NÃO foram para a
    cozinha (`itens` = queryset/lista de VendaItem; padrão: todos os pendentes). Devolve (pedido, criado). Com `chave`
    repetida devolve o pedido existente. Se não há item pendente, devolve (None, False)."""
    cliente = venda.cliente
    if chave:
        existente = Pedido.objects.filter(cliente=cliente, chave_idempotencia=chave).first()
        if existente:
            return existente, False
    try:
        with transaction.atomic():
            venda = Venda.objects.select_for_update().select_related("cliente").get(pk=venda.pk)
            if venda.status == Venda.CANCELADA:
                raise RegraNegocioError("A venda está cancelada.")
            pendentes = venda.itens.filter(pedido_item__isnull=True)
            if itens is not None:
                ids = [i.pk if hasattr(i, "pk") else int(i) for i in itens]
                pendentes = pendentes.filter(pk__in=ids)
            pendentes = list(pendentes)
            if not pendentes:
                return None, False
            if tipo not in dict(Pedido.TIPO_CHOICES):
                raise RegraNegocioError("Tipo de pedido inválido.")
            pedido = Pedido.objects.create(
                cliente=cliente, venda=venda, numero=_proximo_numero(cliente.pk), tipo=tipo, observacao=(observacao or "")[:255],
                chave_idempotencia=chave, criado_por=request.user,
            )
            PedidoItem.objects.bulk_create([PedidoItem(pedido=pedido, venda_item=i, descricao=i.descricao, quantidade=i.quantidade, observacao=i.observacao) for i in pendentes])
            _evento(pedido, "", Pedido.NOVO, request.user, "Pedido criado")
            registrar_criacao(request, pedido, modulo="pedido")
            regra = ConfiguracaoPdv.da_unidade(cliente).confirmacao_pedido
            if regra == ConfiguracaoPdv.CONFIRMA_AO_LANCAR or (regra == ConfiguracaoPdv.CONFIRMA_NO_PAGAMENTO and venda.status == Venda.PAGA):
                _confirmar(request, pedido, "Confirmado automaticamente pela regra da unidade")
    except IntegrityError:
        if chave:
            existente = Pedido.objects.filter(cliente=cliente, chave_idempotencia=chave).first()
            if existente:
                return existente, False
        raise
    return pedido, True


def criar_pedido_avulso(request, caixa, itens, tipo=Pedido.BALCAO, mesa=None, consumidor=None, observacao="", chave=None):
    """Pedido vindo do app/integração: abre uma venda ABERTA (origem PEDIDO, ou a comanda da mesa) com os itens, que é paga depois
    pelo PDV, e cria o pedido dela. Os preços vêm do servidor. Devolve (pedido, criado); `chave` repetida devolve o mesmo pedido."""
    cliente = caixa.cliente
    if chave:
        existente = Pedido.objects.filter(cliente=cliente, chave_idempotencia=chave).first()
        if existente:
            return existente, False
    if tipo not in dict(Pedido.TIPO_CHOICES):
        raise RegraNegocioError("Tipo de pedido inválido.")
    with transaction.atomic():
        caixa = pdv._exigir_caixa_do_usuario(request, caixa)
        linhas = pdv._linhas(cliente, itens)
        if mesa is not None:
            venda = Venda.objects.filter(cliente=cliente, mesa=mesa, status=Venda.ABERTA).first() or pdv.abrir_comanda(request, caixa, mesa, consumidor)
        else:
            if consumidor is not None and consumidor.cliente_id != cliente.pk:
                raise RegraNegocioError("Consumidor de outra unidade.")
            venda = Venda.objects.create(
                cliente=cliente, numero=pdv._proximo_numero(cliente.pk), caixa=caixa, consumidor=consumidor, origem=Venda.ORIGEM_PEDIDO,
                observacao=(observacao or "")[:255], criado_por=request.user,
            )
            registrar_criacao(request, venda, modulo="pdv")
        novos = [
            VendaItem.objects.create(
                venda=venda, produto_id=l["produto_id"], ficha=l["precificacao"].ficha, descricao=l["precificacao"].produto.nome[:150],
                quantidade=l["quantidade"], preco_unitario=l["preco"], total=l["total"], observacao=l["observacao"],
            )
            for l in linhas
        ]
        pdv._recalcular_subtotal(venda)
        return criar_pedido(request, venda, itens=novos, tipo=tipo, observacao=observacao, chave=chave)



def _confirmar(request, pedido, nota=""):
    """NOVO -> CONFIRMADO + baixa de estoque dos itens do pedido (pedido já bloqueado, dentro de transação)."""
    if pedido.venda.status == Venda.CANCELADA:
        raise RegraNegocioError("A venda está cancelada.")
    itens_venda = [i.venda_item for i in pedido.itens.select_related("venda_item") if i.venda_item_id]
    pdv.baixar_estoque(request.user, pedido.venda, itens_venda)  # saldo insuficiente derruba a confirmação inteira
    _mudar(request, pedido, Pedido.CONFIRMADO, nota)


def confirmar(request, pedido):
    with transaction.atomic():
        pedido = _travar(pedido)
        if pedido.status != Pedido.NOVO:
            raise RegraNegocioError("Só um pedido novo pode ser confirmado.")
        _confirmar(request, pedido)
    return pedido


def confirmar_apos_pagamento(request, venda):
    """Chamado pelo PDV quando a venda é paga: confirma os pedidos NOVOS dela se a regra da unidade é `pagamento`."""
    if ConfiguracaoPdv.da_unidade(venda.cliente).confirmacao_pedido != ConfiguracaoPdv.CONFIRMA_NO_PAGAMENTO:
        return []
    confirmados = []
    for pedido in Pedido.objects.filter(venda=venda, status=Pedido.NOVO):
        confirmados.append(confirmar(request, pedido))
    return confirmados


# --------------------------------------------------------------------------
# Fila de produção
# --------------------------------------------------------------------------

def avancar(request, pedido):
    """Próximo estado: novo→confirmado→em preparo→pronto→entregue. Em preparo marca os itens como preparados."""
    with transaction.atomic():
        pedido = _travar(pedido)
        novo = AVANCO.get(pedido.status)
        if novo is None:
            raise RegraNegocioError(f"Um pedido {ROTULOS[pedido.status].lower()} não pode avançar.")
        if novo == Pedido.CONFIRMADO:
            _confirmar(request, pedido)
        else:
            if novo == Pedido.EM_PREPARO:
                VendaItem.objects.filter(pedido_item__pedido=pedido).update(preparado=True)  # insumo consumido: sem reposição depois
            _mudar(request, pedido, novo)
    return pedido


def voltar(request, pedido):
    """Corrige um passo: pronto→em preparo, em preparo→confirmado (desfaz a marca de preparado)."""
    with transaction.atomic():
        pedido = _travar(pedido)
        anterior = RETROCESSO.get(pedido.status)
        if anterior is None:
            raise RegraNegocioError(f"Um pedido {ROTULOS[pedido.status].lower()} não pode voltar.")
        if pedido.status == Pedido.EM_PREPARO:
            VendaItem.objects.filter(pedido_item__pedido=pedido).update(preparado=False)
        _mudar(request, pedido, anterior, "Estado corrigido (voltou um passo)")
    return pedido


def cancelar(request, pedido, motivo):
    """Cancela um pedido ativo de venda AINDA NÃO PAGA: repõe o estoque dos itens não preparados e retira os itens da venda
    (o total da comanda se ajusta). Venda paga: use cancelar a venda/devolver itens no PDV."""
    motivo = " ".join((motivo or "").split())
    if not motivo:
        raise RegraNegocioError("Informe o motivo do cancelamento.")
    with transaction.atomic():
        pedido = _travar(pedido)
        if not pedido.ativo:
            raise RegraNegocioError(f"Um pedido {ROTULOS[pedido.status].lower()} não pode ser cancelado.")
        venda = Venda.objects.select_for_update().get(pk=pedido.venda_id)
        if venda.status == Venda.PAGA:
            raise RegraNegocioError("A venda já foi paga: cancele a venda ou registre a devolução no PDV.")
        itens = [i.venda_item for i in pedido.itens.select_related("venda_item") if i.venda_item_id]
        for item in itens:
            if item.baixa_estoque_em and not item.preparado:
                pdv._repor_estoque(request.user, item, 1, venda)
        _mudar(request, pedido, Pedido.CANCELADO, motivo)
        pedido.motivo_cancelamento, pedido.cancelado_em = motivo[:255], timezone.now()
        pedido.save(update_fields=["motivo_cancelamento", "cancelado_em"])
        for item in itens:
            item.delete()
        if venda.status == Venda.ABERTA:
            pdv._recalcular_subtotal(venda)
    return pedido


def cancelar_por_venda(request, venda, motivo="Venda cancelada"):
    """Chamado pelo PDV ao cancelar a venda: encerra os pedidos ativos dela (o PDV já repõe o estoque dos itens)."""
    with transaction.atomic():
        for pedido in Pedido.objects.select_for_update().filter(venda=venda, status__in=Pedido.ATIVOS):
            pedido.motivo_cancelamento, pedido.cancelado_em = motivo[:255], timezone.now()
            antes = pedido.status
            pedido.status = Pedido.CANCELADO
            pedido.save(update_fields=["status", "motivo_cancelamento", "cancelado_em", "atualizado_em"])
            _evento(pedido, antes, Pedido.CANCELADO, request.user, motivo)
            registrar_alteracoes(request, pedido, [{"campo": "status", "rotulo": "Status", "antes": ROTULOS[antes], "depois": "Cancelado"}], modulo="pedido")


# --------------------------------------------------------------------------
# Consulta
# --------------------------------------------------------------------------

def fila(clientes):
    """Pedidos ATIVOS das unidades (novo, confirmado, em preparo, pronto), do mais antigo para o mais novo. Os NOVOS (aguardando confirmação
    manual ou o pagamento) entram na fila: antes ficavam invisíveis na cozinha e só apareciam em "Todos os pedidos"."""
    return (
        Pedido.objects.filter(cliente__in=clientes, status__in=Pedido.ATIVOS)
        .select_related("venda", "venda__mesa", "venda__consumidor").prefetch_related("itens").order_by("confirmado_em", "criado_em", "pk")
    )


def versao_da_fila(clientes):
    """Marca barata do estado dos pedidos das unidades (quantidade + última alteração): muda sempre que um pedido nasce ou muda de estado.
    O stream de eventos da tela compara esta marca; ela não carrega dado de pedido (a tela busca a fila pela rota autenticada)."""
    dados = Pedido.objects.filter(cliente__in=clientes).aggregate(n=Count("pk"), m=Max("atualizado_em"))
    return f"{dados['n']}:{dados['m'].isoformat() if dados['m'] else ''}"
