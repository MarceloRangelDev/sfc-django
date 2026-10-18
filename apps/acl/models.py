# Papéis e permissões reaproveitam django.contrib.auth.models.Group e .Permission
# (ver apps/acl/permissions_matrix.py e apps/acl/views.py). O único model deste
# app guarda as EXCEÇÕES INDIVIDUAIS de um usuário em relação ao papel dele.
from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import models


class PermissaoUsuario(models.Model):
    """Exceção individual: permite ou nega UMA permissão a UM usuário,
    independentemente do papel (ver apps/acl/backends.py para a precedência).
    Sem registro = o usuário apenas herda o que o papel concede."""

    EFEITO_PERMITIR = "permitir"
    EFEITO_NEGAR = "negar"
    EFEITO_CHOICES = [(EFEITO_PERMITIR, "Permitir"), (EFEITO_NEGAR, "Negar")]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="permissoes_individuais")
    permissao = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="+")
    efeito = models.CharField(max_length=10, choices=EFEITO_CHOICES)

    class Meta:
        verbose_name = "Permissão individual"
        verbose_name_plural = "Permissões individuais"
        constraints = [models.UniqueConstraint(fields=["usuario", "permissao"], name="permissao_usuario_unica")]

    def __str__(self):
        return f"{self.usuario} {self.efeito} {self.permissao.codename}"


class PapelConfig(models.Model):
    """Configuração de interface de um papel (Group). `ocultar_menu_lateral`: usuários cujos papéis TODOS têm isto ligado não veem a barra
    lateral esquerda (ex.: perfil "Caixa", que só usa as abas do PDV na tela principal). Superusuários sempre veem o menu."""

    group = models.OneToOneField("auth.Group", on_delete=models.CASCADE, related_name="config_menu")
    ocultar_menu_lateral = models.BooleanField(default=False, db_default=False, verbose_name="Ocultar o menu lateral")

    class Meta:
        verbose_name = "Configuração de papel"
        verbose_name_plural = "Configurações de papéis"

    def __str__(self):
        return f"{self.group} (menu lateral {'oculto' if self.ocultar_menu_lateral else 'visível'})"
