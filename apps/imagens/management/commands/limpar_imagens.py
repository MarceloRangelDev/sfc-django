"""Limpeza de imagens: remove do R2 os originais substituídos/removidos após a retenção e os envios nunca confirmados.
Agende (cron/Agendador de Tarefas) uma vez por dia:  python manage.py limpar_imagens"""
from django.core.management.base import BaseCommand

from apps.imagens import servico


class Command(BaseCommand):
    help = "Apaga do R2 originais expirados (retenção) e uploads órfãos."

    def handle(self, *args, **opcoes):
        apagadas, falhas = servico.limpar()
        self.stdout.write(f"Objetos removidos: {apagadas}. Falhas (serão tentadas na próxima execução): {falhas}.")
