"""Auxiliares para testes automatizados."""
from apps.modulo.models import ClienteModulo, Modulo, Segmento


def habilitar_modulo(cliente, slug, ativo=True):
    """Deixa `slug` disponível para o cliente: coloca o cliente no segmento
    padrão "produto" (criado pelas migrations, com todos os módulos de
    negócio) se ele ainda não tiver segmento, e cria a habilitação."""
    if cliente.segmento_id is None:
        cliente.segmento = Segmento.objects.get(slug="produto")
        cliente.save(update_fields=["segmento"])
    modulo = Modulo.objects.get(slug=slug)
    vinculo, _ = ClienteModulo.objects.update_or_create(cliente=cliente, modulo=modulo, defaults={"ativo": ativo})
    if slug == "pdv":  # as telas do PDV são módulos-filhos: habilitar/desabilitar o PDV acompanha todas
        for filho in Modulo.objects.filter(slug__startswith="pdv-"):
            ClienteModulo.objects.update_or_create(cliente=cliente, modulo=filho, defaults={"ativo": ativo})
    return vinculo
