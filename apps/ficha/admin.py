from django.contrib import admin
from .models import Ficha


@admin.register(Ficha)
class FichaAdmin(admin.ModelAdmin):
    list_display = ("nome", "criado_em", "atualizado_em")
    search_fields = ("nome",)
