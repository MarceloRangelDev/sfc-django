from django.db import models


class Modulo(models.Model):
    """Um módulo é uma tela/funcionalidade de negócio que pode ser
    habilitada ou desabilitada por Cliente (ex: Insumos, Receitas). O
    `slug` é o identificador estável usado no código (urls.py, menu,
    decorators) — não deve ser alterado depois de criado."""

    nome = models.CharField(max_length=100, verbose_name="Nome")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Identificador")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    icone = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ícone",
        help_text="Classe do ícone (Tabler Icons), ex: 'ti tabler-app-window'.",
    )
    # Interruptor geral: um módulo inativo fica indisponível para TODOS os
    # clientes, mesmo que algum ClienteModulo individual esteja marcado
    # como ativo (útil para desligar uma funcionalidade em manutenção).
    ativo = models.BooleanField(default=True, verbose_name="Disponível no sistema")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Módulo"
        verbose_name_plural = "Módulos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class ClienteModulo(models.Model):
    """Relação entre um Cliente e um Módulo: define se aquele módulo está
    habilitado especificamente para aquele Cliente."""

    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.CASCADE,
        related_name="modulos_habilitados",
    )
    modulo = models.ForeignKey(
        Modulo,
        on_delete=models.CASCADE,
        related_name="clientes_habilitados",
    )
    ativo = models.BooleanField(default=True, verbose_name="Habilitado")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Módulo do cliente"
        verbose_name_plural = "Módulos do cliente"
        unique_together = ("cliente", "modulo")

    def __str__(self):
        return f"{self.modulo.nome} - {self.cliente.nome_razao_social}"
