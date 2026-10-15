from django.conf import settings
from django.db import models
from django.db.models import Q

from web_project.historico import HistoricoImutavelMixin, HistoricoQuerySet


class Pedido(models.Model):
    """Pedido de produção (cozinha) ligado a uma VENDA do PDV (`apps/pdv`): a venda cuida de preço, pagamento, estoque e
    financeiro; o pedido cuida da FILA DE PRODUÇÃO. Uma comanda de mesa pode gerar vários pedidos (rodadas).

    Estados e transições válidas (services.py):

        NOVO -> CONFIRMADO -> EM_PREPARO -> PRONTO -> ENTREGUE
        (volta permitida: PRONTO -> EM_PREPARO e EM_PREPARO -> CONFIRMADO)   (CANCELADO a partir de qualquer estado ativo)

    Quando o pedido é CONFIRMADO (regra de confirmação configurável por unidade) o ESTOQUE dos seus itens é baixado, uma
    única vez. Cada mudança de estado grava um evento imutável (quem, quando, de/para).
    """

    NOVO, CONFIRMADO, EM_PREPARO, PRONTO, ENTREGUE, CANCELADO = "novo", "confirmado", "em_preparo", "pronto", "entregue", "cancelado"
    STATUS_CHOICES = [
        (NOVO, "Novo"), (CONFIRMADO, "Confirmado"), (EM_PREPARO, "Em preparo"), (PRONTO, "Pronto"), (ENTREGUE, "Entregue"), (CANCELADO, "Cancelado"),
    ]
    ATIVOS = (NOVO, CONFIRMADO, EM_PREPARO, PRONTO)
    BALCAO, MESA, RETIRADA, ENTREGA = "balcao", "mesa", "retirada", "entrega"
    TIPO_CHOICES = [(BALCAO, "Balcão"), (MESA, "Mesa"), (RETIRADA, "Retirada"), (ENTREGA, "Entrega")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="pedidos", verbose_name="Unidade")
    venda = models.ForeignKey("pdv.Venda", on_delete=models.PROTECT, related_name="pedidos", verbose_name="Venda")
    numero = models.PositiveIntegerField(editable=False, verbose_name="Número")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default=BALCAO, db_default=BALCAO, verbose_name="Tipo")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=NOVO, db_default=NOVO, verbose_name="Status")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação")
    # Idempotência: reenvio (app/tela) com a mesma chave devolve o MESMO pedido.
    chave_idempotencia = models.CharField(max_length=64, null=True, blank=True, editable=False)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmado_em = models.DateTimeField(null=True, blank=True, editable=False)
    preparo_em = models.DateTimeField(null=True, blank=True, editable=False)
    pronto_em = models.DateTimeField(null=True, blank=True, editable=False)
    entregue_em = models.DateTimeField(null=True, blank=True, editable=False)
    cancelado_em = models.DateTimeField(null=True, blank=True, editable=False)
    motivo_cancelamento = models.CharField(max_length=255, blank=True, default="", editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ["-criado_em", "-pk"]
        permissions = [("mover_pedido", "Mover pedidos na fila de produção"), ("cancelar_pedido", "Cancelar pedidos")]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "numero"], name="pedido_numero_unico_por_unidade"),
            models.UniqueConstraint(fields=["cliente", "chave_idempotencia"], condition=Q(chave_idempotencia__isnull=False), name="pedido_chave_idempotencia_unica"),
        ]
        indexes = [models.Index(fields=["cliente", "status"], name="pedido_cliente_status_idx")]

    def __str__(self):
        return f"Pedido #{self.numero}"

    @property
    def ativo(self):
        return self.status in self.ATIVOS


class PedidoItem(models.Model):
    """Item do pedido: aponta para a linha da venda (`venda_item`; nulo se o item foi retirado ao cancelar o pedido) e guarda
    o snapshot do que a cozinha precisa ver."""

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="itens")
    venda_item = models.OneToOneField("pdv.VendaItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="pedido_item")
    descricao = models.CharField(max_length=150, verbose_name="Item")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="Quantidade")
    observacao = models.CharField(max_length=120, blank=True, default="", verbose_name="Observação")

    class Meta:
        verbose_name = "Item do pedido"
        verbose_name_plural = "Itens do pedido"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.descricao} × {self.quantidade.normalize():f}"


class PedidoEvento(HistoricoImutavelMixin, models.Model):
    """Histórico de estados do pedido (imutável): quem moveu, quando, de/para e a nota (ex.: motivo do cancelamento)."""

    pedido = models.ForeignKey(Pedido, on_delete=models.PROTECT, related_name="eventos")
    de_status = models.CharField(max_length=12, blank=True, default="")
    para_status = models.CharField(max_length=12)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    usuario_nome = models.CharField(max_length=150, blank=True, default="")
    nota = models.CharField(max_length=255, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()

    class Meta:
        verbose_name = "Evento do pedido"
        verbose_name_plural = "Eventos do pedido"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.pedido}: {self.de_status or '—'} → {self.para_status}"
