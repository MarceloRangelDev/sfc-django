from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from web_project.regras.dinheiro import DinheiroService

LIMITE_OBSERVACAO_ITEM = 255  # observação por item/preço: validada no servidor (formulários) e na interface (maxlength)
LIMITE_OBSERVACAO_GERAL = 1000


class Cotacao(models.Model):
    """Cotação de preços de uma unidade (matriz/filial): itens a comprar, lojas consultadas e os preços de
    cada loja por item. Os itens vêm da lista de compras (`apps/compras`) ou são digitados (inclusive serviços).

    Fluxo de status: RASCUNHO -> ENVIADA -> FECHADA (escolhidos os vencedores por item) e CANCELADA.
    FECHADA e CANCELADA não aceitam mais alterações; uma FECHADA sem conta a pagar gerada pode ser reaberta.
    """

    RASCUNHO, ENVIADA, FECHADA, CANCELADA = "rascunho", "enviada", "fechada", "cancelada"
    STATUS_CHOICES = [(RASCUNHO, "Rascunho"), (ENVIADA, "Enviada"), (FECHADA, "Fechada"), (CANCELADA, "Cancelada")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="cotacoes", verbose_name="Unidade")
    titulo = models.CharField(max_length=150, verbose_name="Título")
    observacoes = models.TextField(max_length=LIMITE_OBSERVACAO_GERAL, blank=True, default="", verbose_name="Observações gerais")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RASCUNHO, db_default=RASCUNHO, editable=False, verbose_name="Status")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    enviada_em = models.DateTimeField(null=True, blank=True, editable=False)
    fechada_em = models.DateTimeField(null=True, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cotação"
        verbose_name_plural = "Cotações"
        ordering = ["-criado_em", "-pk"]
        indexes = [models.Index(fields=["cliente", "status"], name="cotacao_cliente_status_idx")]

    def __str__(self):
        return self.titulo

    @property
    def editavel(self):
        return self.status in (self.RASCUNHO, self.ENVIADA)


class CotacaoLoja(models.Model):
    """Loja/fornecedor consultado na cotação. Endereço, telefone e e-mail são opcionais."""

    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="lojas")
    nome = models.CharField(max_length=150, verbose_name="Nome da loja")
    endereco = models.CharField(max_length=200, blank=True, default="", verbose_name="Endereço")
    telefone = models.CharField(max_length=20, blank=True, default="", verbose_name="Telefone")
    email = models.EmailField(max_length=254, blank=True, default="", verbose_name="E-mail")
    # Preenchido quando a conta a pagar dos itens vencedores desta loja é gerada (impede gerar duas vezes).
    conta_pagar = models.OneToOneField("financeiro.ContaPagar", null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Contato da loja é dado de empresa, mas a trilha só registra que mudou.
    AUDITORIA_SEM_CONTEUDO = ("endereco", "telefone", "email")

    class Meta:
        verbose_name = "Loja da cotação"
        verbose_name_plural = "Lojas da cotação"
        ordering = ["nome", "pk"]
        constraints = [models.UniqueConstraint(fields=["cotacao", "nome"], name="cotacao_loja_nome_unico")]

    def __str__(self):
        return self.nome


class CotacaoItem(models.Model):
    """Item a cotar: de um insumo, avulso ou serviço (`servico`: a marca deixa de ser obrigatória nos preços)."""

    cotacao = models.ForeignKey(Cotacao, on_delete=models.CASCADE, related_name="itens")
    insumo = models.ForeignKey("insumo.Insumo", null=True, blank=True, on_delete=models.PROTECT, related_name="itens_cotacao", verbose_name="Insumo")
    descricao = models.CharField(max_length=150, verbose_name="Item")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
        verbose_name="Quantidade",
    )
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="itens_cotacao", verbose_name="Unidade de medida")
    servico = models.BooleanField(default=False, db_default=False, verbose_name="É um serviço")
    observacao = models.CharField(max_length=LIMITE_OBSERVACAO_ITEM, blank=True, default="", verbose_name="Observação")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Item da cotação"
        verbose_name_plural = "Itens da cotação"
        ordering = ["descricao", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["cotacao", "insumo"], condition=Q(insumo__isnull=False), name="cotacao_item_insumo_unico"),
        ]

    def __str__(self):
        return self.descricao


class CotacaoPreco(models.Model):
    """Oferta de UMA loja para UM item. Preços em centavos POR UNIDADE de `medida` (R$ por kg, por litro...).

    - `preco_unitario`: preço da unidade;
    - `preco_atacado` + `qtd_minima_atacado`: preço menor válido a partir da quantidade mínima;
    - `preco_e_atacado`: o seletor "o preço informado é de atacado" — `preco_unitario` JÁ é o preço de atacado
      (a loja só vende assim); `qtd_minima_atacado` (opcional) é então a quantidade mínima exigida.
    A regra de qual preço vale e o comparativo ficam em apps/cotacao/services.py.
    """

    item = models.ForeignKey(CotacaoItem, on_delete=models.CASCADE, related_name="precos")
    loja = models.ForeignKey(CotacaoLoja, on_delete=models.CASCADE, related_name="precos")
    marca = models.CharField(max_length=60, blank=True, default="", verbose_name="Marca")
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="precos_cotacao", verbose_name="Unidade de medida")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
        verbose_name="Quantidade",
    )
    preco_unitario = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], verbose_name="Preço unitário")
    preco_atacado = models.PositiveBigIntegerField(null=True, blank=True, validators=[MinValueValidator(1)], verbose_name="Preço no atacado")
    preco_e_atacado = models.BooleanField(default=False, db_default=False, verbose_name="O preço informado é de atacado")
    qtd_minima_atacado = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))],
        verbose_name="Quantidade mínima para atacado",
    )
    observacao = models.CharField(max_length=LIMITE_OBSERVACAO_ITEM, blank=True, default="", verbose_name="Observação")
    # Vencedor do item (no máximo um por item; ver constraint e services.escolher_preco).
    escolhido = models.BooleanField(default=False, db_default=False, editable=False, verbose_name="Escolhido")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("preco_unitario", "preco_atacado")

    class Meta:
        verbose_name = "Preço da cotação"
        verbose_name_plural = "Preços da cotação"
        ordering = ["loja__nome", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["item", "loja"], name="cotacao_preco_item_loja_unico"),
            models.UniqueConstraint(fields=["item"], condition=Q(escolhido=True), name="cotacao_preco_um_vencedor_por_item"),
        ]

    def __str__(self):
        return f"{self.loja} - {self.item}"

    @property
    def preco_unitario_formatado(self):
        return DinheiroService.formatar(self.preco_unitario)
