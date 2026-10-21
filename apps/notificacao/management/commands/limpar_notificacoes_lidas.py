from django.core.management.base import BaseCommand

from apps.notificacao.services import limpar_lidas_expiradas


class Command(BaseCommand):
    help = "Remove as notificações lidas há 48 h ou mais. Agende a cada hora (cron / Agendador de Tarefas)."

    def handle(self, *args, **options):
        self.stdout.write(f"{limpar_lidas_expiradas()} notificação(ões) lida(s) expirada(s) removida(s).")
