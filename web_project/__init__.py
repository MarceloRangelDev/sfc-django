
# from web_project.bootstrap import TemplateBootstrap
from web_project.template_helpers.theme import TemplateHelper
from django.conf import settings


class TemplateLayout:
    # Initialize the bootstrap files and page layout
    def init(self, context):
        # Init the Template Context using TEMPLATE_CONFIG
        context = TemplateHelper.init_context(context)
        # Set a default layout globally using settings.py. Can be set in the page level view file as well.
        layout = context["layout"]

        # Set the selected layout
        context.update(
            {
                "layout_path": TemplateHelper.set_layout(
                    "layout_" + layout + ".html", context
                ),
                # Set default rtl True if the language Arabic else use rtl_mode value from TEMPLATE_CONFIG
                "rtl_mode": True
                if self.request.COOKIES.get('django_text_direction') == "rtl"
                else settings.TEMPLATE_CONFIG.get("rtl_mode"),
            }
        )

        # Papéis que ocultam o menu lateral (ex.: Caixa): o layout nativo "sem menu" já remove a barra e ajusta a página.
        from apps.acl.interface import menu_lateral_oculto

        if menu_lateral_oculto(self.request.user):
            context["is_menu"] = False

        # Map context variables
        TemplateHelper.map_context(context)

        return context
