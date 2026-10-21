import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q

from .adaptadores import CHOICES


def novo_token_webhook():
    """Token público e imprevisível da URL do webhook (não é segredo de autenticação: a assinatura do provedor é que autentica)."""
    return secrets.token_urlsafe(24)


class Conexao(models.Model):
    """Conexão de uma unidade (matriz/filial) com um provedor externo. As credenciais ficam CIFRADAS (cripto.py) e nunca são
    exibidas, logadas ou auditadas em texto; `config` guarda só dados não secretos (id do calendário, número, URL base...)."""

    PENDENTE, CONECTADA, ERRO, REVOGADA, DESCONECTADA = "pendente", "conectada", "erro", "revogada", "desconectada"
    STATUS_CHOICES = [(PENDENTE, "Pendente"), (CONECTADA, "Conectada"), (ERRO, "Com erro"), (REVOGADA, "Revogada"), (DESCONECTADA, "Desconectada")]

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="conexoes", verbose_name="Unidade")
    provedor = models.CharField(max_length=30, choices=CHOICES, verbose_name="Provedor")
    nome = models.CharField(max_length=80, verbose_name="Nome da conexão")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDENTE, db_default=PENDENTE, verbose_name="Estado")
    escopos = models.CharField(max_length=300, blank=True, default="", verbose_name="Escopos concedidos")
    credenciais_cifradas = models.TextField(blank=True, default="", editable=False)
    config = models.JSONField(default=dict, blank=True, verbose_name="Configuração")
    expira_em = models.DateTimeField(null=True, blank=True, verbose_name="Credencial expira em")
    ultimo_erro = models.CharField(max_length=255, blank=True, default="", verbose_name="Último erro")
    ultima_sincronizacao = models.DateTimeField(null=True, blank=True, verbose_name="Última sincronização")
    webhook_token = models.CharField(max_length=64, unique=True, default=novo_token_webhook, editable=False)
    # Notificar (Web Push do sistema) os usuários autorizados quando chegar mensagem desta conexão.
    notificar_usuarios = models.BooleanField(default=True, db_default=True, verbose_name="Notificar a equipe ao receber mensagens")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conexão"
        verbose_name_plural = "Conexões"
        ordering = ["provedor", "nome"]
        permissions = [("enviar_mensagem", "Enviar mensagens pelas integrações")]
        constraints = [models.UniqueConstraint(fields=["cliente", "nome"], name="conexao_nome_unico_por_unidade")]
        indexes = [models.Index(fields=["cliente", "status"], name="conexao_cliente_status_idx")]

    def __str__(self):
        return f"{self.nome} ({self.get_provedor_display()})"

    @property
    def ativa(self):
        return self.status in (self.CONECTADA,)

    @property
    def adaptador(self):
        from .adaptadores import obter

        return obter(self.provedor)


class EventoWebhook(models.Model):
    """Webhook recebido: garante IDEMPOTÊNCIA (o mesmo evento do provedor é processado uma vez) e dá rastro operacional. Só o mínimo é
    guardado (sem o corpo bruto)."""

    conexao = models.ForeignKey(Conexao, on_delete=models.CASCADE, related_name="webhooks")
    id_externo = models.CharField(max_length=120)
    tipo = models.CharField(max_length=20, blank=True, default="")
    recebido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evento de webhook"
        verbose_name_plural = "Eventos de webhook"
        constraints = [models.UniqueConstraint(fields=["conexao", "id_externo"], name="webhook_evento_unico")]
        indexes = [models.Index(fields=["recebido_em"], name="webhook_recebido_idx")]


class Mensagem(models.Model):
    """Caixa de saída (fila com reenvio) e de entrada das conexões de mensagem."""

    SAIDA, ENTRADA = "saida", "entrada"
    DIRECAO_CHOICES = [(SAIDA, "Enviada"), (ENTRADA, "Recebida")]
    PENDENTE, ENVIADA, ERRO, CANCELADA, RECEBIDA = "pendente", "enviada", "erro", "cancelada", "recebida"
    STATUS_CHOICES = [(PENDENTE, "Na fila"), (ENVIADA, "Enviada"), (ERRO, "Falhou"), (CANCELADA, "Cancelada"), (RECEBIDA, "Recebida")]

    conexao = models.ForeignKey(Conexao, on_delete=models.PROTECT, related_name="mensagens")
    direcao = models.CharField(max_length=8, choices=DIRECAO_CHOICES)
    contato = models.CharField(max_length=60, verbose_name="Contato")  # número/id do provedor: dado pessoal, nunca vai para log
    texto = models.TextField(max_length=2000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDENTE)
    tentativas = models.PositiveSmallIntegerField(default=0)
    proxima_tentativa = models.DateTimeField(null=True, blank=True)
    erro = models.CharField(max_length=255, blank=True, default="")
    id_externo = models.CharField(max_length=120, blank=True, default="")
    chave_idempotencia = models.CharField(max_length=64, null=True, blank=True, editable=False)
    tipo = models.CharField(max_length=20, blank=True, default="mensagem")  # mensagem | comentario
    lida_em = models.DateTimeField(null=True, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Mensagem"
        verbose_name_plural = "Mensagens"
        ordering = ["-criado_em", "-pk"]
        constraints = [models.UniqueConstraint(fields=["conexao", "chave_idempotencia"], condition=Q(chave_idempotencia__isnull=False), name="mensagem_chave_unica")]
        indexes = [models.Index(fields=["status", "proxima_tentativa"], name="mensagem_fila_idx"), models.Index(fields=["conexao", "direcao"], name="mensagem_conexao_direcao_idx")]

    def __str__(self):
        return f"{self.get_direcao_display()} #{self.pk}"


class Compromisso(models.Model):
    """Reserva, entrega ou compromisso da agenda do sistema (sincroniza com Google Agenda/iCloud, nos dois sentidos).
    `origem` externa = veio do calendário. Desativar não apaga (o evento externo é removido pela sincronização)."""

    RESERVA, ENTREGA, COMPROMISSO, EVENTO, TAREFA = "reserva", "entrega", "compromisso", "evento", "tarefa"
    TIPO_CHOICES = [(RESERVA, "Reserva"), (ENTREGA, "Entrega"), (COMPROMISSO, "Compromisso"), (EVENTO, "Evento"), (TAREFA, "Tarefa")]
    LOCAL, EXTERNA = "local", "externa"

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.PROTECT, related_name="compromissos", verbose_name="Unidade")
    tipo = models.CharField(max_length=12, choices=TIPO_CHOICES, default=COMPROMISSO, verbose_name="Tipo")
    titulo = models.CharField(max_length=150, verbose_name="Título")
    descricao = models.CharField(max_length=500, blank=True, default="", verbose_name="Descrição")
    inicio = models.DateTimeField(verbose_name="Início")
    fim = models.DateTimeField(verbose_name="Fim")
    todo_o_dia = models.BooleanField(default=False, db_default=False, verbose_name="Dia inteiro")
    pedido = models.ForeignKey("pedido.Pedido", null=True, blank=True, on_delete=models.SET_NULL, related_name="compromissos", verbose_name="Pedido")
    origem = models.CharField(max_length=7, default=LOCAL, db_default=LOCAL, editable=False)
    ativo = models.BooleanField(default=True, db_default=True, verbose_name="Ativo")
    # Concluído (tarefas, entregas, compromissos...): continua na agenda, marcado (não altera a sincronização com calendários externos).
    concluido = models.BooleanField(default=False, db_default=False, verbose_name="Concluído")
    # Lembrete: minutos ANTES do início (0 = na hora; nulo = sem lembrete). `lembrete_enviado_em` garante que o aviso
    # sai uma única vez (comando enviar_lembretes_agenda); mudar início ou antecedência o zera para avisar de novo.
    lembrete_minutos = models.PositiveIntegerField(null=True, blank=True, verbose_name="Lembrete")
    lembrete_enviado_em = models.DateTimeField(null=True, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compromisso"
        verbose_name_plural = "Compromissos"
        ordering = ["inicio", "pk"]
        indexes = [models.Index(fields=["cliente", "inicio"], name="compromisso_cliente_inicio_idx")]

    def __str__(self):
        return self.titulo


class ItemSincronizado(models.Model):
    """Vínculo entre um item LOCAL (conta, compromisso) e o evento EXTERNO de uma conexão de agenda, com o hash do conteúdo enviado
    (só reenvia se mudou) e a etag/hora do provedor (para detectar edição externa)."""

    conexao = models.ForeignKey(Conexao, on_delete=models.CASCADE, related_name="itens_sincronizados")
    tipo_local = models.CharField(max_length=20)  # conta_pagar | conta_receber | compromisso
    id_local = models.PositiveBigIntegerField()
    id_externo = models.CharField(max_length=200)
    hash_conteudo = models.CharField(max_length=64, blank=True, default="")
    etag = models.CharField(max_length=120, blank=True, default="")
    sincronizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Item sincronizado"
        verbose_name_plural = "Itens sincronizados"
        constraints = [
            models.UniqueConstraint(fields=["conexao", "tipo_local", "id_local"], name="item_sincronizado_local_unico"),
            models.UniqueConstraint(fields=["conexao", "id_externo"], name="item_sincronizado_externo_unico"),
        ]


class LogIntegracao(models.Model):
    """Registro operacional das integrações (conexão, renovação, envio, webhook, sincronização). NUNCA contém credencial,
    token, corpo de mensagem ou dado pessoal; só o necessário para diagnosticar."""

    INFO, AVISO, ERRO = "info", "aviso", "erro"
    NIVEL_CHOICES = [(INFO, "Info"), (AVISO, "Aviso"), (ERRO, "Erro")]

    conexao = models.ForeignKey(Conexao, on_delete=models.CASCADE, related_name="logs")
    nivel = models.CharField(max_length=5, choices=NIVEL_CHOICES, default=INFO)
    evento = models.CharField(max_length=40)
    detalhe = models.CharField(max_length=300, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Log de integração"
        verbose_name_plural = "Logs de integração"
        ordering = ["-criado_em", "-pk"]
        indexes = [models.Index(fields=["conexao", "criado_em"], name="log_integracao_conexao_idx")]


class TemplateWhatsApp(models.Model):
    """Template de mensagem do WhatsApp Business (API oficial da Meta) de uma conexão `whatsapp_cloud`.

    Guarda DOIS estados separados, para nunca afirmar algo que a Meta não confirmou:
    - `situacao_local`: o que acontece AQUI (rascunho nunca enviado; sincronizado = igual ao último conteúdo trocado com a Meta; alterado =
      editado aqui e ainda não enviado);
    - `status_meta` (+ `motivo_rejeicao`, `id_meta`): EXATAMENTE o que a Meta devolveu (PENDING, APPROVED, REJECTED, PAUSED, DISABLED...).
      Vazio enquanto a Meta não respondeu. Aprovação só existe quando a Meta a informa (nunca é simulada).
    A unidade (tenant) é a da conexão: toda consulta filtra por `conexao__cliente`."""

    RASCUNHO, SINCRONIZADO, ALTERADO = "rascunho", "sincronizado", "alterado"
    SITUACAO_CHOICES = [(RASCUNHO, "Rascunho (não enviado à Meta)"), (SINCRONIZADO, "Igual ao da Meta"), (ALTERADO, "Alterado aqui (falta enviar à Meta)")]
    MARKETING, UTILITY, AUTHENTICATION = "MARKETING", "UTILITY", "AUTHENTICATION"
    # Criação/edição aqui: só MARKETING e UTILITY. AUTHENTICATION (OTP) aparece apenas em templates importados da Meta, somente leitura.
    CATEGORIA_CHOICES = [(MARKETING, "Marketing"), (UTILITY, "Utilidade"), (AUTHENTICATION, "Autenticação")]

    conexao = models.ForeignKey(Conexao, on_delete=models.PROTECT, related_name="templates_whatsapp", verbose_name="Conexão")
    nome = models.CharField(max_length=512, verbose_name="Nome")
    idioma = models.CharField(max_length=10, verbose_name="Idioma")
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, verbose_name="Categoria")
    cabecalho = models.CharField(max_length=60, blank=True, default="", verbose_name="Cabeçalho (texto)")
    corpo = models.TextField(max_length=1024, verbose_name="Corpo")
    rodape = models.CharField(max_length=60, blank=True, default="", verbose_name="Rodapé")
    # [{"tipo": QUICK_REPLY|URL|PHONE_NUMBER, "texto", "url"?, "telefone"?, "exemplo"?}]
    botoes = models.JSONField(default=list, blank=True, verbose_name="Botões")
    # {"cabecalho": "valor", "corpo": ["valor {{1}}", ...]}: valores de exemplo que a Meta exige para cada variável.
    exemplos = models.JSONField(default=dict, blank=True, verbose_name="Exemplos das variáveis")
    # Importado da Meta com recursos que este módulo não edita (cabeçalho de mídia, carrossel, OTP...): só consulta e exclusão.
    somente_leitura = models.BooleanField(default=False, db_default=False, verbose_name="Somente leitura")
    situacao_local = models.CharField(max_length=12, choices=SITUACAO_CHOICES, default=RASCUNHO, db_default=RASCUNHO, verbose_name="Situação local")
    status_meta = models.CharField(max_length=30, blank=True, default="", verbose_name="Status na Meta")
    motivo_rejeicao = models.CharField(max_length=255, blank=True, default="", verbose_name="Motivo informado pela Meta")
    id_meta = models.CharField(max_length=40, blank=True, default="", verbose_name="ID na Meta")
    ultimo_erro = models.CharField(max_length=300, blank=True, default="", verbose_name="Último erro")
    sincronizado_em = models.DateTimeField(null=True, blank=True, verbose_name="Última troca com a Meta")
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Template do WhatsApp"
        verbose_name_plural = "Templates do WhatsApp"
        ordering = ["nome", "idioma"]
        constraints = [models.UniqueConstraint(fields=["conexao", "nome", "idioma"], name="template_whatsapp_unico")]
        indexes = [models.Index(fields=["conexao", "status_meta"], name="template_wa_conexao_status_idx")]

    def __str__(self):
        return f"{self.nome} ({self.idioma})"

    @property
    def enviado(self):
        return bool(self.id_meta)
