from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.processado.models import LIMITE_OBSERVACOES, LIMITE_RECEITA
from web_project.regras.dinheiro import DinheiroService


class Ficha(models.Model):
    """Ficha técnica de um produto: os insumos (inclusive os gerados por
    Processados) e as quantidades usadas, com o custo de cada item e o total.

    O custo é CALCULADO (apps/ficha/services.py) e recalculado sempre que um
    insumo muda — o motor de apps/processado/recalculo.py chama o gancho
    registrado em apps.py. Um produto pode ter mais de uma ficha (variações)."""

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="fichas", verbose_name="Cliente",
    )
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="fichas", verbose_name="Produto")
    nome = models.CharField(max_length=150, verbose_name="Nome da ficha")
    # Soma dos custos dos itens, em centavos (calculada em services.recalcular_ficha).
    custo_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo total (centavos)")
    # Receita (modo de preparo) e observações: texto simples (quebras de linha e listas com "- ";
    # nunca HTML). Só quem tem a permissão `ver_receita` vê ou edita; a auditoria registra apenas
    # que o texto mudou, sem o conteúdo (ver AUDITORIA_SEM_CONTEUDO).
    receita = models.TextField(max_length=LIMITE_RECEITA, blank=True, default="", verbose_name="Receita / modo de preparo")
    observacoes = models.TextField(max_length=LIMITE_OBSERVACOES, blank=True, default="", verbose_name="Observações")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("custo_total",)
    # Campos de texto longo: a auditoria só diz que mudaram (receita é segredo do negócio).
    AUDITORIA_SEM_CONTEUDO = ("receita", "observacoes")

    class Meta:
        verbose_name = "Ficha Técnica"
        verbose_name_plural = "Fichas Técnicas"
        permissions = [("ver_receita", "Ver e editar receita e observações")]
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        indexes = [models.Index(fields=["cliente", "ativo"], name="ficha_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    @property
    def tem_texto_extra(self):
        """True se há receita ou observações (decide se a exportação pergunta se deve incluí-las)."""
        return bool(self.receita or self.observacoes)

    @property
    def custo_total_formatado(self):
        return DinheiroService.formatar(self.custo_total)


class FichaItem(models.Model):
    """Linha da ficha: quanto de um insumo é usado por receita/porção."""

    ficha = models.ForeignKey(Ficha, on_delete=models.CASCADE, related_name="itens")
    # PROTECT: insumo usado numa ficha não é apagado (as telas/API bloqueiam
    # antes: processado.services.mensagem_insumo_em_uso).
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="usos_em_fichas")
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="itens_de_ficha")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
    )
    # Custo desta quantidade, em centavos (calculado a cada recálculo).
    custo = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "Item da ficha"
        verbose_name_plural = "Itens da ficha"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["ficha", "insumo"], name="ficha_item_insumo_unico")]

    def __str__(self):
        return f"{self.insumo} ({self.quantidade})"

    @property
    def custo_formatado(self):
        return DinheiroService.formatar(self.custo)


class FichaMaoDeObra(models.Model):
    """Mão de obra cadastrada (colaborador) usada na ficha de um SERVIÇO: quantas horas dele cada execução consome. O custo é
    valor da hora do colaborador × horas, calculado no servidor (services.custo_da_mao_de_obra) e refeito quando o valor da hora
    muda. Só existe para segmentos que usam mão de obra (Serviço e Produto e Serviço); nada aqui é obrigatório: uma ficha pode ter só
    insumos, só mão de obra, os dois ou, nesses segmentos, nenhum."""

    ficha = models.ForeignKey(Ficha, on_delete=models.CASCADE, related_name="mao_de_obra")
    # PROTECT: colaborador usado numa ficha não é apagado (a tela de colaboradores só o desativa).
    colaborador = models.ForeignKey("maodeobra.Colaborador", on_delete=models.PROTECT, related_name="usos_em_fichas")
    horas = models.DecimalField(
        max_digits=7, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"), message="As horas devem ser maiores que zero.")],
    )
    # Custo destes colaborador × horas, em centavos (calculado a cada recálculo).
    custo = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "Mão de obra da ficha"
        verbose_name_plural = "Mão de obra da ficha"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["ficha", "colaborador"], name="ficha_mao_de_obra_unica")]

    def __str__(self):
        return f"{self.colaborador} ({self.horas} h)"

    @property
    def custo_formatado(self):
        return DinheiroService.formatar(self.custo)
