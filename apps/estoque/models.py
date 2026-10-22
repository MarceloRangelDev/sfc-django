from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from web_project.historico import HistoricoImutavelError, HistoricoImutavelMixin, HistoricoQuerySet  # noqa: F401 (reexportados)

QUANTIDADE_MINIMA = Decimal("0.0001")


# Lote gerado automaticamente quando o usuário informa a validade sem informar o lote: o
# código "VAL-AAAAMMDD" agrupa tudo que vence no mesmo dia, mantendo o PEPS por validade.
PREFIXO_LOTE_VALIDADE = "VAL-"


def codigo_do_lote_por_validade(validade):
    return f"{PREFIXO_LOTE_VALIDADE}{validade:%Y%m%d}"


# Motivos da baixa manual (insumo e produto). "outro" exige que a observação explique.
MOTIVO_CHOICES = [
    ("perda", "Perda"), ("vencimento", "Vencimento"), ("quebra", "Quebra/avaria"), ("consumo_interno", "Consumo interno"),
    ("inventario", "Acerto de inventário"), ("outro", "Outro"),
]
MOTIVOS = dict(MOTIVO_CHOICES)


class Lote(models.Model):
    """Lote de um insumo numa unidade, com validade opcional e o SALDO atual.

    Todo movimento cai em um lote. O "lote vazio" (codigo = "") representa o
    estoque sem lote informado, um por insumo. O saldo é mantido sob bloqueio de
    linha (select_for_update) dentro da transação de cada movimento, e o banco
    recusa saldo negativo (CheckConstraint): duas saídas simultâneas não deixam
    o estoque inconsistente. `manage.py verificar_estoque` confere o saldo contra
    a soma do histórico."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="lotes_estoque", verbose_name="Cliente")
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="lotes", verbose_name="Insumo")
    codigo = models.CharField(max_length=60, blank=True, verbose_name="Lote")
    validade = models.DateField(null=True, blank=True, verbose_name="Validade")
    # Na unidade de medida do insumo (Insumo.medida); ver services.para_unidade_do_insumo.
    saldo = models.DecimalField(max_digits=15, decimal_places=4, default=Decimal("0"), verbose_name="Saldo")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lote"
        verbose_name_plural = "Lotes"
        ordering = ["validade", "criado_em", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "insumo", "codigo"], name="lote_unico_por_insumo"),
            models.CheckConstraint(condition=models.Q(saldo__gte=0), name="lote_saldo_nao_negativo"),
        ]
        indexes = [models.Index(fields=["cliente", "validade"], name="lote_cliente_validade_idx")]

    def __str__(self):
        return self.codigo_exibicao or "Sem lote"

    @property
    def lote_automatico(self):
        """True para o lote criado só a partir da validade (o usuário não informou lote)."""
        return self.codigo.startswith(PREFIXO_LOTE_VALIDADE) and self.validade is not None

    @property
    def codigo_exibicao(self):
        """O que mostrar na coluna "Lote": vazio quando o lote é só a validade."""
        return "" if self.lote_automatico else self.codigo


class MovimentoEstoque(HistoricoImutavelMixin, models.Model):
    """Uma entrada, saída ou ajuste de estoque. Não se apaga e não se altera em lote; a
    única correção permitida é `services.editar_movimento` (saldos recalculados, tudo
    auditado, `editado_em` preenchido). Quantidade sempre positiva; o sinal vem do tipo."""

    ENTRADA, SAIDA = "entrada", "saida"
    AJUSTE_ENTRADA, AJUSTE_SAIDA = "ajuste_entrada", "ajuste_saida"
    TRANSFERENCIA_ENTRADA, TRANSFERENCIA_SAIDA = "transferencia_entrada", "transferencia_saida"
    TIPO_CHOICES = [
        (ENTRADA, "Entrada"), (SAIDA, "Saída"), (AJUSTE_ENTRADA, "Ajuste (acréscimo)"), (AJUSTE_SAIDA, "Ajuste (redução)"),
        (TRANSFERENCIA_ENTRADA, "Transferência (recebida)"), (TRANSFERENCIA_SAIDA, "Transferência (enviada)"),
    ]
    TIPOS_DE_ENTRADA = (ENTRADA, AJUSTE_ENTRADA, TRANSFERENCIA_ENTRADA)

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="movimentos_estoque", verbose_name="Cliente")
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="movimentos_estoque", verbose_name="Insumo")
    lote = models.ForeignKey(Lote, on_delete=models.PROTECT, related_name="movimentos", verbose_name="Lote")
    tipo = models.CharField(max_length=25, choices=TIPO_CHOICES, verbose_name="Tipo")
    quantidade = models.DecimalField(
        max_digits=15, decimal_places=4, validators=[MinValueValidator(QUANTIDADE_MINIMA)], verbose_name="Quantidade",
    )
    saldo_lote_apos = models.DecimalField(max_digits=15, decimal_places=4, verbose_name="Saldo do lote após")
    # Responsável: a conta (pode ser removida depois) e o nome gravado no momento.
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="movimentos_estoque", verbose_name="Responsável",
    )
    responsavel_nome = models.CharField(max_length=150, blank=True, verbose_name="Responsável (nome)")
    transferencia = models.ForeignKey(
        "estoque.TransferenciaEstoque", null=True, blank=True, on_delete=models.PROTECT, related_name="movimentos", verbose_name="Transferência",
    )
    observacao = models.CharField(max_length=255, blank=True, verbose_name="Observação")
    # Motivo da baixa MANUAL (saída/ajuste de redução); vazio nas demais (entradas, vendas do PDV, transferências).
    motivo = models.CharField(max_length=20, blank=True, default="", db_default="", choices=MOTIVO_CHOICES, verbose_name="Motivo")
    data = models.DateTimeField(default=timezone.now, verbose_name="Data")
    criado_em = models.DateTimeField(auto_now_add=True)
    # Preenchido quando o movimento foi corrigido pela tela de edição (services.editar_movimento).
    editado_em = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Editado em")

    objects = HistoricoQuerySet.as_manager()

    class Meta:
        verbose_name = "Movimento de estoque"
        verbose_name_plural = "Movimentos de estoque"
        ordering = ["-data", "-pk"]
        indexes = [models.Index(fields=["cliente", "insumo", "data"], name="mov_cliente_insumo_data_idx")]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade} de {self.insumo}"

    @property
    def sinal(self):
        return 1 if self.tipo in self.TIPOS_DE_ENTRADA else -1

    @property
    def editavel(self):
        """Movimentos de transferência pertencem a um par (saída + entrada) e só se desfazem
        por nova transferência; os demais podem ser corrigidos."""
        return self.transferencia_id is None



class TransferenciaEstoque(HistoricoImutavelMixin, models.Model):
    """Cabeçalho de uma transferência entre unidades da mesma estrutura
    (matriz ↔ filiais): origem, destino, responsável e data. Os itens são os
    MovimentoEstoque ligados a ela (uma saída na origem e uma entrada no destino
    por item). IMUTÁVEL como o histórico."""

    origem = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="transferencias_enviadas", verbose_name="Origem")
    destino = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="transferencias_recebidas", verbose_name="Destino")
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="transferencias_estoque", verbose_name="Responsável",
    )
    responsavel_nome = models.CharField(max_length=150, blank=True, verbose_name="Responsável (nome)")
    observacao = models.CharField(max_length=255, blank=True, verbose_name="Observação")
    data = models.DateTimeField(default=timezone.now, verbose_name="Data")
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()

    class Meta:
        verbose_name = "Transferência de estoque"
        verbose_name_plural = "Transferências de estoque"
        ordering = ["-data", "-pk"]
        constraints = [models.CheckConstraint(condition=~models.Q(origem=models.F("destino")), name="transferencia_origem_diferente_destino")]

    def __str__(self):
        return f"Transferência #{self.pk}: {self.origem} → {self.destino}"



class EstoqueMinimo(models.Model):
    """Quantidade mínima de um insumo (na unidade de medida do insumo): abaixo
    dela o estoque gera alerta. 0 = sem alerta."""

    insumo = models.OneToOneField("insumo.Insumo", on_delete=models.CASCADE, related_name="estoque_minimo", verbose_name="Insumo")
    minimo = models.DecimalField(max_digits=15, decimal_places=4, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Quantidade mínima")
    # Lista de compras (apps/compras): quantidade sugerida = mínimo x fator_reposicao - saldo, arredondada
    # PARA CIMA ao múltiplo de compra (embalagem) quando informado. Fator 2 = "repor até o dobro do mínimo".
    fator_reposicao = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("2"), db_default=Decimal("2"),
        validators=[MinValueValidator(Decimal("1"))], verbose_name="Fator de reposição",
    )
    multiplo_compra = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.0001"))], verbose_name="Múltiplo de compra",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Estoque mínimo"
        verbose_name_plural = "Estoques mínimos"

    def __str__(self):
        return f"{self.insumo}: mínimo {self.minimo}"


class AlertaEnviado(models.Model):
    """Registro de que um alerta já foi notificado hoje, para não repetir a
    mesma notificação (idempotência do comando e dos disparos por movimento)."""

    TIPO_BAIXO, TIPO_VENCIMENTO = "baixo", "vencimento"
    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.CASCADE, related_name="alertas_estoque")
    tipo = models.CharField(max_length=15)
    referencia = models.PositiveBigIntegerField(help_text="Id do insumo (baixo) ou do lote (vencimento).")
    data = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cliente", "tipo", "referencia", "data"], name="alerta_estoque_unico_por_dia")]


class SaldoProduto(models.Model):
    """Saldo atual de um PRODUTO com controle de estoque (`Produto.controla_estoque`) na unidade dona dele. Uma linha por produto;
    é bloqueada (select_for_update) dentro da transação de cada movimento e o banco recusa saldo negativo: duas vendas simultâneas
    do último item não deixam o estoque inconsistente. `manage.py verificar_estoque` confere o saldo contra o histórico."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="saldos_produto", verbose_name="Cliente")
    produto = models.OneToOneField("produto.Produto", on_delete=models.PROTECT, related_name="saldo_estoque", verbose_name="Produto")
    saldo = models.DecimalField(max_digits=15, decimal_places=3, default=Decimal("0"), verbose_name="Saldo")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Saldo de produto"
        verbose_name_plural = "Saldos de produtos"
        constraints = [models.CheckConstraint(condition=models.Q(saldo__gte=0), name="saldo_produto_nao_negativo")]

    def __str__(self):
        return f"{self.produto}: {self.saldo}"


class MovimentoProduto(HistoricoImutavelMixin, models.Model):
    """Entrada, saída, ajuste, baixa de venda ou estorno de venda de um produto. IMUTÁVEL (correções são ajustes de sinal oposto).
    Quantidade sempre positiva; o sinal vem do tipo. `venda_item` liga a baixa/estorno automático à linha da venda do PDV: a
    restrição única de `baixa_venda` por item impede baixar duas vezes o mesmo item, mesmo em reprocessamentos."""

    ENTRADA, SAIDA, AJUSTE_ENTRADA, AJUSTE_SAIDA = "entrada", "saida", "ajuste_entrada", "ajuste_saida"
    BAIXA_VENDA, ESTORNO_VENDA = "baixa_venda", "estorno_venda"
    TIPO_CHOICES = [
        (ENTRADA, "Entrada"), (SAIDA, "Saída"), (AJUSTE_ENTRADA, "Ajuste (acréscimo)"), (AJUSTE_SAIDA, "Ajuste (redução)"),
        (BAIXA_VENDA, "Baixa por venda"), (ESTORNO_VENDA, "Estorno de venda"),
    ]
    TIPOS_DE_ENTRADA = (ENTRADA, AJUSTE_ENTRADA, ESTORNO_VENDA)

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="movimentos_produto", verbose_name="Cliente")
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="movimentos_estoque", verbose_name="Produto")
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, verbose_name="Tipo")
    quantidade = models.DecimalField(max_digits=15, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Quantidade")
    saldo_apos = models.DecimalField(max_digits=15, decimal_places=3, verbose_name="Saldo após")
    motivo = models.CharField(max_length=20, blank=True, default="", db_default="", choices=MOTIVO_CHOICES, verbose_name="Motivo")
    observacao = models.CharField(max_length=255, blank=True, verbose_name="Observação")
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="movimentos_produto", verbose_name="Responsável",
    )
    responsavel_nome = models.CharField(max_length=150, blank=True, verbose_name="Responsável (nome)")
    venda_item = models.ForeignKey("pdv.VendaItem", null=True, blank=True, on_delete=models.PROTECT, related_name="movimentos_produto", verbose_name="Item da venda")
    data = models.DateTimeField(default=timezone.now, verbose_name="Data")
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()

    class Meta:
        verbose_name = "Movimento de produto"
        verbose_name_plural = "Movimentos de produtos"
        ordering = ["-data", "-pk"]
        indexes = [models.Index(fields=["cliente", "produto", "data"], name="movprod_cli_prod_data_idx")]
        constraints = [
            models.UniqueConstraint(fields=["venda_item"], condition=models.Q(tipo="baixa_venda"), name="movprod_uma_baixa_por_item_de_venda"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade} de {self.produto}"

    @property
    def sinal(self):
        return 1 if self.tipo in self.TIPOS_DE_ENTRADA else -1
