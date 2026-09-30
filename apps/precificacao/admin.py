from django.contrib import admin
from .models import Precificacao


@admin.register(Precificacao)
class PrecificacaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "criado_em", "atualizado_em")
    search_fields = ("nome",)
