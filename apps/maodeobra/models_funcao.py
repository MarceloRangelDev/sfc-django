"""Funções de mão de obra, vínculo com o colaborador e simulações de promoção (ver custos.py e composicao.py).

Nenhuma destas tabelas altera o cálculo de apontamentos: são o custo COMPLETO (informativo) e o planejamento.
O dono (unidade) de ComponenteFuncao, VinculoFuncao e PersonalizacaoColaborador vem do cadastro-pai (função/colaborador),
então não há coluna de cliente duplicada que possa divergir.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from web_project.regras import RegraNegocioError

from . import custos
from .models import Colaborador


class FuncaoMaoDeObra(models.Model):
    """Função/cargo da unidade, com a composição de custo (itens em `ComponenteFuncao`) e a referência da CBO."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="funcoes_mao_de_obra", verbose_name="Unidade")
    nome = models.CharField(max_length=80, verbose_name="Nome")
    descricao = models.CharField(max_length=255, blank=True, default="", verbose_name="Descrição")
    # CBO (Classificação Brasileira de Ocupações): 6 dígitos, digitados pelo usuário (sem catálogo externo).
    cbo_codigo = models.CharField(max_length=6, blank=True, default="", validators=[RegexValidator(r"^\d{6}$", "A CBO tem 6 dígitos.")], verbose_name="CBO (código)")
    cbo_descricao = models.CharField(max_length=150, blank=True, default="", verbose_name="CBO (descrição)")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observações")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativa")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Função de mão de obra"
        verbose_name_plural = "Funções de mão de obra"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="funcao_mo_nome_unico")]
        indexes = [models.Index(fields=["cliente", "ativo"], name="funcao_mo_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    def clean(self):
        self.nome = " ".join((self.nome or "").split())


class ComponenteFuncao(models.Model):
    """Item de custo da função com vigência (salário base, adicional, DSR, encargo, provisão, benefício ou indireto).
    Mudou o valor? Encerre o item (`fim`) e crie outro com nova vigência: o passado continua calculável."""

    funcao = models.ForeignKey(FuncaoMaoDeObra, on_delete=models.CASCADE, related_name="componentes", verbose_name="Função")
    categoria = models.CharField(max_length=10, choices=custos.CATEGORIAS, verbose_name="Categoria")
    nome = models.CharField(max_length=100, verbose_name="Nome")
    tipo_valor = models.CharField(max_length=10, choices=custos.TIPOS_VALOR, default=custos.FIXO, verbose_name="Tipo de valor")
    valor = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Valor (centavos)")
    percentual = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True, verbose_name="Percentual (%)")
    base = models.CharField(max_length=22, choices=custos.BASES, default=custos.B_SALARIO, db_default=custos.B_SALARIO, verbose_name="Base do percentual")
    periodicidade = models.CharField(max_length=6, choices=custos.PERIODICIDADES, default=custos.MENSAL, db_default=custos.MENSAL, verbose_name="Periodicidade")
    personalizavel = models.BooleanField(default=False, db_default=False, verbose_name="Pode ser personalizado por colaborador")
    condicao = models.CharField(max_length=255, blank=True, default="", verbose_name="Condição / elegibilidade / fonte")
    inicio = models.DateField(verbose_name="Início da vigência")
    fim = models.DateField(null=True, blank=True, verbose_name="Fim da vigência")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Item de custo da função"
        verbose_name_plural = "Itens de custo das funções"
        ordering = ["funcao", "categoria", "inicio", "nome"]
        indexes = [models.Index(fields=["funcao", "categoria", "inicio"], name="componente_funcao_vig_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valor__isnull=False, percentual__isnull=True) | models.Q(valor__isnull=True, percentual__isnull=False),
                name="componente_valor_ou_percentual",
            ),
        ]

    def __str__(self):
        return f"{self.nome} ({self.funcao})"

    @property
    def cliente_id(self):
        return self.funcao.cliente_id

    def clean(self):
        self.nome = " ".join((self.nome or "").split())
        try:
            custos.validar_item(self.categoria, self.tipo_valor, self.valor, self.percentual, self.base, self.periodicidade)
        except RegraNegocioError as erro:
            raise ValidationError(str(erro)) from None
        if self.inicio and self.fim and self.fim < self.inicio:
            raise ValidationError({"fim": "O fim da vigência não pode ser anterior ao início."})
        if self.categoria == custos.SALARIO and self.inicio and self.funcao_id and self.ativo:
            # Um único salário base por data: as vigências do salário não podem se sobrepor.
            outros = ComponenteFuncao.objects.filter(funcao_id=self.funcao_id, categoria=custos.SALARIO, ativo=True).exclude(pk=self.pk)
            for outro in outros:
                if self.inicio <= (outro.fim or date.max) and (self.fim or date.max) >= outro.inicio:
                    raise ValidationError("Já existe um salário base vigente neste período. Encerre o anterior antes de criar outro.")


class VinculoFuncao(models.Model):
    """Histórico de funções do colaborador: no máximo UM vínculo aberto (`fim` vazio). Trocar de função encerra o aberto
    e cria outro, sem apagar o passado (os custos antigos continuam calculáveis pela vigência)."""

    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="vinculos_funcao", verbose_name="Colaborador")
    funcao = models.ForeignKey(FuncaoMaoDeObra, on_delete=models.PROTECT, related_name="vinculos", verbose_name="Função")
    inicio = models.DateField(verbose_name="Início")
    fim = models.DateField(null=True, blank=True, verbose_name="Fim")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vínculo de função do colaborador"
        verbose_name_plural = "Vínculos de função dos colaboradores"
        ordering = ["colaborador", "-inicio"]
        constraints = [models.UniqueConstraint(fields=["colaborador"], condition=models.Q(fim__isnull=True), name="vinculo_funcao_um_aberto")]
        indexes = [models.Index(fields=["colaborador", "inicio"], name="vinculo_funcao_colab_ini_idx")]

    def __str__(self):
        return f"{self.colaborador} → {self.funcao}"

    def clean(self):
        if self.colaborador_id and self.funcao_id and self.colaborador.cliente_id != self.funcao.cliente_id:
            raise ValidationError({"funcao": "A função deve ser da mesma unidade do colaborador."})
        if self.inicio and self.fim and self.fim < self.inicio:
            raise ValidationError({"fim": "O fim não pode ser anterior ao início."})


class PersonalizacaoColaborador(models.Model):
    """Valor individual de um item marcado como personalizável, para UM vínculo (trocar de função não carrega personalizações)."""

    vinculo = models.ForeignKey(VinculoFuncao, on_delete=models.CASCADE, related_name="personalizacoes", verbose_name="Vínculo")
    componente = models.ForeignKey(ComponenteFuncao, on_delete=models.PROTECT, related_name="personalizacoes", verbose_name="Item")
    valor = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Valor (centavos)")
    percentual = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True, verbose_name="Percentual (%)")
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("valor",)

    class Meta:
        verbose_name = "Personalização do colaborador"
        verbose_name_plural = "Personalizações dos colaboradores"
        constraints = [
            models.UniqueConstraint(fields=["vinculo", "componente"], name="personalizacao_unica"),
            models.CheckConstraint(
                condition=models.Q(valor__isnull=False, percentual__isnull=True) | models.Q(valor__isnull=True, percentual__isnull=False),
                name="personalizacao_valor_ou_percentual",
            ),
        ]

    def clean(self):
        if not (self.vinculo_id and self.componente_id):
            return
        componente = self.componente
        if componente.funcao_id != self.vinculo.funcao_id:
            raise ValidationError("O item não pertence à função do vínculo.")
        if not componente.personalizavel:
            raise ValidationError("Este item não pode ser personalizado.")
        try:
            custos.validar_item(componente.categoria, componente.tipo_valor, self.valor, self.percentual, componente.base, componente.periodicidade)
        except RegraNegocioError as erro:
            raise ValidationError(str(erro)) from None


class SimulacaoPromocao(models.Model):
    """Cenário de PLANEJAMENTO (promoção). Guarda só os parâmetros; o resultado é recalculado a cada consulta e NUNCA altera
    colaborador ou função. Por função: `funcao_origem` (+ `funcao_destino` para movimentar todos; vazio = só ajustar o salário da
    própria função). Por pessoa: `colaborador` (+ `funcao_destino` opcional)."""

    FUNCAO, PESSOA = "funcao", "pessoa"
    TIPOS = [(FUNCAO, "Por função"), (PESSOA, "Por pessoa")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="simulacoes_promocao", verbose_name="Unidade")
    nome = models.CharField(max_length=100, verbose_name="Nome do cenário")
    tipo = models.CharField(max_length=6, choices=TIPOS, default=PESSOA, verbose_name="Tipo")
    colaborador = models.ForeignKey(Colaborador, null=True, blank=True, on_delete=models.PROTECT, related_name="simulacoes", verbose_name="Colaborador")
    funcao_origem = models.ForeignKey(FuncaoMaoDeObra, null=True, blank=True, on_delete=models.PROTECT, related_name="simulacoes_origem", verbose_name="Função de origem")
    funcao_destino = models.ForeignKey(FuncaoMaoDeObra, null=True, blank=True, on_delete=models.PROTECT, related_name="simulacoes_destino", verbose_name="Função de destino")
    novo_salario = models.PositiveBigIntegerField(null=True, blank=True, verbose_name="Novo salário mensal (centavos)")
    ajuste_salario_percentual = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, verbose_name="Ajuste do salário (%)")
    data_referencia = models.DateField(verbose_name="Data de referência")
    observacao = models.CharField(max_length=255, blank=True, default="", verbose_name="Observações")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_MONETARIOS = ("novo_salario",)

    class Meta:
        verbose_name = "Simulação de promoção"
        verbose_name_plural = "Simulações de promoção"
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["cliente", "criado_em"], name="simulacao_cliente_data_idx")]

    def __str__(self):
        return self.nome

    def clean(self):
        self.nome = " ".join((self.nome or "").split())
        erros = {}
        if self.tipo == self.PESSOA:
            if not self.colaborador_id:
                erros["colaborador"] = "Escolha o colaborador."
            self.funcao_origem = None
        else:
            if not self.funcao_origem_id:
                erros["funcao_origem"] = "Escolha a função de origem."
            self.colaborador = None
        if self.novo_salario is not None and self.ajuste_salario_percentual is not None:
            erros["ajuste_salario_percentual"] = "Informe o novo salário OU o ajuste percentual, não os dois."
        if self.novo_salario is not None and self.novo_salario > custos.LIMITE_CENTAVOS:
            erros["novo_salario"] = "Valor acima do limite permitido."
        if self.ajuste_salario_percentual is not None and not (Decimal("-100") < self.ajuste_salario_percentual <= Decimal("1000")):
            erros["ajuste_salario_percentual"] = "Use um ajuste entre -100% (exclusive) e 1000%."
        for campo in ("colaborador", "funcao_origem", "funcao_destino"):
            objeto = getattr(self, campo)
            if objeto is not None and self.cliente_id and objeto.cliente_id != self.cliente_id:
                erros[campo] = "Deve ser da mesma unidade da simulação."
        if erros:
            raise ValidationError(erros)
