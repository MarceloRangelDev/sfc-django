from django.contrib import admin

from .models import ClienteModulo, Modulo


@admin.register(Modulo)
class ModuloAdmin(admin.ModelAdmin):
    list_display = ("nome", "slug", "ativo", "atualizado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "slug")


@admin.register(ClienteModulo)
class ClienteModuloAdmin(admin.ModelAdmin):
    list_display = ("cliente", "modulo", "ativo", "atualizado_em")
    list_filter = ("ativo", "modulo")
    search_fields = ("cliente__nome_razao_social", "modulo__nome")
