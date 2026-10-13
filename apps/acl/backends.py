"""Autorização com exceções individuais por usuário.

Precedência (a mais específica vence):
    1. superusuário: sempre tem tudo (exceções não se aplicam);
    2. exceção individual NEGAR  -> o usuário não tem a permissão, mesmo que o
       papel (ou user_permissions do Django) a conceda;
    3. exceção individual PERMITIR -> o usuário tem a permissão, mesmo que o
       papel não a conceda;
    4. sem exceção -> vale o que o papel concede (comportamento de sempre).

Como menu, decorators (module_required), templates (`perms`) e a API consultam
`user.has_perm`, a regra vale em todos eles — inclusive no servidor.
"""
from django.contrib.auth.backends import ModelBackend


class PermissaoIndividualBackend(ModelBackend):
    def get_all_permissions(self, user_obj, obj=None):
        permissoes = super().get_all_permissions(user_obj, obj)
        if obj is not None or not user_obj.is_active or user_obj.is_superuser:
            return permissoes
        if not hasattr(user_obj, "_permissoes_com_excecoes"):
            from .models import PermissaoUsuario

            efetivas = set(permissoes)
            for app, codigo, efeito in user_obj.permissoes_individuais.values_list(
                "permissao__content_type__app_label", "permissao__codename", "efeito"
            ):
                if efeito == PermissaoUsuario.EFEITO_PERMITIR:
                    efetivas.add(f"{app}.{codigo}")
                else:
                    efetivas.discard(f"{app}.{codigo}")
            user_obj._permissoes_com_excecoes = efetivas  # 1 consulta por usuário/requisição
        return user_obj._permissoes_com_excecoes
