"""Orçamentos: proposta de preço de produtos e/ou serviços de uma unidade, com itens, descontos, totais calculados no servidor, PDF e envio por
e-mail/WhatsApp. Ver apps/orcamento/README.md.

Dinheiro SEMPRE em centavos inteiros (como no resto do sistema); quantidade com 3 casas. Totais NUNCA vêm do cliente: são recalculados em
services.calcular_totais a cada gravação. Isolamento multi-tenant: `cliente` (unidade) em tudo; as telas/API filtram por
`clientes_dos_itens(user, ação)`."""
import secrets

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from decimal import Decimal

from web_project.regras.dinheiro import DinheiroService


def novo_token():
    """Token público do link do PDF (WhatsApp): imprevisível e sem relação com o id do orçamento."""
    return secrets.token_urlsafe(32)


class Orcamento(models.Model):
    RASCUNHO, ENVIADO, APROVADO, RECUSADO, CANCELADO = "rascunho", "enviado", "aprovado", "recusado", "cancelado"
    STATUS_CHOICES = [(RASCUNHO, "Rascunho"), (ENVIADO, "Enviado"), (APROVADO, "Aprovado"), (RECUSADO, "Recusado"), (CANCELADO, "Cancelado")]
    # Só nestes estados o conteúdo pode ser alterado; aprovado/recusado/cancelado são históricos (reabrir = criar outro orçamento).
    EDITAVEIS = (RASCUNHO, ENVIADO)

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="orcamentos", verbose_name="Unidade")
    # Numeração sequencial POR UNIDADE (1, 2, 3...), gerada no servidor.
    numero = models.PositiveIntegerField(editable=False, verbose_name="Número")
    consumidor = models.ForeignKey("consumidor.Consumidor", null=True, blank=True, on_delete=models.SET_NULL, related_name="orcamentos", verbose_name="Cliente final")
    # Dados do destinatário COPIADOS no orçamento (o cadastro do consumidor pode mudar ou ser anonimizado depois).
    destinatario_nome = models.CharField(max_length=150, verbose_name="Destinatário")
    destinatario_email = models.EmailField(max_length=254, blank=True, default="", verbose_name="E-mail do destinatário")
    destinatario_whatsapp = models.CharField(max_length=20, blank=True, default="", verbose_name="WhatsApp do destinatário")
    validade = models.DateField(null=True, blank=True, verbose_name="Válido até")
    observacoes = models.TextField(max_length=1000, blank=True, default="", verbose_name="Observações")
    # Desconto GERAL (além do desconto de cada item), em centavos.
    desconto = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Desconto geral (centavos)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RASCUNHO, db_default=RASCUNHO, verbose_name="Situação")
    # Calculados (nunca digitados):
    subtotal = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Soma dos itens (centavos)")
    desconto_itens = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Descontos dos itens (centavos)")
    total = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Total (centavos)")
    enviado_em = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Enviado em")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("desconto", "subtotal", "desconto_itens", "total")
    # Contatos do destinatário são dados pessoais: a auditoria diz QUE mudaram, não o conteúdo.
    AUDITORIA_SEM_CONTEUDO = ("destinatario_email", "destinatario_whatsapp", "observacoes")

    class Meta:
        verbose_name = "Orçamento"
        verbose_name_plural = "Orçamentos"
        ordering = ["-numero"]
        permissions = [("enviar_orcamento", "Enviar orçamento por e-mail ou WhatsApp")]
        constraints = [models.UniqueConstraint(fields=["cliente", "numero"], name="orcamento_numero_unico_por_unidade")]
        indexes = [models.Index(fields=["cliente", "status"], name="orcamento_cliente_status_idx")]

    def __str__(self):
        return f"Orçamento nº {self.numero}"

    @property
    def editavel(self):
        return self.status in self.EDITAVEIS

    @property
    def total_formatado(self):
        return DinheiroService.formatar(self.total)


class OrcamentoItem(models.Model):
    """Linha do orçamento: produto/serviço do cadastro OU linha avulsa (sem `produto`, só descrição). Nome e tipo ficam copiados."""

    PRODUTO, SERVICO = "produto", "servico"
    TIPO_CHOICES = [(PRODUTO, "Produto"), (SERVICO, "Serviço")]

    orcamento = models.ForeignKey(Orcamento, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("produto.Produto", null=True, blank=True, on_delete=models.PROTECT, related_name="itens_de_orcamento", verbose_name="Produto/serviço")
    tipo = models.CharField(max_length=7, choices=TIPO_CHOICES, default=PRODUTO, db_default=PRODUTO, verbose_name="Tipo")
    descricao = models.CharField(max_length=150, verbose_name="Descrição")
    quantidade = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"), message="A quantidade deve ser maior que zero.")])
    preco_unitario = models.PositiveBigIntegerField(verbose_name="Preço unitário (centavos)")
    desconto = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Desconto (centavos)")
    total = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Total da linha (centavos)")
    ordem = models.PositiveSmallIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = "Item do orçamento"
        verbose_name_plural = "Itens do orçamento"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"{self.descricao} ({self.quantidade})"


class OrcamentoEnvio(models.Model):
    """Registro de cada geração de PDF/impressão e de cada envio (e-mail/WhatsApp), com o resultado. O destino fica MASCARADO (dado pessoal)
    e `itens_omitidos` guarda quais itens ficaram de fora DAQUELA versão (o orçamento salvo não muda por isso)."""

    PDF, EMAIL, WHATSAPP = "pdf", "email", "whatsapp"
    CANAL_CHOICES = [(PDF, "PDF/impressão"), (EMAIL, "E-mail"), (WHATSAPP, "WhatsApp")]
    OK, FALHA = "ok", "falha"

    orcamento = models.ForeignKey(Orcamento, on_delete=models.CASCADE, related_name="envios")
    canal = models.CharField(max_length=8, choices=CANAL_CHOICES)
    status = models.CharField(max_length=5, choices=[(OK, "Concluído"), (FALHA, "Falhou")])
    destino = models.CharField(max_length=80, blank=True, default="", verbose_name="Destino (mascarado)")
    detalhe = models.CharField(max_length=255, blank=True, default="")  # mensagem segura, sem credenciais
    itens_omitidos = models.JSONField(default=list, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Envio de orçamento"
        verbose_name_plural = "Envios de orçamento"
        ordering = ["-criado_em", "-pk"]


class OrcamentoLink(models.Model):
    """Link público (sem login) para baixar o PDF enviado por WhatsApp: token longo, validade curta e escopo do orçamento + itens que
    ficaram de fora naquela versão. É apagado quando o orçamento é alterado, cancelado ou excluído."""

    orcamento = models.ForeignKey(Orcamento, on_delete=models.CASCADE, related_name="links")
    token = models.CharField(max_length=64, unique=True, default=novo_token, editable=False)
    itens_omitidos = models.JSONField(default=list, blank=True)
    expira_em = models.DateTimeField()
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Link do PDF do orçamento"
        verbose_name_plural = "Links do PDF do orçamento"
        indexes = [models.Index(fields=["expira_em"], name="orcamento_link_expira_idx")]
