from django.db import models


class Insumo(models.Model):
    """
    Modelo base da tela de Insumos.

    Serve, por enquanto, de âncora para as permissões nativas do Django
    (view/add/change/delete) usadas pelo ACL do menu e das telas — ver
    web_project/decorators.py e templates/layout/partials/menu/vertical/json/vertical_menu.json.
    Os campos completos (unidade de medida, valor pago, cálculo automático
    por Kg/L/Un etc.) serão adicionados quando a tela de CRUD for implementada.
    """

    nome = models.CharField(max_length=150)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"

    def __str__(self):
        return self.nome
