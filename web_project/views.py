from django.views.generic import TemplateView
from web_project import TemplateLayout
from web_project.template_helpers.theme import TemplateHelper


class SystemView(TemplateView):
    template_name = "pages/system/not-found.html"
    status = ""

    def get_context_data(self, **kwargs):
        # A function to init the global layout. It is defined in web_project/__init__.py file
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))

        # Define the layout for this module
        # _templates/layout/system.html
        context.update(
            {
                "layout_path": TemplateHelper.set_layout("system.html", context),
                "status": self.status,
            }
        )

        return context

    def render_to_response(self, context, **response_kwargs):
        # Bug corrigido na Fase 6: `self.status` só era usado para EXIBIR o
        # número na página (ex: "403" no template) — nunca era repassado
        # como código de status HTTP de verdade, então handler403/404/400/500
        # (config/urls.py) sempre respondiam 200 OK, mesmo mostrando a
        # página de "Não autorizado"/"Não encontrado". Descoberto ao
        # escrever os testes automatizados de módulo/permissão (Fase 6),
        # que checam o status_code da resposta, não só o conteúdo da página.
        if self.status:
            response_kwargs.setdefault("status", self.status)
        return super().render_to_response(context, **response_kwargs)

    # Segundo bug corrigido na Fase 6: quando uma exceção (Http404,
    # PermissionDenied etc.) é levantada durante uma requisição POST/PUT/
    # PATCH/DELETE, o Django chama o handler configurado (handler403/404/...)
    # usando o MESMO objeto request — ou seja, com request.method ainda
    # "POST". Como esta view (TemplateView) só definia `get()`, o
    # View.dispatch() dela caía no "método não permitido" e respondia 405
    # em vez de mostrar a página de erro com o status correto. Ex: excluir
    # um registro de outro cliente (IDOR) devia dar 404, mas dava 405.
    # A página de erro é sempre a mesma independente do método HTTP usado
    # na requisição original, então os métodos abaixo só reusam get().
    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    put = post
    patch = post
    delete = post
