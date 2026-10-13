"""Ponto único de entrada do recálculo quando um Insumo é gravado por
qualquer caminho (tela, API, admin, script)."""
from .recalculo import propagar_insumos

# Campos que mudam custo/unidade: só eles justificam recalcular dependentes.
CAMPOS_DE_CUSTO = {"quantidade", "medida", "preco_pago", "preco_unidade", "ativo"}


def propagar_alteracao_de_insumo(sender, instance, created, update_fields=None, **kwargs):
    # Insumo novo ainda não é usado por ninguém. `_sem_propagacao` é marcado
    # pelo próprio motor ao gravar o insumo gerado de um processado (ele já
    # propaga aos níveis seguintes, e sem a marca haveria recálculo repetido).
    if created or getattr(instance, "_sem_propagacao", False):
        return
    if update_fields is not None and not CAMPOS_DE_CUSTO & set(update_fields):
        return
    propagar_insumos([instance.pk], instance.cliente_id)
