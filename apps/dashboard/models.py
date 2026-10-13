from django.conf import settings
from django.db import models


class Presenca(models.Model):
    """Última atividade de cada usuário (uma linha por usuário). "Online" = atividade
    nos últimos `presenca.MINUTOS_ONLINE` minutos. Guarda só o instante: nenhum IP,
    página ou dado pessoal além do vínculo com o usuário."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="presenca")
    ultima_atividade = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "Presença"
        verbose_name_plural = "Presenças"

    def __str__(self):
        return f"{self.usuario} em {self.ultima_atividade:%d/%m/%Y %H:%M}"
