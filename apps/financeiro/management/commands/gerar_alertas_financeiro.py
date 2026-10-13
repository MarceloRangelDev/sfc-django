"""Notifica contas a pagar/receber atrasadas ou vencendo em breve.

    python manage.py gerar_alertas_financeiro            # todas as unidades
    python manage.py gerar_alertas_financeiro --cliente 3

Pensado para rodar todo dia (cron/agendador). Idempotente: cada conta só é avisada
uma vez por dia por tipo (tabela AlertaFinanceiro). O prazo de "vencendo" vem de
settings.FINANCEIRO_DIAS_ALERTA_VENCIMENTO (padrão 3 dias).
"""
from django.core.management.base import BaseCommand

from apps.cliente.models import Cliente
from apps.financeiro.services import gerar_alertas


class Command(BaseCommand):
    help = "Envia as notificações de contas atrasadas e vencendo (uma vez por conta por dia)."

    def add_arguments(self, parser):
        parser.add_argument("--cliente", type=int, help="Limita ao cliente (unidade) informado.")

    def handle(self, *args, **options):
        clientes = Cliente.objects.filter(ativo=True)
        if options["cliente"]:
            clientes = clientes.filter(pk=options["cliente"])
        total = gerar_alertas(list(clientes))
        resumo = ", ".join(f"{qtd} {tipo.replace('_', ' ')}" for tipo, qtd in sorted(total.items()) if qtd) or "nenhuma conta nova"
        self.stdout.write(self.style.SUCCESS(f"Alertas financeiros enviados: {resumo}."))
