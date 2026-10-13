"""Gera as notificações de estoque baixo e de lotes vencidos/próximos do vencimento.

    python manage.py gerar_alertas_estoque            # todas as unidades
    python manage.py gerar_alertas_estoque --cliente 3

Pensado para rodar todo dia (cron/agendador). Idempotente: cada item só é avisado
uma vez por dia (tabela AlertaEnviado), então rodar de novo não repete notificações.
O prazo de vencimento vem de settings.ESTOQUE_DIAS_ALERTA_VENCIMENTO (padrão 7 dias).
"""
from django.core.management.base import BaseCommand

from apps.cliente.models import Cliente
from apps.estoque.services import gerar_alertas


class Command(BaseCommand):
    help = "Envia as notificações de estoque baixo e de vencimento (uma vez por item por dia)."

    def add_arguments(self, parser):
        parser.add_argument("--cliente", type=int, help="Limita ao cliente (unidade) informado.")

    def handle(self, *args, **options):
        clientes = Cliente.objects.filter(ativo=True)
        if options["cliente"]:
            clientes = clientes.filter(pk=options["cliente"])
        total = gerar_alertas(list(clientes))
        self.stdout.write(self.style.SUCCESS(
            f"{total['baixo']} item(ns) de estoque baixo e {total['vencimento']} lote(s) a vencer notificado(s)."
        ))
