"""
URL configuration for web_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path
from web_project.views import SystemView

urlpatterns = [
    path("admin/", admin.site.urls),

    # Auth
    path("", include("auth.urls")),
    # starter urls
    path("", include("apps.sample.urls")),
    # pages urls
    path("", include("apps.pages.urls")),
    # insumo urls
    path("", include("apps.insumo.urls")),
    # produto urls
    path("", include("apps.produto.urls")),
    # processados urls
    path("", include("apps.processado.urls")),
    # categoria urls
    path("", include("apps.categoria.urls")),
    # ficha urls
    path("", include("apps.ficha.urls")),
    # precificacao urls
    path("", include("apps.precificacao.urls")),
    # cliente urls (cadastro de clientes/tenants - CPF/CNPJ)
    path("", include("apps.cliente.urls")),
    # modulo urls (habilitar/desabilitar módulos por cliente)
    path("", include("apps.modulo.urls")),
    # acl urls (papéis e permissões)
    path("", include("apps.acl.urls")),
    # medida urls (cadastro global de unidades de medida - administradores)
    path("", include("apps.medida.urls")),
]

handler404 = SystemView.as_view(template_name="pages_misc_error.html", status=404)
handler403 = SystemView.as_view(template_name="pages_misc_not_authorized.html", status=403)
handler400 = SystemView.as_view(template_name="pages_misc_error.html", status=400)
handler500 = SystemView.as_view(template_name="pages_misc_error.html", status=500)
