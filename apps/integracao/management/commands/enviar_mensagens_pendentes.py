"""Envia a fila de mensagens das integrações (com reenvio e espera crescente em falhas temporárias). Agende a cada minuto.

    python manage.py enviar_mensagens_pendentes [--limite 50]

Não há garantia de entrega instantânea: o envio acontece na próxima execução do agendador. Idempotente e seguro para rodar em paralelo
(cada mensagem é reservada com bloqueio de linha quando o banco permite).
"""
from django.core.management.base import BaseCommand

from apps.integracao import services


class Command(BaseCommand):
    help = "Processa a fila de mensagens de saída."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)

    def handle(self, *args, **options):
        r = services.processar_fila(limite=max(1, min(options["limite"], 500)))
        self.stdout.write(self.style.SUCCESS(f"enviadas={r['enviadas']} falhas={r['falhas']} adiadas={r['adiadas']}"))
