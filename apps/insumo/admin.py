from django.contrib import admin
from .models import Insumo


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ("nome", "cliente", "categoria", "unidade_medida", "quantidade", "preco", "ativo", "criado_em")
    list_filter = ("cliente", "categoria", "unidade_medida", "ativo")
    search_fields = ("nome",)
