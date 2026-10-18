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
    # insumoglobal urls (cadastro padrão de insumos e fila de insumos fora do padrão)
    path("", include("apps.insumoglobal.urls")),
    # assinatura (Pagar.me) e webhook público
    path("", include("apps.assinatura.urls")),
    # kanban urls (somente administrador)
    path("", include("apps.kanban.urls")),
    # produto urls
    path("", include("apps.produto.urls")),
    # processados urls
    path("", include("apps.processado.urls")),
    # pdvconfig urls (formas de pagamento, mesas e ajustes do PDV - Configurações)
    path("", include("apps.pdvconfig.urls")),
    # pdv urls (caixa, vendas, comandas e comprovantes)
    path("", include("apps.pdv.urls")),
    # pedido urls (pedidos de produção e tela da cozinha)
    path("", include("apps.pedido.urls")),
    # maodeobra urls (colaboradores e apontamentos por hora/dia)
    path("", include("apps.maodeobra.urls")),
    # integracao urls (conexões, mensagens, agenda e webhooks)
    path("", include("apps.integracao.urls")),
    # fiscal urls (configuração fiscal, certificado e documentos fiscais)
    path("", include("apps.fiscal.urls")),
    # imagens urls (envio, entrega autorizada e painel do proprietário)
    path("", include("apps.imagens.urls")),
    # cotacao urls (cotações de preços entre lojas)
    path("", include("apps.cotacao.urls")),
    # compras urls (lista de compras e sugestões do estoque)
    path("", include("apps.compras.urls")),
    # consumidor urls (clientes finais da empresa)
    path("", include("apps.consumidor.urls")),
    # categoria urls
    path("", include("apps.categoria.urls")),
    # dashboard urls (usuários online do painel; o painel em si é a rota "index")
    path("", include("apps.dashboard.urls")),
    # financeiro urls (contas a pagar/receber, categorias, DRE e balanço)
    path("", include("apps.financeiro.urls")),
    # estoque urls (saldos, lotes, movimentações, transferências e alertas)
    path("", include("apps.estoque.urls")),
    # custo urls (custo operacional fixo/variável e distribuição por categoria)
    path("", include("apps.custo.urls")),
    # canal urls (canais de venda e taxas - Configurações)
    path("", include("apps.canal.urls")),
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
    # API REST v1 + documentação Swagger/OpenAPI
    path("", include("apps.api.urls")),
    # usuario urls (cadastro de usuários vinculados a cliente e perfil)
    path("", include("apps.usuario.urls")),
    # notificacao urls (envio, histórico e caixa de entrada do usuário)
    path("", include("apps.notificacao.urls")),
    # tour urls (preferência "não mostrar novamente" dos tours de página)
    path("", include("apps.tour.urls")),
    # auditoria urls (consulta administrativa do log de auditoria)
    path("", include("apps.auditoria.urls")),
    # medida urls (cadastro global de unidades de medida - administradores)
    path("", include("apps.medida.urls")),
]

handler404 = SystemView.as_view(template_name="pages_misc_error.html", status=404)
handler403 = SystemView.as_view(template_name="pages_misc_not_authorized.html", status=403)
handler400 = SystemView.as_view(template_name="pages_misc_error.html", status=400)
handler500 = SystemView.as_view(template_name="pages_misc_error.html", status=500)
