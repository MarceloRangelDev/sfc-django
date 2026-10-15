"""Processa a fila de documentos fiscais PENDENTES (agende a cada poucos minutos).

    python manage.py processar_fiscal [--limite 50]

Documentos cujo provedor/cadastro ainda não está pronto apenas têm o motivo atualizado (não gastam tentativa). Nada é marcado como
autorizado sem a resposta real da autoridade fiscal.
"""
from django.core.management.base import BaseCommand

from apps.fiscal import services


class Command(BaseCommand):
    help = "Processa a fila de documentos fiscais pendentes."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=50)

    def handle(self, *args, **options):
        r = services.processar_pendentes(limite=max(1, min(options["limite"], 500)))
        self.stdout.write(self.style.SUCCESS(f"autorizadas={r['autorizadas']} rejeitadas={r['rejeitadas']} aguardando={r['aguardando']} erros={r['erros']}"))
