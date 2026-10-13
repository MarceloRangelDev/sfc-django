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
    # Desativar esconde a categoria dos formulários novos, sem mexer nos
    # registros que já a usam (produtos, fichas, precificações): nada em cascata.
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    # Dados de venda informados pelo usuário (ainda não há módulo de vendas):
    # base do ticket médio e do % do faturamento. Faturamento em CENTAVOS.
    quantidade_vendida = models.PositiveIntegerField(default=0, db_default=0, verbose_name="Quantidade vendida")
    faturamento = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Faturamento")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("faturamento",)

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ["nome"]
        # Mesmo nome de categoria pode existir em clientes diferentes, mas
        # não duplicado dentro do mesmo cliente.
        unique_together = ("cliente", "nome")

    def __str__(self):
        return self.nome

    def em_uso(self):
        """True se algum registro de qualquer tabela referencia a categoria
        (regra comum em web_project/mixins.py::em_uso)."""
        from web_project.mixins import em_uso

        return em_uso(self)
