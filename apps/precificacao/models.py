from django.db import models


class Precificacao(models.Model):
    """
    Modelo base da tela de Precificação.

    Serve, por enquanto, de âncora para as permissões nativas do Django
    (view/add/change/delete) usadas pelo ACL do menu e das telas — ver
    web_project/decorators.py e templates/layout/partials/menu/vertical/json/vertical_menu.json.

    As regras de precificação (Serviço x Produto) usarão o segmento de
    trabalho do usuário — ver Profile.segmento_trabalho, Profile.precifica_servico
    e Profile.precifica_produto em auth/models.py — quando esta tela for implementada.
    """

    nome = models.CharField(max_length=150)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Precificação"
        verbose_name_plural = "Precificações"

    def __str__(self):
        return self.nome
