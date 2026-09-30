from django.contrib import admin
from .models import Processado


@admin.register(Processado)
class ProcessadoAdmin(admin.ModelAdmin):
    list_display = ("nome", "criado_em", "atualizado_em")
    search_fields = ("nome",)
