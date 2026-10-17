"""Metadados das imagens guardadas no Cloudflare R2 (os bytes NUNCA ficam no banco).

- Imagem: um objeto original no R2 + a quem ele pertence (tenant, tipo de entidade e ID da entidade).
- TarifaImagens: tarifas oficiais usadas na ESTIMATIVA de custo (editáveis só pelo proprietário da plataforma).
- UsoImagens: contadores diários de operações no R2 (base do painel de custos).
- TransformacaoUnica: combinação imagem+variante+mês já servida (uma "transformação única" faturável).
"""
import uuid

from django.conf import settings
from django.db import models


class Imagem(models.Model):
    PENDENTE, ATIVA, PARA_EXCLUIR, EXCLUIDA = "pendente", "ativa", "para_excluir", "excluida"
    ESTADO_CHOICES = [
        (PENDENTE, "Envio não confirmado"),
        (ATIVA, "Ativa (é a imagem atual da entidade)"),
        (PARA_EXCLUIR, "Substituída/removida, objeto ainda no R2 (retenção)"),
        (EXCLUIDA, "Objeto removido do R2"),
    ]

    # Identificador público e aleatório: é o que aparece nas URLs (nunca o ID numérico nem a chave do R2).
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Tenant derivado do servidor (nunca do cliente): a unidade dona da entidade.
    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="imagens", verbose_name="Unidade")
    entidade_tipo = models.CharField(max_length=20, verbose_name="Tipo de entidade")  # chave de registro.ENTIDADES
    entidade_id = models.PositiveBigIntegerField(verbose_name="ID da entidade")
    chave = models.CharField(max_length=255, unique=True, editable=False, verbose_name="Chave do objeto no R2")
    mime = models.CharField(max_length=30, verbose_name="Tipo MIME validado")
    tamanho = models.PositiveIntegerField(verbose_name="Tamanho (bytes)")
    largura = models.PositiveIntegerField(verbose_name="Largura (px)")
    altura = models.PositiveIntegerField(verbose_name="Altura (px)")
    checksum = models.CharField(max_length=64, verbose_name="SHA-256 do original")
    estado = models.CharField(max_length=14, choices=ESTADO_CHOICES, default=PENDENTE, db_index=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Enviada por")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    # Quando deixou de ser a imagem atual (base da retenção e do cálculo histórico de armazenamento).
    removida_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Imagem"
        verbose_name_plural = "Imagens"
        constraints = [
            # No máximo uma imagem atual por entidade; a troca desativa a anterior na mesma transação.
            models.UniqueConstraint(fields=["entidade_tipo", "entidade_id"], condition=models.Q(estado="ativa"), name="imagem_uma_ativa_por_entidade"),
        ]
        indexes = [models.Index(fields=["entidade_tipo", "entidade_id", "estado"], name="imagem_entidade_idx"), models.Index(fields=["cliente", "estado"], name="imagem_cliente_estado_idx")]

    def __str__(self):
        return f"Imagem {self.uuid} ({self.entidade_tipo} {self.entidade_id})"


class TarifaImagens(models.Model):
    """Tarifa usada na estimativa. Valores iniciais vêm da documentação oficial (ver `fonte` e `consultado_em`) e podem ser atualizados
    no painel do proprietário SEM mexer no código. É uma ESTIMATIVA: não substitui a fatura da Cloudflare."""

    ARMAZENAMENTO, CLASSE_A, CLASSE_B, TRANSFORMACAO = "armazenamento", "classe_a", "classe_b", "transformacao"
    CHAVE_CHOICES = [
        (ARMAZENAMENTO, "R2 - armazenamento (por GB-mês)"),
        (CLASSE_A, "R2 - operações Classe A (por milhão)"),
        (CLASSE_B, "R2 - operações Classe B (por milhão)"),
        (TRANSFORMACAO, "Images - transformações únicas (por mil)"),
    ]
    chave = models.CharField(max_length=14, choices=CHAVE_CHOICES, unique=True)
    preco = models.DecimalField(max_digits=12, decimal_places=6, verbose_name="Preço por unidade")
    franquia = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name="Franquia gratuita mensal (na mesma unidade)")
    moeda = models.CharField(max_length=3, default="USD")
    fonte = models.URLField(verbose_name="Fonte oficial")
    consultado_em = models.DateField(verbose_name="Data da consulta")
    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "Tarifa de imagens"
        verbose_name_plural = "Tarifas de imagens"

    def __str__(self):
        return f"{self.get_chave_display()}: {self.preco} {self.moeda}"


class UsoImagens(models.Model):
    """Contador diário por unidade e métrica (`classe_a`, `classe_b`, `transformacao`). Medido pela própria aplicação: só enxerga o que
    passa por ela (não vê acessos feitos direto no R2 nem a fatura real)."""

    dia = models.DateField(db_index=True)
    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.CASCADE, related_name="+")
    metrica = models.CharField(max_length=14)
    quantidade = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dia", "cliente", "metrica"], name="uso_imagens_unico")]


class TransformacaoUnica(models.Model):
    """Registra a 1ª vez no mês que uma variante de uma imagem foi servida: é o proxy de "transformação única" (repetições no mês contam 1)."""

    imagem = models.ForeignKey(Imagem, on_delete=models.CASCADE, related_name="+")
    variante = models.CharField(max_length=12)
    mes = models.CharField(max_length=7)  # AAAA-MM
    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["imagem", "variante", "mes"], name="transformacao_unica_mes")]
