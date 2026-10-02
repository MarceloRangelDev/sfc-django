from rest_framework.permissions import SAFE_METHODS, BasePermission

from web_project.acesso import administrador_global
from web_project.decorators import modulo_habilitado_para

# ação do ViewSet -> permissão nativa do Django exigida
ACAO_PARA_PERMISSAO = {
    "list": "view",
    "retrieve": "view",
    "create": "add",
    "update": "change",
    "partial_update": "change",
    "destroy": "delete",
}


class PermissaoDoSistema(BasePermission):
    """Mesmas regras das telas web, aplicadas à API (no backend):

    - autenticado;
    - tem a permissão nativa do Django (view/add/change/delete) do model,
      declarada na view por `permissao_app` e `permissao_model`;
    - se a view declara `modulo_slug`, o módulo precisa estar habilitado
      para o cliente do usuário (igual a `module_required`);
    - ações extras (ativar/inativar...) usam a permissão da view em
      `permissoes_por_acao = {"ativar": "change"}`;
    - `leitura_livre = True` libera GET para qualquer usuário autenticado
      (ex.: medidas, que todo app precisa listar para montar formulários).
    """

    message = "Você não tem permissão para esta operação."

    def has_permission(self, request, view):
        usuario = request.user
        if not (usuario and usuario.is_authenticated):
            return False

        modulo = getattr(view, "modulo_slug", None)
        if modulo and not modulo_habilitado_para(usuario, modulo):
            self.message = "Este módulo não está habilitado para o seu cliente."
            return False

        if getattr(view, "leitura_livre", False) and request.method in SAFE_METHODS:
            return True

        acao = getattr(view, "action", None)
        if acao in (None, "metadata"):
            # Método que o ViewSet nem implementa (ex.: DELETE onde não há
            # exclusão): deixa o DRF responder 405 em vez de um 403 confuso.
            return True
        tipo = getattr(view, "permissoes_por_acao", {}).get(acao) or ACAO_PARA_PERMISSAO.get(acao)
        if tipo is None:
            return False  # ação desconhecida: nega por padrão
        return usuario.has_perm(f"{view.permissao_app}.{tipo}_{view.permissao_model}")


class SomenteAdministradorGlobal(BasePermission):
    message = "Somente administradores podem acessar este recurso."

    def has_permission(self, request, view):
        return administrador_global(request.user)
