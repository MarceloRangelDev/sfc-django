"""Regras da cotação: cálculo do preço aplicado, comparativo, escolha do vencedor, status e contas a pagar.

QUAL PREÇO VALE (por oferta; ver CotacaoPreco):
  1. `preco_e_atacado` marcado -> o preço unitário JÁ é de atacado. Atende se não há quantidade mínima ou se a
     quantidade da oferta a alcança; abaixo do mínimo a oferta NÃO atende (fica fora da disputa, com o motivo).
  2. senão, se há preço de atacado + mínimo e a quantidade da oferta alcança o mínimo -> vale o ATACADO.
  3. senão -> vale o preço unitário (varejo), mesmo que exista atacado (a tela avisa a partir de quanto).

COMPARAÇÃO: todos os preços são normalizados para o preço de 1 KG / 1 L / 1 UN... da dimensão do item
(`UNIDADE_PADRAO_PRECO`), então uma loja que cota em gramas e outra em quilos são comparáveis. Vence o MENOR preço
normalizado entre as ofertas que atendem; empate -> menor total -> primeira cadastrada.

ARREDONDAMENTO: moeda em centavos inteiros. O total da linha = quantidade × preço da unidade, arredondado UMA vez
(meio para cima). A comparação usa o preço normalizado exato (Decimal), sem arredondar antes; o valor mostrado
é arredondado ao centavo.

FLUXO: RASCUNHO -> ENVIADA -> FECHADA (vencedores escolhidos); CANCELADA. Só RASCUNHO/ENVIADA aceitam alterações.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.auditoria.utils import registrar_alteracoes, registrar_criacao, registrar_edicao, registrar_exclusao
from web_project.regras import RegraNegocioError
from web_project.regras.conversao import UNIDADE_PADRAO_PRECO
from web_project.regras.dinheiro import DinheiroService

from .models import Cotacao, CotacaoItem, CotacaoLoja, CotacaoPreco

CENTAVO = Decimal("1")


def reais(centavos):
    """1250 -> \"12,50\": o formato que o campo de dinheiro do formulário de contas aceita."""
    return f"{centavos // 100},{centavos % 100:02d}"


def _arredondar(valor):
    return int(Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# Cálculo e comparativo
# --------------------------------------------------------------------------

def avaliar_preco(preco):
    """Dict com o preço aplicado a uma oferta: tipo, valor da unidade, total, preço normalizado e se atende."""
    quantidade, minimo = preco.quantidade, preco.qtd_minima_atacado
    atende, motivo = True, ""
    if preco.preco_e_atacado:
        tipo, valor = "atacado", Decimal(preco.preco_unitario)
        if minimo is not None and quantidade < minimo:
            atende, motivo = False, f"abaixo do mínimo de atacado ({minimo.normalize():f})"
    elif preco.preco_atacado and minimo is not None and quantidade >= minimo:
        tipo, valor = "atacado", Decimal(preco.preco_atacado)
    else:
        tipo, valor = "varejo", Decimal(preco.preco_unitario)
        if preco.preco_atacado and minimo is not None:
            motivo = f"atacado a partir de {minimo.normalize():f}"
    fator_padrao, sigla = UNIDADE_PADRAO_PRECO[preco.medida.dimensao]
    por_base = valor / preco.medida.fator_base * fator_padrao
    return {
        "preco": preco, "tipo": tipo, "valor_unitario": valor, "atende": atende, "motivo": motivo,
        "total": _arredondar(quantidade * valor), "por_base": por_base, "por_base_centavos": _arredondar(por_base),
        "unidade_base": sigla, "melhor": False,
    }


def _formatar_oferta(avaliacao):
    """Textos prontos para telas e exportações (dinheiro sempre via DinheiroService)."""
    avaliacao["valor_unitario_formatado"] = DinheiroService.formatar(int(avaliacao["valor_unitario"]))
    avaliacao["total_formatado"] = DinheiroService.formatar(avaliacao["total"])
    avaliacao["por_base_formatado"] = DinheiroService.formatar(avaliacao["por_base_centavos"])
    return avaliacao


def comparativo(cotacao):
    """Por item: lista de ofertas avaliadas (a de menor preço normalizado entre as que atendem vem marcada `melhor`)."""
    resultado = []
    itens = cotacao.itens.select_related("medida", "insumo").prefetch_related("precos__loja", "precos__medida")
    for item in itens:
        ofertas = [_formatar_oferta(avaliar_preco(p)) for p in item.precos.all()]
        candidatas = [o for o in ofertas if o["atende"]]
        if candidatas:
            melhor = min(candidatas, key=lambda o: (o["por_base"], o["total"], o["preco"].pk))
            melhor["melhor"] = True
        ofertas.sort(key=lambda o: (not o["atende"], o["por_base"], o["preco"].pk))
        escolhida = next((o for o in ofertas if o["preco"].escolhido), None)
        resultado.append({"item": item, "ofertas": ofertas, "escolhida": escolhida})
    return resultado


def resumo_escolhidos(dados_comparativo):
    """Vencedores agrupados por loja: [{loja, linhas, total}] e o total geral (centavos)."""
    por_loja = {}
    for linha in dados_comparativo:
        escolhida = linha["escolhida"]
        if escolhida:
            loja = escolhida["preco"].loja
            bloco = por_loja.setdefault(loja.pk, {"loja": loja, "linhas": [], "total": 0})
            bloco["linhas"].append({"item": linha["item"], **escolhida})
            bloco["total"] += escolhida["total"]
    blocos = sorted(por_loja.values(), key=lambda b: b["loja"].nome.lower())
    for bloco in blocos:
        bloco["total_formatado"] = DinheiroService.formatar(bloco["total"])
    return blocos, sum(b["total"] for b in blocos)


# --------------------------------------------------------------------------
# Gravação com auditoria (compartilhada por telas e API)
# --------------------------------------------------------------------------

def cotacao_do(objeto):
    return objeto if isinstance(objeto, Cotacao) else (objeto.cotacao if hasattr(objeto, "cotacao") else objeto.item.cotacao)


def exigir_editavel(cotacao):
    if not cotacao.editavel:
        raise RegraNegocioError(f"A cotação está {cotacao.get_status_display().lower()} e não pode ser alterada.")


def salvar_filho(request, form):
    """Grava loja/item/preço (form JÁ válido) e audita. Para edição, `form.foto_antes` é a foto tirada ANTES de o
    formulário alterar a instância (ver tirar_foto)."""
    instancia = form.instance
    criando = instancia.pk is None
    with transaction.atomic():
        exigir_editavel(cotacao_do(instancia))
        objeto = form.save()
        if criando:
            registrar_criacao(request, objeto)
        else:
            registrar_edicao(request, objeto, form.foto_antes)
    return objeto


def excluir_filho(request, objeto):
    """Remove loja/item/preço (e, em cascata, os preços dependentes) com auditoria."""
    with transaction.atomic():
        cotacao = cotacao_do(objeto)
        exigir_editavel(cotacao)
        if isinstance(objeto, CotacaoLoja) and objeto.conta_pagar_id:
            raise RegraNegocioError("Esta loja já tem conta a pagar gerada.")
        registrar_exclusao(request, objeto)
        objeto.delete()


def importar_da_lista(request, cotacao, ids=None):
    """Copia itens da lista de compras da unidade da cotação para a cotação (os já existentes são ignorados;
    `ids` None = todos). Devolve os itens criados. Idempotente."""
    from apps.compras.models import ItemCompra

    exigir_editavel(cotacao)
    existentes = set(cotacao.itens.filter(insumo__isnull=False).values_list("insumo_id", flat=True))
    descricoes = set(cotacao.itens.filter(insumo__isnull=True).values_list("descricao", flat=True))
    consulta = ItemCompra.objects.filter(cliente_id=cotacao.cliente_id).select_related("medida")
    if ids is not None:
        consulta = consulta.filter(pk__in=ids)
    criados = []
    with transaction.atomic():
        for item_compra in consulta:
            if (item_compra.insumo_id and item_compra.insumo_id in existentes) or (not item_compra.insumo_id and item_compra.descricao in descricoes):
                continue
            item = CotacaoItem.objects.create(
                cotacao=cotacao, insumo_id=item_compra.insumo_id, descricao=item_compra.descricao, quantidade=item_compra.quantidade,
                medida=item_compra.medida, observacao=item_compra.observacao[:255],
            )
            registrar_criacao(request, item)
            criados.append(item)
    return criados


# --------------------------------------------------------------------------
# Escolha do vencedor
# --------------------------------------------------------------------------

def escolher_preco(request, preco):
    """Marca a oferta como vencedora do item (desmarcando a anterior). A oferta precisa atender (ex.: mínimo de atacado)."""
    with transaction.atomic():
        item = CotacaoItem.objects.select_for_update().select_related("cotacao").get(pk=preco.item_id)
        exigir_editavel(item.cotacao)
        avaliacao = avaliar_preco(preco)
        if not avaliacao["atende"]:
            raise RegraNegocioError(f"Esta oferta não atende: {avaliacao['motivo']}.")
        atual = item.precos.filter(escolhido=True).exclude(pk=preco.pk).first()
        if preco.escolhido and atual is None:
            return preco
        if atual:
            CotacaoPreco.objects.filter(pk=atual.pk).update(escolhido=False)
            registrar_alteracoes(request, atual, [{"campo": "escolhido", "rotulo": "Escolhido", "antes": "Sim", "depois": "Não"}])
        CotacaoPreco.objects.filter(pk=preco.pk).update(escolhido=True)
        preco.escolhido = True
        registrar_alteracoes(request, preco, [{"campo": "escolhido", "rotulo": "Escolhido", "antes": "Não", "depois": "Sim"}])
    return preco


def limpar_escolha(request, item):
    with transaction.atomic():
        exigir_editavel(item.cotacao)
        for preco in item.precos.filter(escolhido=True):
            CotacaoPreco.objects.filter(pk=preco.pk).update(escolhido=False)
            registrar_alteracoes(request, preco, [{"campo": "escolhido", "rotulo": "Escolhido", "antes": "Sim", "depois": "Não"}])


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def _mudar_status(request, cotacao, novo, **campos):
    antes = cotacao.get_status_display()
    cotacao.status = novo
    for nome, valor in campos.items():
        setattr(cotacao, nome, valor)
    cotacao.save(update_fields=["status", "atualizado_em", *campos])
    registrar_alteracoes(request, cotacao, [{"campo": "status", "rotulo": "Status", "antes": antes, "depois": cotacao.get_status_display()}])


def mudar_status(request, cotacao, acao):
    """acao: enviar | fechar | reabrir | cancelar. Valida as transições; tudo numa transação."""
    with transaction.atomic():
        cotacao = Cotacao.objects.select_for_update().get(pk=cotacao.pk)
        tem_conta = cotacao.lojas.filter(conta_pagar__isnull=False).exists()
        if acao == "enviar":
            if cotacao.status != Cotacao.RASCUNHO:
                raise RegraNegocioError("Só uma cotação em rascunho pode ser enviada.")
            if not cotacao.itens.exists() or not cotacao.lojas.exists():
                raise RegraNegocioError("Inclua ao menos um item e uma loja antes de enviar.")
            _mudar_status(request, cotacao, Cotacao.ENVIADA, enviada_em=timezone.now())
        elif acao == "fechar":
            if not cotacao.editavel:
                raise RegraNegocioError("Só uma cotação em rascunho ou enviada pode ser fechada.")
            if not CotacaoPreco.objects.filter(item__cotacao=cotacao, escolhido=True).exists():
                raise RegraNegocioError("Escolha ao menos um vencedor antes de fechar.")
            _mudar_status(request, cotacao, Cotacao.FECHADA, fechada_em=timezone.now())
        elif acao == "reabrir":
            if cotacao.status != Cotacao.FECHADA:
                raise RegraNegocioError("Só uma cotação fechada pode ser reaberta.")
            if tem_conta:
                raise RegraNegocioError("Há conta a pagar gerada por esta cotação; ela não pode ser reaberta.")
            _mudar_status(request, cotacao, Cotacao.ENVIADA, fechada_em=None)
        elif acao == "cancelar":
            if cotacao.status == Cotacao.CANCELADA:
                raise RegraNegocioError("A cotação já está cancelada.")
            if tem_conta:
                raise RegraNegocioError("Há conta a pagar gerada por esta cotação; ela não pode ser cancelada.")
            _mudar_status(request, cotacao, Cotacao.CANCELADA)
        else:
            raise RegraNegocioError("Ação inválida.")
    return cotacao


def excluir_cotacao(request, cotacao):
    with transaction.atomic():
        if cotacao.lojas.filter(conta_pagar__isnull=False).exists():
            raise RegraNegocioError("Há conta a pagar gerada por esta cotação; ela não pode ser excluída.")
        registrar_exclusao(request, cotacao)
        cotacao.delete()  # itens, lojas e preços são parte da cotação (CASCADE)


# --------------------------------------------------------------------------
# Conta a pagar
# --------------------------------------------------------------------------

def gerar_contas_a_pagar(request, cotacao, categoria_id, vencimento):
    """Para cada loja com vencedores e ainda sem conta, cria UMA conta a pagar com o total dos itens vencedores dela.
    Exige cotação FECHADA. Validação (categoria de despesa da unidade, vencimento) pelo mesmo formulário da tela de
    contas. IDEMPOTENTE por loja: quem já tem conta é ignorado. Devolve as contas criadas."""
    from apps.financeiro.forms import conta_form_para
    from apps.financeiro.models import TIPO_DESPESA, ContaPagar

    if cotacao.status != Cotacao.FECHADA:
        raise RegraNegocioError("Feche a cotação antes de gerar as contas a pagar.")
    blocos, _ = resumo_escolhidos(comparativo(cotacao))
    Form = conta_form_para(ContaPagar, TIPO_DESPESA)
    criadas = []
    with transaction.atomic():
        lojas = CotacaoLoja.objects.select_for_update().filter(pk__in=[b["loja"].pk for b in blocos])
        por_pk = {l.pk: l for l in lojas}
        for bloco in blocos:
            loja = por_pk[bloco["loja"].pk]
            if loja.conta_pagar_id:
                continue
            dados = {
                "descricao": f"Cotação: {cotacao.titulo}"[:150], "contraparte": loja.nome[:150], "categoria": categoria_id,
                "valor": reais(bloco["total"]), "emissao": timezone.localdate().isoformat(), "vencimento": vencimento.isoformat(),
                "observacao": f"Gerada pela cotação ID {cotacao.pk}"[:255],
            }
            form = Form(dados, cliente=cotacao.cliente)
            if not form.is_valid():
                raise RegraNegocioError("; ".join(f"{c}: {' '.join(str(m) for m in e)}" for c, e in form.errors.items()))
            conta = form.save(commit=False)
            conta.cliente = cotacao.cliente
            conta.save()
            registrar_criacao(request, conta)
            loja.conta_pagar = conta
            loja.save(update_fields=["conta_pagar", "atualizado_em"])
            registrar_alteracoes(request, loja, [{"campo": "conta_pagar", "rotulo": "Conta a pagar", "antes": None, "depois": f"ID {conta.pk} ({DinheiroService.formatar(conta.valor)})"}])
            criadas.append(conta)
    return criadas
