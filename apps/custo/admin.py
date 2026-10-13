from django.contrib import admin

from .models import CustoModelo


@admin.register(CustoModelo)
class CustoModeloAdmin(admin.ModelAdmin):
    """Custos comuns. Os PADRÕES DO SISTEMA (sem cliente) só são mantidos aqui, pelos administradores;
    os que cada empresa cadastra em Configurações > Custos comuns aparecem com o cliente preenchido."""

    list_display = ("nome", "tipo", "cliente", "ordem", "ativo")
    list_filter = ("tipo", "ativo")
    search_fields = ("nome",)
