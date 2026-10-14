from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from web_project.regras.conversao import (
    DIMENSAO_COMPRIMENTO,
    DIMENSAO_MASSA,
    DIMENSAO_UNIDADE,
    DIMENSAO_VOLUME,
    preco_por_unidade_padrao,
)
from web_project.regras.dinheiro import DinheiroService

# Dimensões aceitas como unidade de saída do processado: massa (g, kg), volume (mL, L), unidade
# e comprimento. O insumo gerado sempre usa a maior grandeza (1 kg, 1 L, 1 un, 1 m).
DIMENSOES_DE_SAIDA = (DIMENSAO_MASSA, DIMENSAO_VOLUME, DIMENSAO_UNIDADE, DIMENSAO_COMPRIMENTO)

# Limites dos textos de receita/observações (validados no servidor, nos formulários da tela e da API).
LIMITE_RECEITA = 5000
LIMITE_OBSERVACOES = 1000


class Processado(models.Model):
    """Receita ou insumo processado de uma unidade (Cliente matriz/filial).

    A ficha técnica (itens) soma o custo dos insumos usados; o resultado vira
    um Insumo "gerado" (campo `insumo`) com nome, rendimento e custo total, que
    aparece na lista geral de Insumos e pode ser usado por outros processados
    (vários níveis). O Insumo gerado só é alterado por aqui — ver
    apps/processado/services.py e o motor de recálculo em recalculo.py.

    Exemplo: 1 kg de camarão (R$ 39,90) rende 500 g limpos -> custo_total
    3990, rendimento 500 g -> R$ 79,80 por kg e fator de rendimento 0,5.
    """

    # Isolamento multi-tenant (ver web_project/mixins.py::ClienteScopedMixin).
    cliente = models.ForeignKey(
        "cliente.Cliente", on_delete=models.PROTECT, related_name="processados", verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Nome")
    rendimento_quantidade = models.DecimalField(
        max_digits=15, decimal_places=4, default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.0001"), message="O rendimento deve ser maior que zero.")],
        verbose_name="Rendimento",
    )
    rendimento_medida = models.ForeignKey(
        "medida.Medida", on_delete=models.PROTECT, related_name="processados_rendimento", verbose_name="Unidade de saída",
    )
    # Calculados em services.recalcular_processado (nunca digitados): soma dos
    # custos dos itens, em centavos, e razão rendimento/quantidade usada
    # (nulo quando os itens não estão na mesma dimensão da saída).
    custo_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Custo total (centavos)")
    fator_rendimento = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, editable=False, verbose_name="Fator de rendimento",
    )
    # Insumo que representa este processado na lista geral de Insumos.
    # PROTECT: o insumo gerado nunca é apagado por fora do processado.
    insumo = models.OneToOneField(
        "insumo.Insumo", null=True, blank=True, editable=False, on_delete=models.PROTECT,
        related_name="processado_origem", verbose_name="Insumo gerado",
    )
    # Exclusão lógica, como nos insumos: preserva histórico e referências.
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
        verbose_name = "Processado"
        verbose_name_plural = "Processados"
        permissions = [("ver_receita", "Ver e editar receita e observações")]
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        indexes = [models.Index(fields=["cliente", "ativo"], name="processado_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    @property
    def tem_texto_extra(self):
        """True se há receita ou observações (decide se a exportação pergunta se deve incluí-las)."""
        return bool(self.receita or self.observacoes)

    @property
    def custo_total_formatado(self):
        return DinheiroService.formatar(self.custo_total)

    @property
    def custo_unitario(self):
        """Custo de 1 kg / 1 L da saída, em centavos (None se não calculável)."""
        try:
            return preco_por_unidade_padrao(self.custo_total, self.rendimento_quantidade, self.rendimento_medida)
        except Exception:  # rendimento inválido: a tela mostra "—"
            return None

    @property
    def custo_unitario_formatado(self):
        return DinheiroService.formatar(self.custo_unitario)

    @property
    def rotulo_custo_unitario(self):
        return f"por {self.rendimento_medida.sigla_preco}"

    @property
    def reducao_percentual(self):
        """Quanto se perde no preparo (ex.: 50 para fator 0,5); None sem fator."""
        if self.fator_rendimento is None:
            return None
        return ((Decimal(1) - self.fator_rendimento) * 100).quantize(Decimal("0.01"))


class ProcessadoItem(models.Model):
    """Linha da ficha técnica de um processado: quanto de um insumo é usado."""

    processado = models.ForeignKey(Processado, on_delete=models.CASCADE, related_name="itens")
    # PROTECT: um insumo usado numa receita não pode ser apagado (ver
    # services.processados_que_usam, que as telas/API consultam antes de excluir).
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="usos_em_processados")
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="itens_de_processado")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
    )
    # Custo desta quantidade, em centavos (calculado a cada recálculo).
    custo = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "Item do processado"
        verbose_name_plural = "Itens do processado"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["processado", "insumo"], name="processado_item_insumo_unico"),
        ]

    def __str__(self):
        return f"{self.insumo} ({self.quantidade})"

    @property
    def custo_formatado(self):
        return DinheiroService.formatar(self.custo)
