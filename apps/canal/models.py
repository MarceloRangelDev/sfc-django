from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService

CEM = Decimal("100")


def _percentual_texto(valor):
    """26.2 -> '26,20%' (no mínimo 2 casas; mais só se o valor tiver)."""
    texto = f"{valor.normalize():f}"
    casas = len(texto.split(".")[1]) if "." in texto else 0
    return f"{valor:.{max(casas, 2)}f}".replace(".", ",") + "%"


class CanalVenda(models.Model):
    """Canal de venda de uma unidade (Cliente matriz/filial): onde o produto é
    vendido e quanto o canal cobra. Usado pela Precificação.

    As taxas ficam em `CanalTaxa` (uma linha por taxa, percentual OU fixa), e os
    totais abaixo são calculados a cada gravação (services.salvar_canal):

        IFOOD | MOTOBOY PLATAFORMA - com antecipação
        taxas: 26,20% + 1,59%  ->  total 27,79%  ->  líquido 72,21%
    """

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="canais_venda", verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Nome do canal")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    # Totais calculados (nunca digitados). O percentual incide sobre o preço de
    # venda; o fixo é um valor em reais por venda, em CENTAVOS.
    taxa_percentual_total = models.DecimalField(
        max_digits=7, decimal_places=4, default=Decimal("0"), editable=False, verbose_name="Total de taxas (%)",
    )
    taxa_fixa_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Total de taxas fixas (centavos)")
    # Exclusão lógica por inativação: canal usado em precificações nunca some.
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Canal de venda"
        verbose_name_plural = "Canais de venda"
        ordering = ["nome", "descricao"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "nome", "descricao"], name="canal_venda_unico"),
        ]
        indexes = [models.Index(fields=["cliente", "ativo"], name="canal_cliente_ativo_idx")]

    def __str__(self):
        return f"{self.nome} | {self.descricao}" if self.descricao else self.nome

    @property
    def liquido_percentual(self):
        """O que sobra de cada 100 do preço depois das taxas percentuais."""
        return CEM - self.taxa_percentual_total

    @property
    def taxa_percentual_formatada(self):
        return _percentual_texto(self.taxa_percentual_total)

    @property
    def liquido_formatado(self):
        return _percentual_texto(self.liquido_percentual)

    @property
    def taxa_fixa_formatada(self):
        return DinheiroService.formatar(self.taxa_fixa_total)

    @property
    def descricao_das_taxas(self):
        """'26,20% + 1,59% + R$ 1,00' (ou 'Sem taxas')."""
        partes = [t.texto for t in self.taxas.all()]
        return " + ".join(partes) if partes else "Sem taxas"


class CanalTaxa(models.Model):
    """Uma taxa do canal: ou PERCENTUAL sobre o preço, ou VALOR FIXO por venda
    (nunca os dois na mesma linha — a restrição do banco garante)."""

    canal = models.ForeignKey(CanalVenda, on_delete=models.CASCADE, related_name="taxas")
    descricao = models.CharField(max_length=100, blank=True, verbose_name="Descrição da taxa")
    percentual = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99.9999"))],
        verbose_name="Percentual (%)",
    )
    valor_fixo = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Valor fixo (centavos)")

    class Meta:
        verbose_name = "Taxa do canal"
        verbose_name_plural = "Taxas do canal"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(percentual__isnull=False, valor_fixo__isnull=True)
                           | models.Q(percentual__isnull=True, valor_fixo__isnull=False)),
                name="canal_taxa_percentual_ou_fixa",
            ),
        ]

    def __str__(self):
        return self.texto

    @property
    def texto(self):
        valor = _percentual_texto(self.percentual) if self.percentual is not None else DinheiroService.formatar(self.valor_fixo)
        return f"{valor} ({self.descricao})" if self.descricao else valor
