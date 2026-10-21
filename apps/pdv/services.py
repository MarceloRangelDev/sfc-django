"""Regras do PDV: caixa, vendas (balcão e comanda), pagamentos, baixa de estoque, conta a receber, cancelamento e devolução.

PRINCÍPIOS
- Tudo que muda dinheiro/estoque roda numa transação; falhou em qualquer passo, nada é gravado.
- Preço e ficha vêm SEMPRE do servidor (Precificação marcada `usar_no_pdv`); o cliente (tela/app) só diz produto e quantidade.
- Cada item e pagamento guarda snapshot (nome, preço, taxa); alterar cadastros depois não muda vendas antigas.
- Estoque: a baixa acontece UMA vez por venda (`baixa_estoque_em`), pelos insumos da ficha técnica; cada baixa é registrada em
  `VendaItemInsumo` para a devolução repor exatamente o que saiu e só se o item NÃO foi preparado.
- Financeiro: a venda paga gera uma conta a receber (categoria "Vendas PDV") já baixada, uma baixa por pagamento.
  Devolução de itens gera uma conta a pagar já baixada (categoria "Devoluções de vendas"). Cancelar estorna as baixas.
- Idempotência: `chave` (por tentativa) devolve a MESMA venda em reenvios; não duplica nada.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.auditoria.models import ACAO_CRIAR
from apps.auditoria.utils import _descricao_com_alteracoes, registrar, registrar_alteracoes, registrar_criacao, registrar_edicao, tirar_foto
from apps.cliente.models import Cliente
from apps.estoque.services import registrar_entrada, registrar_saida
from apps.financeiro.models import TIPO_DESPESA, TIPO_RECEITA, BaixaBase, CategoriaFinanceira, ContaPagar, ContaReceber
from apps.financeiro.services import estornar_baixa, registrar_baixa
from apps.pdvconfig.models import ConfiguracaoPdv, FormaPagamento
from web_project.globais import disponiveis_para
from apps.precificacao.models import Precificacao
from web_project.regras import RegraNegocioError
from web_project.regras.dinheiro import DinheiroService

from .models import LIMITE_CENTAVOS, Caixa, Devolucao, MovimentoCaixa, Venda, VendaItem, VendaItemInsumo, VendaPagamento

MAX_LINHAS = 100
MAX_PAGAMENTOS = 10
MAX_QUANTIDADE = Decimal("9999")
TRES_CASAS = Decimal("0.001")
QUATRO_CASAS = Decimal("0.0001")
CATEGORIA_VENDAS, CATEGORIA_DEVOLUCOES = "Vendas PDV", "Devoluções de vendas"


def _reais(centavos):
    return DinheiroService.formatar(centavos)


def _arredondar(valor):
    return int(Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _nome_usuario(usuario):
    return (usuario.get_full_name() or usuario.get_username())[:150]


# --------------------------------------------------------------------------
# Catálogo de venda
# --------------------------------------------------------------------------

LIMITE_CATALOGO = 300  # itens exibidos de uma vez na tela do PDV (antes 80, cortando o catálogo em silêncio); a busca alcança todos


def produtos_do_pdv(cliente, termo="", limite=LIMITE_CATALOGO):
    """Precificações que servem de preço/ficha do PDV (uma por produto), dentro da unidade. Só produtos e fichas ativos.
    `limite=None` devolve o queryset inteiro (a API pagina). A busca vale para nome, categoria e código de barras."""
    consulta = Precificacao.objects.filter(
        cliente=cliente, usar_no_pdv=True, ativo=True, produto__ativo=True, ficha__ativo=True, preco_venda__gt=0,
    ).select_related("produto", "ficha", "produto__categoria")
    termo = (termo or "").strip()
    if termo:
        # nome contém o texto OU o código de barras/QR lido é exatamente o digitado
        consulta = consulta.filter(Q(produto__nome__icontains=termo) | Q(produto__categoria__nome__icontains=termo) | Q(produto__codigo_barras=termo))
    consulta = consulta.order_by("produto__nome", "pk")
    return consulta if limite is None else consulta[:limite]


def produto_por_codigo(cliente, codigo):
    """Produto ativo da unidade com o código de barras/QR lido; None se não houver (ou não tiver preço no PDV)."""
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    return next(iter(p for p in produtos_do_pdv(cliente, codigo, limite=20) if p.produto.codigo_barras == codigo), None)


def _precificacao_do_produto(cliente, produto_id):
    precificacao = (
        Precificacao.objects.filter(cliente=cliente, produto_id=produto_id, usar_no_pdv=True, ativo=True, produto__ativo=True, ficha__ativo=True, preco_venda__gt=0)
        .select_related("produto", "ficha").first()
    )
    if precificacao is None:
        raise RegraNegocioError("Produto indisponível no PDV (sem precificação marcada para o PDV, inativo ou sem preço).")
    return precificacao


def _quantidade(valor):
    try:
        quantidade = Decimal(str(valor)).quantize(TRES_CASAS)
    except Exception:
        raise RegraNegocioError("Quantidade inválida.") from None
    if not Decimal("0.001") <= quantidade <= MAX_QUANTIDADE:
        raise RegraNegocioError("A quantidade deve estar entre 0,001 e 9.999.")
    return quantidade


def _linhas(cliente, itens):
    """Itens recebidos ({produto, quantidade, observacao?}) -> linhas com preço do servidor. Mesmo produto soma."""
    if not itens:
        raise RegraNegocioError("Inclua ao menos um item.")
    if len(itens) > MAX_LINHAS:
        raise RegraNegocioError(f"No máximo {MAX_LINHAS} linhas por venda.")
    acumulado = {}
    for entrada in itens:
        try:
            produto_id = int(entrada["produto"])
        except (KeyError, TypeError, ValueError):
            raise RegraNegocioError("Item sem produto válido.") from None
        quantidade = _quantidade(entrada.get("quantidade", 1))
        linha = acumulado.setdefault(produto_id, {"produto_id": produto_id, "quantidade": Decimal("0"), "observacao": ""})
        linha["quantidade"] += quantidade
        linha["observacao"] = (entrada.get("observacao") or linha["observacao"])[:120]
    linhas = []
    for linha in acumulado.values():
        if linha["quantidade"] > MAX_QUANTIDADE:
            raise RegraNegocioError("A quantidade deve estar entre 0,001 e 9.999.")
        precificacao = _precificacao_do_produto(cliente, linha["produto_id"])
        total = _arredondar(Decimal(precificacao.preco_venda) * linha["quantidade"])
        linhas.append({**linha, "precificacao": precificacao, "preco": precificacao.preco_venda, "total": total})
    return linhas


def _proximo_numero(cliente_id):
    """Número sequencial por unidade. A linha da unidade é bloqueada para duas vendas simultâneas não repetirem o número."""
    Cliente.objects.select_for_update().get(pk=cliente_id)
    return (Venda.objects.filter(cliente_id=cliente_id).aggregate(m=Max("numero"))["m"] or 0) + 1


def _texto_itens(venda):
    return "; ".join(f"{i.descricao} × {i.quantidade.normalize():f} = {_reais(i.total)}" for i in venda.itens.all()) or None


def _texto_pagamentos(venda):
    return "; ".join(f"{p.forma_nome} {_reais(p.valor)}" + (f" (taxa {_reais(p.taxa_valor)})" if p.taxa_valor else "") for p in venda.pagamentos.all()) or None


# --------------------------------------------------------------------------
# Caixa
# --------------------------------------------------------------------------

def caixa_aberto_do(usuario):
    return Caixa.objects.filter(operador=usuario, status=Caixa.ABERTO).select_related("cliente").first()


def abrir_caixa(request, valor_abertura=0):
    usuario = request.user
    cliente = usuario.profile.cliente
    if cliente is None:
        raise RegraNegocioError("Seu usuário não está vinculado a uma empresa.")
    if not 0 <= valor_abertura <= LIMITE_CENTAVOS:
        raise RegraNegocioError("Troco inicial inválido.")
    try:
        with transaction.atomic():
            caixa = Caixa.objects.create(cliente=cliente, operador=usuario, valor_abertura=valor_abertura)
            registrar_criacao(request, caixa)
    except IntegrityError:
        raise RegraNegocioError("Você já tem um caixa aberto.") from None
    return caixa


def registrar_movimento(request, caixa, tipo, valor, motivo):
    """Sangria (retirada) ou suprimento (entrada) de dinheiro no caixa ABERTO."""
    motivo = " ".join((motivo or "").split())
    if not motivo:
        raise RegraNegocioError("Informe o motivo.")
    if not 0 < valor <= LIMITE_CENTAVOS:
        raise RegraNegocioError("Informe um valor maior que zero.")
    with transaction.atomic():
        caixa = Caixa.objects.select_for_update().get(pk=caixa.pk)
        if not caixa.aberto:
            raise RegraNegocioError("O caixa está fechado.")
        if tipo == MovimentoCaixa.SANGRIA and valor > dinheiro_esperado(caixa)["esperado"]:
            raise RegraNegocioError("A sangria é maior que o dinheiro disponível no caixa.")
        movimento = MovimentoCaixa.objects.create(caixa=caixa, tipo=tipo, valor=valor, motivo=motivo[:255], usuario=request.user)
        registrar_criacao(request, movimento, modulo="pdv")
    return movimento


def dinheiro_esperado(caixa):
    """Quanto deveria haver em DINHEIRO no caixa agora: abertura + vendas em dinheiro + suprimentos − sangrias − devoluções em dinheiro."""
    dinheiro = VendaPagamento.objects.filter(venda__caixa=caixa, venda__status=Venda.PAGA, forma_tipo=FormaPagamento.DINHEIRO).aggregate(t=Sum("valor"))["t"] or 0
    movimentos = {m["tipo"]: m["t"] for m in caixa.movimentos.values("tipo").annotate(t=Sum("valor"))}
    devolvido = Devolucao.objects.filter(caixa=caixa, forma_estorno__tipo=FormaPagamento.DINHEIRO).aggregate(t=Sum("valor"))["t"] or 0
    suprimentos, sangrias = movimentos.get(MovimentoCaixa.SUPRIMENTO, 0), movimentos.get(MovimentoCaixa.SANGRIA, 0)
    return {
        "abertura": caixa.valor_abertura, "vendas_dinheiro": dinheiro, "suprimentos": suprimentos, "sangrias": sangrias, "devolucoes": devolvido,
        "esperado": caixa.valor_abertura + dinheiro + suprimentos - sangrias - devolvido,
    }


def resumo_do_caixa(caixa):
    """Totais para a tela de fechamento/relatório: por forma de pagamento (bruto, taxas, líquido) e dinheiro esperado."""
    por_forma = list(
        VendaPagamento.objects.filter(venda__caixa=caixa, venda__status=Venda.PAGA).values("forma_nome").annotate(bruto=Sum("valor"), taxas=Sum("taxa_valor")).order_by("forma_nome")
    )
    for linha in por_forma:
        linha["liquido"] = linha["bruto"] - linha["taxas"]
    vendas = caixa.vendas.filter(status=Venda.PAGA)
    return {
        "dinheiro": dinheiro_esperado(caixa), "por_forma": por_forma, "qtd_vendas": vendas.count(),
        "total_vendas": vendas.aggregate(t=Sum("total"))["t"] or 0, "canceladas": caixa.vendas.filter(status=Venda.CANCELADA).count(),
        "comandas_abertas": caixa.vendas.filter(status=Venda.ABERTA).count(),
    }


def fechar_caixa(request, caixa, valor_contado, observacao=""):
    """Fecha o caixa do operador informando o dinheiro contado. Recusa se houver comanda aberta."""
    if not 0 <= valor_contado <= LIMITE_CENTAVOS:
        raise RegraNegocioError("Valor contado inválido.")
    with transaction.atomic():
        caixa = Caixa.objects.select_for_update().get(pk=caixa.pk)
        if not caixa.aberto:
            raise RegraNegocioError("O caixa já está fechado.")
        if caixa.vendas.filter(status=Venda.ABERTA).exists():
            raise RegraNegocioError("Há comanda(s) aberta(s) neste caixa. Receba ou cancele antes de fechar.")
        foto = tirar_foto(caixa)
        esperado = dinheiro_esperado(caixa)["esperado"]
        caixa.status, caixa.fechado_em = Caixa.FECHADO, timezone.now()
        caixa.valor_esperado, caixa.valor_contado, caixa.diferenca = esperado, valor_contado, valor_contado - esperado
        caixa.observacao_fechamento = (observacao or "")[:255]
        caixa.save()
        registrar_edicao(request, caixa, foto)
    return caixa


def conferir_caixa(request, caixa, observacao=""):
    """Marca o fechamento como conferido por quem tem a permissão (revisão da diferença)."""
    with transaction.atomic():
        caixa = Caixa.objects.select_for_update().get(pk=caixa.pk)
        if caixa.aberto:
            raise RegraNegocioError("Só se confere um caixa fechado.")
        if caixa.conferido_em:
            raise RegraNegocioError("Este caixa já foi conferido.")
        caixa.conferido_por, caixa.conferido_em, caixa.observacao_conferencia = request.user, timezone.now(), (observacao or "")[:255]
        caixa.save(update_fields=["conferido_por", "conferido_em", "observacao_conferencia"])
        registrar_alteracoes(request, caixa, [{"campo": "conferido", "rotulo": "Conferência", "antes": "Pendente", "depois": f"Conferido por {_nome_usuario(request.user)}"}])
    return caixa


# --------------------------------------------------------------------------
# Estoque
# --------------------------------------------------------------------------

def baixar_estoque(usuario, venda, itens=None):
    """Dá baixa nos insumos da ficha técnica de cada item AINDA NÃO baixado (cada item é baixado uma só vez; `itens` limita a
    quais). Respeita `ConfiguracaoPdv.controla_estoque`. Saldo insuficiente derruba a transação inteira, com a mensagem do estoque.
    `venda.baixa_estoque_em` marca quando TODOS os itens já foram baixados. Devolve se baixou algum item."""
    pendentes = venda.itens.filter(baixa_estoque_em__isnull=True)
    if itens is not None:
        pendentes = pendentes.filter(pk__in=[i.pk for i in itens])
    configuracao = ConfiguracaoPdv.da_unidade(venda.cliente)
    pendentes = list(pendentes.select_related("ficha").prefetch_related("ficha__itens__insumo", "ficha__itens__medida"))
    if not pendentes:
        return False
    agora = timezone.now()
    if configuracao.controla_estoque:
        for item in pendentes:
            for ficha_item in item.ficha.itens.all():
                quantidade = (ficha_item.quantidade * item.quantidade).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP)
                if quantidade <= 0:
                    continue
                try:
                    registrar_saida(usuario, ficha_item.insumo, quantidade, ficha_item.medida, f"Venda PDV #{venda.numero}")
                except RegraNegocioError as erro:
                    raise RegraNegocioError(f"{item.descricao}: {erro}") from None
                VendaItemInsumo.objects.create(item=item, insumo=ficha_item.insumo, medida=ficha_item.medida, quantidade=quantidade)
    VendaItem.objects.filter(pk__in=[i.pk for i in pendentes]).update(baixa_estoque_em=agora)
    if not venda.itens.filter(baixa_estoque_em__isnull=True).exists():
        venda.baixa_estoque_em = agora
        venda.save(update_fields=["baixa_estoque_em", "atualizado_em"])
    return True


def _repor_estoque(usuario, item, fracao, venda):
    """Repõe `fracao` (0..1 da quantidade do item) do que foi baixado, sem passar do baixado. Devolve se repôs algo."""
    reposto = False
    for baixa in item.insumos_baixados.select_related("insumo", "medida"):
        quantidade = min((baixa.quantidade * fracao).quantize(QUATRO_CASAS, rounding=ROUND_HALF_UP), baixa.quantidade - baixa.quantidade_reposta)
        if quantidade <= 0:
            continue
        registrar_entrada(usuario, baixa.insumo, quantidade, baixa.medida, observacao=f"Devolução/cancelamento da venda PDV #{venda.numero}")
        VendaItemInsumo.objects.filter(pk=baixa.pk).update(quantidade_reposta=baixa.quantidade_reposta + quantidade)
        reposto = True
    return reposto


# --------------------------------------------------------------------------
# Financeiro
# --------------------------------------------------------------------------

def _categoria(request, cliente, nome, tipo):
    categoria = CategoriaFinanceira.objects.filter(cliente=cliente, nome=nome, tipo=tipo).first()
    if categoria is None:
        categoria = CategoriaFinanceira.objects.create(cliente=cliente, nome=nome, tipo=tipo)
        registrar_criacao(request, categoria, modulo="financeiro")
    elif not categoria.ativo:
        raise RegraNegocioError(f'A categoria financeira "{nome}" está inativa; reative-a para continuar.')
    return categoria


def _receber_venda(request, venda):
    """Conta a receber da venda, já baixada (uma baixa por pagamento)."""
    hoje = timezone.localdate()
    conta = ContaReceber.objects.create(
        cliente=venda.cliente, descricao=f"Venda PDV #{venda.numero}", contraparte=(venda.consumidor.nome if venda.consumidor_id else "Consumidor final")[:150],
        categoria=_categoria(request, venda.cliente, CATEGORIA_VENDAS, TIPO_RECEITA), valor=venda.total, emissao=hoje, vencimento=hoje,
        observacao=f"Gerada pelo PDV (caixa {venda.caixa_id})"[:255],
    )
    registrar_criacao(request, conta, modulo="financeiro")
    for pagamento in venda.pagamentos.all():
        registrar_baixa(request.user, conta, pagamento.valor, hoje, f"{pagamento.forma_nome}"[:255])
    venda.conta_receber = conta
    venda.save(update_fields=["conta_receber", "atualizado_em"])


# --------------------------------------------------------------------------
# Vendas
# --------------------------------------------------------------------------

def _exigir_caixa_do_usuario(request, caixa):
    caixa = Caixa.objects.select_for_update().get(pk=caixa.pk)
    if not caixa.aberto:
        raise RegraNegocioError("O caixa está fechado.")
    if caixa.operador_id != request.user.pk:
        raise RegraNegocioError("Este caixa pertence a outro operador.")
    return caixa


def _validar_pagamentos(cliente, pagamentos, total):
    """Pagamentos recebidos -> linhas validadas com taxa. Soma tem de bater exatamente com o total; troco só em dinheiro."""
    if not pagamentos or len(pagamentos) > MAX_PAGAMENTOS:
        raise RegraNegocioError(f"Informe de 1 a {MAX_PAGAMENTOS} pagamentos.")
    formas = {f.pk: f for f in disponiveis_para(FormaPagamento, cliente, ativo=True)}  # inclui as globais liberadas ao segmento
    linhas, soma, troco = [], 0, 0
    for p in pagamentos:
        try:
            forma, valor = formas[int(p["forma"])], int(p["valor"])
        except (KeyError, TypeError, ValueError):
            raise RegraNegocioError("Forma de pagamento inválida ou inativa.") from None
        if isinstance(p["valor"], bool) or not 0 < valor <= LIMITE_CENTAVOS:
            raise RegraNegocioError("Cada pagamento deve ter valor maior que zero.")
        recebido = p.get("valor_recebido")
        if recebido in (None, ""):
            recebido = None
        else:
            try:
                recebido = int(recebido)
            except (TypeError, ValueError):
                raise RegraNegocioError("Valor recebido inválido.") from None
            if forma.tipo != FormaPagamento.DINHEIRO:
                raise RegraNegocioError("Só pagamentos em dinheiro têm valor recebido (troco).")
            if recebido < valor:
                raise RegraNegocioError("O valor recebido em dinheiro é menor que o valor pago.")
            troco += recebido - valor
        soma += valor
        linhas.append({"forma": forma, "valor": valor, "recebido": recebido, "taxa": forma.calcular_taxa(valor), "pagador": (p.get("pagador") or "")[:60]})
    if soma != total:
        raise RegraNegocioError(f"A soma dos pagamentos ({_reais(soma)}) deve ser igual ao total da venda ({_reais(total)}).")
    return linhas, troco


def _aplicar_desconto(subtotal, desconto, pode_desconto):
    desconto = int(desconto or 0)
    if desconto < 0:
        raise RegraNegocioError("Desconto inválido.")
    if desconto and not pode_desconto:
        raise RegraNegocioError("Você não tem permissão para conceder desconto.")
    if desconto > subtotal:
        raise RegraNegocioError("O desconto não pode ser maior que o subtotal.")
    if subtotal - desconto <= 0:
        raise RegraNegocioError("O total da venda deve ser maior que zero.")
    return desconto


def _finalizar(request, venda, pagamentos, desconto, pode_desconto, chave=None):
    """Passo comum de balcão e comanda: totais, pagamentos, baixa de estoque, conta a receber e auditoria (venda já bloqueada)."""
    subtotal = venda.itens.aggregate(t=Sum("total"))["t"] or 0
    desconto = _aplicar_desconto(subtotal, desconto, pode_desconto)
    total = subtotal - desconto
    linhas, troco = _validar_pagamentos(venda.cliente, pagamentos, total)
    for linha in linhas:
        forma = linha["forma"]
        VendaPagamento.objects.create(
            venda=venda, forma=forma, forma_nome=forma.nome, forma_tipo=forma.tipo, valor=linha["valor"], valor_recebido=linha["recebido"],
            taxa_valor=linha["taxa"], taxa_descricao=forma.descricao_da_taxa[:80], pagador=linha["pagador"],
        )
    venda.subtotal, venda.desconto, venda.total, venda.troco = subtotal, desconto, total, troco
    venda.status, venda.finalizada_em = Venda.PAGA, timezone.now()
    if chave:
        venda.chave_idempotencia = chave
    venda.save()
    baixar_estoque(request.user, venda)
    _receber_venda(request, venda)
    if venda.pedidos.exists():
        from apps.pedido import services as pedidos  # import tardio: o app de pedidos depende do PDV

        pedidos.confirmar_apos_pagamento(request, venda)
    return venda


def _auditar_venda_criada(request, venda, rotulo="Criado"):
    extras = [
        {"campo": "itens", "rotulo": "Itens", "antes": None, "depois": _texto_itens(venda)},
        {"campo": "pagamentos", "rotulo": "Pagamentos", "antes": None, "depois": _texto_pagamentos(venda)},
    ]
    extras += [{"campo": c, "rotulo": r, "antes": None, "depois": _reais(getattr(venda, c))} for c, r in (("subtotal", "Subtotal"), ("desconto", "Desconto"), ("total", "Total"))]
    registrar(
        request, acao=ACAO_CRIAR, modulo="pdv", tabela=venda._meta.db_table, registro_id=venda.pk, registro_nome=str(venda),
        descricao=_descricao_com_alteracoes(f"{rotulo} venda ID {venda.pk} - {venda}.", extras), alteracoes=extras, silencioso=False,
    )


def registrar_venda(request, caixa, itens, pagamentos, consumidor=None, desconto=0, observacao="", chave=None, pode_desconto=False, cozinha=None):
    """Venda de balcão: itens + pagamentos de uma vez. Devolve (venda, criada). Com `chave` repetida devolve a venda
    já registrada (criada=False) sem duplicar nada. `cozinha` (balcao/retirada/entrega) também envia os itens à cozinha."""
    cliente = caixa.cliente
    if chave:
        existente = Venda.objects.filter(cliente=cliente, chave_idempotencia=chave).first()
        if existente:
            return existente, False
    try:
        with transaction.atomic():
            caixa = _exigir_caixa_do_usuario(request, caixa)
            if consumidor is not None and consumidor.cliente_id != cliente.pk:
                raise RegraNegocioError("Consumidor de outra unidade.")
            linhas = _linhas(cliente, itens)
            venda = Venda.objects.create(
                cliente=cliente, numero=_proximo_numero(cliente.pk), caixa=caixa, consumidor=consumidor, origem=Venda.ORIGEM_BALCAO,
                observacao=(observacao or "")[:255], criado_por=request.user,
            )
            VendaItem.objects.bulk_create([
                VendaItem(venda=venda, produto_id=l["produto_id"], ficha=l["precificacao"].ficha, descricao=l["precificacao"].produto.nome[:150],
                          quantidade=l["quantidade"], preco_unitario=l["preco"], total=l["total"], observacao=l["observacao"])
                for l in linhas
            ])
            _finalizar(request, venda, pagamentos, desconto, pode_desconto, chave)
            _auditar_venda_criada(request, venda)
            if cozinha:
                from apps.pedido import services as pedidos  # import tardio: o app de pedidos depende do PDV

                pedidos.criar_pedido(request, venda, tipo=cozinha)
    except IntegrityError:
        if chave:  # corrida: outra requisição com a mesma chave gravou primeiro
            existente = Venda.objects.filter(cliente=cliente, chave_idempotencia=chave).first()
            if existente:
                return existente, False
        raise
    return venda, True


# --- comandas (mesa) ---------------------------------------------------------------------

def abrir_comanda(request, caixa, mesa, consumidor=None, observacao=""):
    cliente = caixa.cliente
    if mesa.cliente_id != cliente.pk or not mesa.ativo:
        raise RegraNegocioError("Mesa inválida ou inativa.")
    try:
        with transaction.atomic():
            caixa = _exigir_caixa_do_usuario(request, caixa)
            if consumidor is not None and consumidor.cliente_id != cliente.pk:
                raise RegraNegocioError("Consumidor de outra unidade.")
            venda = Venda.objects.create(
                cliente=cliente, numero=_proximo_numero(cliente.pk), caixa=caixa, mesa=mesa, consumidor=consumidor, origem=Venda.ORIGEM_MESA,
                observacao=(observacao or "")[:255], criado_por=request.user,
            )
            registrar_criacao(request, venda, modulo="pdv")
    except IntegrityError:
        raise RegraNegocioError("Esta mesa já tem uma comanda aberta.") from None
    return venda


def _comanda_editavel(venda):
    venda = Venda.objects.select_for_update().get(pk=venda.pk)
    if venda.status != Venda.ABERTA:
        raise RegraNegocioError("A comanda não está aberta.")
    return venda


def adicionar_item(request, venda, produto_id, quantidade, observacao=""):
    """Adiciona (ou soma a) um item da comanda aberta. Preço e ficha vêm do servidor."""
    with transaction.atomic():
        venda = _comanda_editavel(venda)
        _exigir_caixa_do_usuario(request, venda.caixa)
        antes = _texto_itens(venda)
        linha = _linhas(venda.cliente, [{"produto": produto_id, "quantidade": quantidade, "observacao": observacao}])[0]
        existente = venda.itens.filter(produto_id=linha["produto_id"], baixa_estoque_em__isnull=True, pedido_item__isnull=True).first()
        if existente and existente.quantidade_devolvida == 0 and existente.baixa_estoque_em is None and not hasattr(existente, 'pedido_item'):
            nova = existente.quantidade + linha["quantidade"]
            if nova > MAX_QUANTIDADE:
                raise RegraNegocioError("A quantidade deve estar entre 0,001 e 9.999.")
            existente.quantidade, existente.total = nova, _arredondar(Decimal(existente.preco_unitario) * nova)
            existente.save(update_fields=["quantidade", "total"])
        else:
            VendaItem.objects.create(
                venda=venda, produto_id=linha["produto_id"], ficha=linha["precificacao"].ficha, descricao=linha["precificacao"].produto.nome[:150],
                quantidade=linha["quantidade"], preco_unitario=linha["preco"], total=linha["total"], observacao=linha["observacao"],
            )
        _recalcular_subtotal(venda)
        registrar_alteracoes(request, venda, [{"campo": "itens", "rotulo": "Itens", "antes": antes, "depois": _texto_itens(venda)}], modulo="pdv")
    return venda


def remover_item(request, venda, item_id):
    with transaction.atomic():
        venda = _comanda_editavel(venda)
        _exigir_caixa_do_usuario(request, venda.caixa)
        antes = _texto_itens(venda)
        item = venda.itens.filter(pk=item_id).first()
        if item is None:
            raise RegraNegocioError("Item não encontrado nesta comanda.")
        if hasattr(item, "pedido_item"):
            raise RegraNegocioError("O item já foi enviado à cozinha: cancele o pedido para retirá-lo.")
        item.delete()
        _recalcular_subtotal(venda)
        registrar_alteracoes(request, venda, [{"campo": "itens", "rotulo": "Itens", "antes": antes, "depois": _texto_itens(venda)}], modulo="pdv")
    return venda


def _recalcular_subtotal(venda):
    venda.subtotal = venda.itens.aggregate(t=Sum("total"))["t"] or 0
    venda.total = venda.subtotal - venda.desconto
    venda.save(update_fields=["subtotal", "total", "atualizado_em"])


def pagar_comanda(request, venda, pagamentos, desconto=0, pode_desconto=False, chave=None):
    """Recebe a comanda (conta dividida = vários pagamentos). Repetir com a MESMA chave devolve a venda já paga."""
    atual = Venda.objects.get(pk=venda.pk)  # relê: o objeto recebido pode estar desatualizado (reenvio/duplo clique)
    if chave and atual.status == Venda.PAGA and atual.chave_idempotencia == chave:
        return atual, False
    with transaction.atomic():
        venda = _comanda_editavel(venda)
        _exigir_caixa_do_usuario(request, venda.caixa)
        if not venda.itens.exists():
            raise RegraNegocioError("A comanda não tem itens.")
        _finalizar(request, venda, pagamentos, desconto, pode_desconto, chave)
        _auditar_venda_criada(request, venda, rotulo="Recebida")
    return venda, True


def dividir_em_partes(total, partes):
    """Conta dividida em N partes iguais: devolve N valores em centavos somando exatamente `total` (as primeiras levam o resto)."""
    if not 1 <= partes <= MAX_PAGAMENTOS:
        raise RegraNegocioError(f"Divida em 1 a {MAX_PAGAMENTOS} partes.")
    base, resto = divmod(total, partes)
    return [base + (1 if i < resto else 0) for i in range(partes)]


# --- cancelamento e devolução ------------------------------------------------------------

def cancelar_venda(request, venda, motivo):
    """Cancela uma venda aberta ou paga. Venda PAGA só com o caixa dela ainda ABERTO (depois, use a devolução de itens).
    Repõe o estoque dos itens NÃO preparados, estorna as baixas e inativa a conta a receber."""
    motivo = " ".join((motivo or "").split())
    if not motivo:
        raise RegraNegocioError("Informe o motivo do cancelamento.")
    with transaction.atomic():
        venda = Venda.objects.select_for_update().select_related("caixa", "conta_receber", "cliente").get(pk=venda.pk)
        if venda.status == Venda.CANCELADA:
            raise RegraNegocioError("A venda já está cancelada.")
        if venda.devolucoes.exists():
            raise RegraNegocioError("A venda tem devoluções registradas e não pode ser cancelada.")
        if venda.status == Venda.PAGA and not venda.caixa.aberto:
            raise RegraNegocioError("O caixa desta venda já foi fechado. Use a devolução de itens.")
        for item in venda.itens.prefetch_related("insumos_baixados"):
            if not item.preparado:
                _repor_estoque(request.user, item, Decimal("1"), venda)
        if venda.status == Venda.PAGA:
            conta = venda.conta_receber
            if conta is not None:
                for baixa in conta.baixas.filter(tipo=BaixaBase.BAIXA, estornada_por__isnull=True):
                    estornar_baixa(request.user, baixa, f"Cancelamento da venda PDV #{venda.numero}")
                conta.refresh_from_db()
                conta.ativo = False
                conta.save(update_fields=["ativo", "atualizado_em"])
        antes = venda.get_status_display()
        venda.status, venda.cancelada_em, venda.cancelada_por, venda.motivo_cancelamento = Venda.CANCELADA, timezone.now(), request.user, motivo[:255]
        venda.save()
        if venda.pedidos.exists():
            from apps.pedido import services as pedidos  # import tardio: o app de pedidos depende do PDV

            pedidos.cancelar_por_venda(request, venda)
        registrar_alteracoes(request, venda, [
            {"campo": "status", "rotulo": "Status", "antes": antes, "depois": "Cancelada"},
            {"campo": "motivo_cancelamento", "rotulo": "Motivo do cancelamento", "antes": None, "depois": motivo[:255]},
        ], modulo="pdv")
    return venda


def devolver_item(request, item, quantidade, motivo, forma_estorno, repor_estoque=False):
    """Devolve `quantidade` de um item de venda PAGA. Valor devolvido = parte proporcional do item (com o desconto da venda
    rateado). Repor estoque só é permitido se o item NÃO foi preparado. Em dinheiro, exige caixa aberto do usuário (a saída
    entra na conferência) e o valor sai do esperado."""
    motivo = " ".join((motivo or "").split())
    if not motivo:
        raise RegraNegocioError("Informe o motivo da devolução.")
    quantidade = _quantidade(quantidade)
    with transaction.atomic():
        item = VendaItem.objects.select_for_update().select_related("venda", "venda__cliente", "venda__caixa").get(pk=item.pk)
        venda = item.venda
        if venda.status != Venda.PAGA:
            raise RegraNegocioError("Só se devolve itens de venda paga.")
        if forma_estorno.cliente_id != venda.cliente_id or not forma_estorno.ativo:
            raise RegraNegocioError("Forma de devolução inválida.")
        if quantidade > item.disponivel_para_devolucao:
            raise RegraNegocioError(f"Só restam {item.disponivel_para_devolucao.normalize():f} para devolver.")
        if repor_estoque and item.preparado:
            raise RegraNegocioError("O item já foi preparado: o estoque não pode ser reposto.")
        fracao = quantidade / item.quantidade
        valor = max(_arredondar(Decimal(item.total) * fracao * (Decimal(venda.total) / Decimal(venda.subtotal))), 1)
        caixa = None
        if forma_estorno.tipo == FormaPagamento.DINHEIRO:
            caixa = caixa_aberto_do(request.user)
            if caixa is None or caixa.cliente_id != venda.cliente_id:
                raise RegraNegocioError("Para devolver em dinheiro, abra o seu caixa.")
            if dinheiro_esperado(caixa)["esperado"] < valor:
                raise RegraNegocioError("Caixa sem dinheiro suficiente para a devolução.")
        if repor_estoque:
            _repor_estoque(request.user, item, fracao, venda)
        VendaItem.objects.filter(pk=item.pk).update(quantidade_devolvida=item.quantidade_devolvida + quantidade)
        hoje = timezone.localdate()
        conta = ContaPagar.objects.create(
            cliente=venda.cliente, descricao=f"Devolução da venda PDV #{venda.numero}", contraparte=(venda.consumidor.nome if venda.consumidor_id else "Consumidor final")[:150],
            categoria=_categoria(request, venda.cliente, CATEGORIA_DEVOLUCOES, TIPO_DESPESA), valor=valor, emissao=hoje, vencimento=hoje, observacao=motivo[:255],
        )
        registrar_criacao(request, conta, modulo="financeiro")
        registrar_baixa(request.user, conta, valor, hoje, f"Devolução em {forma_estorno.nome}"[:255])
        devolucao = Devolucao.objects.create(
            venda=venda, item=item, quantidade=quantidade, valor=valor, repor_estoque=repor_estoque, forma_estorno=forma_estorno, caixa=caixa,
            motivo=motivo[:255], usuario=request.user, conta_pagar=conta,
        )
        registrar_criacao(request, devolucao, modulo="pdv")
    return devolucao
