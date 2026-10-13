"""Base para registros de HISTÓRICO que só crescem (movimentos de estoque,
baixas de contas...): depois de gravados não podem ser editados nem apagados,
nem em lote. Correções são novos registros de sentido oposto (ajuste, estorno)."""
from django.db import models


class HistoricoImutavelError(Exception):
    """Tentativa de alterar ou apagar um registro do histórico."""


class HistoricoQuerySet(models.QuerySet):
    """Impede alterar/apagar em lote (`update()`/`delete()`)."""

    def update(self, **kwargs):
        raise HistoricoImutavelError("O histórico não pode ser alterado.")

    def delete(self):
        raise HistoricoImutavelError("O histórico não pode ser apagado.")


def corrigir_registro(registro, **campos):
    """ÚNICA porta controlada para corrigir um registro de histórico já gravado.
    Só deve ser chamada por um serviço que valide a regra de negócio, rode numa
    transação com os registros relacionados bloqueados e AUDITE a alteração
    (ver apps/estoque/services.py::editar_movimento). Grava só os campos informados."""
    for nome, valor in campos.items():
        setattr(registro, nome, valor)
    models.Model.save(registro, update_fields=list(campos))  # ignora a trava do mixin de propósito


def corrigir_em_lote(queryset, **campos):
    """Como `corrigir_registro`, para vários registros de uma vez (aceita expressões F()).
    Mesma exigência: uso restrito a serviços que validam e auditam."""
    return models.QuerySet.update(queryset, **campos)


class HistoricoImutavelMixin(models.Model):
    """Mixin abstrato: `save()` só cria e `delete()` é proibido. O model concreto
    deve declarar `objects = HistoricoQuerySet.as_manager()`."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise HistoricoImutavelError("Registros de histórico não podem ser alterados; registre uma correção.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise HistoricoImutavelError("Registros de histórico não podem ser apagados; registre uma correção.")
