from django.conf import settings
from django.db import models


class RegistroAuditoria(models.Model):
    """Trilha de auditoria: quem fez o quê, em qual cliente/módulo, quando
    e com que resultado. Nunca editável pela aplicação depois de criado
    (ver apps/auditoria/admin.py) — um log de auditoria que pode ser
    alterado não serve para nada.

    Preenchido por apps/auditoria/utils.py::registrar(), chamado pelos
    sinais de login/logout (signals.py) e pelas views de negócio (ver
    apps/insumo/views.py para o padrão a seguir em outros módulos).
    """

    RESULTADO_SUCESSO = "sucesso"
    RESULTADO_FALHA = "falha"
    RESULTADO_CHOICES = [
        (RESULTADO_SUCESSO, "Sucesso"),
        (RESULTADO_FALHA, "Falha"),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name="Usuário",
    )
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name="Cliente",
    )
    acao = models.CharField(max_length=50, verbose_name="Ação")
    modulo = models.CharField(max_length=50, blank=True, verbose_name="Módulo")
    registro_id = models.CharField(max_length=50, blank=True, verbose_name="Registro afetado")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Endereço IP")
    resultado = models.CharField(
        max_length=10, choices=RESULTADO_CHOICES, default=RESULTADO_SUCESSO, verbose_name="Resultado"
    )
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Data/hora")

    class Meta:
        verbose_name = "Registro de auditoria"
        verbose_name_plural = "Registros de auditoria"
        ordering = ["-criado_em"]

    def __str__(self):
        quem = self.usuario.username if self.usuario_id else "anônimo"
        return f"{self.acao} ({quem}) em {self.criado_em:%d/%m/%Y %H:%M}"
