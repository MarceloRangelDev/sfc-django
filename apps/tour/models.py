from django.conf import settings
from django.db import models


class TourPreferencia(models.Model):
    """Registra que o usuário escolheu "Não mostrar novamente" no tour de uma
    página. `pagina` é só a chave usada em initTour({page: ...}) — não há
    cadastro prévio de páginas: criar um tour novo não exige migration."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tours_ocultos")
    pagina = models.SlugField(max_length=50)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Preferência de tour"
        verbose_name_plural = "Preferências de tour"
        constraints = [models.UniqueConstraint(fields=["usuario", "pagina"], name="tour_usuario_pagina_unico")]

    def __str__(self):
        return f"{self.usuario} - {self.pagina}"
