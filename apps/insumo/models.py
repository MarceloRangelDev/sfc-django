from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


def formatar_moeda_brl(valor):
    """Formata um Decimal como 'R$ 1.234,56' sem depender da configuração
    de locale do sistema operacional (evita formatação inconsistente entre
    ambientes de desenvolvimento e produção)."""
    if valor is None:
        return "-"
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


class Insumo(models.Model):
    """Insumo utilizado nas receitas/produtos de um Cliente.

    Campos monetários e de quantidade usam DecimalField (nunca FloatField)
    para não perder precisão em cálculos financeiros — ver a seção
    "Segurança dos valores" do README. MinValueValidator(0) bloqueia
    valores negativos tanto vindos do formulário quanto de qualquer outro
    código que grave um Insumo diretamente (Django Admin, um script...).
    """

    UNIDADE_KG = "KG"
    UNIDADE_G = "G"
    UNIDADE_L = "L"
    UNIDADE_ML = "ML"
    UNIDADE_UN = "UN"
    UNIDADE_CHOICES = [
        (UNIDADE_KG, "Quilograma (kg)"),
        (UNIDADE_G, "Grama (g)"),
        (UNIDADE_L, "Litro (L)"),
        (UNIDADE_ML, "Mililitro (mL)"),
        (UNIDADE_UN, "Unidade (un)"),
    ]

    # Isolamento multi-tenant: todo Insumo pertence a exatamente um
    # Cliente, e toda consulta/edição/exclusão deve filtrar por ele (ver
    # web_project/mixins.py::ClienteScopedMixin, usado em apps/insumo/views.py).
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.PROTECT,
        related_name="insumos",
        verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Insumo")
    categoria = models.ForeignKey(
        "categoria.Categoria",
        on_delete=models.PROTECT,
        related_name="insumos",
        verbose_name="Categoria",
    )
    unidade_medida = models.CharField(
        max_length=2,
        choices=UNIDADE_CHOICES,
        default=UNIDADE_UN,
        verbose_name="Unidade de medida",
    )
    quantidade = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Quantidade",
    )
    preco = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Preço pago (R$)",
    )
    # Exclusão lógica: "excluir" um insumo só marca ativo=False (ver
    # apps/insumo/views.py::InsumoExcluirView) — o registro permanece no
    # banco (histórico, fichas técnicas que já o referenciam etc.) e some
    # apenas da listagem padrão.
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Cadastrado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    class Meta:
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"
        ordering = ["nome"]
        unique_together = ("cliente", "nome")

    def __str__(self):
        return self.nome

    @property
    def codigo(self):
        """Código de exibição — não é um campo próprio para nunca
        dessincronizar do id real do registro."""
        return f"INS-{self.pk:05d}" if self.pk else ""

    @property
    def preco_por_unidade(self):
        if not self.quantidade:
            return None
        return self.preco / self.quantidade

    @property
    def preco_formatado(self):
        return formatar_moeda_brl(self.preco)

    @property
    def preco_por_unidade_formatado(self):
        return formatar_moeda_brl(self.preco_por_unidade)
