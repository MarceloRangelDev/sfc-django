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

    @property
    def cpf_cnpj_formatado(self):
        return formatar_documento(self.cpf_cnpj, self.tipo_pessoa)
