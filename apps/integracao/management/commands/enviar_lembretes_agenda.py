"""Envia os lembretes da agenda que já venceram. Agende a cada 5 minutos (cron ou Agendador de Tarefas do Windows):

    python manage.py enviar_lembretes_agenda

Idempotente: cada compromisso é avisado uma única vez por horário/antecedência (ver apps/integracao/lembretes.py).
"""
from django.core.management.base import BaseCommand

from apps.integracao.lembretes import enviar_lembretes


class Command(BaseCommand):
    help = "Envia os lembretes de compromissos da agenda."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"{enviar_lembretes()} lembrete(s) enviado(s)."))
