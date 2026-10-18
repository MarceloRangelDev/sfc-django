"""Quadro Kanban simples (colunas configuráveis + cartões) de um Cliente. Somente administrador global (ver services.py)."""
from django.conf import settings
from django.db import models


class Coluna(models.Model):
    """Coluna do quadro do Cliente. `ordem` é a posição (0, 1, 2...), sempre renumerada pelo serviço."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="kanban_colunas")
    nome = models.CharField(max_length=80, verbose_name="Nome")
    ordem = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Coluna do Kanban"
        verbose_name_plural = "Colunas do Kanban"
        ordering = ["ordem", "pk"]
        permissions = [("mover_cartao", "Mover cartões do Kanban")]

    def __str__(self):
        return self.nome


class Cartao(models.Model):
    """Cartão do quadro. `cliente` é redundante com a coluna de propósito: toda consulta filtra direto por tenant."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="kanban_cartoes")
    coluna = models.ForeignKey(Coluna, on_delete=models.PROTECT, related_name="cartoes")
    titulo = models.CharField(max_length=150, verbose_name="Título")
    descricao = models.TextField(blank=True, default="", max_length=2000, verbose_name="Descrição")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Responsável")
    prazo = models.DateField(null=True, blank=True, verbose_name="Prazo")
    ordem = models.PositiveIntegerField(default=0)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cartão do Kanban"
        verbose_name_plural = "Cartões do Kanban"
        ordering = ["ordem", "pk"]

    def __str__(self):
        return self.titulo
