from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService


class Ficha(models.Model):
    """Ficha técnica de um produto: os insumos (inclusive os gerados por
    Processados) e as quantidades usadas, com o custo de cada item e o total.

    O custo é CALCULADO (apps/ficha/services.py) e recalculado sempre que um
    insumo muda — o motor de apps/processado/recalculo.py chama o gancho
    registrado em apps.py. Um produto pode ter mais de uma ficha (variações)."""

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="fichas", verbose_name="Cliente",
    )
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="fichas", verbose_name="Produto")
    nome = models.CharField(max_length=150, verbose_name="Nome da ficha")
    # Soma dos custos dos itens, em centavos (calculada em services.recalcular_ficha).
    custo_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo total (centavos)")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("custo_total",)

    class Meta:
        verbose_name = "Ficha Técnica"
        verbose_name_plural = "Fichas Técnicas"
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        indexes = [models.Index(fields=["cliente", "ativo"], name="ficha_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    @property
    def custo_total_formatado(self):
        return DinheiroService.formatar(self.custo_total)


class FichaItem(models.Model):
    """Linha da ficha: quanto de um insumo é usado por receita/porção."""

    ficha = models.ForeignKey(Ficha, on_delete=models.CASCADE, related_name="itens")
    # PROTECT: insumo usado numa ficha não é apagado (as telas/API bloqueiam
    # antes: processado.services.mensagem_insumo_em_uso).
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="usos_em_fichas")
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="itens_de_ficha")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
    )
    # Custo desta quantidade, em centavos (calculado a cada recálculo).
    custo = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "Item da ficha"
        verbose_name_plural = "Itens da ficha"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["ficha", "insumo"], name="ficha_item_insumo_unico")]

    def __str__(self):
        return f"{self.insumo} ({self.quantidade})"

    @property
    def custo_formatado(self):
        return DinheiroService.formatar(self.custo)
