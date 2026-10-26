from django.db import models


class Segmento(models.Model):
    """Segmento de trabalho de um Cliente (ex.: Produto Alimentício).

    Cadeia de acesso: Usuário -> Cliente -> Segmento -> Módulos -> Permissão.
    O segmento define o CONJUNTO de módulos que os clientes dele podem usar;
    `tipo_precificacao` mantém a regra de precificação (serviço/produto/ambos)
    que antes era escolhida por usuário no Profile.
    """

    TIPO_SERVICO = "servico"
    TIPO_PRODUTO = "produto"
    TIPO_AMBOS = "ambos"
    TIPO_CHOICES = [
        (TIPO_SERVICO, "Serviço"),
        (TIPO_PRODUTO, "Produto"),
        (TIPO_AMBOS, "Serviço e Produto"),
    ]

    nome = models.CharField(max_length=100, unique=True, verbose_name="Nome")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Identificador")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    tipo_precificacao = models.CharField(
        max_length=10, choices=TIPO_CHOICES, default=TIPO_PRODUTO, verbose_name="Tipo de precificação",
        help_text="Define se as regras de precificação usadas são as de serviço, de produto ou ambas.",
    )
    modulos = models.ManyToManyField("modulo.Modulo", blank=True, related_name="segmentos", verbose_name="Módulos")
    ativo = models.BooleanField(default=True, verbose_name="Ativo")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Segmento de trabalho"
        verbose_name_plural = "Segmentos de trabalho"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    @property
    def precifica_servico(self):
        return self.tipo_precificacao in (self.TIPO_SERVICO, self.TIPO_AMBOS)

    @property
    def precifica_produto(self):
        return self.tipo_precificacao in (self.TIPO_PRODUTO, self.TIPO_AMBOS)


class Modulo(models.Model):
    """Um módulo é uma tela/funcionalidade do sistema. É a ÚNICA fonte de
    verdade para: item de menu (nome, ícone, rota, ordem, seção), permissão
    exigida e linha na matriz de papéis. Cadastrar um módulo já o coloca no
    menu e na edição dos papéis (ver apps/modulo/README.md).

    O `slug` é o identificador estável usado no código (urls.py, decorators)
    — não deve ser alterado depois de criado."""

    SECAO_PRINCIPAL = "principal"
    SECAO_CONFIGURACOES = "configuracoes"
    SECAO_CHOICES = [
        (SECAO_PRINCIPAL, "Menu principal"),
        (SECAO_CONFIGURACOES, "Configurações"),
    ]

    nome = models.CharField(max_length=100, verbose_name="Nome")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Identificador")
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
    icone = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ícone",
        help_text="Classe do ícone (Tabler Icons), ex: 'ti tabler-app-window'.",
    )
    rota = models.CharField(
        max_length=100, blank=True, verbose_name="Rota (nome da URL)",
        help_text="Nome da URL no urls.py (ex: 'insumo'). Vazio = módulo sem item de menu.",
    )
    app = models.CharField(max_length=50, blank=True, verbose_name="App", help_text="App Django que implementa o módulo (ex: 'insumo').")
    permissao = models.CharField(
        max_length=100, blank=True, verbose_name="Permissão de acesso",
        help_text="Permissão do Django que libera o menu/tela, no formato 'app.view_modelo'.",
    )
    secao = models.CharField(max_length=20, choices=SECAO_CHOICES, default=SECAO_PRINCIPAL, verbose_name="Seção do menu")
    ordem = models.PositiveSmallIntegerField(default=100, verbose_name="Ordem no menu")
    # Módulos "essenciais" (Configurações) dependem só da permissão do
    # perfil: não passam por segmento/habilitação por cliente.
    essencial = models.BooleanField(
        default=False, verbose_name="Essencial",
        help_text="Não depende de segmento nem de habilitação por cliente; só da permissão do perfil.",
    )
    # False = permissão sensível, nunca atribuível pela tela de Papéis
    # (ex.: gerenciar papéis); fica só com o Administrador (seed_acl).
    delegavel = models.BooleanField(
        default=True, verbose_name="Aparece na edição dos papéis",
        help_text="Se marcado, o módulo aparece na matriz de permissões da tela de Papéis.",
    )
    instrucoes = models.TextField(
        blank=True, verbose_name="Instruções de uso",
        help_text="Documentação para desenvolvedores: onde e como o módulo aparece no sistema.",
    )
    # Interruptor geral: um módulo inativo fica indisponível para TODOS os
    # clientes, mesmo que algum ClienteModulo individual esteja marcado
    # como ativo (útil para desligar uma funcionalidade em manutenção).
    ativo = models.BooleanField(default=True, verbose_name="Disponível no sistema")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Módulo"
        verbose_name_plural = "Módulos"
        ordering = ["ordem", "nome"]

    def __str__(self):
        return self.nome


class ClienteModulo(models.Model):
    """Relação entre um Cliente e um Módulo: define se aquele módulo está
    habilitado especificamente para aquele Cliente."""

    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.CASCADE,
        related_name="modulos_habilitados",
    )
    modulo = models.ForeignKey(
        Modulo,
        on_delete=models.CASCADE,
        related_name="clientes_habilitados",
    )
    ativo = models.BooleanField(default=True, verbose_name="Habilitado")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Módulo do cliente"
        verbose_name_plural = "Módulos do cliente"
        unique_together = ("cliente", "modulo")

    def __str__(self):
        return f"{self.modulo.nome} - {self.cliente.nome_razao_social}"


class OrdemMenuCliente(models.Model):
    """Ordem PERSONALIZADA de um módulo no menu de um cliente (tenant). Sem linha, vale `Modulo.ordem` (padrão do sistema).

    O valor guardado ocupa um dos "lugares" (valores de `Modulo.ordem`) dos módulos reordenáveis do cliente, de modo que a nova ordem
    se intercala corretamente com módulos que não são reordenáveis (ex.: Configurações). Ver apps/modulo/services.py."""

    cliente = models.ForeignKey("cliente.Cliente", on_delete=models.CASCADE, related_name="ordem_menu")
    modulo = models.ForeignKey(Modulo, on_delete=models.CASCADE, related_name="ordens_por_cliente")
    ordem = models.PositiveSmallIntegerField(verbose_name="Ordem no menu do cliente")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ordem do menu do cliente"
        verbose_name_plural = "Ordens do menu dos clientes"
        constraints = [models.UniqueConstraint(fields=["cliente", "modulo"], name="ordem_menu_unica_por_cliente_modulo")]
