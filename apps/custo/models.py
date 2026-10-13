from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService

TIPO_FIXO = "fixo"
TIPO_VARIAVEL = "variavel"
TIPO_CHOICES = [(TIPO_FIXO, "Fixo"), (TIPO_VARIAVEL, "Variável")]


class CustoModelo(models.Model):
    """Custo comum (água, energia, aluguel...) usado como atalho no Custo operacional.

    Dois tipos de linha no mesmo catálogo:
    - `cliente` NULO: padrão do SISTEMA, igual para todos, mantido pelo admin do Django
      (apps/custo/admin.py); as unidades só o consultam e o escolhem;
    - `cliente` preenchido: custo comum PRÓPRIO daquela unidade, com CRUD em
      Configurações > Custos comuns (nunca visível para outras unidades).
    Nada é copiado automaticamente para os clientes: a unidade só passa a ter o custo
    quando o adiciona (ver CustoOperacional.modelo), informando o próprio valor."""

    # Dono do custo comum: nulo = padrão do sistema (somente leitura para os clientes).
    cliente = models.ForeignKey(
        "cliente.Cliente", null=True, blank=True, on_delete=models.PROTECT, related_name="custos_comuns", verbose_name="Cliente",
    )
    nome = models.CharField(max_length=100, verbose_name="Nome")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name="Tipo")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    ordem = models.PositiveSmallIntegerField(default=100, verbose_name="Ordem")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True, null=True, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True, null=True, editable=False)

    class Meta:
        verbose_name = "Custo comum"
        verbose_name_plural = "Custos comuns"
        ordering = ["ordem", "nome"]
        constraints = [
            # Nome único entre os padrões do sistema e, separadamente, dentro de cada unidade.
            models.UniqueConstraint(fields=["nome"], condition=models.Q(cliente__isnull=True), name="custo_comum_nome_global"),
            models.UniqueConstraint(fields=["cliente", "nome"], condition=models.Q(cliente__isnull=False), name="custo_comum_nome_por_cliente"),
        ]

    def __str__(self):
        return self.nome

    @property
    def padrao_do_sistema(self):
        return self.cliente_id is None


class CustoOperacional(models.Model):
    """Custo operacional de uma unidade (Cliente matriz/filial): fixo (aluguel,
    internet) ou variável (água, energia), com o valor MENSAL em centavos e a
    distribuição por categoria (CustoRateio). Quanto cada categoria recebe e o
    custo por unidade vendida estão em apps/custo/services.py (e no README)."""

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="custos_operacionais", verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Nome")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=TIPO_FIXO, verbose_name="Tipo")
    # Valor do mês, em CENTAVOS (fixo: o combinado; variável: a média/estimativa do mês).
    valor = models.PositiveBigIntegerField(default=0, verbose_name="Valor mensal (centavos)")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    # Origem opcional no catálogo global (nulo = custo próprio da unidade).
    modelo = models.ForeignKey(
        CustoModelo, null=True, blank=True, on_delete=models.PROTECT, related_name="custos", verbose_name="Custo comum",
    )
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Custo operacional"
        verbose_name_plural = "Custos operacionais"
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        constraints = [
            # A unidade usa cada custo comum no máximo uma vez (sem duplicar o catálogo).
            models.UniqueConstraint(
                fields=["cliente", "modelo"], condition=models.Q(modelo__isnull=False), name="custo_modelo_unico_por_cliente",
            ),
        ]
        indexes = [models.Index(fields=["cliente", "ativo"], name="custo_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    @property
    def valor_formatado(self):
        return DinheiroService.formatar(self.valor)


class CustoAjusteCategoria(models.Model):
    """Ajuste MANUAL do custo operacional de uma categoria, em reais (centavos).

    O sistema calcula sozinho quanto do custo operacional total cabe a cada categoria
    (proporcional às vendas — apps/custo/calculos.py::distribuir_por_vendas). Esta tabela
    guarda só as decisões do usuário: a categoria com linha aqui recebe EXATAMENTE este
    valor e o restante é redistribuído entre as demais. Nada sobrescreve um ajuste em
    silêncio: ele só some quando o usuário pede "Restaurar cálculo automático"."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="ajustes_custo_categoria")
    # PROTECT: categoria com ajuste nunca é apagada (só desativada).
    categoria = models.ForeignKey("categoria.Categoria", on_delete=models.PROTECT, related_name="ajustes_de_custo")
    valor = models.PositiveBigIntegerField(verbose_name="Valor ajustado (centavos)")
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Ajuste do custo por categoria"
        verbose_name_plural = "Ajustes do custo por categoria"
        constraints = [models.UniqueConstraint(fields=["cliente", "categoria"], name="custo_ajuste_categoria_unico")]

    def __str__(self):
        return f"{self.categoria}: {DinheiroService.formatar(self.valor)}"


class CustoRateio(models.Model):
    """LEGADO: parcela do custo atribuída a uma categoria, em % (a soma das parcelas
    de um custo era 100 %). Substituída pela distribuição automática em R$ por vendas
    (CustoAjusteCategoria); a migração 0004 converteu as distribuições existentes em
    ajustes manuais. A tabela é mantida (sem uso) para não perder dados nem quebrar
    o rollback; pode ser removida numa limpeza futura."""

    custo = models.ForeignKey(CustoOperacional, on_delete=models.CASCADE, related_name="rateios")
    # PROTECT: categoria com custo distribuído nunca é apagada (só desativada).
    categoria = models.ForeignKey("categoria.Categoria", on_delete=models.PROTECT, related_name="rateios_de_custo")
    percentual = models.DecimalField(
        max_digits=7, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001")), MaxValueValidator(Decimal("100"))],
        verbose_name="Percentual (%)",
    )

    class Meta:
        verbose_name = "Distribuição do custo"
        verbose_name_plural = "Distribuições do custo"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["custo", "categoria"], name="custo_rateio_categoria_unica")]

    def __str__(self):
        return f"{self.categoria}: {self.percentual}%"
