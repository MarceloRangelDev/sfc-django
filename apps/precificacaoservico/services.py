"""Regras de negócio do módulo, compartilhadas por telas e API. Tudo é da unidade (`cliente`) recebida; quem chama garante o escopo.

Principais responsabilidades:
- `montar_premissas`: o RETRATO (custo-hora, percentuais, versão) que fica gravado em cada proposta;
- `salvar_proposta`: valida, calcula e grava proposta + itens + custos em uma transação;
- `mudar_status`, `registrar_lancamento`, `registrar_pagamento`: ciclo de vida e execução;
- `relatorio`: previsto x realizado.
"""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.utils import timezone

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, registrar_edicao, registrar_exclusao, tirar_foto
from web_project.regras import RegraNegocioError

from . import calculos as c
from .models import ConfiguracaoServico, DespesaFixa, Lancamento, Pagamento, Proposta, PropostaCusto, PropostaItem

MODULO = "precificacaoservico"
MAX_LINHAS = 100


def totais_de_despesas(cliente):
    """(despesas fixas mensais, provisões mensais) ATIVAS da unidade, em centavos (Decimal), já convertidas para base mensal."""
    despesas = provisoes = Decimal(0)
    for d in DespesaFixa.objects.filter(cliente=cliente, ativo=True):
        mensal = c.valor_mensal(d.valor, d.periodicidade)
        if d.natureza == DespesaFixa.PROVISAO:
            provisoes += mensal
        else:
            despesas += mensal
    return despesas, provisoes


def calcular_custo_hora(cliente, configuracao=None):
    """Capacidade + custo-hora com a configuração e as despesas VIVAS da unidade (é o que a tela de configuração e o simulador mostram)."""
    cfg = configuracao or ConfiguracaoServico.da_unidade(cliente)
    cap = c.capacidade(cfg.dias_mes, cfg.horas_dia, cfg.horas_nao_faturaveis, cfg.horas_faturaveis_informadas)
    despesas, provisoes = totais_de_despesas(cliente)
    return cap, c.custo_hora(cfg.remuneracao_mensal, despesas, provisoes, cap["horas_faturaveis"], cfg.custo_variavel_hora)


def montar_premissas(cliente):
    """Retrato JSON das premissas de hoje. Decimais viram texto para não perder precisão. Falha (RegraNegocioError) se a configuração for
    inválida — por exemplo horas faturáveis zeradas ou impostos + taxas + margem >= 100%."""
    cfg = ConfiguracaoServico.da_unidade(cliente)
    cap, ch = calcular_custo_hora(cliente, cfg)
    c.validar_percentuais(cfg.impostos_pct, cfg.taxas_pct, cfg.margem_pct)
    return {
        "moeda": cfg.moeda, "versao_configuracao": cfg.versao, "calculado_em": timezone.now().isoformat(timespec="seconds"),
        "horas_dia": str(cfg.horas_dia), "dias_mes": str(cfg.dias_mes),
        "horas_disponiveis": str(cap["horas_disponiveis"]), "horas_faturaveis": str(cap["horas_faturaveis"]),
        "remuneracao_mensal": str(ch["remuneracao_mensal"]), "despesas_mensais": str(ch["despesas_mensais"].quantize(Decimal("0.01"))),
        "provisoes_mensais": str(ch["provisoes_mensais"].quantize(Decimal("0.01"))), "custo_fixo_mensal": str(ch["custo_fixo_mensal"].quantize(Decimal("0.01"))),
        "custo_fixo_hora": str(ch["custo_fixo_hora"]), "custo_variavel_hora": str(ch["custo_variavel_hora"]), "custo_hora_total": str(ch["custo_hora_total"]),
        "impostos_pct": str(cfg.impostos_pct), "taxas_pct": str(cfg.taxas_pct), "margem_pct": str(cfg.margem_pct),
    }


def salvar_configuracao(request, cfg, foto):
    """Grava a configuração (já preenchida pelo formulário/serializer), validando custo-hora e percentuais ANTES de gravar, e sobe a versão."""
    c.capacidade(cfg.dias_mes, cfg.horas_dia, cfg.horas_nao_faturaveis, cfg.horas_faturaveis_informadas)
    c.validar_percentuais(cfg.impostos_pct, cfg.taxas_pct, cfg.margem_pct)
    with transaction.atomic():
        cfg.versao += 1
        cfg.save()
        registrar_edicao(request, cfg, foto, modulo=MODULO)
    return cfg


# ---------------------------------------------------------------------------------------------------------------------------
# Proposta
# ---------------------------------------------------------------------------------------------------------------------------

CAMPOS_CALCULO = ("modo", "metodo_preco", "margem_pct", "markup_pct", "preco_fechado", "minimo_cobranca", "horas_minimas", "arredondamento_minutos",
                  "horas_deslocamento", "cobra_deslocamento", "desconto_tipo", "desconto", "acrescimo_pct", "acrescimo_valor", "custo_financeiro_pct")


def entrada_de_calculo(proposta, itens, custos):
    """Proposta (objeto, mesmo não salvo) + linhas -> dicionário de `calculos.calcular_proposta`."""
    entrada = {campo: getattr(proposta, campo) for campo in CAMPOS_CALCULO}
    if entrada["desconto_tipo"] == "valor":
        entrada["desconto"] = Decimal(entrada["desconto"] or 0)
    entrada["itens"] = [{"modo": i["modo"], "quantidade": i["quantidade"], "horas_por_unidade": i.get("horas_por_unidade"), "preco_unitario": i.get("preco_unitario")} for i in itens]
    entrada["custos"] = [{"repasse": k["repasse"], "quantidade": k["quantidade"], "custo_unitario": k["custo_unitario"]} for k in custos]
    return entrada


def simular(cliente, proposta, itens, custos, premissas=None):
    """Calcula SEM gravar. `premissas` None = as de hoje; propostas existentes passam o próprio retrato."""
    premissas = premissas or montar_premissas(cliente)
    return calcular_resultado(proposta, itens, custos, premissas), premissas


def calcular_resultado(proposta, itens, custos, premissas):
    if len(itens) > MAX_LINHAS or len(custos) > MAX_LINHAS:
        raise RegraNegocioError(f"No máximo {MAX_LINHAS} itens e {MAX_LINHAS} custos por proposta.")
    return c.calcular_proposta(entrada_de_calculo(proposta, itens, custos), premissas)


def normalizar_linhas(itens, custos):
    """Confere o que o cálculo não confere (textos e opções) e devolve cópias limpas. Tipos numéricos já vêm convertidos por quem chama."""
    categorias = dict(PropostaCusto.CATEGORIA_CHOICES)
    limpos_i, limpos_c = [], []
    for n, i in enumerate(itens, 1):
        descricao = " ".join(str(i.get("descricao") or "").split())
        if not descricao or len(descricao) > 150:
            raise RegraNegocioError(f"Item {n}: informe a descrição (até 150 caracteres).")
        limpos_i.append({**i, "descricao": descricao, "unidade": str(i.get("unidade") or "")[:20]})
    for n, k in enumerate(custos, 1):
        descricao = " ".join(str(k.get("descricao") or "").split())
        if not descricao or len(descricao) > 150:
            raise RegraNegocioError(f"Custo {n}: informe a descrição (até 150 caracteres).")
        if (k.get("categoria") or "outro") not in categorias:
            raise RegraNegocioError(f"Custo {n}: tipo de custo inválido.")
        limpos_c.append({**k, "descricao": descricao, "categoria": k.get("categoria") or "outro", "unidade": str(k.get("unidade") or "")[:20]})
    return limpos_i, limpos_c


def linhas_salvas(proposta):
    """Itens e custos gravados -> dicts de `salvar_proposta` (para recalcular sem mexer nas linhas)."""
    itens = [{"descricao": i.descricao, "modo": i.modo, "quantidade": i.quantidade, "unidade": i.unidade, "horas_por_unidade": i.horas_por_unidade, "preco_unitario": i.preco_unitario}
             for i in proposta.itens.all()]
    custos = [{"descricao": k.descricao, "categoria": k.categoria, "quantidade": k.quantidade, "unidade": k.unidade, "custo_unitario": k.custo_unitario, "repasse": k.repasse}
              for k in proposta.custos.all()]
    return itens, custos


def _proximo_numero(cliente):
    return (Proposta.objects.filter(cliente=cliente).aggregate(m=Max("numero"))["m"] or 0) + 1


def _texto_linhas(proposta):
    itens = "; ".join(f"{i.descricao} x{i.quantidade.normalize():f} ({i.modo})" for i in proposta.itens.all())
    custos = "; ".join(f"{k.descricao} x{k.quantidade.normalize():f} ({k.repasse})" for k in proposta.custos.all())
    return " | ".join(p for p in (itens, custos) if p) or None


def salvar_proposta(request, proposta, itens, custos, foto_antes=None, atualizar_premissas=False):
    """Cria ou atualiza a proposta, SUBSTITUINDO itens e custos. `itens`/`custos`: listas de dicts já normalizados.

    Criação: tira o retrato das premissas de HOJE. Edição: reaproveita o retrato gravado (preço antigo não muda por mudança de
    configuração); `atualizar_premissas=True` (ação explícita do usuário) refaz o retrato. Só rascunho/enviada é alterada."""
    criando = proposta.pk is None
    if not criando and not proposta.editavel:
        raise RegraNegocioError("Esta proposta já foi fechada e não pode ser alterada.")
    itens, custos = normalizar_linhas(itens, custos)
    if criando or atualizar_premissas or not proposta.premissas:
        proposta.premissas = montar_premissas(proposta.cliente)
    if proposta.desconto_tipo not in ("valor", "percentual"):
        raise RegraNegocioError("Tipo de desconto inválido.")
    resultado = calcular_resultado(proposta, itens, custos, proposta.premissas)
    proposta.resultado = resultado
    proposta.custo_total, proposta.total, proposta.lucro = resultado["custo_total"], resultado["total"], resultado["lucro"]
    for tentativa in range(3):  # numeração por unidade: gravações simultâneas podem disputar o mesmo número
        try:
            with transaction.atomic():
                antes = None if criando else _texto_linhas(proposta)
                if criando:
                    proposta.numero = _proximo_numero(proposta.cliente)
                proposta.save()
                proposta.itens.all().delete()
                proposta.custos.all().delete()
                PropostaItem.objects.bulk_create([
                    PropostaItem(proposta=proposta, ordem=n, descricao=i["descricao"], modo=i["modo"], quantidade=i["quantidade"], unidade=i.get("unidade") or "",
                                 horas_por_unidade=i.get("horas_por_unidade"), preco_unitario=i.get("preco_unitario"))
                    for n, i in enumerate(itens)
                ])
                PropostaCusto.objects.bulk_create([
                    PropostaCusto(proposta=proposta, ordem=n, categoria=k["categoria"], descricao=k["descricao"], quantidade=k["quantidade"], unidade=k.get("unidade") or "",
                                  custo_unitario=k["custo_unitario"], repasse=k["repasse"])
                    for n, k in enumerate(custos)
                ])
                if criando:
                    registrar_criacao(request, proposta, modulo=MODULO)
                else:
                    alteracoes = comparar(foto_antes, tirar_foto(proposta)) if foto_antes else []
                    depois = _texto_linhas(proposta)
                    if antes != depois:
                        alteracoes.append({"campo": "linhas", "rotulo": "Itens e custos", "antes": antes, "depois": depois})
                    if alteracoes:
                        registrar_alteracoes(request, proposta, alteracoes, modulo=MODULO)
            return proposta
        except IntegrityError:
            if not criando or tentativa == 2:
                raise
    return proposta


def mudar_status(request, proposta, novo, aceite_nome=""):
    """Enviada/aprovada/recusada/cancelada/concluída, só pelas transições de `Proposta.TRANSICOES`. Aprovar registra data e nome do aceite;
    concluir exige início e fecha a data de conclusão (hoje, se vazia)."""
    permitidos = Proposta.TRANSICOES.get(proposta.status, ())
    if novo not in permitidos:
        raise RegraNegocioError("Esta mudança de situação não é permitida a partir de “%s”." % proposta.get_status_display())
    foto = tirar_foto(proposta)
    with transaction.atomic():
        proposta.status = novo
        campos = ["status", "atualizado_em"]
        if novo == Proposta.APROVADA:
            proposta.aceite_em, proposta.aceite_nome = timezone.now(), (aceite_nome or proposta.destinatario_nome)[:150]
            campos += ["aceite_em", "aceite_nome"]
        if novo == Proposta.CONCLUIDA:
            proposta.data_conclusao = proposta.data_conclusao or date.today()
            campos.append("data_conclusao")
            if proposta.data_inicio is None:
                primeiro = proposta.lancamentos.order_by("data").first()
                proposta.data_inicio = primeiro.data if primeiro else proposta.data_conclusao
                campos.append("data_inicio")
        proposta.save(update_fields=campos)
        registrar_edicao(request, proposta, foto, modulo=MODULO)
    return proposta


def excluir_proposta(request, proposta):
    """Só rascunho é apagado; as demais se cancelam (histórico preservado)."""
    if proposta.status != Proposta.RASCUNHO:
        raise RegraNegocioError("Só rascunhos podem ser excluídos; cancele a proposta para encerrá-la.")
    with transaction.atomic():
        registrar_exclusao(request, proposta, modulo=MODULO)
        proposta.delete()


# ---------------------------------------------------------------------------------------------------------------------------
# Execução: lançamentos, pagamentos e relatório
# ---------------------------------------------------------------------------------------------------------------------------

def _exigir_em_execucao(proposta):
    if proposta.status not in Proposta.EM_EXECUCAO:
        raise RegraNegocioError("Só propostas aprovadas ou concluídas recebem lançamentos e pagamentos.")


def registrar_lancamento(request, proposta, dados):
    """Lançamento real. Horas sem custo informado valem horas x custo-hora do retrato da proposta."""
    _exigir_em_execucao(proposta)
    horas = c.dec(dados.get("horas") or 0, "Horas")
    if not 0 <= horas <= 1000:
        raise RegraNegocioError("Horas: informe um valor entre 0 e 1000.")
    custo = dados.get("custo")
    if custo in (None, ""):
        custo = c.centavos(horas * Decimal(proposta.premissas["custo_hora_total"]))
    custo = c._centavos_validos(custo, "Custo real")
    receita = c._centavos_validos(dados.get("receita") or 0, "Valor cobrado")
    if dados["tipo"] not in dict(Lancamento.TIPO_CHOICES):
        raise RegraNegocioError("Tipo de lançamento inválido.")
    if dados["tipo"] != "extra" and receita:
        raise RegraNegocioError("Só serviços extras aprovados têm valor cobrado.")
    if dados["tipo"] == "horas" and horas <= 0:
        raise RegraNegocioError("Informe as horas trabalhadas.")
    with transaction.atomic():
        lancamento = Lancamento.objects.create(proposta=proposta, tipo=dados["tipo"], data=dados["data"], descricao=(dados.get("descricao") or "")[:150],
                                               horas=horas, custo=custo, receita=receita, criado_por=request.user)
        registrar_criacao(request, lancamento, modulo=MODULO)
        if proposta.data_inicio is None:
            proposta.data_inicio = dados["data"]
            proposta.save(update_fields=["data_inicio", "atualizado_em"])
    return lancamento


def registrar_pagamento(request, proposta, dados):
    _exigir_em_execucao(proposta)
    valor = c._centavos_validos(dados.get("valor"), "Valor", minimo=1)
    taxa = c._centavos_validos(dados.get("taxa") or 0, "Taxa")
    if taxa > valor:
        raise RegraNegocioError("A taxa não pode ser maior que o valor do pagamento.")
    if dados.get("meio") not in dict(Pagamento.MEIO_CHOICES) or dados.get("status", Pagamento.RECEBIDO) not in (Pagamento.RECEBIDO, Pagamento.PENDENTE):
        raise RegraNegocioError("Meio de pagamento ou situação inválidos.")
    with transaction.atomic():
        pagamento = Pagamento.objects.create(proposta=proposta, valor=valor, data=dados["data"], meio=dados["meio"], taxa=taxa,
                                             status=dados.get("status") or Pagamento.RECEBIDO, observacao=(dados.get("observacao") or "")[:255], criado_por=request.user)
        registrar_criacao(request, pagamento, modulo=MODULO)
    return pagamento


def excluir_registro(request, registro):
    """Apaga um lançamento ou pagamento (auditado); propostas fechadas por cancelamento/recusa não chegam aqui (ver _exigir_em_execucao)."""
    _exigir_em_execucao(registro.proposta)
    with transaction.atomic():
        registrar_exclusao(request, registro, modulo=MODULO)
        registro.delete()


def atualizar_datas(request, proposta, inicio, conclusao):
    _exigir_em_execucao(proposta)
    if inicio and conclusao and conclusao < inicio:
        raise RegraNegocioError("A conclusão não pode ser anterior ao início.")
    foto = tirar_foto(proposta)
    proposta.data_inicio, proposta.data_conclusao = inicio, conclusao
    proposta.save(update_fields=["data_inicio", "data_conclusao", "atualizado_em"])
    registrar_edicao(request, proposta, foto, modulo=MODULO)


def relatorio(proposta):
    """Previsto x realizado de uma proposta (valores em centavos).

    variação de custo = custo real - custo previsto
    resultado = valor recebido - custos reais - impostos e taxas registrados (taxas dos pagamentos)
    Os dados reais NÃO alteram preços-base futuros: servem para o usuário ajustar a configuração se quiser."""
    previsto = proposta.resultado
    lanc = proposta.lancamentos.aggregate(horas=Sum("horas"), custo=Sum("custo"), receita=Sum("receita"))
    recebido = proposta.pagamentos.filter(status=Pagamento.RECEBIDO).aggregate(v=Sum("valor"), t=Sum("taxa"))
    pendente = proposta.pagamentos.filter(status=Pagamento.PENDENTE).aggregate(v=Sum("valor"))["v"] or 0
    horas_reais = lanc["horas"] or Decimal(0)
    custo_real = lanc["custo"] or 0
    extras = lanc["receita"] or 0
    valor_recebido, taxas = recebido["v"] or 0, recebido["t"] or 0
    receita_total = proposta.total + extras
    return {
        "previsto": {"horas": previsto.get("horas_trabalho"), "custo": proposta.custo_total, "valor": proposta.total, "lucro": proposta.lucro},
        "realizado": {"horas": str(horas_reais), "custo": custo_real, "extras_aprovados": extras, "valor_total": receita_total},
        "variacao_horas": str(horas_reais - Decimal(previsto.get("horas_trabalho") or 0)),
        "variacao_custo": custo_real - proposta.custo_total,
        "recebido": valor_recebido, "pendente": pendente, "a_receber": max(receita_total - valor_recebido - pendente, 0),
        "impostos_taxas_registrados": taxas,
        "resultado": valor_recebido - custo_real - taxas,
        "data_inicio": proposta.data_inicio, "data_conclusao": proposta.data_conclusao,
    }
