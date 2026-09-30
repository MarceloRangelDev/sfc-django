"""
Mixins reaproveitáveis pelas telas de negócio (Insumo, Categoria, Produto,
Processado, Ficha, Precificação) para aplicar o isolamento multi-tenant
(cada Cliente só enxerga os próprios dados) sempre da mesma forma.
"""


class ClienteScopedMixin:
    """Restringe o queryset de uma ListView/DetailView/UpdateView/DeleteView
    ao Cliente do usuário logado, e força o Cliente do objeto salvo por uma
    CreateView/UpdateView a ser sempre o do usuário logado.

    Requer que o model tenha um campo `cliente` (FK para cliente.Cliente).

    Por quê: sem isso, bastaria alguém trocar o "pk" na URL (ex:
    /insumo/123/editar) para tentar acessar um registro de outro Cliente
    (ataque conhecido como IDOR). Filtrando sempre pelo Cliente do usuário
    autenticado — nunca por um valor vindo do formulário/POST/querystring —
    um "pk" de outro Cliente simplesmente não é encontrado (404), em vez de
    vazar dado ou permitir edição indevida.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        cliente = getattr(self.request.user.profile, "cliente", None)
        return queryset.filter(cliente=cliente)

    def form_valid(self, form):
        form.instance.cliente = self.request.user.profile.cliente
        return super().form_valid(form)
