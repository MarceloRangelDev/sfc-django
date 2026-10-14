import re
from datetime import date

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.cliente.validators import normalizar_documento, validar_cnpj, validar_cpf

UFS = (
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
REGEX_INSTAGRAM = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def mascarar_documento(documento):
    """Documento para LISTAS e telas sem necessidade do número completo: CPF `***.456.789-**`,
    CNPJ `**.345.678/****-**`. O número completo só aparece no formulário/detalhe (permissão de
    visualizar/editar). Outro tamanho de documento é totalmente ocultado."""
    d = normalizar_documento(documento)
    if len(d) == 11:
        return f"***.{d[3:6]}.{d[6:9]}-**"
    if len(d) == 14:
        return f"**.{d[2:5]}.{d[5:8]}/****-**"
    return "—" if not d else "***"


class Consumidor(models.Model):
    """Cliente FINAL de uma empresa (quem compra no PDV, faz pedidos, recebe cotações...).

    Não confundir com `cliente.Cliente`, que é o TENANT (a empresa que usa o sistema): o campo
    `cliente` deste model aponta para a empresa/unidade dona do cadastro (isolamento multi-tenant,
    ver web_project/mixins.py::ClienteScopedMixin).

    Dados pessoais (LGPD): só o necessário; documento, contatos e endereço não vão para a trilha de
    auditoria (AUDITORIA_SEM_CONTEUDO) nem para listas (documento é mascarado). "Excluir" = ANONIMIZAR
    (services.anonimizar_consumidor): apaga os dados pessoais e mantém o registro para não quebrar
    vendas/pedidos futuros que o referenciem.
    """

    TIPO_PF, TIPO_PJ = "PF", "PJ"
    TIPO_CHOICES = [(TIPO_PF, "Pessoa Física"), (TIPO_PJ, "Pessoa Jurídica")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="consumidores", verbose_name="Unidade")
    nome = models.CharField(max_length=150, verbose_name="Nome / Razão social")
    tipo_pessoa = models.CharField(max_length=2, choices=TIPO_CHOICES, default=TIPO_PF, db_default=TIPO_PF, verbose_name="Tipo de pessoa")
    # Sempre GRAVADO só com dígitos (CPF 11 / CNPJ 14); opcional. max_length 18 deixa a entrada com
    # máscara chegar ao clean(), que normaliza (mesma razão do campo de apps/cliente/models.py).
    documento = models.CharField(max_length=18, blank=True, default="", verbose_name="CPF/CNPJ")
    email = models.EmailField(max_length=254, blank=True, default="", verbose_name="E-mail")
    telefone = models.CharField(max_length=20, blank=True, default="", verbose_name="Telefone")
    whatsapp = models.CharField(max_length=20, blank=True, default="", verbose_name="WhatsApp")
    instagram = models.CharField(max_length=40, blank=True, default="", verbose_name="Instagram")
    data_nascimento = models.DateField(null=True, blank=True, verbose_name="Data de nascimento")
    cep = models.CharField(max_length=9, blank=True, default="", verbose_name="CEP")
    logradouro = models.CharField(max_length=150, blank=True, default="", verbose_name="Logradouro")
    numero = models.CharField(max_length=20, blank=True, default="", verbose_name="Número")
    complemento = models.CharField(max_length=100, blank=True, default="", verbose_name="Complemento")
    bairro = models.CharField(max_length=100, blank=True, default="", verbose_name="Bairro")
    cidade = models.CharField(max_length=100, blank=True, default="", verbose_name="Cidade")
    uf = models.CharField(max_length=2, blank=True, default="", verbose_name="UF")
    # Consentimento LGPD: registrado (com data) quando marcado; limpo quando retirado.
    consentimento_lgpd = models.BooleanField(default=False, db_default=False, verbose_name="Consentimento LGPD")
    consentimento_em = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Consentimento em")
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    # Preenchido quando os dados pessoais foram apagados; o registro não pode mais ser editado ou reativado.
    anonimizado_em = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Anonimizado em")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Dados pessoais: a auditoria diz QUE mudaram, nunca o conteúdo.
    AUDITORIA_SEM_CONTEUDO = (
        "documento", "email", "telefone", "whatsapp", "instagram", "data_nascimento",
        "cep", "logradouro", "numero", "complemento", "bairro",
    )

    class Meta:
        verbose_name = "Consumidor"
        verbose_name_plural = "Consumidores"
        ordering = ["nome"]
        constraints = [
            # Mesmo documento não se repete na mesma unidade (vários consumidores sem documento são permitidos).
            models.UniqueConstraint(
                fields=["cliente", "documento"], condition=~Q(documento=""), name="consumidor_documento_unico_por_unidade"
            ),
        ]
        indexes = [
            models.Index(fields=["cliente", "ativo"], name="consumidor_cliente_ativo_idx"),
            models.Index(fields=["cliente", "nome"], name="consumidor_cliente_nome_idx"),
        ]

    def __str__(self):
        return self.nome

    @property
    def documento_mascarado(self):
        return mascarar_documento(self.documento)

    @property
    def anonimizado(self):
        return self.anonimizado_em is not None

    @property
    def cidade_uf(self):
        return " / ".join(p for p in (self.cidade, self.uf) if p) or "—"

    def clean(self):
        """Normaliza e valida no MODEL: vale para tela, API e qualquer gravação (não só o formulário)."""
        erros = {}

        self.nome = (self.nome or "").strip()
        if not self.nome:
            erros["nome"] = "O nome é obrigatório."

        self.documento = normalizar_documento(self.documento)
        if self.documento:
            valido = validar_cpf(self.documento) if self.tipo_pessoa == self.TIPO_PF else validar_cnpj(self.documento)
            if not valido:
                erros["documento"] = "CPF inválido." if self.tipo_pessoa == self.TIPO_PF else "CNPJ inválido."
            elif self.cliente_id:
                repetido = Consumidor.objects.filter(cliente_id=self.cliente_id, documento=self.documento).exclude(pk=self.pk)
                if repetido.exists():
                    erros["documento"] = "Já existe um consumidor com este documento nesta unidade."

        self.email = (self.email or "").strip().lower()

        for campo, minimo, maximo in (("telefone", 10, 11), ("whatsapp", 10, 13)):  # whatsapp aceita o DDI 55
            digitos = re.sub(r"\D", "", getattr(self, campo) or "")
            setattr(self, campo, digitos)
            if digitos and not minimo <= len(digitos) <= maximo:
                erros[campo] = f"Informe DDD + número ({minimo} a {maximo} dígitos)."

        instagram = (self.instagram or "").strip().lower()
        instagram = re.sub(r"^(https?://)?(www\.)?instagram\.com/", "", instagram).strip("/").lstrip("@")
        self.instagram = instagram
        if instagram and not REGEX_INSTAGRAM.match(instagram):
            erros["instagram"] = "Usuário do Instagram inválido (letras, números, ponto e sublinhado; até 30)."

        self.cep = re.sub(r"\D", "", self.cep or "")
        if self.cep and len(self.cep) != 8:
            erros["cep"] = "O CEP deve ter 8 dígitos."

        self.uf = (self.uf or "").strip().upper()
        if self.uf and self.uf not in UFS:
            erros["uf"] = "UF inválida."

        if self.data_nascimento and not date(1900, 1, 1) <= self.data_nascimento <= timezone.localdate():
            erros["data_nascimento"] = "Data de nascimento inválida."

        # Consentimento: a data acompanha o aceite; retirar o consentimento limpa a data.
        if self.consentimento_lgpd and self.consentimento_em is None:
            self.consentimento_em = timezone.now()
        elif not self.consentimento_lgpd:
            self.consentimento_em = None

        if erros:
            raise ValidationError(erros)
