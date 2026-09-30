from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from web_project import TemplateLayout
from auth.models import Profile

"""
Tela "Meu Perfil": permite ao usuário logado escolher o segmento de trabalho
(Serviço / Produto / Ambos), usado para aplicar regras diferentes de
precificação em outras partes do sistema (ver Profile.precifica_servico /
Profile.precifica_produto em auth/models.py).
"""


@method_decorator(login_required, name="dispatch")
class ProfileView(TemplateView):
    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.update(
            {
                "profile": self.request.user.profile,
                "segmento_choices": Profile.SEGMENTO_CHOICES,
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        segmento = request.POST.get("segmento_trabalho")
        valid_values = dict(Profile.SEGMENTO_CHOICES)

        if segmento not in valid_values:
            messages.error(request, "Selecione um segmento de trabalho válido.")
            return redirect("profile")

        profile = request.user.profile
        profile.segmento_trabalho = segmento
        profile.save(update_fields=["segmento_trabalho"])
        messages.success(request, "Segmento de trabalho atualizado com sucesso.")
        return redirect("profile")
