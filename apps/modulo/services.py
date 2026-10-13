from django.db import transaction

from apps.acl.padroes import aplicar_padrao_aos_modulos
from apps.auditoria.utils import registrar_alteracoes

from .models import ClienteModulo, Modulo


def modulos_do_cliente(cliente):
    """Módulos que podem ser (des)habilitados para o cliente: os de negócio
    do segmento dele. Sem segmento, nenhum."""
    if not cliente.segmento_id:
        return Modulo.objects.none()
    return cliente.segmento.modulos.filter(essencial=False).order_by("ordem", "nome")


def atualizar_modulos_do_cliente(request, cliente, ids_marcados):
    """Habilita exatamente os módulos de `ids_marcados` para o cliente e
    desabilita os demais módulos do segmento dele. Tudo numa transação, com
    auditoria de quais módulos mudaram. Compartilhado pela tela web e pela API.

    Habilitar um módulo também o torna EFETIVO para os usuários do cliente: os perfis
    padrão (Gestor, Operador...) que ainda não têm nenhuma permissão do módulo recebem
    as permissões padrão dele (apps/acl/padroes.py). Sem isso o módulo aparecia
    habilitado mas nenhum usuário o enxergava (só com "permissão especial" por usuário).

    `ids_marcados`: conjunto de ids (str ou int) de Modulo.
    """
    marcados = {str(i) for i in ids_marcados}
    with transaction.atomic():
        estado_anterior = dict(ClienteModulo.objects.filter(cliente=cliente).values_list("modulo_id", "ativo"))
        alteracoes, habilitados = [], []
        for modulo in modulos_do_cliente(cliente):
            habilitado = str(modulo.pk) in marcados
            if habilitado:
                habilitados.append(modulo)
            ClienteModulo.objects.update_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": habilitado})
            if estado_anterior.get(modulo.pk, False) != habilitado:
                alteracoes.append({
                    "campo": f"modulo_{modulo.slug}", "rotulo": f"Módulo {modulo.nome}",
                    "antes": "Habilitado" if estado_anterior.get(modulo.pk, False) else "Desabilitado",
                    "depois": "Habilitado" if habilitado else "Desabilitado",
                })
        if alteracoes:
            registrar_alteracoes(request, cliente, alteracoes, modulo="modulo")
        if habilitados:
            # Todos os habilitados (não só os recém-ligados): salvar a tela de novo também
            # completa o que faltou. É idempotente e só acrescenta (ver apps/acl/padroes.py).
            aplicar_padrao_aos_modulos(habilitados)
    return alteracoes
