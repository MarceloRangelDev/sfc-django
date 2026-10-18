import re
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from web_project.regras import RegraNegocioError
from web_project.regras.conversao import preco_por_unidade_padrao
from web_project.regras.dinheiro import DinheiroService

# Teto de segurança para valores em centavos (R$ 10 trilhões): impede estouro
# do BIGINT quando uma quantidade minúscula é convertida para preço por KG/L.
LIMITE_CENTAVOS = 10**15


def validar_marca(valor):
    """A marca é texto livre curto: letras (com acento), números, espaço e
    pontuação comum de nomes comerciais. Barra "<", ">", aspas duplas e
    caracteres de controle (evita marcação/script colado em telas e relatórios)."""
    if not re.fullmatch(r"[\w .,&'+/()%\-]*", valor or ""):
        raise ValidationError("A marca contém caracteres não permitidos.")


class Insumo(models.Model):
    """Insumo utilizado nas receitas/produtos de um Cliente.

    Dinheiro é guardado em CENTAVOS (inteiro) — nunca float nem Decimal no
    banco (ver web_project/regras/dinheiro.py). Exemplo do cadastro:

        comprou 100 g por R$ 4,00  ->  quantidade=100, medida=Gramas,
                                       preco_pago=400, preco_unidade=4000
                                       (R$ 40,00 por KG, calculado)

    `preco_unidade` é recalculado a cada save() pela regra central
    (web_project/regras/conversao.py) usando o fator da tabela `medidas`.

    Insumos NÃO têm categoria (as categorias são dos produtos). A coluna
    `categoria` continua no banco, oculta e opcional, só para não perder os
    dados de insumos cadastrados antes desta mudança.
    """

    # Isolamento multi-tenant: todo Insumo pertence a exatamente um
    # Cliente, e toda consulta/edição/exclusão deve filtrar por ele (ver
    # web_project/mixins.py::ClienteScopedMixin, usado em apps/insumo/views.py).
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.PROTECT,
        related_name="insumos",
        verbose_name="Cliente",
    )
    nome = models.CharField(max_length=150, verbose_name="Insumo")
    categoria = models.ForeignKey(
        "categoria.Categoria",
        on_delete=models.PROTECT,
        related_name="insumos",
        verbose_name="Categoria",
        null=True,
        blank=True,
        editable=False,  # oculta: fora de formulários, admin e API
    )
    # PROTECT: uma medida em uso nunca é apagada em cascata (ver
    # apps/medida/views.py::MedidaExcluirView — ela só é inativada).
    medida = models.ForeignKey(
        "medida.Medida",
        on_delete=models.PROTECT,
        related_name="insumos",
        verbose_name="Unidade de medida",
    )
    quantidade = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"), message="A quantidade deve ser maior que zero.")],
        verbose_name="Quantidade comprada",
    )
    preco_pago = models.PositiveBigIntegerField(verbose_name="Valor pago", default=0)
    # Marca do produto comprado (ex.: "Qualy"). Opcional: vazio é válido.
    marca = models.CharField(
        max_length=60, blank=True, default="", validators=[validar_marca], verbose_name="Marca do produto",
    )
    # Preço de 1 KG / 1 L / 1 UN (conforme a dimensão da medida), em
    # centavos. Calculado em save(); None se não puder ser calculado.
    preco_unidade = models.PositiveBigIntegerField(null=True, blank=True, editable=False, verbose_name="Preço por unidade (centavos)")
    # Exclusão lógica: "excluir" um insumo só marca ativo=False (ver
    # apps/insumo/views.py::InsumoExcluirView) — o registro permanece no
    # banco (histórico, fichas técnicas que já o referenciam etc.) e some
    # apenas da listagem padrão.
    # Identificador público ESTÁVEL do insumo (usado no QR Code das etiquetas): aleatório, não adivinhável
    # em sequência e sem dado sensível. Nunca muda. Quem escaneia ainda precisa de login e do tenant certo
    # (ver apps/insumo/views.py::InsumoPorUuidView).
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, verbose_name="Identificador (QR Code)")
    # Validade padrão em dias após a emissão da etiqueta; usada quando o usuário escolhe validade "calculada".
    dias_validade = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)], verbose_name="Validade padrão (dias)",
    )
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    # Padronização (cadastro global de insumos): `insumo_global` = o padrão a que este insumo pertence; `nao_conforme` = nome digitado que
    # NÃO existe no cadastro global, aguardando o administrador (ver apps/insumoglobal/services.py).
    insumo_global = models.ForeignKey("insumoglobal.InsumoGlobal", null=True, blank=True, editable=False, on_delete=models.PROTECT, related_name="insumos", verbose_name="Insumo padrão")
    nao_conforme = models.BooleanField(default=False, db_default=False, editable=False, verbose_name="Fora do padrão")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Cadastrado em")
    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")

    # Campos em centavos: a auditoria os mostra como "R$ 40,00".
    AUDITORIA_MONETARIOS = ("preco_pago",)
    # Campos opcionais que, quando preenchidos, entram também no registro de CRIAÇÃO.
    AUDITORIA_NA_CRIACAO = ("marca",)

    class Meta:
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"
        ordering = ["nome"]
        unique_together = ("cliente", "nome")
        indexes = [models.Index(fields=["cliente", "ativo"], name="insumo_cliente_ativo_idx")]

    def __str__(self):
        return self.nome

    def calcular_preco_unidade(self):
        """Preço por KG/L/UN em centavos, ou None se a quantidade for zero.

        Levanta RegraNegocioError para entradas inválidas (usado pelo form
        para dar uma mensagem amigável antes de gravar)."""
        if not self.quantidade or self.quantidade <= 0:
            return None
        valor = preco_por_unidade_padrao(self.preco_pago, self.quantidade, self.medida)
        if valor > LIMITE_CENTAVOS:
            raise RegraNegocioError("O preço por unidade calculado é grande demais; confira a quantidade e o valor pago.")
        return valor

    def save(self, *args, **kwargs):
        # Só recalcula quando o save grava os campos de que o preço depende
        # (o soft delete usa update_fields=["ativo", ...] e não precisa).
        campos = kwargs.get("update_fields")
        if campos is None or {"quantidade", "medida", "preco_pago"} & set(campos):
            self.preco_unidade = self.calcular_preco_unidade()
            if campos is not None and "preco_unidade" not in campos:
                kwargs["update_fields"] = [*campos, "preco_unidade"]
        super().save(*args, **kwargs)

    @property
    def codigo(self):
        """Código de exibição — não é um campo próprio para nunca
        dessincronizar do id real do registro."""
        return f"INS-{self.pk:05d}" if self.pk else ""

    @property
    def preco_pago_formatado(self):
        return DinheiroService.formatar(self.preco_pago)

    @property
    def preco_unidade_formatado(self):
        return DinheiroService.formatar(self.preco_unidade)

    @property
    def rotulo_preco(self):
        """'PREÇO (por KG)' — a sigla muda conforme a dimensão da medida."""
        return f"PREÇO (por {self.medida.sigla_preco})"

    @property
    def quantidade_por_extenso(self):
        return self.medida.por_extenso(self.quantidade)
