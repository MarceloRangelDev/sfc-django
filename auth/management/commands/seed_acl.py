"""
Cria/atualiza os grupos padrão de ACL (Access Control List) do sistema,
aplicando as permissões nativas do Django (view/add/change/delete) sobre
as telas de negócio (Insumo, Produto, Processado, Categoria, Ficha,
Precificação).

Rodar sempre que uma nova tela/model for adicionada ao ACL:

    python manage.py seed_acl

É idempotente: pode ser rodado várias vezes sem duplicar nada.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Apps (app_label) que compõem as telas de negócio protegidas pelo ACL.
SCREEN_APPS = [
    "insumo",
    "produto",
    "processado",
    "categoria",
    "ficha",
    "precificacao",
]

# name do grupo -> ações permitidas (subconjunto de view/add/change/delete)
# aplicadas em TODAS as telas de negócio listadas em SCREEN_APPS.
GROUPS = {
    "Administrador": ["view", "add", "change", "delete"],
    "Gestor": ["view", "add", "change"],
    "Operador": ["view", "add"],
    "Visualizador": ["view"],
}


class Command(BaseCommand):
    help = "Cria/atualiza os grupos padrão de ACL (Administrador, Gestor, Operador, Visualizador)."

    def handle(self, *args, **options):
        permissions_by_action = {"view": [], "add": [], "change": [], "delete": []}

        for app_label in SCREEN_APPS:
            for perm in Permission.objects.filter(content_type__app_label=app_label):
                for action in permissions_by_action:
                    if perm.codename.startswith(f"{action}_"):
                        permissions_by_action[action].append(perm)

        for group_name, actions in GROUPS.items():
            group, created = Group.objects.get_or_create(name=group_name)

            perms_for_group = []
            for action in actions:
                perms_for_group.extend(permissions_by_action[action])

            group.permissions.set(perms_for_group)

            status = "criado" if created else "atualizado"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Grupo '{group_name}' {status} com {len(perms_for_group)} permissões."
                )
            )

        self.stdout.write(self.style.SUCCESS("ACL padrão aplicada com sucesso."))
