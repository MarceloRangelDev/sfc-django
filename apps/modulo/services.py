from django.db import transaction

from apps.acl.padroes import aplicar_padrao_aos_modulos
from apps.auditoria.utils import registrar_alteracoes

from web_project.regras import RegraNegocioError

from .models import ClienteModulo, Modulo, OrdemMenuCliente


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


def modulos_ordenaveis(cliente):
    """Módulos do menu principal do cliente que o administrador pode reordenar (habilitados para ele), na ordem EFETIVA (personalizada ou padrão).

    Ficam de fora os que não têm item próprio no menu (sem rota), os de Configurações e as telas do PDV (o PDV é um grupo único no menu)."""
    from web_project.menu import TELAS_PDV

    modulos = list(
        modulos_do_cliente(cliente)
        .filter(secao=Modulo.SECAO_PRINCIPAL, ativo=True, clientes_habilitados__cliente=cliente, clientes_habilitados__ativo=True)
        .exclude(rota="").exclude(slug__in=TELAS_PDV)
    )
    personalizada = dict(OrdemMenuCliente.objects.filter(cliente=cliente, modulo__in=modulos).values_list("modulo_id", "ordem"))
    return sorted(modulos, key=lambda m: (personalizada.get(m.pk, m.ordem), m.nome))


def salvar_ordem_do_menu(request, cliente, ids_em_ordem):
    """Grava a nova ordem do menu do cliente. `ids_em_ordem` precisa conter EXATAMENTE os módulos reordenáveis dele (sem faltar, repetir
    nem trazer de fora): ids de outro cliente/segmento são recusados. A nova sequência reaproveita os valores de ordem já usados por esses
    módulos; se coincide com a padrão, as linhas personalizadas são removidas. Tudo numa transação, com auditoria."""
    atuais = modulos_ordenaveis(cliente)
    try:
        ids = [int(i) for i in ids_em_ordem]
    except (TypeError, ValueError):
        raise RegraNegocioError("Ordem inválida.") from None
    if len(set(ids)) != len(ids) or set(ids) != {m.pk for m in atuais}:
        raise RegraNegocioError("A lista de módulos mudou. Recarregue a página e tente de novo.")
    por_id = {m.pk: m for m in atuais}
    lugares = sorted(m.ordem for m in atuais)  # os "lugares" ocupados no menu, em ordem crescente
    # a ordem dos lugares precisa ser estrita para a sequência sobreviver ao desempate por nome: força valores distintos
    estritos = []
    for valor in lugares:
        estritos.append(max(valor, estritos[-1] + 1) if estritos else valor)
    lugares = estritos
    with transaction.atomic():
        antes = ", ".join(m.nome for m in atuais)
        padrao = [m.pk for m in sorted(atuais, key=lambda m: (m.ordem, m.nome))]
        if ids == padrao:
            OrdemMenuCliente.objects.filter(cliente=cliente).delete()
        else:
            for ordem, modulo_id in zip(lugares, ids):
                OrdemMenuCliente.objects.update_or_create(cliente=cliente, modulo_id=modulo_id, defaults={"ordem": ordem})
        depois = ", ".join(por_id[i].nome for i in ids)
        if antes != depois:
            registrar_alteracoes(request, cliente, [{"campo": "ordem_menu", "rotulo": "Ordem dos módulos no menu", "antes": antes, "depois": depois}], modulo="modulo")
    return modulos_ordenaveis(cliente)


def restaurar_ordem_do_menu(request, cliente):
    """Volta ao padrão do sistema (apaga a ordem personalizada do cliente)."""
    with transaction.atomic():
        antes = ", ".join(m.nome for m in modulos_ordenaveis(cliente))
        OrdemMenuCliente.objects.filter(cliente=cliente).delete()
        depois = ", ".join(m.nome for m in modulos_ordenaveis(cliente))
        if antes != depois:
            registrar_alteracoes(request, cliente, [{"campo": "ordem_menu", "rotulo": "Ordem dos módulos no menu", "antes": antes, "depois": depois}], modulo="modulo")
    return modulos_ordenaveis(cliente)


def salvar_ordem_do_catalogo(request, ids_em_ordem):
    """Grava a ordem PADRÃO do sistema (`Modulo.ordem`) a partir da lista do catálogo. `ids_em_ordem` precisa conter exatamente todos os
    módulos. A sequência reaproveita os valores de ordem já usados (em ordem crescente, forçados a distintos), então a posição relativa
    dentro de cada seção do menu acompanha o que foi arrastado. Clientes com ordem personalizada não são afetados."""
    atuais = list(Modulo.objects.all())
    try:
        ids = [int(i) for i in ids_em_ordem]
    except (TypeError, ValueError):
        raise RegraNegocioError("Ordem inválida.") from None
    if len(set(ids)) != len(ids) or set(ids) != {m.pk for m in atuais}:
        raise RegraNegocioError("A lista de módulos mudou. Recarregue a página e tente de novo.")
    por_id = {m.pk: m for m in atuais}
    lugares = sorted(m.ordem for m in atuais)
    estritos = []
    for valor in lugares:
        estritos.append(max(valor, estritos[-1] + 1) if estritos else valor)
    lugares = estritos
    with transaction.atomic():
        antes = ", ".join(m.nome for m in sorted(atuais, key=lambda m: (m.ordem, m.nome)))
        for ordem, modulo_id in zip(lugares, ids):
            if por_id[modulo_id].ordem != ordem:
                Modulo.objects.filter(pk=modulo_id).update(ordem=ordem)
        depois = ", ".join(por_id[i].nome for i in ids)
        if antes != depois:
            registrar_alteracoes(request, por_id[ids[0]], [{"campo": "ordem_catalogo", "rotulo": "Ordem dos módulos no catálogo", "antes": antes, "depois": depois}], modulo="modulo")
    return list(Modulo.objects.all())

