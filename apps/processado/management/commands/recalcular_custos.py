"""Recalcula custos de todos os processados (idempotente).

    python manage.py recalcular_custos            # todos os clientes
    python manage.py recalcular_custos --cliente 3

Use após corrigir dados ou depois de uma falha de recálculo: cada cliente é
reprocessado numa transação, em ordem de dependência, sem duplicar nada.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.processado.recalculo import recalcular_todos
from web_project.regras import RegraNegocioError


class Command(BaseCommand):
    help = "Recalcula o custo de todos os processados (e dos insumos gerados por eles)."

    def add_arguments(self, parser):
        parser.add_argument("--cliente", type=int, help="Limita ao cliente (unidade) informado.")

    def handle(self, *args, **options):
        try:
            total = recalcular_todos(options["cliente"])
        except RegraNegocioError as erro:
            raise CommandError(str(erro)) from None
        self.stdout.write(self.style.SUCCESS(f"{total} processado(s) recalculado(s)."))
