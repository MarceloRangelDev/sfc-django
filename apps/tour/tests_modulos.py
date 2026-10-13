"""Tours das telas dos módulos novos (src/assets/js/tours-modulos.js).

Como o tour roda no navegador, aqui se garante o que dá para garantir no servidor:
cada chave do registro está ligada a uma tela real, cada tela que chama o tour usa uma
chave existente, os seletores dos passos apontam para elementos que o template tem
e a preferência "não mostrar novamente" funciona para todas as chaves.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.cliente.models import Cliente
from apps.tour.models import TourPreferencia

RAIZ = Path(settings.BASE_DIR)
ARQUIVO_JS = RAIZ / "src" / "assets" / "js" / "tours-modulos.js"

# chave do tour -> template da tela (a chave de conta é "contas-pagar"/"contas-receber")
TELAS = {
    "processados": "apps/processado/templates/processado.html",
    "processado-form": "apps/processado/templates/processado_form.html",
    "canais-venda": "apps/canal/templates/canal.html",
    "canal-form": "apps/canal/templates/canal_form.html",
    "categorias": "apps/categoria/templates/categoria.html",
    "categoria-form": "apps/categoria/templates/categoria_form.html",
    "produtos": "apps/produto/templates/produto.html",
    "produto-form": "apps/produto/templates/produto_form.html",
    "fichas": "apps/ficha/templates/ficha.html",
    "ficha-form": "apps/ficha/templates/ficha_form.html",
    "precificacoes": "apps/precificacao/templates/precificacao.html",
    "precificacao-form": "apps/precificacao/templates/precificacao_form.html",
    "custos": "apps/custo/templates/custo.html",
    "custo-form": "apps/custo/templates/custo_form.html",
    "custo-categorias": "apps/custo/templates/custo_categorias.html",
    "custos-comuns": "apps/custo/templates/custo_comum.html",
    "estoque": "apps/estoque/templates/estoque.html",
    "estoque-movimentar": "apps/estoque/templates/estoque_movimentar.html",
    "estoque-movimentos": "apps/estoque/templates/estoque_movimentos.html",
    "estoque-transferencias": "apps/estoque/templates/estoque_transferencias.html",
    "estoque-transferencia-form": "apps/estoque/templates/estoque_transferencia_form.html",
    "estoque-alertas": "apps/estoque/templates/estoque_alertas.html",
    "contas-pagar": "apps/financeiro/templates/conta_list.html",
    "contas-receber": "apps/financeiro/templates/conta_list.html",
    "conta-form": "apps/financeiro/templates/conta_form.html",
    "conta-detalhe": "apps/financeiro/templates/conta_detalhe.html",
    "financeiro-dre": "apps/financeiro/templates/financeiro_dre.html",
    "financeiro-balanco": "apps/financeiro/templates/financeiro_balanco.html",
    "financeiro-categorias": "apps/financeiro/templates/financeiro_categorias.html",
    "painel": "apps/dashboard/templates/dashboard.html",
}
def _registro():
    """{chave: {"passos": quantidade, "seletores": [seletores literais]}} lido do arquivo JS
    (sem executá-lo). Passos montados por helpers (NOVO(...), FILTROS(...)) contam como passo
    mas não têm seletor literal."""
    texto = ARQUIVO_JS.read_text(encoding="utf-8")
    corpo = texto[texto.index("var TOURS = {"):texto.index("window.TOURS_MODULOS")]
    registro = {}
    atual = None
    for linha in corpo.splitlines():
        chave = re.match(r"\s{4}'([a-z0-9-]+)': \[", linha)
        if chave:
            atual = chave.group(1)
            registro[atual] = {"passos": 0, "seletores": []}
        elif atual and re.match(r"\s{6}\S", linha) and not linha.strip().startswith("//"):
            registro[atual]["passos"] += 1
            registro[atual]["seletores"] += re.findall(r"element: '((?:[^'\\]|\\.)*)'", linha)
    return registro


class RegistroDeToursTests(TestCase):
    def test_toda_chave_tem_tela_e_toda_tela_usa_chave_existente(self):
        registro = _registro()
        self.assertEqual(set(registro), set(TELAS))
        for chave in registro:
            self.assertTrue(1 <= len(chave) <= 50 and re.fullmatch(r"[a-z0-9-]+", chave), chave)  # cabe no slug da preferência
        usados = set()
        for template in set(TELAS.values()):
            texto = (RAIZ / template).read_text(encoding="utf-8")
            chamadas = re.findall(r"initTourModulo\('([^']+)'\)", texto)
            self.assertTrue(chamadas, f"{template} não chama initTourModulo")
            for chamada in chamadas:
                chave = chamada.replace("{{ chave }}", "pagar") if "{{ chave }}" in chamada else chamada
                usados.add(chave)
                if "{{ chave }}" in chamada:
                    usados.add(chamada.replace("{{ chave }}", "receber"))
        self.assertEqual(usados, set(registro))

    def test_todo_passo_tem_titulo_texto_e_seletor(self):
        texto = ARQUIVO_JS.read_text(encoding="utf-8")
        self.assertIn("window.initTourModulo", texto)
        for chave, dados in _registro().items():
            self.assertGreaterEqual(dados["passos"], 2, f"tour '{chave}' com poucos passos")
        # títulos e textos dos passos específicos estão preenchidos (sem strings vazias)
        self.assertNotRegex(texto, r"title: '',|text: ''[,}]")

    def test_seletores_de_id_existem_no_template_da_tela(self):
        for chave, dados in _registro().items():
            template = (RAIZ / TELAS[chave]).read_text(encoding="utf-8")
            for seletor in dados["seletores"]:
                for ident in re.findall(r"#([A-Za-z][\w-]*)", seletor):
                    if ident.startswith("id_"):  # campo de formulário: id_<campo> ← {{ form.<campo> }}
                        campo = ident[3:]
                        self.assertRegex(template, rf"form\.{campo}\b|formset\.empty_form\.{campo}\b", f"{chave}: campo {campo} não está no template")
                    else:
                        self.assertIn(f'id="{ident}"', template, f"{chave}: #{ident} não existe em {TELAS[chave]}")

    def test_arquivo_e_carregado_em_todas_as_telas_depois_do_tour_base(self):
        scripts = (RAIZ / "templates/layout/partials/scripts.html").read_text(encoding="utf-8")
        self.assertLess(scripts.index("js/tour.js"), scripts.index("js/tours-modulos.js"))
        self.assertTrue(ARQUIVO_JS.exists())


class PreferenciaPorChaveTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="u", email="u@t.local", password="SenhaForte123!")
        self.outro = User.objects.create_user(username="o", email="o@t.local", password="SenhaForte123!")

    def test_nao_mostrar_novamente_vale_por_usuario_para_todas_as_chaves(self):
        self.client.force_login(self.usuario)
        for chave in _registro():
            self.assertEqual(self.client.get(reverse("tour-estado", args=[chave])).json(), {"ocultar": False}, chave)
            self.assertEqual(self.client.post(reverse("tour-ocultar", args=[chave])).json(), {"ocultar": True}, chave)
            self.assertEqual(self.client.get(reverse("tour-estado", args=[chave])).json(), {"ocultar": True}, chave)
        self.assertEqual(TourPreferencia.objects.filter(usuario=self.usuario).count(), len(_registro()))
        self.client.force_login(self.outro)  # a preferência não vaza para outro usuário
        self.assertEqual(self.client.get(reverse("tour-estado", args=["painel"])).json(), {"ocultar": False})


class TelasComTourTests(TestCase):
    """As telas renderizam a chamada do tour com a chave certa (superusuário vê todas)."""

    def setUp(self):
        cliente = Cliente.objects.create(tipo_pessoa="PJ", cpf_cnpj="11222333000181", nome_razao_social="Empresa")
        self.admin = User.objects.create_user(username="adm", email="adm@t.local", password="SenhaForte123!", is_superuser=True)
        self.admin.profile.cliente = cliente
        self.admin.profile.save()
        self.client.force_login(self.admin)

    def test_telas_de_lista_e_formulario_chamam_o_tour(self):
        telas = {
            "index": "painel", "processado": "processados", "processado-create": "processado-form", "canal-list": "canais-venda",
            "canal-create": "canal-form", "categoria": "categorias", "categoria-create": "categoria-form", "produto": "produtos",
            "produto-create": "produto-form", "ficha": "fichas", "ficha-create": "ficha-form", "precificacao": "precificacoes",
            "precificacao-create": "precificacao-form", "custo-list": "custos", "custo-create": "custo-form",
            "custo-categorias": "custo-categorias", "custo-comum-list": "custos-comuns", "estoque": "estoque", "estoque-movimentar": "estoque-movimentar",
            "estoque-movimentos": "estoque-movimentos", "estoque-transferencias": "estoque-transferencias",
            "estoque-transferencia-nova": "estoque-transferencia-form", "estoque-alertas": "estoque-alertas",
            "conta-pagar-list": "contas-pagar", "conta-receber-list": "contas-receber", "conta-pagar-create": "conta-form",
            "financeiro-dre": "financeiro-dre", "financeiro-balanco": "financeiro-balanco", "financeiro-categorias": "financeiro-categorias",
        }
        for rota, chave in telas.items():
            resposta = self.client.get(reverse(rota))
            self.assertEqual(resposta.status_code, 200, rota)
            self.assertContains(resposta, f"initTourModulo('{chave}')", msg_prefix=rota)
            self.assertContains(resposta, "js/tours-modulos.js", msg_prefix=rota)
