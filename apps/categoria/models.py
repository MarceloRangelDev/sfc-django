from django.db import models


class Categoria(models.Model):
    """
    Modelo base da tela de Categorias.

    Serve, por enquanto, de âncora para as permissões nativas do Django
    (view/add/change/delete) usadas pelo ACL do menu e das telas — ver
    web_project/decorators.py e templates/layout/partials/menu/vertical/json/vertical_menu.json.
    """

    nome = models.CharField(max_length=150)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"

    def __str__(self):
        return self.nome
