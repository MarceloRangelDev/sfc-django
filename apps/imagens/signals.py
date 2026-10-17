"""Quando uma entidade com imagem é EXCLUÍDA, a imagem deixa de ser servida e segue a retenção (não é apagada na hora, para permitir
recuperação). Inativar/desativar o registro NÃO mexe na imagem."""
from django.apps import apps
from django.db.models.signals import post_delete

from . import registro, servico


def _ao_excluir(sender, instance, **kwargs):
    tipo = registro.tipo_do_modelo(sender)
    if tipo:
        servico.desvincular(tipo, instance.pk)


for _reg in registro.ENTIDADES.values():
    post_delete.connect(_ao_excluir, sender=apps.get_model(_reg.modelo), dispatch_uid=f"imagens-{_reg.modelo}")
