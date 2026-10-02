from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter

from web_project.acesso import administrador_global

from . import views

router = SimpleRouter()
router.register("insumos", views.InsumoViewSet, basename="api-insumo")
router.register("medidas", views.MedidaViewSet, basename="api-medida")
router.register("categorias", views.CategoriaViewSet, basename="api-categoria")
router.register("usuarios", views.UsuarioViewSet, basename="api-usuario")
router.register("clientes", views.ClienteViewSet, basename="api-cliente")
router.register("modulos", views.ModuloViewSet, basename="api-modulo")
router.register("perfis", views.PerfilViewSet, basename="api-perfil")
router.register("permissoes", views.PermissaoViewSet, basename="api-permissao")
router.register("auditoria", views.AuditoriaViewSet, basename="api-auditoria")


def somente_administrador(view):
    """Documentação (schema/Swagger/Redoc): exige login na sessão web E ser
    administrador — é a ferramenta do administrador para testar a API. O
    schema descreve toda a superfície da API, então não fica público."""

    @login_required
    def protegida(request, *args, **kwargs):
        if not administrador_global(request.user):
            raise PermissionDenied("Somente administradores podem acessar a documentação da API.")
        return view(request, *args, **kwargs)

    return protegida


urlpatterns = [
    # Autenticação
    path("api/v1/auth/login/", views.LoginView.as_view(), name="api-login"),
    path("api/v1/auth/logout/", views.LogoutView.as_view(), name="api-logout"),
    path("api/v1/auth/me/", views.MeView.as_view(), name="api-me"),
    path("api/v1/auth/register/", views.CadastroView.as_view(), name="api-register"),
    path("api/v1/auth/password/change/", views.AlterarSenhaView.as_view(), name="api-password-change"),
    path("api/v1/auth/password/forgot/", views.EsqueciSenhaView.as_view(), name="api-password-forgot"),
    path("api/v1/auth/password/reset/", views.RedefinirSenhaView.as_view(), name="api-password-reset"),
    # Recursos
    path("api/v1/", include(router.urls)),
    # Documentação OpenAPI (gerada do código)
    path("api/schema/", somente_administrador(SpectacularAPIView.as_view()), name="api-schema"),
    path("api/docs/", somente_administrador(SpectacularSwaggerView.as_view(url_name="api-schema")), name="api-docs"),
    path("api/redoc/", somente_administrador(SpectacularRedocView.as_view(url_name="api-schema")), name="api-redoc"),
]
