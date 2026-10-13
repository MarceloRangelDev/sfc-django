from django.conf import settings
from django.db import models


class Notificacao(models.Model):
    """A mensagem (regra de negócio): quem criou, o que diz e para quem foi
    endereçada. O ENVIO por canal (web push hoje, FCM no futuro) fica em
    canais.py; o resultado por pessoa fica em DestinatarioNotificacao."""

    DESTINO_TODOS = "todos"
    DESTINO_SEGMENTO = "segmento"
    DESTINO_USUARIOS = "usuarios"
    DESTINO_CHOICES = [
        (DESTINO_TODOS, "Todos"),
        (DESTINO_SEGMENTO, "Segmento"),
        (DESTINO_USUARIOS, "Usuários específicos"),
    ]

    titulo = models.CharField(max_length=120, verbose_name="Título")
    mensagem = models.TextField(max_length=500, verbose_name="Mensagem")
    # Caminho interno aberto ao clicar (ex.: "/insumo"). Só caminhos do
    # próprio sistema são aceitos (ver forms.py), nunca URLs externas.
    link = models.CharField(max_length=200, blank=True, verbose_name="Link (opcional)")
    tipo_destinatario = models.CharField(max_length=10, choices=DESTINO_CHOICES, verbose_name="Destinatários")
    segmentos = models.ManyToManyField("modulo.Segmento", blank=True, related_name="notificacoes")
    usuarios_alvo = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="notificacoes_alvo")
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="notificacoes_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Notificação"
        verbose_name_plural = "Notificações"
        ordering = ["-criado_em"]

    def __str__(self):
        return self.titulo


class DestinatarioNotificacao(models.Model):
    """Uma notificação entregue a UMA pessoa: guarda o resultado da entrega
    e a leitura. É a "caixa de entrada" do usuário (navbar) e o histórico."""

    ENTREGA_PENDENTE = "pendente"
    ENTREGA_ENTREGUE = "entregue"
    ENTREGA_FALHA = "falha"
    ENTREGA_CHOICES = [
        (ENTREGA_PENDENTE, "Pendente"),
        (ENTREGA_ENTREGUE, "Entregue"),
        (ENTREGA_FALHA, "Não entregue"),
    ]

    notificacao = models.ForeignKey(Notificacao, on_delete=models.CASCADE, related_name="destinatarios")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notificacoes")
    status_entrega = models.CharField(max_length=10, choices=ENTREGA_CHOICES, default=ENTREGA_PENDENTE)
    entregue_em = models.DateTimeField(null=True, blank=True)
    erro_entrega = models.CharField(max_length=255, blank=True)
    lida_em = models.DateTimeField(null=True, blank=True)  # nulo = não lida
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Destinatário da notificação"
        verbose_name_plural = "Destinatários das notificações"
        ordering = ["-criado_em", "-id"]
        constraints = [models.UniqueConstraint(fields=["notificacao", "usuario"], name="destinatario_unico")]
        indexes = [models.Index(fields=["usuario", "lida_em"], name="notif_usuario_lida_idx")]

    def __str__(self):
        return f"{self.notificacao} -> {self.usuario}"

    @property
    def lida(self):
        return self.lida_em is not None
