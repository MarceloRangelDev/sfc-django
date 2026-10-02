from django.contrib import admin

from .models import Insumo


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ("nome", "cliente", "medida", "quantidade", "preco_pago", "preco_unidade", "ativo", "criado_em")
    list_filter = ("cliente", "medida", "ativo")
    search_fields = ("nome",)
    readonly_fields = ("preco_unidade",)
