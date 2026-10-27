"""Precificação, proposta e acompanhamento de SERVIÇOS (segmentos Serviço e Serviço e Produto). Ver apps/precificacaoservico/README.md.

Dinheiro SEMPRE em centavos inteiros; percentuais em pontos percentuais (10 = 10%). Todo registro pertence a uma unidade (`cliente`,
o tenant); telas e API filtram por ela. Os valores calculados são sempre refeitos no servidor (calculos.py), nunca confiados ao cliente.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService

from .calculos import MODOS, REPASSES

MODO_CHOICES = [("hora", "Por hora"), ("dia", "Por dia"), ("semana", "Por semana"), ("quinzena", "Por quinzena"), ("mes", "Por mês"),
                ("unidade", "Por unidade ou etapa"), ("pacote", "Pacote / preço fechado")]
assert tuple(m for m, _ in MODO_CHOICES) == MODOS

PERCENTUAL = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("99.99"))]


class ConfiguracaoServico(models.Model):
    """Premissas de precificação da unidade (uma linha por unidade, criada no primeiro uso). `versao` sobe a cada alteração e é gravada
    no retrato (premissas) de cada proposta, para saber com qual configuração o preço foi calculado."""

    cliente = models.OneToOneField("cliente.Cliente", on_delete=models.CASCADE, related_name="configuracao_servico", verbose_name="Unidade")
    moeda = models.CharField(max_length=3, default="BRL", db_default="BRL", verbose_name="Moeda")
    remuneracao_mensal = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Remuneração mensal desejada (centavos)")
    dias_mes = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("22"), db_default=Decimal("22"), validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("31"))], verbose_name="Dias de trabalho no mês")
    horas_dia = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("8"), db_default=Decimal("8"), validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("24"))], verbose_name="Horas de trabalho por dia")
    horas_nao_faturaveis = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas não faturáveis no mês")
    # Vazio = disponíveis - não faturáveis.
    horas_faturaveis_informadas = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))], verbose_name="Horas faturáveis no mês (informadas)")
    custo_variavel_hora = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Custo variável por hora (centavos)")
    impostos_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=PERCENTUAL, verbose_name="Impostos estimados (% do preço)")
    taxas_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=PERCENTUAL, verbose_name="Taxas estimadas (% do preço)")
    margem_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("20"), db_default=Decimal("20"), validators=PERCENTUAL, verbose_name="Margem de lucro desejada (% do preço)")
    versao = models.PositiveIntegerField(default=1, db_default=1, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("remuneracao_mensal", "custo_variavel_hora")

    class Meta:
        verbose_name = "Configuração de precificação de serviços"
        verbose_name_plural = "Configurações de precificação de serviços"

    def __str__(self):
        return f"Precificação de serviços de {self.cliente}"

    @classmethod
    def da_unidade(cls, cliente):
        configuracao, _ = cls.objects.get_or_create(cliente=cliente)
        return configuracao


class DespesaFixa(models.Model):
    """Despesa fixa/indireta (aluguel, internet...) ou provisão mensal (férias, 13º, reserva de ferramentas...), com periodicidade; é
    convertida para base mensal em calculos.valor_mensal."""

    DESPESA, PROVISAO = "despesa", "provisao"
    NATUREZA_CHOICES = [(DESPESA, "Despesa fixa/indireta"), (PROVISAO, "Provisão mensal")]
    PERIODICIDADE_CHOICES = [("semanal", "Semanal"), ("mensal", "Mensal"), ("bimestral", "Bimestral"), ("trimestral", "Trimestral"), ("semestral", "Semestral"), ("anual", "Anual")]
    CATEGORIA_CHOICES = [
        ("aluguel", "Aluguel e condomínio"), ("utilidades", "Água, energia, telefone e internet"), ("contabilidade", "Contabilidade"), ("sistemas", "Sistemas e assinaturas"),
        ("seguros", "Seguros"), ("ferramentas", "Manutenção e reposição de ferramentas"), ("marketing", "Marketing"), ("bancarias", "Taxas bancárias"),
        ("depreciacao", "Depreciação de veículo e equipamentos"), ("outras", "Outras"),
    ]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="despesas_servico", verbose_name="Unidade")
    natureza = models.CharField(max_length=8, choices=NATUREZA_CHOICES, default=DESPESA, db_default=DESPESA, verbose_name="Natureza")
    descricao = models.CharField(max_length=150, verbose_name="Descrição")
    categoria = models.CharField(max_length=15, choices=CATEGORIA_CHOICES, default="outras", db_default="outras", verbose_name="Categoria")
    valor = models.PositiveBigIntegerField(verbose_name="Valor (centavos)")
    periodicidade = models.CharField(max_length=10, choices=PERIODICIDADE_CHOICES, default="mensal", db_default="mensal", verbose_name="Periodicidade")
    vigencia_inicio = models.DateField(null=True, blank=True, verbose_name="Vigente desde")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativa")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Despesa fixa de serviços"
        verbose_name_plural = "Despesas fixas de serviços"
        ordering = ["natureza", "descricao"]
        indexes = [models.Index(fields=["cliente", "ativo"], name="psdespesa_cliente_ativo_idx")]

    def __str__(self):
        return self.descricao


class ModeloServico(models.Model):
    """Modelo de serviço reutilizável: forma de cobrança, unidade, preço-base, esforço e margem própria (por serviço/categoria).
    Ao criar uma proposta a partir dele, os valores são COPIADOS; mudar o modelo depois não altera propostas existentes."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="modelos_servico", verbose_name="Unidade")
    nome = models.CharField(max_length=150, verbose_name="Nome")
    categoria = models.CharField(max_length=80, blank=True, default="", verbose_name="Categoria")
    modo = models.CharField(max_length=8, choices=MODO_CHOICES, default="hora", db_default="hora", verbose_name="Forma de cobrança")
    unidade = models.CharField(max_length=20, blank=True, default="", verbose_name="Unidade (m², ponto, visita...)")
    preco_base = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Preço-base por unidade (centavos)")
    horas_por_unidade = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas por unidade")
    margem_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTUAL, verbose_name="Margem própria (%)")
    descricao = models.TextField(max_length=1000, blank=True, default="", verbose_name="Descrição / escopo padrão")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("preco_base",)

    class Meta:
        verbose_name = "Modelo de serviço"
        verbose_name_plural = "Modelos de serviço"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="psmodelo_nome_unico_por_unidade")]

    def __str__(self):
        return self.nome


class Proposta(models.Model):
    """Proposta de serviço. Guarda o RETRATO das premissas (`premissas`) e o resultado do cálculo (`resultado`) do momento: alterar a
    configuração, as despesas ou os modelos depois NÃO muda propostas antigas (só a ação explícita "atualizar premissas", em
    rascunho/enviada, refaz o retrato)."""

    RASCUNHO, ENVIADA, APROVADA, RECUSADA, CANCELADA, CONCLUIDA = "rascunho", "enviada", "aprovada", "recusada", "cancelada", "concluida"
    STATUS_CHOICES = [(RASCUNHO, "Rascunho"), (ENVIADA, "Enviada"), (APROVADA, "Aprovada"), (RECUSADA, "Recusada"), (CANCELADA, "Cancelada"), (CONCLUIDA, "Concluída")]
    EDITAVEIS = (RASCUNHO, ENVIADA)
    # Transições permitidas (status atual -> novos). Recusada/cancelada/concluída são finais.
    TRANSICOES = {RASCUNHO: (ENVIADA, APROVADA, RECUSADA, CANCELADA), ENVIADA: (APROVADA, RECUSADA, CANCELADA), APROVADA: (CONCLUIDA, CANCELADA)}
    EM_EXECUCAO = (APROVADA, CONCLUIDA)
    METODO_CHOICES = [("margem", "Margem sobre o preço de venda"), ("markup", "Markup (acréscimo sobre o custo)")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="propostas_servico", verbose_name="Unidade")
    numero = models.PositiveIntegerField(editable=False, verbose_name="Número")
    modelo = models.ForeignKey(ModeloServico, null=True, blank=True, on_delete=models.SET_NULL, related_name="propostas", verbose_name="Modelo de origem")
    consumidor = models.ForeignKey("consumidor.Consumidor", null=True, blank=True, on_delete=models.SET_NULL, related_name="propostas_servico", verbose_name="Cliente final")
    destinatario_nome = models.CharField(max_length=150, verbose_name="Cliente")
    destinatario_email = models.EmailField(max_length=254, blank=True, default="", verbose_name="E-mail")
    destinatario_whatsapp = models.CharField(max_length=20, blank=True, default="", verbose_name="WhatsApp")
    descricao = models.CharField(max_length=200, verbose_name="Descrição do serviço")
    modo = models.CharField(max_length=8, choices=MODO_CHOICES, default="hora", db_default="hora", verbose_name="Forma de cobrança")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RASCUNHO, db_default=RASCUNHO, verbose_name="Situação")
    validade = models.DateField(null=True, blank=True, verbose_name="Válida até")
    prazo = models.CharField(max_length=150, blank=True, default="", verbose_name="Prazo estimado")
    condicoes_pagamento = models.TextField(max_length=500, blank=True, default="", verbose_name="Condições de pagamento")
    # Escopo (o que a proposta inclui/exclui) e regras para extras: texto livre, impresso na proposta.
    escopo_incluido = models.TextField(max_length=1500, blank=True, default="", verbose_name="Escopo incluído")
    escopo_excluido = models.TextField(max_length=1500, blank=True, default="", verbose_name="Escopo excluído")
    etapas = models.TextField(max_length=1500, blank=True, default="", verbose_name="Etapas ou entregáveis")
    materiais_incluidos = models.TextField(max_length=1000, blank=True, default="", verbose_name="Materiais incluídos")
    materiais_excluidos = models.TextField(max_length=1000, blank=True, default="", verbose_name="Materiais excluídos")
    limites = models.CharField(max_length=300, blank=True, default="", verbose_name="Limites (visitas, revisões, alterações)")
    regras_extras = models.TextField(max_length=1000, blank=True, default="", verbose_name="Extras, hora extra, espera e alteração de escopo")
    observacoes = models.TextField(max_length=1000, blank=True, default="", verbose_name="Observações")
    # Parâmetros do preço (entradas do usuário).
    metodo_preco = models.CharField(max_length=6, choices=METODO_CHOICES, default="margem", db_default="margem", verbose_name="Método de preço")
    margem_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTUAL, verbose_name="Margem desta proposta (%)")
    markup_pct = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("10000"))], verbose_name="Markup (%)")
    preco_fechado = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Preço fechado informado (centavos)")
    minimo_cobranca = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Cobrança mínima (centavos)")
    horas_minimas = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas mínimas")
    arredondamento_minutos = models.PositiveSmallIntegerField(default=0, db_default=0, verbose_name="Arredondar horas em blocos de (min)")
    horas_deslocamento = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas de deslocamento")
    cobra_deslocamento = models.BooleanField(default=False, db_default=False, verbose_name="Cobrar o tempo de deslocamento")
    desconto_tipo = models.CharField(max_length=10, choices=[("valor", "Valor"), ("percentual", "Percentual")], default="valor", db_default="valor", verbose_name="Tipo de desconto")
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), verbose_name="Desconto (centavos ou %)")
    acrescimo_pct = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Acréscimo (urgência, risco...) %")
    acrescimo_valor = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Acréscimo em valor (centavos)")
    custo_financeiro_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=PERCENTUAL, verbose_name="Custo financeiro do parcelamento (%)")
    detalhar_custos = models.BooleanField(default=False, db_default=False, verbose_name="Mostrar custos discriminados ao cliente")
    # Retrato e resultado (ver docstring da classe).
    premissas = models.JSONField(default=dict, editable=False)
    resultado = models.JSONField(default=dict, editable=False)
    custo_total = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Custo estimado (centavos)")
    total = models.PositiveBigIntegerField(default=0, db_default=0, editable=False, verbose_name="Total (centavos)")
    lucro = models.BigIntegerField(default=0, db_default=0, editable=False, verbose_name="Lucro estimado (centavos, pode ser negativo)")
    # Aceite e execução.
    aceite_em = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Aceite em")
    aceite_nome = models.CharField(max_length=150, blank=True, default="", verbose_name="Aceite dado por")
    data_inicio = models.DateField(null=True, blank=True, verbose_name="Início do serviço")
    data_conclusao = models.DateField(null=True, blank=True, verbose_name="Conclusão do serviço")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("preco_fechado", "minimo_cobranca", "acrescimo_valor", "custo_total", "total", "lucro")
    AUDITORIA_SEM_CONTEUDO = ("destinatario_email", "destinatario_whatsapp")

    class Meta:
        verbose_name = "Proposta de serviço"
        verbose_name_plural = "Propostas de serviço"
        ordering = ["-numero"]
        constraints = [models.UniqueConstraint(fields=["cliente", "numero"], name="psproposta_numero_unico_por_unidade")]
        indexes = [models.Index(fields=["cliente", "status"], name="psproposta_cliente_status_idx")]

    def __str__(self):
        return f"Proposta nº {self.numero}"

    @property
    def editavel(self):
        return self.status in self.EDITAVEIS

    @property
    def total_formatado(self):
        return DinheiroService.formatar(self.total)


class PropostaItem(models.Model):
    """Item de TRABALHO da proposta: quantidade na forma de cobrança (horas, dias, unidades...). `horas_por_unidade` vazio = padrão da
    forma de cobrança (calculos.horas_padrao_do_modo); `preco_unitario` vazio = o preço sai da formação por margem/markup."""

    proposta = models.ForeignKey(Proposta, on_delete=models.CASCADE, related_name="itens")
    descricao = models.CharField(max_length=150, verbose_name="Descrição")
    modo = models.CharField(max_length=8, choices=MODO_CHOICES, default="hora", db_default="hora", verbose_name="Cobrança")
    quantidade = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Quantidade")
    unidade = models.CharField(max_length=20, blank=True, default="", verbose_name="Unidade")
    horas_por_unidade = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas por unidade")
    preco_unitario = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Preço unitário (centavos)")
    ordem = models.PositiveSmallIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = "Item de trabalho da proposta"
        verbose_name_plural = "Itens de trabalho da proposta"
        ordering = ["ordem", "id"]


class PropostaCusto(models.Model):
    """Custo direto do serviço (material, ajudante, deslocamento, taxa...) e como entra no orçamento: discriminado ao cliente, incluído
    no preço ou absorvido como custo."""

    CATEGORIA_CHOICES = [
        ("material", "Materiais e peças"), ("terceiro", "Ajudantes e terceiros"), ("deslocamento", "Deslocamento, combustível, pedágio e estacionamento"),
        ("alimentacao", "Alimentação e hospedagem"), ("equipamento", "Equipamentos e ferramentas alugadas"), ("taxa", "Taxas de cartão ou plataforma"),
        ("reserva", "Descarte, garantia, perdas e retrabalho"), ("outro", "Outros custos"),
    ]
    REPASSE_CHOICES = [("discriminado", "Discriminado ao cliente"), ("incluido", "Incluído no preço"), ("absorvido", "Absorvido como custo")]
    assert tuple(r for r, _ in REPASSE_CHOICES) == REPASSES

    proposta = models.ForeignKey(Proposta, on_delete=models.CASCADE, related_name="custos")
    categoria = models.CharField(max_length=12, choices=CATEGORIA_CHOICES, default="material", db_default="material", verbose_name="Tipo")
    descricao = models.CharField(max_length=150, verbose_name="Descrição")
    quantidade = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))], verbose_name="Quantidade")
    unidade = models.CharField(max_length=20, blank=True, default="", verbose_name="Unidade")
    custo_unitario = models.PositiveBigIntegerField(verbose_name="Custo unitário (centavos)")
    repasse = models.CharField(max_length=12, choices=REPASSE_CHOICES, default="incluido", db_default="incluido", verbose_name="No orçamento")
    ordem = models.PositiveSmallIntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = "Custo direto da proposta"
        verbose_name_plural = "Custos diretos da proposta"
        ordering = ["ordem", "id"]


class Lancamento(models.Model):
    """Registro REAL da execução (horas, custos, deslocamentos, extras aprovados) de uma proposta aprovada. Horas sem custo informado
    valem horas x custo-hora do retrato da proposta (nunca o custo-hora de hoje)."""

    TIPO_CHOICES = [("horas", "Horas trabalhadas"), ("custo", "Custo direto"), ("deslocamento", "Deslocamento"), ("extra", "Serviço extra aprovado")]

    proposta = models.ForeignKey(Proposta, on_delete=models.CASCADE, related_name="lancamentos")
    tipo = models.CharField(max_length=12, choices=TIPO_CHOICES, verbose_name="Tipo")
    data = models.DateField(verbose_name="Data")
    descricao = models.CharField(max_length=150, blank=True, default="", verbose_name="Descrição")
    horas = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))], verbose_name="Horas")
    custo = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Custo real (centavos)")
    receita = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Valor cobrado do extra (centavos)")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    AUDITORIA_MONETARIOS = ("custo", "receita")

    class Meta:
        verbose_name = "Lançamento de execução"
        verbose_name_plural = "Lançamentos de execução"
        ordering = ["data", "id"]

    def __str__(self):
        return f"{self.get_tipo_display()} em {self.data:%d/%m/%Y}"


class Pagamento(models.Model):
    """Pagamento da proposta: valor, data, meio, taxa do meio (cartão/plataforma, em centavos) e situação (recebido x pendente)."""

    MEIO_CHOICES = [("dinheiro", "Dinheiro"), ("pix", "Pix"), ("cartao", "Cartão"), ("boleto", "Boleto"), ("transferencia", "Transferência"), ("outro", "Outro")]
    RECEBIDO, PENDENTE = "recebido", "pendente"

    proposta = models.ForeignKey(Proposta, on_delete=models.CASCADE, related_name="pagamentos")
    valor = models.PositiveBigIntegerField(verbose_name="Valor (centavos)")
    data = models.DateField(verbose_name="Data")
    meio = models.CharField(max_length=14, choices=MEIO_CHOICES, default="pix", db_default="pix", verbose_name="Meio de pagamento")
    taxa = models.PositiveBigIntegerField(default=0, db_default=0, verbose_name="Taxa do meio (centavos)")
    status = models.CharField(max_length=10, choices=[(RECEBIDO, "Recebido"), (PENDENTE, "Pendente")], default=RECEBIDO, db_default=RECEBIDO, verbose_name="Situação")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    AUDITORIA_MONETARIOS = ("valor", "taxa")

    class Meta:
        verbose_name = "Pagamento da proposta"
        verbose_name_plural = "Pagamentos da proposta"
        ordering = ["data", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(taxa__lte=models.F("valor")), name="pspagamento_taxa_ate_valor")]

    def __str__(self):
        return f"Pagamento de {DinheiroService.formatar(self.valor)} em {self.data:%d/%m/%Y}"
