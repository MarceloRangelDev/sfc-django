from django.contrib import admin

from .models import Medida


@admin.register(Medida)
class MedidaAdmin(admin.ModelAdmin):
    list_display = ("id", "medida", "sigla", "dimensao", "fator_base", "ativo")
    list_filter = ("dimensao", "ativo")
    search_fields = ("medida", "sigla")
