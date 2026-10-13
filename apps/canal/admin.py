from django.contrib import admin

from .models import CanalVenda


@admin.register(CanalVenda)
class CanalVendaAdmin(admin.ModelAdmin):
    list_display = ("id", "nome", "descricao", "cliente", "taxa_percentual_total", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "descricao")
