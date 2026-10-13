from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from web_project import TemplateLayout

"""
Tela "Meu Perfil": mostra a conta e o segmento de trabalho do usuário. O
segmento pertence ao Cliente (ver cliente.segmento) e é definido por um
administrador — o usuário apenas o herda (ver Profile.segmento).
"""


@method_decorator(login_required, name="dispatch")
class ProfileView(TemplateView):
    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["profile"] = self.request.user.profile
        return context
