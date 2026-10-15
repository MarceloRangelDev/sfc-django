from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService


class Precificacao(models.Model):
    """Precificação de um produto: parte do custo da ficha técnica, soma o custo
    operacional, aplica as taxas do canal de venda e a formação do lucro, e
    chega ao preço de venda. As fórmulas e a ordem das operações estão em
    apps/precificacao/calculos.py (e no README do app).

    Um produto pode ter VÁRIAS precificações (canais/margens diferentes). Os
    campos "calculados" são gravados a cada salvar e recalculados quando a ficha
    ou o canal mudam (services.recalcular_da_ficha / recalcular_do_canal)."""

    METODO_MARGEM = "margem"
    METODO_MARKUP = "markup"
    METODO_VALOR = "valor"
    METODO_CHOICES = [
        (METODO_MARGEM, "Margem (% sobre o preço de venda)"),
        (METODO_MARKUP, "Markup (% sobre o custo)"),
        (METODO_VALOR, "Valor em reais"),
    ]

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="precificacoes", verbose_name="Cliente",
    )
    # Rótulo GERADO ("Ficha — Canal"): a precificação é identificada pela ficha técnica
    # (produto) e pelo canal, não por um nome digitado (services.preencher).
    nome = models.CharField(max_length=150, blank=True, editable=False, verbose_name="Identificação")
    ficha = models.ForeignKey("ficha.Ficha", on_delete=models.PROTECT, related_name="precificacoes", verbose_name="Ficha técnica")
    # Sempre o produto da ficha (copiado ao salvar/recalcular): permite filtrar e
    # ter várias precificações do mesmo produto sem consultar a ficha.
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="precificacoes", verbose_name="Produto", editable=False)

    # Canal: um canal cadastrado (as taxas abaixo são cópia dele, atualizadas
    # quando ele muda) OU uma configuração personalizada (canal nulo: nome,
    # descrição e taxas digitados aqui).
    canal = models.ForeignKey(
        "canal.CanalVenda", null=True, blank=True, on_delete=models.PROTECT, related_name="precificacoes", verbose_name="Canal de venda",
    )
    canal_nome = models.CharField(max_length=150, blank=True, verbose_name="Nome do canal")
    canal_descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição do canal")
    taxa_percentual = models.DecimalField(
        max_digits=7, decimal_places=4, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99.9999"))],
        verbose_name="Taxas do canal (%)",
    )
    taxa_fixa = models.PositiveBigIntegerField(default=0, verbose_name="Taxas fixas do canal (centavos)")

    # Custo operacional por unidade vendida da categoria do produto (centavos). Vem da DISTRIBUIÇÃO
    # AUTOMÁTICA do módulo Custo operacional (apps/custo/services.py::custo_operacional_da_categoria).
    # `custo_operacional_manual` = True quando o usuário ajustou o valor: aí ele é preservado e nunca
    # sobrescrito; False = acompanha a distribuição (é refeito quando custos, vendas ou ajustes mudam).
    custo_operacional = models.PositiveBigIntegerField(default=0, verbose_name="Custo operacional (centavos)")
    custo_operacional_manual = models.BooleanField(default=False, editable=False, verbose_name="Custo operacional ajustado manualmente")

    # Formação do lucro: o método diz qual dos dois campos vale.
    metodo_lucro = models.CharField(max_length=10, choices=METODO_CHOICES, default=METODO_MARGEM, verbose_name="Método do lucro")
    percentual_lucro = models.DecimalField(
        max_digits=9, decimal_places=4, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Margem ou markup (%)",
    )
    valor_lucro = models.PositiveBigIntegerField(default=0, verbose_name="Lucro em reais (centavos)")

    # --- Calculados (nunca digitados) ---
    custo_ficha = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo da ficha (centavos)")
    custo_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo antes do canal e do lucro (centavos)")
    taxas_percentuais_valor = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Taxas percentuais em reais (centavos)")
    lucro = models.BigIntegerField(default=0, editable=False, verbose_name="Lucro (centavos)")
    preco_venda = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Preço de venda (centavos)")
    cmv_percentual = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0"), editable=False, verbose_name="CMV (%)")

    # Esta precificação fornece o PREÇO e a FICHA usados pelo PDV para vender o produto (no máximo uma ativa por produto;
    # ao marcar uma, as outras do mesmo produto são desmarcadas — services.salvar_precificacao).
    usar_no_pdv = models.BooleanField(default=False, db_default=False, verbose_name="Usar no PDV")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("taxa_fixa", "custo_operacional", "valor_lucro", "custo_ficha", "custo_total",
                            "taxas_percentuais_valor", "lucro", "preco_venda")

    class Meta:
        verbose_name = "Precificação"
        verbose_name_plural = "Precificações"
        ordering = ["nome", "pk"]
        indexes = [models.Index(fields=["cliente", "ativo"], name="precificacao_cliente_ativo_idx")]
        constraints = [
            models.UniqueConstraint(fields=["produto"], condition=models.Q(usar_no_pdv=True, ativo=True), name="precificacao_uma_para_o_pdv_por_produto"),
        ]

    def __str__(self):
        return self.nome

    @staticmethod
    def _reais(centavos):
        return DinheiroService.formatar(centavos)

    @property
    def preco_venda_formatado(self):
        return self._reais(self.preco_venda)

    @property
    def lucro_formatado(self):
        return self._reais(self.lucro)

    @property
    def custo_total_formatado(self):
        return self._reais(self.custo_total)

    @property
    def custo_ficha_formatado(self):
        return self._reais(self.custo_ficha)

    @property
    def custo_operacional_formatado(self):
        return self._reais(self.custo_operacional)

    @property
    def nome_do_canal(self):
        """'IFOOD | MOTOBOY' (ou só o nome); vazio se não houver canal."""
        return f"{self.canal_nome} | {self.canal_descricao}" if self.canal_nome and self.canal_descricao else self.canal_nome
