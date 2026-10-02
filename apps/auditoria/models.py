from django.conf import settings
from django.db import models


# Códigos de ação gravados no banco e seus rótulos de exibição.
ACAO_CRIAR = "criar"
ACAO_EDITAR = "editar"
ACAO_EXCLUIR = "excluir"
ACOES = {
    ACAO_CRIAR: "Criação (CREATE)",
    ACAO_EDITAR: "Atualização (UPDATE)",
    ACAO_EXCLUIR: "Exclusão (DELETE)",
    "login": "Login",
    "logout": "Logout",
    "exportar": "Exportação",
}


class RegistroAuditoria(models.Model):
    """Trilha de auditoria: quem fez o quê, em qual cliente/módulo, quando
    e com que resultado. Nunca editável pela aplicação depois de criado
    (ver apps/auditoria/admin.py) — um log de auditoria que pode ser
    alterado não serve para nada.

    Preenchido por apps/auditoria/utils.py: `registrar()` (genérico, usado
    pelos sinais de login/logout) e `registrar_criacao/edicao/exclusao()`
    (CRUD, chamados pelo AuditoriaFormMixin e pelas views de exclusão).
    Para auditar um novo módulo, veja a seção "Auditoria" do README.
    """

    RESULTADO_SUCESSO = "sucesso"
    RESULTADO_FALHA = "falha"
    RESULTADO_CHOICES = [
        (RESULTADO_SUCESSO, "Sucesso"),
        (RESULTADO_FALHA, "Falha"),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name="Usuário",
    )
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_auditoria",
        verbose_name="Cliente",
    )
    acao = models.CharField(max_length=50, verbose_name="Ação")
    modulo = models.CharField(max_length=50, blank=True, verbose_name="Módulo")
    # Tabela do banco afetada (ex.: "insumo_insumo"), id e nome/descrição do
    # registro no momento da operação (o registro pode ser excluído depois,
    # por isso o nome é gravado aqui e não consultado).
    tabela = models.CharField(max_length=100, blank=True, verbose_name="Tabela")
    registro_id = models.CharField(max_length=50, blank=True, verbose_name="Registro afetado")
    registro_nome = models.CharField(max_length=255, blank=True, verbose_name="Nome do registro")
    descricao = models.TextField(blank=True, verbose_name="Descrição")
    # Lista de {"campo", "rotulo", "antes", "depois"} — só campos que mudaram
    # (UPDATE). Textos já formatados para exibição (ex.: "R$ 35,00").
    alteracoes = models.JSONField(default=list, blank=True, verbose_name="Alterações")
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Endereço IP")
    resultado = models.CharField(
        max_length=10, choices=RESULTADO_CHOICES, default=RESULTADO_SUCESSO, verbose_name="Resultado"
    )
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Data/hora")

    class Meta:
        verbose_name = "Registro de auditoria"
        verbose_name_plural = "Registros de auditoria"
        ordering = ["-criado_em"]
        # Log imutável: só existe a permissão de visualizar (nada de
        # add/change/delete pela aplicação).
        default_permissions = ("view",)
        indexes = [
            models.Index(fields=["modulo", "criado_em"], name="auditoria_modulo_data_idx"),
            models.Index(fields=["cliente", "criado_em"], name="auditoria_cliente_data_idx"),
        ]

    @property
    def acao_rotulo(self):
        return ACOES.get(self.acao, self.acao)

    def __str__(self):
        quem = self.usuario.username if self.usuario_id else "anônimo"
        return f"{self.acao} ({quem}) em {self.criado_em:%d/%m/%Y %H:%M}"
