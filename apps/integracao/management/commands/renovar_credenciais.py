"""Renova credenciais que expiram em breve (ex.: token do Google, ~1 h). Agende a cada 30 minutos.

    python manage.py renovar_credenciais
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.integracao import services
from apps.integracao.models import Conexao


class Command(BaseCommand):
    help = "Renova tokens prestes a expirar."

    def handle(self, *args, **options):
        limite = timezone.now() + timedelta(minutes=30)
        renovadas = 0
        for conexao in Conexao.objects.filter(status__in=[Conexao.CONECTADA, Conexao.ERRO], expira_em__isnull=False, expira_em__lte=limite):
            renovadas += bool(services.renovar_credenciais(conexao))
        self.stdout.write(self.style.SUCCESS(f"{renovadas} credencial(is) renovada(s)."))
