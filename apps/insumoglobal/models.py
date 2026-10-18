"""Cadastro GLOBAL (padrão da plataforma) de insumos: o usuário escolhe o nome já padronizado em vez de digitar variações ("Açúcar" x "Asucar").

Cada Insumo de um cliente pode apontar para um InsumoGlobal (`Insumo.insumo_global`). Insumo digitado que NÃO existe aqui é salvo como
"fora do padrão" (`Insumo.nao_conforme`) e o administrador é notificado para padronizá-lo (ver services.py).
"""
import unicodedata
import uuid

from django.conf import settings
from django.db import models


def normalizar(nome):
    """Chave de comparação: sem acento, minúsculas e espaços únicos ("  AÇÚCAR  refinado" -> "acucar refinado")."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", str(nome or "")) if not unicodedata.combining(c))
    return " ".join(sem_acento.casefold().split())


class InsumoGlobal(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    nome = models.CharField(max_length=150, verbose_name="Nome padrão")
    # Garante um único padrão por nome (ignora acento, caixa e espaços repetidos). Calculado em save().
    nome_normalizado = models.CharField(max_length=150, unique=True, editable=False)
    medida = models.ForeignKey("medida.Medida", null=True, blank=True, on_delete=models.PROTECT, related_name="insumos_globais", verbose_name="Unidade de medida sugerida")
    descricao = models.CharField(max_length=255, blank=True, default="", verbose_name="Descrição")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Insumo global"
        verbose_name_plural = "Insumos globais"
        ordering = ["nome"]
        permissions = [
            ("view_naoconformes", "Ver insumos fora do padrão (não conformidade)"),
            ("padronizar_insumo", "Padronizar insumos: promover a global ou redirecionar para outro padrão"),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        self.nome = " ".join(self.nome.split())
        self.nome_normalizado = normalizar(self.nome)
        super().save(*args, **kwargs)
