from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class ItemCompra(models.Model):
    """Item da lista de compras de uma unidade (matriz/filial).

    Dois tipos de item, ambos editáveis e removíveis (com auditoria):
    - de INSUMO (`insumo` preenchido): nasce de uma sugestão do estoque ou é escolhido à mão;
      há no máximo um por insumo na lista (a quantidade se ajusta editando o item);
    - AVULSO (`insumo` vazio): texto livre, inclusive SERVIÇOS (a cotação aceita os dois tipos).

    A quantidade é na `medida` informada (insumos: mesma dimensão da unidade do insumo).
    """

    ORIGEM_SUGESTAO, ORIGEM_MANUAL = "sugestao", "manual"
    ORIGEM_CHOICES = [(ORIGEM_SUGESTAO, "Sugestão do estoque"), (ORIGEM_MANUAL, "Manual")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="itens_compra", verbose_name="Unidade")
    insumo = models.ForeignKey("insumo.Insumo", null=True, blank=True, on_delete=models.PROTECT, related_name="itens_compra", verbose_name="Insumo")
    # Nome mostrado: o do insumo (copiado ao gravar, para a lista continuar legível) ou o texto livre.
    descricao = models.CharField(max_length=150, verbose_name="Item")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
        verbose_name="Quantidade",
    )
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="itens_compra", verbose_name="Unidade de medida")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação")
    origem = models.CharField(max_length=10, choices=ORIGEM_CHOICES, default=ORIGEM_MANUAL, db_default=ORIGEM_MANUAL, verbose_name="Origem")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Item de compra"
        verbose_name_plural = "Itens de compra"
        ordering = ["descricao", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "insumo"], condition=Q(insumo__isnull=False), name="item_compra_insumo_unico"),
        ]
        indexes = [models.Index(fields=["cliente", "origem"], name="item_compra_cliente_origem_idx")]

    def __str__(self):
        return self.descricao
