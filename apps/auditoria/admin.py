from django.contrib import admin

from .models import RegistroAuditoria


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("criado_em", "usuario", "cliente", "acao", "modulo", "resultado", "ip")
    list_filter = ("acao", "modulo", "resultado", "cliente")
    search_fields = ("usuario__username", "descricao", "registro_id")
    date_hierarchy = "criado_em"

    # Um log de auditoria que pode ser criado/editado manualmente pela
    # tela do admin não serve como prova de nada — só leitura.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
