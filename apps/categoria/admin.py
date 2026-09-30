from django.contrib import admin
from .models import Categoria


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nome", "cliente", "criado_em", "atualizado_em")
    list_filter = ("cliente",)
    search_fields = ("nome",)
