"""Confere o saldo de cada lote contra a soma do histórico de movimentos.

    python manage.py verificar_estoque              # só relata
    python manage.py verificar_estoque --corrigir   # ajusta o saldo gravado (o histórico manda)
    python manage.py verificar_estoque --cliente 3

Idempotente. Sai com código 1 se achar divergência e não corrigir (útil em monitoramento).
"""
from django.core.management.base import BaseCommand, CommandError

from apps.cliente.models import Cliente
from apps.estoque.services import verificar_consistencia


class Command(BaseCommand):
    help = "Confere (e opcionalmente corrige) o saldo dos lotes contra o histórico de movimentos."

    def add_arguments(self, parser):
        parser.add_argument("--corrigir", action="store_true", help="Ajusta o saldo gravado para o calculado pelo histórico.")
        parser.add_argument("--cliente", type=int, help="Limita ao cliente (unidade) informado.")

    def handle(self, *args, **options):
        clientes = Cliente.objects.filter(pk=options["cliente"]) if options["cliente"] else None
        divergencias = verificar_consistencia(clientes, corrigir=options["corrigir"])
        for lote, gravado, calculado in divergencias:
            self.stdout.write(f"Lote {lote.pk} ({lote.insumo.nome}, '{lote.codigo}'): gravado {gravado}, pelo histórico {calculado}")
        if not divergencias:
            self.stdout.write(self.style.SUCCESS("Estoque consistente."))
        elif options["corrigir"]:
            self.stdout.write(self.style.WARNING(f"{len(divergencias)} lote(s) corrigido(s)."))
        else:
            raise CommandError(f"{len(divergencias)} lote(s) com divergência. Rode com --corrigir para ajustar.")
