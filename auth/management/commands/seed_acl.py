"""
Cria/atualiza os grupos padrão de ACL (Access Control List) do sistema,
aplicando as permissões nativas do Django (view/add/change/delete) sobre
as telas de negócio (Insumo, Produto, Processado, Categoria, Ficha,
Precificação, Custo operacional, Estoque, Financeiro...).

As regras do que cada perfil recebe ficam em apps/acl/padroes.py (fonte única,
também usada quando um módulo é habilitado para um cliente). Este comando
REDEFINE as permissões dos quatro perfis padrão para o padrão do sistema:

    python manage.py seed_acl

É idempotente: pode ser rodado várias vezes sem duplicar nada. Atenção: ajustes
manuais feitos na tela de Papéis nos perfis Administrador/Gestor/Operador/
Visualizador são sobrescritos por este comando (para só completar o que falta
em módulos novos, basta habilitar o módulo para o cliente ou rodar `migrate`).
"""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.acl.padroes import GROUPS, permissoes_padrao


class Command(BaseCommand):
    help = "Cria/atualiza os grupos padrão de ACL (Administrador, Gestor, Operador, Visualizador)."

    def handle(self, *args, **options):
        padroes = permissoes_padrao()
        for group_name in GROUPS:
            group, created = Group.objects.get_or_create(name=group_name)
            group.permissions.set(padroes[group_name])
            status = "criado" if created else "atualizado"
            self.stdout.write(
                self.style.SUCCESS(f"Grupo '{group_name}' {status} com {len(padroes[group_name])} permissões.")
            )

        self.stdout.write(self.style.SUCCESS("ACL padrão aplicada com sucesso."))
