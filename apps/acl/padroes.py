"""Permissões PADRÃO de cada perfil (Administrador, Gestor, Operador, Visualizador).

É a fonte única do que `manage.py seed_acl` concede e também do que é concedido
automaticamente quando um módulo é HABILITADO para um cliente (ou depois de um
`migrate`). Antes, as permissões de um módulo novo só chegavam aos perfis se alguém
rodasse o `seed_acl`: o módulo aparecia habilitado para o cliente, mas os usuários
não o enxergavam (só com "permissão especial" por usuário).

Regra do `aplicar_padrao_do_modulo` (ADITIVA, nunca remove nada):
  um perfil recebe as permissões padrão de um módulo somente se ainda NÃO tem
  nenhuma permissão daquele módulo (módulo "não configurado" para o perfil). Quem
  já tem alguma (inclusive por ajuste manual na tela de Papéis) não é alterado.
  Para bloquear um módulo para um perfil, desabilite-o para o cliente ou use a
  permissão especial "negar" no usuário.
"""
from django.contrib.auth.models import Group, Permission

# Apps (app_label) que compõem as telas de negócio protegidas pelo ACL.
SCREEN_APPS = [
    "insumo",
    "produto",
    "processado",
    "categoria",
    "consumidor",
    "compras",
    "cotacao",
    "pdv",
    "pedido",
    "maodeobra",
    "integracao",
    "fiscal",
    "orcamento",
    "precificacaoservico",
    "ficha",
    "precificacao",
    "custo",
    "estoque",
    "financeiro",
]

# Apps de administração do sistema (não são "telas de negócio" do dia a
# dia): só o grupo Administrador recebe permissão por padrão. Cadastrar
# Clientes (tenants) e vincular usuários a eles é uma ação sensível, então
# fica de fora do acesso automático de Gestor/Operador/Visualizador.
ADMIN_APPS = [
    "cliente",
    "modulo",
    "medida",
    "auditoria",
    "notificacao",
    "canal",
    "pdvconfig",
    "assinatura",  # plano/pagamentos (Pagar.me): Administrador; o Gestor recebe ver+gerenciar em EXTRAS
    "kanban",  # quadro Kanban: só o Administrador (global)
    "insumoglobal",  # cadastro global de insumos: só o Administrador (ou quem receber a permissão)
]

# Permissões (app_label, codename) além das telas de negócio, concedidas
# por padrão a cada perfil. O Gestor administra a própria estrutura
# (usuários, consulta de clientes e envio de notificações).
PDV_GERENCIAIS = [  # fora da matriz: cancelar, devolver, desconto, sangria, suprimento e conferência do caixa
    ("pdv", "cancelar_venda"), ("pdv", "devolver_item"), ("pdv", "conceder_desconto"), ("pdv", "sangria"), ("pdv", "suprimento"),
    ("pdv", "conferir_caixa"), ("pdv", "abrir_caixa"), ("pdv", "remover_item"),
]
# Mão de obra revela valor da hora/diária de pessoas: só Administrador (tudo) e Gestor (ver/criar/alterar; excluir é do Administrador).
MAO_DE_OBRA_GESTOR = [("maodeobra", f"{acao}_{modelo}") for modelo in ("colaborador", "apontamento", "configuracaomaodeobra", "conexao", "mensagem", "eventowebhook", "itemsincronizado", "logintegracao", "configuracaofiscal", "documentofiscal") for acao in ("view", "add", "change")]
# Precificação de serviços revela remuneração, custos e margem: só Administrador (tudo) e Gestor (ver/criar/alterar; excluir é do Administrador).
PRECIFICACAO_SERVICO_GESTOR = [("precificacaoservico", f"{acao}_{modelo}") for modelo in ("configuracaoservico", "despesafixa", "modeloservico", "proposta", "propostaitem", "propostacusto", "lancamento", "pagamento") for acao in ("view", "add", "change")]
# Integrações guardam credenciais de terceiros: só Administrador (tudo) e Gestor (conectar, mensagens e log). Compromissos da agenda: todos que operam.
INTEGRACAO_GESTOR = [("integracao", f"{acao}_{modelo}") for modelo in ("conexao", "mensagem", "templatewhatsapp") for acao in ("view", "add", "change")] + [("integracao", "view_logintegracao"), ("integracao", "enviar_mensagem")]
# Fiscal: certificado e dados fiscais da empresa. Só Administrador e Gestor veem os documentos, pedem emissão e configuram.
FISCAL_GESTOR = [("fiscal", "view_documentofiscal"), ("fiscal", "view_configuracaofiscal"), ("fiscal", "change_configuracaofiscal"), ("fiscal", "emitir_nota")]
PEDIDO_PERMISSOES = [("pedido", "mover_pedido"), ("pedido", "cancelar_pedido")]  # mover: todos os perfis que operam; cancelar: Gestor e Administrador
PDV_CONFIG_GESTOR = [
    (app, f"{acao}_{modelo}") for app, modelo in (("pdvconfig", "formapagamento"), ("pdvconfig", "mesa"), ("pdvconfig", "configuracaopdv"))
    for acao in ("view", "add", "change")
]

PAINEL = [("dashboard", "view_painel")]  # indicadores do Painel: todos os perfis
FOTO_PERFIL = [("accounts", "change_fotoperfil")]  # cada usuário troca a própria foto: todos os perfis

EXTRAS = {
    # PDV: o Operador só vende (e abre/fecha o PRÓPRIO caixa); o resto é de Gestor e Administrador.
    "Visualizador": [*PAINEL, *FOTO_PERFIL],
    "Operador": [*PAINEL, *FOTO_PERFIL, ("pdv", "abrir_caixa"), ("pedido", "mover_pedido"), ("integracao", "change_compromisso")],
    # Receita e observações de Processados/Fichas (segredo do negócio): só Administrador e Gestor.
    "Administrador": [*PAINEL, *FOTO_PERFIL, ("insumoglobal", "padronizar_insumo"), ("kanban", "mover_cartao"), ("assinatura", "gerenciar_assinatura"), ("processado", "ver_receita"), ("ficha", "ver_receita"), *PDV_GERENCIAIS, *PEDIDO_PERMISSOES, ('integracao', 'enviar_mensagem'), ('fiscal', 'emitir_nota'), ("orcamento", "enviar_orcamento")],
    "Gestor": [
        *PAINEL, *FOTO_PERFIL, ("processado", "ver_receita"), ("ficha", "ver_receita"), *PDV_GERENCIAIS, *PDV_CONFIG_GESTOR, *PEDIDO_PERMISSOES, *MAO_DE_OBRA_GESTOR, *PRECIFICACAO_SERVICO_GESTOR, *INTEGRACAO_GESTOR, *FISCAL_GESTOR, ("orcamento", "enviar_orcamento"),
        ("auth", "view_user"), ("auth", "add_user"), ("auth", "change_user"),
        ("assinatura", "view_assinatura"), ("assinatura", "gerenciar_assinatura"),
        ("cliente", "view_cliente"),
        ("estoque", "view_transferenciaestoque"), ("estoque", "add_transferenciaestoque"),
        ("financeiro", "view_relatoriofinanceiro"),
        ("canal", "view_canalvenda"), ("canal", "add_canalvenda"), ("canal", "change_canalvenda"),
        ("cliente", "view_itensfilial"), ("cliente", "add_itensfilial"), ("cliente", "change_itensfilial"),
        ("notificacao", "view_notificacao"), ("notificacao", "add_notificacao"),
    ],
}

# Permissões nativas do próprio Django (app_label "auth", de
# django.contrib.auth.models.Group/Permission) usadas para proteger as
# telas de Papéis/Permissões (Fase 4). Concedidas só ao Administrador —
# de propósito FORA da matriz de permissões atribuível pela tela de
# Papéis (ver apps/acl/permissions_matrix.py), para que um papel comum
# nunca possa se autoconceder a capacidade de gerenciar outros papéis.
AUTH_APP_LABEL = "auth"
AUTH_CODENAMES_ADMINISTRADOR = [
    "view_group",
    "add_group",
    "change_group",
    "delete_group",
    "view_permission",
    # Usuários (Fase 6). Sem "delete_user" de propósito: usuários só são inativados.
    "view_user",
    "add_user",
    "change_user",
]

# Permissões de negócio que NÃO entram na regra "todas as ações por perfil" e só são dadas
# a quem for autorizado explicitamente (Administrador e, via EXTRAS, o Gestor): transferir
# estoque entre matriz e filiais e ver os relatórios financeiros (DRE e balanço).
SOMENTE_AUTORIZADOS = ("transferenciaestoque", "relatoriofinanceiro", "colaborador", "apontamento", "configuracaomaodeobra",
                        "conexao", "mensagem", "eventowebhook", "itemsincronizado", "logintegracao", "templatewhatsapp",
                        "configuracaoservico", "despesafixa", "modeloservico", "proposta", "propostaitem", "propostacusto", "lancamento", "pagamento")

# name do grupo -> ações permitidas (subconjunto de view/add/change/delete)
# aplicadas em TODAS as telas de negócio listadas em SCREEN_APPS.
GROUPS = {
    "Administrador": ["view", "add", "change", "delete"],
    "Gestor": ["view", "add", "change"],
    "Operador": ["view", "add"],
    "Visualizador": ["view"],
}

ACOES = ("view", "add", "change", "delete")


def _por_acao(app_labels, excluir=()):
    resultado = {acao: [] for acao in ACOES}
    for permissao in Permission.objects.filter(content_type__app_label__in=app_labels):
        if any(permissao.codename.endswith(f"_{nome}") for nome in excluir):
            continue
        for acao in ACOES:
            if permissao.codename.startswith(f"{acao}_"):
                resultado[acao].append(permissao)
    return resultado


def permissoes_padrao():
    """{nome do perfil: set de Permission} — o que cada perfil recebe por padrão."""
    telas = _por_acao(SCREEN_APPS, excluir=SOMENTE_AUTORIZADOS)
    telas_completo = _por_acao(SCREEN_APPS)  # o Administrador recebe tudo
    administrativas = _por_acao(ADMIN_APPS)
    padroes = {}
    for nome, acoes in GROUPS.items():
        permissoes = set()
        for acao in acoes:
            permissoes.update((telas_completo if nome == "Administrador" else telas)[acao])
            if nome == "Administrador":  # apps administrativos só entram para o Administrador
                permissoes.update(administrativas[acao])
        if nome == "Administrador":
            permissoes.update(Permission.objects.filter(content_type__app_label=AUTH_APP_LABEL, codename__in=AUTH_CODENAMES_ADMINISTRADOR))
        for app_label, codename in EXTRAS.get(nome, []):
            permissoes.update(Permission.objects.filter(content_type__app_label=app_label, codename=codename))
        padroes[nome] = permissoes
    return padroes


def permissoes_do_modulo(modulo):
    """Permissões que a linha do módulo na matriz de papéis controla: todas as do
    mesmo app e modelo da permissão de acesso do módulo (ex.: `estoque.view_movimentoestoque`
    -> view/add/change/delete_movimentoestoque)."""
    if "." not in (modulo.permissao or ""):
        return set()
    app_label, codename = modulo.permissao.split(".", 1)
    if "_" not in codename:
        return set()
    modelo = codename.split("_", 1)[1]
    return set(Permission.objects.filter(content_type__app_label=app_label, codename__endswith=f"_{modelo}"))


def aplicar_padrao_do_modulo(modulo, padroes=None):
    """Concede aos perfis padrão (que existam) as permissões padrão do módulo, se o
    perfil ainda não tem nenhuma delas. Devolve {perfil: quantidade concedida}."""
    do_modulo = permissoes_do_modulo(modulo)
    if not do_modulo:
        return {}
    padroes = padroes if padroes is not None else permissoes_padrao()
    concedidas = {}
    for grupo in Group.objects.filter(name__in=GROUPS):
        if grupo.permissions.filter(pk__in=[p.pk for p in do_modulo]).exists():
            continue  # já configurado para este perfil: não mexe
        novas = padroes.get(grupo.name, set()) & do_modulo
        if novas:
            grupo.permissions.add(*novas)
            concedidas[grupo.name] = len(novas)
    return concedidas


def aplicar_padrao_aos_modulos(modulos=None):
    """`aplicar_padrao_do_modulo` para vários módulos (padrão: todos os ativos),
    calculando os padrões uma única vez. Idempotente."""
    from apps.modulo.models import Modulo

    modulos = modulos if modulos is not None else Modulo.objects.filter(ativo=True).exclude(permissao="")
    if not Group.objects.filter(name__in=GROUPS).exists():
        return {}  # banco novo: os perfis ainda não existem (o seed_acl os cria)
    padroes = permissoes_padrao()
    return {m.slug: c for m in modulos if (c := aplicar_padrao_do_modulo(m, padroes))}
