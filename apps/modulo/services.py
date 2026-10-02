from django.db import transaction

from apps.auditoria.utils import registrar_alteracoes

from .models import ClienteModulo, Modulo


def atualizar_modulos_do_cliente(request, cliente, ids_marcados):
    """Habilita exatamente os módulos de `ids_marcados` para o cliente e
    desabilita os demais. Tudo numa transação, com auditoria de quais
    módulos mudaram. Compartilhado pela tela web e pela API.

    `ids_marcados`: conjunto de ids (str ou int) de Modulo.
    """
    marcados = {str(i) for i in ids_marcados}
    with transaction.atomic():
        estado_anterior = dict(ClienteModulo.objects.filter(cliente=cliente).values_list("modulo_id", "ativo"))
        alteracoes = []
        for modulo in Modulo.objects.all():
            habilitado = str(modulo.pk) in marcados
            ClienteModulo.objects.update_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": habilitado})
            if estado_anterior.get(modulo.pk, False) != habilitado:
                alteracoes.append({
                    "campo": f"modulo_{modulo.slug}", "rotulo": f"Módulo {modulo.nome}",
                    "antes": "Habilitado" if estado_anterior.get(modulo.pk, False) else "Desabilitado",
                    "depois": "Habilitado" if habilitado else "Desabilitado",
                })
        if alteracoes:
            registrar_alteracoes(request, cliente, alteracoes, modulo="modulo")
    return alteracoes
