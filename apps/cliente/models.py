from django.core.exceptions import ValidationError
from django.db import models

from .validators import (
    formatar_documento,
    normalizar_documento,
    validar_cnpj,
    validar_cpf,
)


class Cliente(models.Model):
    """Representa a organização (pessoa física ou jurídica) identificada
    por CPF/CNPJ — o "tenant" do sistema. Cada Cliente pode ter N usuários
    vinculados (ver auth.models.Profile.cliente), mas cada usuário
    pertence a exatamente um Cliente.
    """

    TIPO_PF = "PF"
    TIPO_PJ = "PJ"
    TIPO_PESSOA_CHOICES = [
        (TIPO_PF, "Pessoa Física"),
        (TIPO_PJ, "Pessoa Jurídica"),
    ]

    tipo_pessoa = models.CharField(
        max_length=2,
        choices=TIPO_PESSOA_CHOICES,
        verbose_name="Tipo de pessoa",
    )
    # Sempre GRAVADO normalizado (somente dígitos, no máximo 14 caracteres:
    # tamanho de um CNPJ) — ver Cliente.clean(). O max_length do campo
    # precisa ser 18 (não 14): a validação de tamanho do Django roda ANTES
    # de clean_fields() chamar clean(), então a entrada ainda com máscara
    # ("11.222.333/0001-81", 18 caracteres) tem que caber no campo para
    # sequer chegar à normalização — senão um CNPJ mascarado era rejeitado
    # só por causa do tamanho, antes de qualquer validação de fato.
    cpf_cnpj = models.CharField(
        max_length=18,
        unique=True,
        db_index=True,
        verbose_name="CPF/CNPJ",
        help_text="Informe com ou sem máscara; é validado e normalizado automaticamente.",
    )
    nome_razao_social = models.CharField(max_length=200, verbose_name="Nome / Razão social")
    nome_fantasia = models.CharField(max_length=200, blank=True, verbose_name="Nome fantasia")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")

    # Estrutura Matriz/Filial: só CNPJ (PJ) pode ter filiais. Uma filial
    # aponta obrigatoriamente para a sua matriz; a matriz é a "raiz" da
    # estrutura e todos os seus integrantes compartilham a mesma visão de
    # clientes e usuários (ver web_project/acesso.py).
    TIPO_MATRIZ = "matriz"
    TIPO_FILIAL = "filial"
    TIPO_ESTABELECIMENTO_CHOICES = [(TIPO_MATRIZ, "Matriz"), (TIPO_FILIAL, "Filial")]
    tipo_estabelecimento = models.CharField(
        max_length=10, choices=TIPO_ESTABELECIMENTO_CHOICES, default=TIPO_MATRIZ, db_default=TIPO_MATRIZ,
        verbose_name="Matriz/Filial",
    )
    matriz = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="filiais", verbose_name="Matriz",
    )
    # Segmento de trabalho do CLIENTE (não do usuário): define os módulos
    # que a empresa pode usar (ver apps/modulo/models.py::Segmento).
    segmento = models.ForeignKey(
        "modulo.Segmento", null=True, blank=True, on_delete=models.PROTECT, related_name="clientes",
        verbose_name="Segmento de trabalho",
    )
    # Marca o cliente dono do sistema ("SISTEMA FATURANDO CERTO SERVIÇOS
    # LTDA"): único ao qual um usuário Admin pode estar vinculado.
    sistema = models.BooleanField(default=False, db_default=False, editable=False, verbose_name="Cliente do sistema")
    # Perfis (papéis/Groups do ACL) que podem ser atribuídos aos usuários
    # DESTE cliente. Ao criar um usuário, o perfil escolhido precisa estar
    # nesta lista (ver apps/usuario/forms.py) — assim "Usuário -> Cliente ->
    # Perfil -> Permissões" é uma cadeia validada no backend.
    perfis = models.ManyToManyField(
        "auth.Group",
        blank=True,
        related_name="clientes",
        verbose_name="Perfis disponíveis",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["nome_razao_social"]
        constraints = [
            # Só um cliente pode ser o "cliente do sistema" (onde o Admin vive).
            models.UniqueConstraint(fields=["sistema"], condition=models.Q(sistema=True), name="cliente_sistema_unico"),
        ]
        # Ações de uma MATRIZ sobre os itens (insumos, categorias...) das suas
        # filiais. Itens da própria unidade seguem as permissões de cada módulo;
        # estas são exigidas EM ADIÇÃO para alcançar os itens das filiais (ver
        # web_project/acesso.py::clientes_dos_itens e apps/acl/README.md).
        permissions = [
            ("view_itensfilial", "Visualizar itens das filiais"),
            ("add_itensfilial", "Criar itens nas filiais"),
            ("change_itensfilial", "Editar itens das filiais"),
            ("delete_itensfilial", "Excluir itens das filiais"),
        ]

    def __str__(self):
        return f"{self.nome_razao_social} ({self.cpf_cnpj_formatado})"

    def clean(self):
        # Normaliza e valida matematicamente o CPF/CNPJ aqui (no model, não
        # só no formulário) para que a regra valha para qualquer forma de
        # gravação (admin do Django, uma tela nova, um script de migração
        # futuro) — nunca confiar só na validação do frontend/formulário.
        documento = normalizar_documento(self.cpf_cnpj)

        if self.tipo_pessoa == self.TIPO_PF:
            if not validar_cpf(documento):
                raise ValidationError({"cpf_cnpj": "CPF inválido. Confira os números informados."})
        elif self.tipo_pessoa == self.TIPO_PJ:
            if not validar_cnpj(documento):
                raise ValidationError({"cpf_cnpj": "CNPJ inválido. Confira os números informados."})
        else:
            raise ValidationError({"tipo_pessoa": "Selecione se é Pessoa Física ou Pessoa Jurídica."})

        self.cpf_cnpj = documento
        self._validar_estrutura()

    def _validar_estrutura(self):
        """Regras de Matriz/Filial (valem para qualquer forma de gravação)."""
        if self.tipo_estabelecimento == self.TIPO_FILIAL:
            if self.tipo_pessoa != self.TIPO_PJ:
                raise ValidationError({"tipo_estabelecimento": "Somente CNPJ pode ser filial."})
            if not self.matriz_id:
                raise ValidationError({"matriz": "Informe a matriz da filial."})
            if self.matriz_id == self.pk:
                raise ValidationError({"matriz": "Uma filial não pode ser matriz de si mesma."})
            if self.matriz.tipo_estabelecimento != self.TIPO_MATRIZ or self.matriz.tipo_pessoa != self.TIPO_PJ:
                raise ValidationError({"matriz": "A matriz precisa ser um CNPJ cadastrado como Matriz."})
            if self.pk and self.filiais.exists():
                raise ValidationError({"tipo_estabelecimento": "Este cliente possui filiais e não pode virar filial."})
        elif self.matriz_id:
            raise ValidationError({"matriz": "Somente filiais possuem matriz."})

    @property
    def raiz_id(self):
        """Id da matriz da estrutura (o próprio id, se for matriz)."""
        return self.matriz_id or self.pk

    @property
    def cpf_cnpj_formatado(self):
        return formatar_documento(self.cpf_cnpj, self.tipo_pessoa)
