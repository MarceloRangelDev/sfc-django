from django.urls import path

from web_project.decorators import module_required

from . import views

# Cada tipo de conta tem módulo e permissões próprios (financeiro.*_contapagar / *_contareceber);
# DRE/balanço usam financeiro.view_relatoriofinanceiro e as categorias financeiro.*_categoriafinanceira.
CONTAS = {
    "pagar": ("financeiro-pagar", "contapagar", "contas-pagar"),
    "receber": ("financeiro-receber", "contareceber", "contas-receber"),
}

urlpatterns = []
for chave, (modulo, modelo, prefixo) in CONTAS.items():
    v = views.criar_views(chave)
    ver, add, alterar, excluir = (module_required(modulo, f"financeiro.{a}_{modelo}") for a in ("view", "add", "change", "delete"))
    urlpatterns += [
        path(prefixo, ver(v["list"].as_view()), name=f"conta-{chave}-list"),
        path(f"{prefixo}/nova", add(v["create"].as_view()), name=f"conta-{chave}-create"),
        path(f"{prefixo}/<int:pk>", ver(v["detail"].as_view()), name=f"conta-{chave}-detail"),
        path(f"{prefixo}/<int:pk>/editar", alterar(v["edit"].as_view()), name=f"conta-{chave}-edit"),
        path(f"{prefixo}/<int:pk>/baixar", alterar(v["baixar"].as_view()), name=f"conta-{chave}-baixar"),
        path(f"{prefixo}/<int:pk>/baixas/<int:baixa_pk>/estornar", alterar(v["estornar"].as_view()), name=f"conta-{chave}-estornar"),
        path(f"{prefixo}/<int:pk>/excluir", excluir(v["excluir"].as_view()), name=f"conta-{chave}-excluir"),
    ]

relatorio = module_required("financeiro-relatorios", "financeiro.view_relatoriofinanceiro")
categorias = lambda acao: module_required("financeiro-categorias", f"financeiro.{acao}_categoriafinanceira")  # noqa: E731
urlpatterns += [
    path("financeiro/dre", relatorio(views.DreView.as_view()), name="financeiro-dre"),
    path("financeiro/balanco", relatorio(views.BalancoView.as_view()), name="financeiro-balanco"),
    path("financeiro/categorias", categorias("view")(views.CategoriasView.as_view()), name="financeiro-categorias"),
    path("financeiro/categorias/padrao", categorias("add")(views.CategoriaPadraoView.as_view()), name="financeiro-categorias-padrao"),
    path("financeiro/categorias/<int:pk>/alternar-ativo", categorias("change")(views.CategoriaAlternarAtivoView.as_view()), name="financeiro-categoria-alternar"),
    path("financeiro/categorias/<int:pk>/excluir", categorias("delete")(views.CategoriaExcluirView.as_view()), name="financeiro-categoria-excluir"),
]
