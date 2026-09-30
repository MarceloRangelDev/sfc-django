from django.contrib import admin

from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome_razao_social", "tipo_pessoa", "cpf_cnpj", "ativo", "criado_em")
    list_filter = ("tipo_pessoa", "ativo")
    search_fields = ("nome_razao_social", "nome_fantasia", "cpf_cnpj")
