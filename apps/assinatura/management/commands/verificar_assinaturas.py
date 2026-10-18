"""Verificação periódica das assinaturas: aplica carência/bloqueio, expira planos anuais, avisa renovações e reconcilia com o
Pagar.me. Agende UMA vez por hora (cron ou Agendador de Tarefas do Windows):

    python manage.py verificar_assinaturas [--sem-provedor]

Idempotente: pode rodar a qualquer momento e repetidamente. `--sem-provedor` pula a consulta à API (só regras locais).
"""
from django.core.management.base import BaseCommand

from apps.assinatura.services import verificar_assinaturas


class Command(BaseCommand):
    help = "Aplica carência/bloqueio e reconcilia as assinaturas com o provedor."

    def add_arguments(self, parser):
        parser.add_argument("--sem-provedor", action="store_true", help="Não consulta o Pagar.me.")

    def handle(self, *args, **options):
        resumo = verificar_assinaturas(consultar_provedor=not options["sem_provedor"])
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k}={v}" for k, v in resumo.items())))
