"""Gera um par de chaves VAPID para o web push.

    python manage.py gerar_chaves_vapid

Copie as três linhas impressas para o arquivo .env. A chave privada é
secreta: nunca a publique nem a versione.
"""
import base64

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand
from py_vapid import Vapid


def _b64url(dados):
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Gera um par de chaves VAPID para as notificações push."

    def handle(self, *args, **options):
        vapid = Vapid()
        vapid.generate_keys()
        privada = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        publica = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.stdout.write(f"VAPID_PUBLIC_KEY={_b64url(publica)}")
        self.stdout.write(f"VAPID_PRIVATE_KEY={_b64url(privada)}")
        self.stdout.write("VAPID_ADMIN_EMAIL=seu-email@dominio.com")
