from django.db import models


class Categoria(models.Model):
    """Categoria usada para organizar os Insumos de um Cliente.

    Ganhou o campo `cliente` na Fase 5 (junto com o CRUD completo de
    Insumos): sem ele, um Insumo poderia referenciar uma Categoria de
    OUTRO cliente, quebrando o isolamento multi-tenant. A tabela ainda não
    tinha nenhum registro quando este campo foi adicionado, então não foi
    necessária nenhuma migração de dados.
    """

    nome = models.CharField(max_length=150, verbose_name="Nome")
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.PROTECT,
        related_name="categorias",
        verbose_name="Cliente",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ["nome"]
        # Mesmo nome de categoria pode existir em clientes diferentes, mas
        # não duplicado dentro do mesmo cliente.
        unique_together = ("cliente", "nome")

    def __str__(self):
        return self.nome
