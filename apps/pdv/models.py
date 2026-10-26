from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from web_project.historico import HistoricoImutavelMixin, HistoricoQuerySet
from web_project.regras.dinheiro import DinheiroService

LIMITE_CENTAVOS = 10**13


class Caixa(models.Model):
    """Turno de caixa de UM operador numa unidade (matriz/filial). O operador abre com o troco inicial (suprimento de
    abertura), vende, e fecha informando o dinheiro contado; a diferença para o esperado é a CONFERÊNCIA, revisada por
    quem tem a permissão `conferir_caixa`. Um operador só pode ter um caixa aberto por vez (restrição no banco)."""

    ABERTO, FECHADO = "aberto", "fechado"
    STATUS_CHOICES = [(ABERTO, "Aberto"), (FECHADO, "Fechado")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="caixas", verbose_name="Unidade")
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="caixas_pdv", verbose_name="Operador")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ABERTO, db_default=ABERTO, verbose_name="Status")
    valor_abertura = models.PositiveBigIntegerField(default=0, verbose_name="Troco inicial (centavos)")
    aberto_em = models.DateTimeField(default=timezone.now, verbose_name="Aberto em")
    fechado_em = models.DateTimeField(null=True, blank=True, verbose_name="Fechado em")
    # No fechamento: o que o sistema esperava em DINHEIRO, o que foi contado e a diferença (contado − esperado).
    valor_esperado = models.BigIntegerField(null=True, blank=True, verbose_name="Dinheiro esperado (centavos)")
    valor_contado = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Dinheiro contado (centavos)")
    diferenca = models.BigIntegerField(null=True, blank=True, verbose_name="Diferença (centavos)")
    observacao_fechamento = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação do fechamento")
    conferido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    conferido_em = models.DateTimeField(null=True, blank=True, editable=False)
    observacao_conferencia = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação da conferência")

    AUDITORIA_MONETARIOS = ("valor_abertura", "valor_esperado", "valor_contado", "diferenca")

    class Meta:
        verbose_name = "Caixa"
        verbose_name_plural = "Caixas"
        ordering = ["-aberto_em", "-pk"]
        permissions = [
            ("abrir_caixa", "Abrir e fechar o próprio caixa"), ("conferir_caixa", "Conferir (revisar) o fechamento de caixas"),
            ("sangria", "Registrar sangria no caixa"), ("suprimento", "Registrar suprimento no caixa"),
            # Telas do menu do PDV (aparecem em Módulos, Papéis e Permissões especiais como linhas "Visualizar").
            ("view_telameucaixa", "Ver a tela Meu caixa do PDV"), ("view_telacaixas", "Ver a tela Caixas do PDV"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["operador"], condition=Q(status="aberto"), name="caixa_um_aberto_por_operador"),
        ]
        indexes = [models.Index(fields=["cliente", "status"], name="caixa_cliente_status_idx")]

    def __str__(self):
        return f"Caixa {self.pk} ({self.operador})"

    @property
    def aberto(self):
        return self.status == self.ABERTO


class MovimentoCaixa(HistoricoImutavelMixin, models.Model):
    """Sangria (retirada de dinheiro) ou suprimento (entrada de dinheiro) do caixa. Imutável: erro se corrige com novo lançamento."""

    SANGRIA, SUPRIMENTO = "sangria", "suprimento"
    TIPO_CHOICES = [(SANGRIA, "Sangria"), (SUPRIMENTO, "Suprimento")]

    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, related_name="movimentos")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name="Tipo")
    valor = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], verbose_name="Valor (centavos)")
    motivo = models.CharField(max_length=255, verbose_name="Motivo")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()
    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Movimento de caixa"
        verbose_name_plural = "Movimentos de caixa"
        ordering = ["-criado_em", "-pk"]

    def __str__(self):
        return f"{self.get_tipo_display()} {DinheiroService.formatar(self.valor)}"


class Venda(models.Model):
    """Venda do PDV (balcão ou comanda de mesa). Valores em centavos.

    Status: ABERTA (comanda aberta, itens ainda editáveis, nada baixado nem recebido), PAGA (finalizada: pagamentos
    gravados, estoque baixado, conta a receber baixada) e CANCELADA. O comprovante é NÃO FISCAL; a emissão fiscal
    (NFC-e/NFS-e) é um fluxo separado (apps/fiscal) e só acontece com provedor configurado e confirmado.

    Cada item e cada pagamento guarda um SNAPSHOT (preço, nome, taxa): mudar produto, preço ou forma de pagamento
    depois não altera vendas antigas.
    """

    ABERTA, PAGA, CANCELADA = "aberta", "paga", "cancelada"
    STATUS_CHOICES = [(ABERTA, "Aberta"), (PAGA, "Paga"), (CANCELADA, "Cancelada")]
    ORIGEM_BALCAO, ORIGEM_MESA, ORIGEM_PEDIDO = "balcao", "mesa", "pedido"
    ORIGEM_CHOICES = [(ORIGEM_BALCAO, "Balcão"), (ORIGEM_MESA, "Mesa/comanda"), (ORIGEM_PEDIDO, "Pedido")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="vendas_pdv", verbose_name="Unidade")
    numero = models.PositiveIntegerField(editable=False, verbose_name="Número")
    caixa = models.ForeignKey(Caixa, on_delete=models.PROTECT, related_name="vendas", verbose_name="Caixa")
    consumidor = models.ForeignKey("consumidor.Consumidor", null=True, blank=True, on_delete=models.SET_NULL, related_name="vendas", verbose_name="Consumidor")
    mesa = models.ForeignKey("pdvconfig.Mesa", null=True, blank=True, on_delete=models.PROTECT, related_name="vendas", verbose_name="Mesa")
    origem = models.CharField(max_length=10, choices=ORIGEM_CHOICES, default=ORIGEM_BALCAO, db_default=ORIGEM_BALCAO, verbose_name="Origem")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ABERTA, db_default=ABERTA, verbose_name="Status")
    subtotal = models.PositiveBigIntegerField(default=0, verbose_name="Subtotal (centavos)")
    desconto = models.PositiveBigIntegerField(default=0, verbose_name="Desconto (centavos)")
    total = models.PositiveBigIntegerField(default=0, verbose_name="Total (centavos)")
    troco = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Troco (centavos)")
    # Idempotência: o app/tela envia uma chave por tentativa; repetir a mesma chave devolve a MESMA venda, sem duplicar.
    chave_idempotencia = models.CharField(max_length=64, null=True, blank=True, editable=False)
    # Estoque: a baixa acontece UMA vez (na finalização ou na confirmação do pedido, o que vier primeiro).
    baixa_estoque_em = models.DateTimeField(null=True, blank=True, editable=False)
    conta_receber = models.OneToOneField("financeiro.ContaReceber", null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="+")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    finalizada_em = models.DateTimeField(null=True, blank=True, editable=False)
    cancelada_em = models.DateTimeField(null=True, blank=True, editable=False)
    cancelada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    motivo_cancelamento = models.CharField(max_length=255, blank=True, default="", editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("subtotal", "desconto", "total", "troco")

    class Meta:
        verbose_name = "Venda"
        verbose_name_plural = "Vendas"
        ordering = ["-criado_em", "-pk"]
        permissions = [
            ("cancelar_venda", "Cancelar vendas"), ("devolver_item", "Registrar devolução de itens"),
            ("conceder_desconto", "Conceder desconto nas vendas"),
            ("remover_item", "Autorizar (com senha) a exclusão de itens lançados no PDV"),
            ("view_telavender", "Ver a tela Vender do PDV"), ("view_telacomandas", "Ver a tela Comandas do PDV"), ("view_televendas", "Ver a tela Vendas do PDV"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "numero"], name="venda_numero_unico_por_unidade"),
            models.UniqueConstraint(fields=["cliente", "chave_idempotencia"], condition=Q(chave_idempotencia__isnull=False), name="venda_chave_idempotencia_unica"),
            models.UniqueConstraint(fields=["mesa"], condition=Q(status="aberta", mesa__isnull=False), name="venda_uma_comanda_aberta_por_mesa"),
            models.CheckConstraint(condition=Q(desconto__lte=F("subtotal")), name="venda_desconto_ate_o_subtotal"),
        ]
        indexes = [models.Index(fields=["cliente", "status"], name="venda_cliente_status_idx"), models.Index(fields=["caixa"], name="venda_caixa_idx")]

    def __str__(self):
        return f"Venda #{self.numero}"

    @property
    def total_formatado(self):
        return DinheiroService.formatar(self.total)

    @property
    def subtotal_formatado(self):
        return DinheiroService.formatar(self.subtotal)

    @property
    def desconto_formatado(self):
        return DinheiroService.formatar(self.desconto)

    @property
    def troco_formatado(self):
        return DinheiroService.formatar(self.troco)

    ENTREGA_ROTULOS = {"": "", "em_andamento": "Em andamento na cozinha", "entregue": "Entregue"}

    @property
    def entrega(self):
        """Estado de entrega derivado dos pedidos (cozinha) da venda, sem coluna própria (fica sempre coerente com a fila):
        "" = sem pedido ativo; "em_andamento" = há pedido ainda não entregue; "entregue" = todos os pedidos não cancelados foram entregues.
        Usa `self.pedidos.all()`: com `prefetch_related("pedidos")` não gera consulta por venda."""
        ativos = [p.status for p in self.pedidos.all() if p.status != "cancelado"]  # literais: o app de pedidos depende deste
        if not ativos:
            return ""
        return "entregue" if all(st == "entregue" for st in ativos) else "em_andamento"

    @property
    def entrega_rotulo(self):
        return self.ENTREGA_ROTULOS[self.entrega]


class VendaItem(models.Model):
    """Linha da venda. `ficha` e `precificacao` são o SNAPSHOT de qual receita e qual preço foram usados (a baixa do estoque
    e a devolução usam exatamente a ficha da época). `preparado`: o item já foi produzido (cozinha) — vedado repor estoque."""

    venda = models.ForeignKey(Venda, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey("produto.Produto", on_delete=models.PROTECT, related_name="itens_venda", verbose_name="Produto")
    ficha = models.ForeignKey("ficha.Ficha", on_delete=models.PROTECT, related_name="itens_venda", verbose_name="Ficha técnica")
    descricao = models.CharField(max_length=150, verbose_name="Item")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Quantidade")
    preco_unitario = models.PositiveBigIntegerField(verbose_name="Preço unitário (centavos)")
    total = models.PositiveBigIntegerField(verbose_name="Total (centavos)")
    quantidade_devolvida = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"), editable=False, verbose_name="Quantidade devolvida")
    preparado = models.BooleanField(default=False, db_default=False, verbose_name="Já preparado")
    # Estoque baixado UMA vez por item (na confirmação do pedido ou no recebimento, o que vier primeiro).
    baixa_estoque_em = models.DateTimeField(null=True, blank=True, editable=False)
    observacao = models.CharField(max_length=120, blank=True, default="", verbose_name="Observação")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Item de venda"
        verbose_name_plural = "Itens de venda"
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=Q(quantidade_devolvida__lte=F("quantidade")), name="venda_item_devolvido_ate_a_quantidade")]

    def __str__(self):
        return f"{self.descricao} × {self.quantidade.normalize():f}"

    @property
    def disponivel_para_devolucao(self):
        return self.quantidade - self.quantidade_devolvida


class VendaPagamento(models.Model):
    """Pagamento de uma venda (uma venda pode ter vários: conta dividida, formas mistas). `valor` é o que abate a venda;
    `valor_recebido` (só dinheiro) é o que o cliente entregou — a diferença é o troco. A taxa é o snapshot da forma na hora."""

    venda = models.ForeignKey(Venda, on_delete=models.CASCADE, related_name="pagamentos")
    forma = models.ForeignKey("pdvconfig.FormaPagamento", on_delete=models.PROTECT, related_name="pagamentos", verbose_name="Forma")
    forma_nome = models.CharField(max_length=60, verbose_name="Forma (nome na época)")
    forma_tipo = models.CharField(max_length=10, verbose_name="Tipo da forma")
    valor = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], verbose_name="Valor (centavos)")
    valor_recebido = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Valor recebido (centavos)")
    taxa_valor = models.PositiveBigIntegerField(default=0, verbose_name="Taxa (centavos)")
    taxa_descricao = models.CharField(max_length=80, blank=True, default="", verbose_name="Taxa aplicada")
    pagador = models.CharField(max_length=60, blank=True, default="", verbose_name="Pagador (conta dividida)")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pagamento de venda"
        verbose_name_plural = "Pagamentos de venda"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.forma_nome} {DinheiroService.formatar(self.valor)}"

    @property
    def liquido(self):
        return self.valor - self.taxa_valor


class Devolucao(HistoricoImutavelMixin, models.Model):
    """Devolução de itens de uma venda paga. IMUTÁVEL. Repõe o estoque só se o item NÃO foi preparado e o responsável
    pediu (`repor_estoque`). O dinheiro devolvido sai do caixa quando a forma é dinheiro e vira uma conta a pagar já
    baixada (despesa "Devoluções de vendas") para o financeiro."""

    venda = models.ForeignKey(Venda, on_delete=models.PROTECT, related_name="devolucoes")
    item = models.ForeignKey(VendaItem, on_delete=models.PROTECT, related_name="devolucoes")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    valor = models.PositiveBigIntegerField(verbose_name="Valor devolvido (centavos)")
    repor_estoque = models.BooleanField(default=False, verbose_name="Estoque reposto")
    forma_estorno = models.ForeignKey("pdvconfig.FormaPagamento", on_delete=models.PROTECT, related_name="+", verbose_name="Devolvido por")
    caixa = models.ForeignKey(Caixa, null=True, blank=True, on_delete=models.PROTECT, related_name="devolucoes", verbose_name="Caixa do estorno")
    motivo = models.CharField(max_length=255, verbose_name="Motivo")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    conta_pagar = models.ForeignKey("financeiro.ContaPagar", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    objects = HistoricoQuerySet.as_manager()
    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Devolução"
        verbose_name_plural = "Devoluções"
        ordering = ["-criado_em", "-pk"]

    def __str__(self):
        return f"Devolução da venda #{self.venda.numero}"


class VendaItemInsumo(models.Model):
    """Rastreabilidade do estoque: quanto de cada INSUMO foi baixado por uma linha de venda (gravado na baixa, a partir da
    ficha técnica da época). A devolução repõe proporcionalmente a ESTAS linhas, mesmo que a ficha mude depois."""

    item = models.ForeignKey(VendaItem, on_delete=models.CASCADE, related_name="insumos_baixados")
    insumo = models.ForeignKey("insumo.Insumo", on_delete=models.PROTECT, related_name="+")
    medida = models.ForeignKey("medida.Medida", on_delete=models.PROTECT, related_name="+")
    quantidade = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="Quantidade baixada")
    quantidade_reposta = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))

    class Meta:
        verbose_name = "Insumo baixado na venda"
        verbose_name_plural = "Insumos baixados na venda"
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=Q(quantidade_reposta__lte=F("quantidade")), name="venda_insumo_reposto_ate_o_baixado")]


class CodigoAutorizacao(models.Model):
    """Código de crachá (barras/QR) de quem autoriza operações sensíveis no PDV (desconto, cancelar, limpar, excluir item).

    Só o HMAC do código é guardado (nunca o texto): o código é gerado pelo sistema com alta entropia, aparece UMA vez para o dono
    e gerar outro invalida o anterior. O hash é determinístico (HMAC com a SECRET_KEY) para permitir a busca direta pela leitura.
    Não tem unidade: o escopo é validado no uso (o autorizador precisa alcançar a unidade da venda; ver autorizacao.py)."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="codigo_autorizacao")
    codigo_hash = models.CharField(max_length=64, unique=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Código de autorização"
        verbose_name_plural = "Códigos de autorização"
