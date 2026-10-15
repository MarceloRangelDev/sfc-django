"""Sincroniza as agendas conectadas (vencimentos e compromissos). Agende no cron/Agendador de Tarefas, ex.: a cada 15 minutos.

    python manage.py sincronizar_integracoes

Respeita a pausa por limite do provedor, o teto de operações por conexão e renova credenciais prestes a expirar. Idempotente.
"""
from django.core.management.base import BaseCommand

from apps.integracao import services
from apps.integracao.models import Conexao


class Command(BaseCommand):
    help = "Sincroniza as conexões de agenda ativas."

    def handle(self, *args, **options):
        total = 0
        for conexao in Conexao.objects.filter(status=Conexao.CONECTADA).select_related("cliente"):
            if not conexao.adaptador.sincroniza_agenda:
                continue
            resumo = services.sincronizar_agenda(conexao)
            total += 1
            self.stdout.write(f"{conexao.nome}: " + ", ".join(f"{k}={v}" for k, v in resumo.items()))
        self.stdout.write(self.style.SUCCESS(f"{total} conexão(ões) de agenda processada(s)."))
