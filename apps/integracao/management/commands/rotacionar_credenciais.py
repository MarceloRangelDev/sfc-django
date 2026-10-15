"""Rotação da chave de cifra: recifra TODAS as credenciais com a chave principal (a primeira de INTEGRACAO_CRYPTO_KEYS).

Passos: (1) coloque a chave NOVA na frente da lista (mantendo a antiga); (2) rode este comando; (3) remova a antiga.
"""
from django.core.management.base import BaseCommand

from apps.integracao import cripto
from apps.integracao.models import Conexao


class Command(BaseCommand):
    help = "Recifra as credenciais com a chave principal."

    def handle(self, *args, **options):
        total = 0
        for conexao in Conexao.objects.exclude(credenciais_cifradas=""):
            Conexao.objects.filter(pk=conexao.pk).update(credenciais_cifradas=cripto.recifrar(conexao.credenciais_cifradas))
            total += 1
        self.stdout.write(self.style.SUCCESS(f"{total} conexão(ões) recifrada(s)."))
