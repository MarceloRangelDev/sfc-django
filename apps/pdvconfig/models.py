from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService

LIMITE_TAXA_FIXA = 10**9  # centavos (R$ 10 milhões por transação): teto de segurança


class FormaPagamento(models.Model):
    """Forma de pagamento aceita no PDV de uma unidade (Dinheiro, PIX, Débito, Crédito, outras) e a taxa que ela
    cobra da empresa. A taxa é ESCOLHIDA por forma (`taxa_modo`):

    - `sem_taxa`   : não cobra;
    - `percentual` : só um percentual sobre o valor pago (ex.: crédito 3,5%);
    - `fixa`       : só um valor fixo POR TRANSAÇÃO (cada pagamento lançado é uma transação), ex.: R$ 0,50;
    - `ambas`      : percentual + valor fixo por transação.

    Taxa de um pagamento = round_half_up(valor × percentual / 100) + valor_fixo. É informativa (líquido recebido,
    relatórios do caixa); ela é gravada na linha do pagamento no momento da venda, então alterar a forma depois não
    muda vendas antigas. O canal de venda (apps/canal) continua existindo e é opcional; o PDV não depende dele.
    """

    SEM_TAXA, PERCENTUAL, FIXA, AMBAS = "sem_taxa", "percentual", "fixa", "ambas"
    MODO_CHOICES = [
        (SEM_TAXA, "Sem taxa"), (PERCENTUAL, "Percentual"), (FIXA, "Valor fixo por transação"), (AMBAS, "Percentual + valor fixo por transação"),
    ]
    DINHEIRO, PIX, DEBITO, CREDITO, OUTRO = "dinheiro", "pix", "debito", "credito", "outro"
    TIPO_CHOICES = [(DINHEIRO, "Dinheiro"), (PIX, "PIX"), (DEBITO, "Cartão de débito"), (CREDITO, "Cartão de crédito"), (OUTRO, "Outra")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="formas_pagamento", verbose_name="Unidade")
    nome = models.CharField(max_length=60, verbose_name="Nome")
    # `tipo` define o comportamento (só DINHEIRO gera troco e movimenta o caixa físico), o `nome` é livre (ex.: "Visa crédito").
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=OUTRO, verbose_name="Tipo")
    taxa_modo = models.CharField(max_length=10, choices=MODO_CHOICES, default=SEM_TAXA, db_default=SEM_TAXA, verbose_name="Como a taxa é cobrada")
    taxa_percentual = models.DecimalField(
        max_digits=7, decimal_places=4, default=Decimal("0"), db_default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99.9999"))], verbose_name="Taxa percentual (%)",
    )
    taxa_fixa = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Taxa fixa por transação (centavos)")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("taxa_fixa",)

    class Meta:
        verbose_name = "Forma de pagamento"
        verbose_name_plural = "Formas de pagamento"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="forma_pagamento_nome_unico")]
        indexes = [models.Index(fields=["cliente", "ativo"], name="forma_pgto_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    @property
    def cobra_percentual(self):
        return self.taxa_modo in (self.PERCENTUAL, self.AMBAS)

    @property
    def cobra_fixa(self):
        return self.taxa_modo in (self.FIXA, self.AMBAS)

    def clean(self):
        """O modo escolhido manda: os campos que o modo não usa são zerados; os que ele usa precisam de valor > 0."""
        self.nome = " ".join((self.nome or "").split())
        erros = {}
        if self.taxa_modo in (self.SEM_TAXA, self.FIXA):
            self.taxa_percentual = Decimal("0")
        if self.taxa_modo in (self.SEM_TAXA, self.PERCENTUAL):
            self.taxa_fixa = 0
        if self.cobra_percentual and not self.taxa_percentual:
            erros["taxa_percentual"] = "Informe o percentual da taxa (maior que zero)."
        if self.cobra_fixa and not self.taxa_fixa:
            erros["taxa_fixa"] = "Informe o valor fixo da taxa (maior que zero)."
        if self.taxa_fixa > LIMITE_TAXA_FIXA:
            erros["taxa_fixa"] = "Valor acima do limite permitido."
        if erros:
            raise ValidationError(erros)

    def calcular_taxa(self, valor_centavos):
        """Taxa (centavos) de UMA transação de `valor_centavos`, conforme o modo. Nunca passa do valor pago."""
        taxa = 0
        if self.cobra_percentual:
            taxa += int((Decimal(valor_centavos) * self.taxa_percentual / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if self.cobra_fixa:
            taxa += self.taxa_fixa
        return min(taxa, valor_centavos)

    @property
    def descricao_da_taxa(self):
        percentual = f"{self.taxa_percentual.normalize():f}".replace(".", ",") + "%"
        fixa = DinheiroService.formatar(self.taxa_fixa) + " por transação"
        return {self.SEM_TAXA: "Sem taxa", self.PERCENTUAL: percentual, self.FIXA: fixa, self.AMBAS: f"{percentual} + {fixa}"}[self.taxa_modo]


class Mesa(models.Model):
    """Mesa/comanda física ou lógica da unidade (ex.: "Mesa 7", "Balcão 2"). Uma mesa tem no máximo UMA comanda aberta."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="mesas", verbose_name="Unidade")
    nome = models.CharField(max_length=40, verbose_name="Nome")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativa")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mesa"
        verbose_name_plural = "Mesas"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="mesa_nome_unico")]

    def __str__(self):
        return self.nome


class ConfiguracaoPdv(models.Model):
    """Ajustes do PDV de uma unidade (uma linha por unidade, criada na primeira gravação/uso)."""

    cliente = models.OneToOneField("cliente.Cliente", on_delete=models.CASCADE, related_name="configuracao_pdv", verbose_name="Unidade")
    # Desligue para segmentos/negócios que não controlam estoque de insumos: a venda não dá baixa nem exige saldo.
    controla_estoque = models.BooleanField(default=True, db_default=True, verbose_name="Dar baixa no estoque ao vender")
    # Tipo de comprovante impresso por padrão (a tela permite trocar na hora).
    FORMATO_CHOICES = [("termica80", "Térmica 80 mm"), ("termica58", "Térmica 58 mm"), ("a4", "Folha A4")]
    formato_comprovante = models.CharField(max_length=10, choices=FORMATO_CHOICES, default="termica80", db_default="termica80", verbose_name="Formato padrão do comprovante")
    rodape_comprovante = models.CharField(max_length=120, blank=True, default="", verbose_name="Mensagem no rodapé do comprovante")
    # Pedidos/cozinha (apps/pedido): quando o pedido é CONFIRMADO (entra na fila e baixa o estoque dos seus itens).
    CONFIRMA_AO_LANCAR, CONFIRMA_NO_PAGAMENTO, CONFIRMA_MANUAL = "lancado", "pagamento", "manual"
    CONFIRMACAO_CHOICES = [
        (CONFIRMA_AO_LANCAR, "Ao lançar o pedido"), (CONFIRMA_NO_PAGAMENTO, "Quando a venda for paga"), (CONFIRMA_MANUAL, "Manualmente (alguém confirma)"),
    ]
    confirmacao_pedido = models.CharField(
        max_length=10, choices=CONFIRMACAO_CHOICES, default=CONFIRMA_NO_PAGAMENTO, db_default=CONFIRMA_NO_PAGAMENTO, verbose_name="Confirmação do pedido",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração do PDV"
        verbose_name_plural = "Configurações do PDV"

    def __str__(self):
        return f"PDV de {self.cliente}"

    @classmethod
    def da_unidade(cls, cliente):
        """Configuração da unidade (cria com os padrões se ainda não existir)."""
        configuracao, _ = cls.objects.get_or_create(cliente=cliente)
        return configuracao
