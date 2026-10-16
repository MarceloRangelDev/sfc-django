from django.db import models


class Produto(models.Model):
    """Produto vendido por uma unidade (Cliente matriz/filial), organizado em
    uma Categoria. O custo vem das Fichas técnicas (apps/ficha) e o preço de
    venda das Precificações; aqui ficam só os dados cadastrais.

    Exclusão: sem uso é apagado; com fichas/precificações só é desativado
    (web_project/mixins.py::ExcluirOuDesativarView), preservando o histórico."""

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="produtos", verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Nome")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    # PROTECT: a categoria de um produto nunca é apagada por fora (ela só é
    # desativada quando em uso — ver apps/categoria/views.py).
    categoria = models.ForeignKey(
        "categoria.Categoria", on_delete=models.PROTECT, related_name="produtos", verbose_name="Categoria",
    )
    # EAN do fabricante ou o código interno gerado no cadastro (ver codigos.py); lido no PDV por leitor de barras/QR.
    codigo_barras = models.CharField(max_length=32, blank=True, default="", db_default="", verbose_name="Código de barras")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Produto"
        verbose_name_plural = "Produtos"
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        indexes = [models.Index(fields=["cliente", "ativo"], name="produto_cliente_ativo_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "codigo_barras"], condition=~models.Q(codigo_barras=""), name="produto_codigo_unico_por_unidade",
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.codigo_barras:  # produto novo (ou sem código): gera o código interno a partir do id
            from .codigos import codigo_interno

            self.codigo_barras = codigo_interno(self.pk)
            Produto.objects.filter(pk=self.pk).update(codigo_barras=self.codigo_barras)
