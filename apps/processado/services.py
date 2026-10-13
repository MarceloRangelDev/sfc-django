"""Gravação e exclusão de processados (usadas pela tela web).

Tudo numa única transação: ficha técnica, custo, insumo gerado, recálculo
dos dependentes e auditoria. Se qualquer passo falhar, nada é gravado.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count

from apps.auditoria.utils import comparar, registrar_alteracoes, registrar_criacao, registrar_exclusao, tirar_foto
from web_project.regras import RegraNegocioError

from .models import Processado, ProcessadoItem
from .recalculo import propagar_insumos, recalcular_processado, verificar_ciclo


def processados_que_usam(insumo):
    """Processados ATIVOS que têm o insumo na ficha técnica (para impedir
    excluir/inativar um insumo em uso e deixar referência inválida)."""
    return Processado.objects.filter(cliente_id=insumo.cliente_id, ativo=True, itens__insumo=insumo).distinct()


def mensagem_insumo_em_uso(insumo):
    """Texto de bloqueio se o insumo está em uso por processados ativos; senão None."""
    from apps.ficha.models import Ficha

    nomes = list(processados_que_usam(insumo).values_list("nome", flat=True)[:3])
    nomes += list(Ficha.objects.filter(cliente_id=insumo.cliente_id, itens__insumo=insumo).distinct().values_list("nome", flat=True)[:3])
    if not nomes:
        from apps.estoque.services import mensagem_saldo_em_estoque  # import tardio: evita ciclo entre apps

        return mensagem_saldo_em_estoque(insumo)  # saldo em estoque também impede a exclusão
    return f'O insumo "{insumo.nome}" é usado em: {", ".join(nomes[:3])}. Remova-o dessas receitas/fichas antes de excluir.'


def _texto_itens(itens):
    return "; ".join(f"{i.insumo.nome}: {i.quantidade.normalize():f} {i.medida.sigla}" for i in itens) or None


def salvar_processado(request, processado, itens, foto_antes=None):
    """Grava o processado e substitui a ficha técnica por `itens` (lista de
    ProcessadoItem ainda sem `processado`). Levanta ValidationError com
    mensagem legível para ciclo, unidade incompatível ou insumo em uso."""
    criando = processado.pk is None
    try:
        with transaction.atomic():
            if not processado.ativo and not criando and processado.insumo_id:
                erro = mensagem_insumo_em_uso(processado.insumo)
                if erro:
                    raise ValidationError(erro.replace("Remova-o dessas receitas/fichas antes de excluir.", "Remova-o delas antes de inativar."))
            verificar_ciclo(processado, [i.insumo_id for i in itens])

            antes_itens = None if criando else _texto_itens(processado.itens.select_related("insumo", "medida"))
            processado.save()
            processado.itens.all().delete()
            for item in itens:
                item.processado = processado
            ProcessadoItem.objects.bulk_create(itens)

            recalcular_processado(processado)  # custo, fator e insumo gerado
            propagar_insumos([processado.insumo_id], processado.cliente_id)  # próximos níveis

            if criando:
                registrar_criacao(request, processado)
            else:
                alteracoes = comparar(foto_antes, tirar_foto(processado)) if foto_antes else []
                depois_itens = _texto_itens(processado.itens.select_related("insumo", "medida"))
                if antes_itens != depois_itens:
                    alteracoes.append({"campo": "itens", "rotulo": "Ficha técnica", "antes": antes_itens, "depois": depois_itens})
                if alteracoes:
                    registrar_alteracoes(request, processado, alteracoes)
    except RegraNegocioError as erro:
        raise ValidationError(str(erro)) from None
    return processado


def excluir_processado(request, processado):
    """Exclusão lógica: inativa o processado e o insumo gerado (some das
    listas, mas o histórico e as referências ficam). Bloqueia se o insumo
    gerado é usado por outra receita ativa."""
    with transaction.atomic():
        if processado.insumo_id:
            erro = mensagem_insumo_em_uso(processado.insumo)
            if erro:
                raise ValidationError(erro)
        registrar_exclusao(request, processado, logica=True)
        processado.ativo = False
        processado.save(update_fields=["ativo", "atualizado_em"])
        if processado.insumo_id:
            insumo = processado.insumo
            insumo.ativo = False
            insumo._sem_propagacao = True
            insumo.save(update_fields=["ativo", "atualizado_em"])
    return processado


def contar_itens(queryset):
    """Anota a quantidade de itens da ficha (para a listagem)."""
    return queryset.annotate(qtd_itens=Count("itens"))
