from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from web_project.historico import HistoricoImutavelMixin, HistoricoQuerySet
from web_project.regras.dinheiro import DinheiroService

TIPO_RECEITA, TIPO_DESPESA = "receita", "despesa"
TIPO_CATEGORIA_CHOICES = [(TIPO_RECEITA, "Receita"), (TIPO_DESPESA, "Despesa")]

STATUS_ABERTA, STATUS_PARCIAL, STATUS_PAGA, STATUS_CANCELADA = "aberta", "parcial", "paga", "cancelada"


class CategoriaFinanceira(models.Model):
    """Categoria de receita ou despesa de uma unidade (cliente matriz/filial):
    é o agrupamento do DRE. Em uso por contas, só é desativada (nunca apagada)."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="categorias_financeiras", verbose_name="Cliente")
    nome = models.CharField(max_length=100, verbose_name="Nome")
    tipo = models.CharField(max_length=10, choices=TIPO_CATEGORIA_CHOICES, verbose_name="Tipo")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria financeira"
        verbose_name_plural = "Categorias financeiras"
        ordering = ["tipo", "nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "tipo", "nome"], name="categoria_financeira_unica")]
        # Quem pode ver o DRE e o balanço (relatórios gerenciais); linha própria na matriz de papéis.
        permissions = [("view_relatoriofinanceiro", "Ver DRE e balanço")]

    def __str__(self):
        return self.nome


class ContaBase(models.Model):
    """Conta a pagar ou a receber (concretas abaixo): descrição, contraparte
    (fornecedor/cliente), categoria, valor, emissão e vencimento. A baixa
    (pagamento/recebimento, total ou parcial) fica em registros imutáveis;
    `valor_baixado` é o total líquido das baixas, atualizado sob bloqueio de
    linha em services.registrar_baixa. Cancelar = `ativo=False` (só sem baixas)."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="%(class)s_set", verbose_name="Cliente")
    descricao = models.CharField(max_length=150, verbose_name="Descrição")
    contraparte = models.CharField(max_length=150, blank=True, verbose_name="Fornecedor/Cliente")
    categoria = models.ForeignKey(CategoriaFinanceira, on_delete=models.PROTECT, related_name="%(class)s_set", verbose_name="Categoria")
    valor = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], verbose_name="Valor (centavos)")
    emissao = models.DateField(default=timezone.localdate, verbose_name="Emissão")
    vencimento = models.DateField(verbose_name="Vencimento")
    observacao = models.CharField(max_length=255, blank=True, verbose_name="Observação")
    valor_baixado = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor baixado (centavos)")
    ativo = models.BooleanField(default=True, verbose_name="Ativa")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("valor", "valor_baixado")

    class Meta:
        abstract = True
        ordering = ["vencimento", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(valor_baixado__lte=models.F("valor")), name="%(app_label)s_%(class)s_baixado_ate_o_valor"),
        ]

    def __str__(self):
        return self.descricao

    @property
    def saldo(self):
        return self.valor - self.valor_baixado

    @property
    def status(self):
        if not self.ativo:
            return STATUS_CANCELADA
        if self.valor_baixado >= self.valor:
            return STATUS_PAGA
        return STATUS_PARCIAL if self.valor_baixado > 0 else STATUS_ABERTA

    @property
    def atrasada(self):
        return self.status in (STATUS_ABERTA, STATUS_PARCIAL) and self.vencimento < timezone.localdate()

    @property
    def valor_formatado(self):
        return DinheiroService.formatar(self.valor)

    @property
    def baixado_formatado(self):
        return DinheiroService.formatar(self.valor_baixado)

    @property
    def saldo_formatado(self):
        return DinheiroService.formatar(self.saldo)


class ContaPagar(ContaBase):
    class Meta(ContaBase.Meta):
        verbose_name = "Conta a pagar"
        verbose_name_plural = "Contas a pagar"
        indexes = [models.Index(fields=["cliente", "vencimento"], name="contapagar_cli_venc_idx")]


class ContaReceber(ContaBase):
    class Meta(ContaBase.Meta):
        verbose_name = "Conta a receber"
        verbose_name_plural = "Contas a receber"
        indexes = [models.Index(fields=["cliente", "vencimento"], name="contareceber_cli_venc_idx")]


class BaixaBase(HistoricoImutavelMixin, models.Model):
    """Pagamento/recebimento (`baixa`) ou seu `estorno` de uma conta. IMUTÁVEL:
    um lançamento errado é corrigido por um estorno, nunca editado ou apagado."""

    BAIXA, ESTORNO = "baixa", "estorno"
    TIPO_CHOICES = [(BAIXA, "Baixa"), (ESTORNO, "Estorno")]

    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=BAIXA, verbose_name="Tipo")
    valor = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], verbose_name="Valor (centavos)")
    data = models.DateField(default=timezone.localdate, verbose_name="Data")
    observacao = models.CharField(max_length=255, blank=True, verbose_name="Observação")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Responsável")
    responsavel_nome = models.CharField(max_length=150, blank=True, verbose_name="Responsável (nome)")
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["-data", "-pk"]

    @property
    def sinal(self):
        return 1 if self.tipo == self.BAIXA else -1

    @property
    def valor_formatado(self):
        return DinheiroService.formatar(self.valor)


class BaixaPagar(BaixaBase):
    conta = models.ForeignKey(ContaPagar, on_delete=models.PROTECT, related_name="baixas", verbose_name="Conta")
    # Preenchido só nos ESTORNOS: a baixa que ele desfaz (OneToOne: cada baixa é estornada no máximo uma vez).
    estorno_de = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="estornada_por", verbose_name="Estorna a baixa")

    class Meta(BaixaBase.Meta):
        verbose_name = "Baixa de conta a pagar"
        verbose_name_plural = "Baixas de contas a pagar"
        indexes = [models.Index(fields=["data"], name="baixapagar_data_idx")]


class BaixaReceber(BaixaBase):
    conta = models.ForeignKey(ContaReceber, on_delete=models.PROTECT, related_name="baixas", verbose_name="Conta")
    # Preenchido só nos ESTORNOS: a baixa que ele desfaz (OneToOne: cada baixa é estornada no máximo uma vez).
    estorno_de = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="estornada_por", verbose_name="Estorna a baixa")

    class Meta(BaixaBase.Meta):
        verbose_name = "Baixa de conta a receber"
        verbose_name_plural = "Baixas de contas a receber"
        indexes = [models.Index(fields=["data"], name="baixareceber_data_idx")]


class AlertaFinanceiro(models.Model):
    """Registra que um alerta de vencimento já foi notificado hoje (uma
    notificação por conta por tipo por dia — idempotência do comando)."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.CASCADE, related_name="alertas_financeiros")
    tipo = models.CharField(max_length=20)  # pagar_vencendo, pagar_atrasada, receber_vencendo, receber_atrasada
    referencia = models.PositiveBigIntegerField(help_text="Id da conta.")
    data = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cliente", "tipo", "referencia", "data"], name="alerta_financeiro_unico_por_dia")]
