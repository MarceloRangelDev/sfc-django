from django.conf import settings
from django.db import models
from django.db.models import Q


class ConfiguracaoFiscal(models.Model):
    """Dados técnicos da emissão fiscal de uma unidade (uma linha por unidade). O REGIME TRIBUTÁRIO e a opção de emitir nota ficam no
    cadastro do Cliente (`cliente_cliente.regime_tributario` / `emite_nota_fiscal`); aqui ficam ambiente, provedor, inscrições, código do
    município, tipos habilitados e o CERTIFICADO DIGITAL A1 (cifrado; senha e arquivo nunca são exibidos).

    ESTADO DA EMISSÃO: o sistema só marca um documento como AUTORIZADO quando o provedor fiscal confirma (protocolo/chave reais).
    Enquanto o provedor não estiver implementado/configurado, os documentos ficam PENDENTES com o motivo — nada é simulado.
    """

    HOMOLOGACAO, PRODUCAO = "homologacao", "producao"
    AMBIENTE_CHOICES = [(HOMOLOGACAO, "Homologação (testes)"), (PRODUCAO, "Produção")]
    PROVEDOR_NENHUM, PROVEDOR_NFSE_NACIONAL = "nenhum", "nfse_nacional"
    PROVEDOR_CHOICES = [(PROVEDOR_NENHUM, "Nenhum (documentos ficam pendentes)"), (PROVEDOR_NFSE_NACIONAL, "Emissor Nacional (NFS-e, nfse.gov.br)")]

    cliente = models.OneToOneField("cliente.Cliente", on_delete=models.CASCADE, related_name="configuracao_fiscal", verbose_name="Unidade")
    ambiente = models.CharField(max_length=12, choices=AMBIENTE_CHOICES, default=HOMOLOGACAO, db_default=HOMOLOGACAO, verbose_name="Ambiente")
    provedor = models.CharField(max_length=20, choices=PROVEDOR_CHOICES, default=PROVEDOR_NENHUM, db_default=PROVEDOR_NENHUM, verbose_name="Provedor fiscal")
    habilita_nfce = models.BooleanField(default=False, db_default=False, verbose_name="NFC-e (cupom fiscal eletrônico, varejo)")
    habilita_nfe = models.BooleanField(default=False, db_default=False, verbose_name="NF-e (nota fiscal eletrônica) e DANFE")
    habilita_nfse = models.BooleanField(default=False, db_default=False, verbose_name="NFS-e (serviços)")
    inscricao_municipal = models.CharField(max_length=20, blank=True, default="", verbose_name="Inscrição municipal")
    inscricao_estadual = models.CharField(max_length=20, blank=True, default="", verbose_name="Inscrição estadual")
    codigo_municipio = models.CharField(max_length=7, blank=True, default="", verbose_name="Código IBGE do município (7 dígitos)")
    serie = models.PositiveSmallIntegerField(default=1, db_default=1, verbose_name="Série")
    # Certificado A1 (.pfx) + senha: cifrados com apps/integracao/cripto.py. Só metadados públicos ficam em claro.
    certificado_cifrado = models.TextField(blank=True, default="", editable=False)
    certificado_validade = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Certificado válido até")
    certificado_assunto = models.CharField(max_length=255, blank=True, default="", editable=False, verbose_name="Titular do certificado")
    atualizado_em = models.DateTimeField(auto_now=True)

    # Fora da trilha: inscrições e município são dados cadastrais da empresa, mas a trilha só registra que mudaram.
    AUDITORIA_SEM_CONTEUDO = ("inscricao_municipal", "inscricao_estadual")

    class Meta:
        verbose_name = "Configuração fiscal"
        verbose_name_plural = "Configurações fiscais"
        permissions = [("emitir_nota", "Solicitar emissão de notas fiscais")]

    def __str__(self):
        return f"Fiscal de {self.cliente}"

    @classmethod
    def da_unidade(cls, cliente):
        configuracao, _ = cls.objects.get_or_create(cliente=cliente)
        return configuracao

    @property
    def tem_certificado(self):
        return bool(self.certificado_cifrado)

    def tipos_habilitados(self):
        return [t for t, ativo in (("nfce", self.habilita_nfce), ("nfe", self.habilita_nfe), ("nfse", self.habilita_nfse)) if ativo]


class DocumentoFiscal(models.Model):
    """Documento fiscal (NFC-e, NF-e ou NFS-e) pedido para uma venda. A venda NÃO depende dele: o comprovante não fiscal continua valendo.

    Estados: PENDENTE (aguardando o provedor/condições — ver `motivo`), PROCESSANDO, AUTORIZADA (somente com resposta real do provedor:
    `chave_acesso` e `protocolo` preenchidos), REJEITADA (o provedor recusou; `motivo` traz a mensagem), CANCELADA, ERRO (falha definitiva).
    `xml_autorizado` guarda o XML devolvido pelo provedor (nunca gerado por nós) para armazenamento e consulta.
    """

    NFCE, NFE, NFSE = "nfce", "nfe", "nfse"
    TIPO_CHOICES = [(NFCE, "NFC-e"), (NFE, "NF-e"), (NFSE, "NFS-e")]
    PENDENTE, PROCESSANDO, AUTORIZADA, REJEITADA, CANCELADA, ERRO = "pendente", "processando", "autorizada", "rejeitada", "cancelada", "erro"
    STATUS_CHOICES = [(PENDENTE, "Pendente"), (PROCESSANDO, "Processando"), (AUTORIZADA, "Autorizada"), (REJEITADA, "Rejeitada"), (CANCELADA, "Cancelada"), (ERRO, "Erro")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="documentos_fiscais", verbose_name="Unidade")
    venda = models.ForeignKey("pdv.Venda", on_delete=models.PROTECT, related_name="documentos_fiscais", verbose_name="Venda")
    tipo = models.CharField(max_length=5, choices=TIPO_CHOICES, verbose_name="Tipo")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDENTE, db_default=PENDENTE, verbose_name="Status")
    ambiente = models.CharField(max_length=12, choices=ConfiguracaoFiscal.AMBIENTE_CHOICES, verbose_name="Ambiente")
    motivo = models.CharField(max_length=500, blank=True, default="", verbose_name="Situação / motivo")
    numero = models.PositiveIntegerField(null=True, blank=True, verbose_name="Número")
    serie = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Série")
    chave_acesso = models.CharField(max_length=60, blank=True, default="", verbose_name="Chave de acesso")
    protocolo = models.CharField(max_length=60, blank=True, default="", verbose_name="Protocolo de autorização")
    xml_autorizado = models.TextField(blank=True, default="", editable=False)
    autorizado_em = models.DateTimeField(null=True, blank=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    proxima_tentativa = models.DateTimeField(null=True, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    AUDITORIA_SEM_CONTEUDO = ("chave_acesso", "protocolo")

    class Meta:
        verbose_name = "Documento fiscal"
        verbose_name_plural = "Documentos fiscais"
        ordering = ["-criado_em", "-pk"]
        constraints = [
            # Um documento ATIVO (não cancelado/erro/rejeitado) por venda e tipo: repetir o pedido não duplica.
            models.UniqueConstraint(fields=["venda", "tipo"], condition=Q(status__in=["pendente", "processando", "autorizada"]), name="documento_fiscal_ativo_unico"),
        ]
        indexes = [models.Index(fields=["cliente", "status"], name="docfiscal_cliente_status_idx"), models.Index(fields=["status", "proxima_tentativa"], name="docfiscal_fila_idx")]

    def __str__(self):
        return f"{self.get_tipo_display()} da venda #{self.venda.numero}"
