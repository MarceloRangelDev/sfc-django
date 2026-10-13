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
]

# Permissões (app_label, codename) além das telas de negócio, concedidas
# por padrão a cada perfil. O Gestor administra a própria estrutura
# (usuários, consulta de clientes e envio de notificações).
EXTRAS = {
    "Gestor": [
        ("auth", "view_user"), ("auth", "add_user"), ("auth", "change_user"),
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
SOMENTE_AUTORIZADOS = ("transferenciaestoque", "relatoriofinanceiro")

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
