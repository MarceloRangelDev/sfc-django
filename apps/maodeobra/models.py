from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web_project.regras.dinheiro import DinheiroService

from .calculo import ARRED_MINUTO, ARREDONDAMENTOS, LIMITE_CENTAVOS, texto_horas


def segmento_permite(cliente):
    """Mão de obra por hora/dia vale para os segmentos de SERVIÇO e de PRODUTO E SERVIÇO (decisão do cliente); produto puro não usa."""
    segmento = getattr(cliente, "segmento", None)
    return bool(segmento and segmento.precifica_servico)


class ConfiguracaoMaoDeObra(models.Model):
    """Regras de cálculo da unidade (uma linha por unidade, criada com os padrões no primeiro uso). Ver calculo.py."""

    cliente = models.OneToOneField("cliente.Cliente", on_delete=models.CASCADE, related_name="configuracao_mao_de_obra", verbose_name="Unidade")
    arredondamento = models.CharField(max_length=10, choices=ARREDONDAMENTOS, default=ARRED_MINUTO, db_default=ARRED_MINUTO, verbose_name="Arredondamento")
    jornada_horas = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("8"), db_default=Decimal("8"),
        validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("24"))], verbose_name="Jornada normal (horas por dia)",
    )
    extra_percentual = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("50"), db_default=Decimal("50"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("300"))], verbose_name="Adicional de hora extra (%)",
    )
    adicional_domingo = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"), db_default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("300"))], verbose_name="Adicional de domingo (%)",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração de mão de obra"
        verbose_name_plural = "Configurações de mão de obra"

    def __str__(self):
        return f"Mão de obra de {self.cliente}"

    @classmethod
    def da_unidade(cls, cliente):
        configuracao, _ = cls.objects.get_or_create(cliente=cliente)
        return configuracao


class Colaborador(models.Model):
    """Quem presta o serviço (funcionário, autônomo, prestador). Valor da hora e, opcionalmente, da diária."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="colaboradores", verbose_name="Unidade")
    nome = models.CharField(max_length=120, verbose_name="Nome")
    funcao = models.CharField(max_length=80, blank=True, default="", verbose_name="Função")
    valor_hora = models.PositiveBigIntegerField(verbose_name="Valor da hora (centavos)")
    # Vazio = valor_hora × jornada da unidade.
    valor_dia = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Valor do dia (centavos)")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("valor_hora", "valor_dia")

    class Meta:
        verbose_name = "Colaborador"
        verbose_name_plural = "Colaboradores"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="colaborador_nome_unico")]
        indexes = [models.Index(fields=["cliente", "ativo"], name="colaborador_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    def clean(self):
        self.nome = " ".join((self.nome or "").split())
        if self.valor_hora is not None and self.valor_hora > LIMITE_CENTAVOS:
            raise ValidationError({"valor_hora": "Valor acima do limite permitido."})
        if self.valor_dia is not None and self.valor_dia > LIMITE_CENTAVOS:
            raise ValidationError({"valor_dia": "Valor acima do limite permitido."})


class Apontamento(models.Model):
    """Lançamento de trabalho de um colaborador: POR HORA (início, fim, pausa) ou POR DIA (dias × diária), opcionalmente ligado a
    um serviço (produto de segmento serviço) ou a uma descrição. Os valores calculados e os parâmetros usados ficam GRAVADOS
    (snapshot): mudar o valor da hora ou a configuração depois não altera lançamentos antigos; só o recálculo explícito."""

    HORA, DIA = "hora", "dia"
    TIPO_CHOICES = [(HORA, "Por hora"), (DIA, "Por dia")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="apontamentos", verbose_name="Unidade")
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="apontamentos", verbose_name="Colaborador")
    servico = models.ForeignKey("produto.Produto", null=True, blank=True, on_delete=models.PROTECT, related_name="apontamentos", verbose_name="Serviço (produto)")
    descricao = models.CharField(max_length=150, blank=True, default="", verbose_name="Descrição")
    data = models.DateField(verbose_name="Data")
    tipo = models.CharField(max_length=5, choices=TIPO_CHOICES, default=HORA, verbose_name="Tipo")
    inicio = models.TimeField(null=True, blank=True, verbose_name="Início")
    fim = models.TimeField(null=True, blank=True, verbose_name="Fim")
    pausa_minutos = models.PositiveSmallIntegerField(default=0, verbose_name="Pausa (minutos)")
    dias = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, verbose_name="Dias trabalhados")
    # --- parâmetros usados no cálculo (snapshot) ---
    valor_hora_usado = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor da hora usado (centavos)")
    valor_dia_usado = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor do dia usado (centavos)")
    arredondamento_usado = models.CharField(max_length=10, blank=True, default="", editable=False)
    jornada_usada = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, editable=False)
    extra_percentual_usado = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    adicional_domingo_usado = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    # --- resultado (calculado no servidor) ---
    minutos_trabalhados = models.PositiveIntegerField(default=0, editable=False, verbose_name="Minutos trabalhados")
    minutos_normais = models.PositiveIntegerField(default=0, editable=False, verbose_name="Minutos normais")
    minutos_extra = models.PositiveIntegerField(default=0, editable=False, verbose_name="Minutos extras")
    valor_normal = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor normal (centavos)")
    valor_extra = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor das horas extras (centavos)")
    valor_total = models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Valor total (centavos)")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observação")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("valor_hora_usado", "valor_dia_usado", "valor_normal", "valor_extra", "valor_total")
    # Resultado do cálculo (editable=False) também entra na trilha: mostra o antes/depois de um recálculo.
    AUDITORIA_INCLUIR = ("valor_hora_usado", "valor_dia_usado", "minutos_trabalhados", "minutos_extra", "valor_normal", "valor_extra", "valor_total")

    class Meta:
        verbose_name = "Apontamento de mão de obra"
        verbose_name_plural = "Apontamentos de mão de obra"
        ordering = ["-data", "-pk"]
        indexes = [models.Index(fields=["cliente", "data"], name="apontamento_cliente_data_idx"), models.Index(fields=["colaborador", "data"], name="apontamento_colab_data_idx")]

    def __str__(self):
        return f"{self.colaborador} em {self.data:%d/%m/%Y}"

    @property
    def valor_total_formatado(self):
        return DinheiroService.formatar(self.valor_total)

    @property
    def horas_texto(self):
        return texto_horas(self.minutos_trabalhados) if self.tipo == self.HORA else f"{self.dias:.1f} dia(s)".replace(".", ",")

    def clean(self):
        """Valida por tipo e CALCULA (snapshot dos parâmetros + resultado). Vale para tela, API e qualquer gravação."""
        from .calculo import calcular_dias, calcular_horas, valor_dia_padrao
        from web_project.regras import RegraNegocioError

        if self.colaborador_id is None:
            return
        colaborador = self.colaborador
        self.cliente_id = colaborador.cliente_id  # o apontamento é sempre da unidade do colaborador
        if not segmento_permite(colaborador.cliente):
            raise ValidationError("O segmento desta empresa não usa mão de obra por hora/dia (só Serviço e Produto e Serviço).")
        if self.servico_id and self.servico.cliente_id != colaborador.cliente_id:
            raise ValidationError({"servico": "O serviço deve ser da mesma unidade do colaborador."})
        self.descricao = " ".join((self.descricao or "").split())
        configuracao = ConfiguracaoMaoDeObra.da_unidade(colaborador.cliente)
        try:
            if self.tipo == self.HORA:
                erros = {}
                if self.inicio is None:
                    erros["inicio"] = "Informe o horário de início."
                if self.fim is None:
                    erros["fim"] = "Informe o horário de fim."
                if erros:
                    raise ValidationError(erros)
                self.dias = None
                resultado = calcular_horas(
                    self.inicio, self.fim, self.pausa_minutos or 0, colaborador.valor_hora, self.data, configuracao.jornada_horas, configuracao.arredondamento,
                    configuracao.extra_percentual, configuracao.adicional_domingo,
                )
                self.minutos_trabalhados, self.minutos_normais, self.minutos_extra = resultado.minutos_trabalhados, resultado.minutos_normais, resultado.minutos_extra
                self.valor_normal, self.valor_extra, self.valor_total = resultado.valor_normal, resultado.valor_extra, resultado.valor_total
                self.valor_hora_usado, self.valor_dia_usado = colaborador.valor_hora, 0
            else:
                if not self.dias:
                    raise ValidationError({"dias": "Informe os dias trabalhados."})
                self.inicio, self.fim, self.pausa_minutos = None, None, 0
                valor_dia = colaborador.valor_dia if colaborador.valor_dia is not None else valor_dia_padrao(colaborador.valor_hora, configuracao.jornada_horas)
                self.valor_total = calcular_dias(self.dias, valor_dia)
                self.minutos_trabalhados = self.minutos_normais = self.minutos_extra = 0
                self.valor_normal, self.valor_extra = self.valor_total, 0
                self.valor_hora_usado, self.valor_dia_usado = colaborador.valor_hora, valor_dia
        except RegraNegocioError as erro:
            raise ValidationError(str(erro)) from None
        self.arredondamento_usado, self.jornada_usada = configuracao.arredondamento, configuracao.jornada_horas
        self.extra_percentual_usado, self.adicional_domingo_usado = configuracao.extra_percentual, configuracao.adicional_domingo
