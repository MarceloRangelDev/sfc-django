"""Assinatura do sistema (cobrança recorrente via Pagar.me). Dinheiro em CENTAVOS; datas com fuso.

Quem assina é o CLIENTE RAIZ (a matriz): filiais são cobertas pela assinatura da matriz. Nenhum dado de cartão é guardado:
o navegador tokeniza no provedor (ver README_PAGARME.md) e o servidor só recebe o token de uso único.
"""
from django.db import models

from web_project.regras.dinheiro import DinheiroService


class Plano(models.Model):
    """Plano vendido (catálogo da PLATAFORMA, sem tenant). Os valores ficam no banco — não no código — e podem ser
    alterados pelo administrador; `pagarme_plan_id` é preenchido ao sincronizar com o provedor."""

    MENSAL, ANUAL = "mensal", "anual"
    INTERVALO_CHOICES = [("month", "Mês"), ("year", "Ano")]

    slug = models.SlugField(max_length=30, unique=True)
    nome = models.CharField(max_length=64)
    valor = models.PositiveBigIntegerField(verbose_name="Valor (centavos)")
    intervalo = models.CharField(max_length=5, choices=INTERVALO_CHOICES, default="month")
    intervalo_qtd = models.PositiveSmallIntegerField(default=1)
    pagarme_plan_id = models.CharField(max_length=60, blank=True, default="")
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plano"
        verbose_name_plural = "Planos"
        ordering = ["valor"]

    def __str__(self):
        return self.nome

    @property
    def valor_reais(self):
        return DinheiroService.formatar(self.valor)

    @property
    def meses(self):
        """Duração de um ciclo em meses (usada para calcular a vigência)."""
        return self.intervalo_qtd * (12 if self.intervalo == "year" else 1)


class Assinatura(models.Model):
    """Assinatura do tenant (uma por cliente raiz). Estados e transições: ver services.py."""

    PENDENTE, ATIVA, EM_ATRASO, BLOQUEADA, CANCELADA, EXPIRADA = "pendente", "ativa", "em_atraso", "bloqueada", "cancelada", "expirada"
    STATUS_CHOICES = [
        (PENDENTE, "Aguardando 1º pagamento"), (ATIVA, "Ativa"), (EM_ATRASO, "Em atraso (carência)"),
        (BLOQUEADA, "Bloqueada"), (CANCELADA, "Cancelada"), (EXPIRADA, "Expirada"),
    ]
    BOLETO, CARTAO = "boleto", "credit_card"
    FORMA_CHOICES = [(BOLETO, "Boleto"), (CARTAO, "Cartão de crédito")]

    cliente = models.OneToOneField("cliente.Cliente", on_delete=models.PROTECT, related_name="assinatura")
    plano = models.ForeignKey(Plano, on_delete=models.PROTECT, related_name="assinaturas")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDENTE, db_index=True)
    forma_pagamento = models.CharField(max_length=12, choices=FORMA_CHOICES, default=BOLETO)
    pagarme_subscription_id = models.CharField(max_length=60, blank=True, default="", db_index=True)
    iniciada_em = models.DateTimeField(auto_now_add=True)
    # Até quando o período pago vale. Cancelada com vigência futura mantém o acesso até essa data.
    vigente_ate = models.DateTimeField(null=True, blank=True)
    proxima_cobranca_em = models.DateTimeField(null=True, blank=True)
    em_atraso_desde = models.DateTimeField(null=True, blank=True)
    bloqueada_em = models.DateTimeField(null=True, blank=True)
    cancelada_em = models.DateTimeField(null=True, blank=True)
    aviso_vencimento_em = models.DateTimeField(null=True, blank=True)  # quando o aviso de renovação saiu (uma vez por ciclo)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Assinatura"
        verbose_name_plural = "Assinaturas"
        permissions = [("gerenciar_assinatura", "Assinar, trocar forma de pagamento e cancelar a assinatura")]

    def __str__(self):
        return f"{self.cliente} — {self.get_status_display()}"


class Cobranca(models.Model):
    """Fatura (invoice) da assinatura. Só dados não sensíveis: ids do provedor, valor, forma, status, datas."""

    PENDENTE, PAGA, FALHOU, CANCELADA = "pendente", "paga", "falhou", "cancelada"
    STATUS_CHOICES = [(PENDENTE, "Pendente"), (PAGA, "Paga"), (FALHOU, "Falhou"), (CANCELADA, "Cancelada")]

    assinatura = models.ForeignKey(Assinatura, on_delete=models.PROTECT, related_name="cobrancas")
    pagarme_invoice_id = models.CharField(max_length=60, unique=True)
    pagarme_charge_id = models.CharField(max_length=60, blank=True, default="")
    valor = models.PositiveBigIntegerField(default=0, verbose_name="Valor (centavos)")
    forma_pagamento = models.CharField(max_length=12, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDENTE)
    vencimento = models.DateTimeField(null=True, blank=True)
    pago_em = models.DateTimeField(null=True, blank=True)
    url_pagamento = models.URLField(max_length=500, blank=True, default="")  # página do provedor (2ª via de boleto)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cobrança"
        verbose_name_plural = "Cobranças"
        ordering = ["-criado_em", "-pk"]

    @property
    def valor_reais(self):
        return DinheiroService.formatar(self.valor)


class EventoWebhook(models.Model):
    """Registro de cada webhook recebido. `hook_id` único = idempotência (reprocessar não repete efeitos). Não guarda o corpo
    inteiro (pode ter dados pessoais): só ids e o resultado."""

    hook_id = models.CharField(max_length=80, unique=True)
    tipo = models.CharField(max_length=60)
    recebido_em = models.DateTimeField(auto_now_add=True)
    processado_em = models.DateTimeField(null=True, blank=True)
    resultado = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        verbose_name = "Evento de webhook"
        verbose_name_plural = "Eventos de webhook"
        ordering = ["-recebido_em"]


class EventoAssinatura(models.Model):
    """Linha do tempo da assinatura (exibida na aba do cliente). Texto curto, sem dados sensíveis."""

    assinatura = models.ForeignKey(Assinatura, on_delete=models.CASCADE, related_name="eventos")
    tipo = models.CharField(max_length=30)
    descricao = models.CharField(max_length=200)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evento da assinatura"
        verbose_name_plural = "Eventos da assinatura"
        ordering = ["-criado_em", "-pk"]
