from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.categoria.models import Categoria
from apps.cliente.models import Cliente
from apps.insumo.models import Insumo
from apps.insumo.tests import criar_cliente_com_acesso_total, medida, novo_insumo
from apps.medida.models import Medida
from apps.modulo.models import ClienteModulo, Modulo
from web_project.testing import habilitar_modulo
from auth.models import Profile

SENHA = "SenhaForte123!"
SENHA_NOVA = "Outra#Senha2026x"
V1 = "/api/v1"


def cliente_api(usuario=None):
    """APIClient autenticado por token (Authorization: Bearer ...)."""
    api = APIClient()
    if usuario is not None:
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
    return api


class BaseAPITests(TestCase):
    def setUp(self):
        cache.clear()  # contadores de rate limit/throttle são por processo
        call_command("seed_acl", verbosity=0)
        self.cliente_a, self.usuario_a = criar_cliente_com_acesso_total("A", "11222333000181")
        self.cliente_b, self.usuario_b = criar_cliente_com_acesso_total("B", "11444777000161")
        for cliente in (self.cliente_a, self.cliente_b):
            habilitar_modulo(cliente, "categoria")
        for usuario in (self.usuario_a, self.usuario_b):
            usuario.groups.first().permissions.add(*Permission.objects.filter(content_type__app_label="categoria"))
        self.admin = User.objects.create_user(username="admin", email="admin@teste.local", password=SENHA)
        self.admin.groups.add(Group.objects.get(name="Administrador"))
        self.api_a = cliente_api(self.usuario_a)
        self.api_b = cliente_api(self.usuario_b)
        self.api_admin = cliente_api(self.admin)
        self.anonimo = cliente_api()

    def ok(self, resposta, codigo=200):
        self.assertEqual(resposta.status_code, codigo, getattr(resposta, "content", b"")[:400])
        corpo = resposta.json() if resposta.content else None
        if corpo is not None:
            self.assertTrue(corpo["success"])
        return corpo

    def erro(self, resposta, codigo):
        self.assertEqual(resposta.status_code, codigo, resposta.content[:400])
        corpo = resposta.json()
        self.assertFalse(corpo["success"])
        self.assertIn("message", corpo)
        self.assertIn("errors", corpo)
        return corpo


class EnvelopeETransporteTests(BaseAPITests):
    def test_sucesso_tem_envelope_success_message_data(self):
        corpo = self.ok(self.api_a.get(f"{V1}/auth/me/"))
        self.assertEqual(set(corpo), {"success", "message", "data"})

    def test_erro_tem_envelope_success_message_errors(self):
        corpo = self.erro(self.anonimo.get(f"{V1}/insumos/"), 401)
        self.assertEqual(set(corpo), {"success", "message", "errors"})

    def test_sem_token_401(self):
        for caminho in ("insumos", "medidas", "usuarios", "clientes", "perfis", "auditoria", "categorias", "modulos", "permissoes"):
            self.erro(self.anonimo.get(f"{V1}/{caminho}/"), 401)

    def test_token_invalido_401(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION="Bearer lixo")
        self.erro(api.get(f"{V1}/insumos/"), 401)

    def test_cabecalho_com_esquema_errado_401(self):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=self.usuario_a)
        api.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")  # só "Bearer" é aceito
        self.erro(api.get(f"{V1}/insumos/"), 401)

    def test_somente_json_e_aceito(self):
        resposta = self.api_a.post(f"{V1}/categorias/", {"nome": "X"}, format="multipart")
        self.erro(resposta, 415)

    def test_json_malformado_400(self):
        resposta = self.api_a.generic("POST", f"{V1}/categorias/", "{nao e json", content_type="application/json")
        self.erro(resposta, 400)

    def test_recurso_inexistente_404_em_json(self):
        self.erro(self.api_a.get(f"{V1}/insumos/999999/"), 404)

    def test_metodo_nao_permitido_405(self):
        self.erro(self.api_a.delete(f"{V1}/categorias/1/"), 405)

    def test_erro_interno_nao_vaza_detalhes(self):
        with mock.patch("apps.api.views.InsumoViewSet.get_queryset", side_effect=RuntimeError("segredo interno")):
            resposta = self.api_a.get(f"{V1}/insumos/")
        corpo = self.erro(resposta, 500)
        self.assertNotIn("segredo", resposta.content.decode())
        self.assertEqual(corpo["message"], "Erro interno do servidor.")


class LoginETokenTests(BaseAPITests):
    def login(self, login="usuario_a", senha=SENHA, api=None):
        return (api or self.anonimo).post(f"{V1}/auth/login/", {"login": login, "password": senha}, format="json")

    def test_login_por_usuario_e_por_email(self):
        for login in ("usuario_a", "usuario_a@teste.local", "USUARIO_A@TESTE.LOCAL"):
            dados = self.ok(self.login(login))["data"]
            self.assertEqual(dados["tipo"], "Bearer")
            self.assertTrue(dados["token"])
            self.assertEqual(dados["usuario"]["cliente"]["id"], self.cliente_a.pk)
            self.assertIn("insumo", dados["usuario"]["modulos"])
            self.assertIn("insumo.view_insumo", dados["usuario"]["permissoes"])

    def test_login_nunca_devolve_senha_nem_hash(self):
        resposta = self.login()
        texto = resposta.content.decode()
        self.assertNotIn(SENHA, texto)
        self.assertNotIn("pbkdf2", texto)
        self.assertNotIn("password", self.ok(resposta)["data"]["usuario"])

    def test_credenciais_invalidas_401_com_mesma_mensagem(self):
        mensagens = {
            self.erro(self.login(senha="errada"), 401)["message"],
            self.erro(self.login(login="nao_existe"), 401)["message"],
            self.erro(self.login(login="nao@existe.com"), 401)["message"],
        }
        self.assertEqual(len(mensagens), 1)  # não revela se o usuário existe

    def test_usuario_inativo_nao_loga(self):
        self.usuario_a.is_active = False
        self.usuario_a.save()
        self.erro(self.login(), 401)

    def test_campos_obrigatorios(self):
        self.erro(self.anonimo.post(f"{V1}/auth/login/", {}, format="json"), 400)

    @override_settings(LOGIN_RATE_LIMIT_ATTEMPTS=3)
    def test_rate_limit_bloqueia_apos_tentativas_falhas(self):
        for _ in range(3):
            self.erro(self.login(senha="errada"), 401)
        self.erro(self.login(senha="errada"), 429)
        self.erro(self.login(), 429)  # mesmo a senha certa fica bloqueada na janela

    def test_login_bem_sucedido_reinicia_o_contador(self):
        for _ in range(2):
            self.login(senha="errada")
        self.ok(self.login())
        for _ in range(2):
            self.erro(self.login(senha="errada"), 401)

    def test_novo_login_invalida_o_token_anterior(self):
        primeiro = self.ok(self.login())["data"]["token"]
        segundo = self.ok(self.login())["data"]["token"]
        self.assertNotEqual(primeiro, segundo)
        antigo = APIClient()
        antigo.credentials(HTTP_AUTHORIZATION=f"Bearer {primeiro}")
        self.erro(antigo.get(f"{V1}/auth/me/"), 401)

    def test_logout_invalida_o_token(self):
        token = self.ok(self.login())["data"]["token"]
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(api.post(f"{V1}/auth/logout/").status_code, 204)
        self.erro(api.get(f"{V1}/auth/me/"), 401)

    @override_settings(API_TOKEN_TTL_DIAS=30)
    def test_token_expirado_401(self):
        token, _ = Token.objects.get_or_create(user=self.usuario_a)
        Token.objects.filter(pk=token.pk).update(created=timezone.now() - timedelta(days=31))
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        corpo = self.erro(api.get(f"{V1}/auth/me/"), 401)
        self.assertIn("expirado", corpo["errors"]["detail"].lower())

    def test_token_de_usuario_inativado_para_de_funcionar(self):
        self.usuario_a.is_active = False
        self.usuario_a.save()
        self.erro(self.api_a.get(f"{V1}/auth/me/"), 401)

    def test_login_e_logout_sao_auditados(self):
        token = self.ok(self.login())["data"]["token"]
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        api.post(f"{V1}/auth/logout/")
        acoes = set(RegistroAuditoria.objects.filter(modulo="api").values_list("acao", flat=True))
        self.assertEqual(acoes, {"login", "logout"})


class SenhaECadastroTests(BaseAPITests):
    def test_alterar_senha(self):
        resposta = self.api_a.post(f"{V1}/auth/password/change/", {"senha_atual": SENHA, "nova_senha": SENHA_NOVA}, format="json")
        novo = self.ok(resposta)["data"]["token"]
        self.usuario_a.refresh_from_db()
        self.assertTrue(self.usuario_a.check_password(SENHA_NOVA))
        self.erro(self.api_a.get(f"{V1}/auth/me/"), 401)  # token antigo morreu
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {novo}")
        self.ok(api.get(f"{V1}/auth/me/"))

    def test_alterar_senha_exige_senha_atual_correta_e_senha_forte(self):
        self.erro(self.api_a.post(f"{V1}/auth/password/change/", {"senha_atual": "errada", "nova_senha": SENHA_NOVA}, format="json"), 400)
        self.erro(self.api_a.post(f"{V1}/auth/password/change/", {"senha_atual": SENHA, "nova_senha": "12345678"}, format="json"), 400)
        self.usuario_a.refresh_from_db()
        self.assertTrue(self.usuario_a.check_password(SENHA))

    def test_esqueci_a_senha_responde_igual_para_email_existente_ou_nao(self):
        existente = self.anonimo.post(f"{V1}/auth/password/forgot/", {"email": "usuario_a@teste.local"}, format="json")
        cache.clear()
        inexistente = self.anonimo.post(f"{V1}/auth/password/forgot/", {"email": "ninguem@teste.local"}, format="json")
        self.assertEqual(existente.status_code, inexistente.status_code)
        self.assertEqual(existente.json(), inexistente.json())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_fluxo_completo_de_redefinicao(self):
        self.anonimo.post(f"{V1}/auth/password/forgot/", {"email": "usuario_a@teste.local"}, format="json")
        self.usuario_a.profile.refresh_from_db()
        token = self.usuario_a.profile.forget_password_token
        self.assertTrue(token)
        self.ok(self.anonimo.post(f"{V1}/auth/password/reset/", {"token": token, "nova_senha": SENHA_NOVA}, format="json"))
        self.usuario_a.refresh_from_db()
        self.assertTrue(self.usuario_a.check_password(SENHA_NOVA))
        # token de uso único
        self.erro(self.anonimo.post(f"{V1}/auth/password/reset/", {"token": token, "nova_senha": SENHA_NOVA + "1"}, format="json"), 400)

    def test_redefinicao_com_token_invalido_ou_expirado(self):
        self.erro(self.anonimo.post(f"{V1}/auth/password/reset/", {"token": "xxx", "nova_senha": SENHA_NOVA}, format="json"), 400)
        Profile.objects.filter(user=self.usuario_a).update(
            forget_password_token="expirado", forget_password_token_expiration=timezone.now() - timedelta(hours=1)
        )
        self.erro(self.anonimo.post(f"{V1}/auth/password/reset/", {"token": "expirado", "nova_senha": SENHA_NOVA}, format="json"), 400)

    def test_redefinicao_exige_senha_forte(self):
        Profile.objects.filter(user=self.usuario_a).update(
            forget_password_token="ok", forget_password_token_expiration=timezone.now() + timedelta(hours=1)
        )
        self.erro(self.anonimo.post(f"{V1}/auth/password/reset/", {"token": "ok", "nova_senha": "123"}, format="json"), 400)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_cadastro_publico(self):
        corpo = self.ok(self.anonimo.post(f"{V1}/auth/register/", {"username": "novo", "email": "novo@teste.local", "password": SENHA_NOVA}, format="json"), 201)
        usuario = User.objects.get(username="novo")
        self.assertEqual(corpo["data"]["id"], usuario.pk)
        self.assertTrue(usuario.groups.filter(name="Visualizador").exists())
        self.assertIsNone(usuario.profile.cliente)  # sem cliente = sem acesso a módulos
        self.assertEqual(len(mail.outbox), 1)
        # sem cliente, não acessa nenhum módulo
        self.erro(cliente_api(usuario).get(f"{V1}/insumos/"), 403)

    def test_cadastro_duplicado_409_e_senha_fraca_400(self):
        base = {"username": "novo", "email": "usuario_a@teste.local", "password": SENHA_NOVA}
        self.erro(self.anonimo.post(f"{V1}/auth/register/", base, format="json"), 409)
        base.update(email="novo@teste.local", password="12345678")
        self.erro(self.anonimo.post(f"{V1}/auth/register/", base, format="json"), 400)
        self.assertFalse(User.objects.filter(username="novo").exists())

    @override_settings(REST_FRAMEWORK={**__import__("django.conf", fromlist=["settings"]).settings.REST_FRAMEWORK,
                                       "DEFAULT_THROTTLE_RATES": {"anon": "1000/hour", "user": "1000/hour", "cadastro": "2/hour", "senha": "2/hour"}})
    def test_throttle_do_cadastro(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle

        ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        codigos = [self.anonimo.post(f"{V1}/auth/register/", {"username": f"u{i}", "email": f"u{i}@t.local", "password": "x"}, format="json").status_code for i in range(4)]
        self.assertEqual(codigos[-1], 429)


class InsumoAPITests(BaseAPITests):
    def payload(self, **extra):
        dados = {"nome": "Queijo", "medida": medida("g").pk, "quantidade": "100", "preco_pago": 400}
        dados.update(extra)
        return dados

    def criar(self, api=None, **extra):
        return (api or self.api_a).post(f"{V1}/insumos/", self.payload(**extra), format="json")

    def test_criar_calcula_preco_por_kg_e_nunca_usa_float(self):
        corpo = self.ok(self.criar(), 201)["data"]
        self.assertEqual(corpo["preco_pago"], 400)
        self.assertEqual(corpo["preco_pago_decimal"], "4.00")
        self.assertEqual(corpo["preco_pago_formatado"], "R$ 4,00")
        self.assertEqual(corpo["preco_unidade"], 4000)
        self.assertEqual(corpo["preco_unidade_decimal"], "40.00")
        self.assertEqual(corpo["rotulo_preco"], "PREÇO (por KG)")
        self.assertEqual(corpo["quantidade_por_extenso"], "100 gramas")
        self.assertIsInstance(corpo["quantidade"], str)  # Decimal como texto
        for campo in ("preco_pago", "preco_unidade"):
            self.assertIsInstance(corpo[campo], int)
            self.assertNotIsInstance(corpo[campo], float)

    def test_valor_float_e_rejeitado(self):
        for valor in (4.5, "4,50", "abc", -1, None, 10**16):
            self.erro(self.criar(preco_pago=valor), 400)

    def test_validacoes(self):
        self.erro(self.criar(quantidade="0"), 400)
        self.erro(self.criar(quantidade="-1"), 400)
        self.erro(self.criar(quantidade="NaN"), 400)
        self.erro(self.criar(medida=99999), 400)
        self.erro(self.criar(nome="  "), 400)
        Medida.objects.filter(sigla="g").update(ativo=False)
        self.erro(self.criar(), 400)

    def test_nome_duplicado_409_mas_outro_cliente_pode_repetir(self):
        self.ok(self.criar(), 201)
        self.erro(self.criar(nome="queijo"), 409)
        self.ok(self.criar(api=self.api_b), 201)

    def test_mass_assignment_e_ignorado(self):
        corpo = self.ok(self.criar(cliente=self.cliente_b.pk, preco_unidade=1, id=9999, ativo=True), 201)["data"]
        insumo = Insumo.objects.get(pk=corpo["id"])
        self.assertEqual(insumo.cliente, self.cliente_a)
        self.assertEqual(insumo.preco_unidade, 4000)
        self.assertNotEqual(insumo.pk, 9999)

    def test_isolamento_entre_clientes(self):
        insumo_a = novo_insumo(self.cliente_a, "Do A")
        self.erro(self.api_b.get(f"{V1}/insumos/{insumo_a.pk}/"), 404)
        self.erro(self.api_b.patch(f"{V1}/insumos/{insumo_a.pk}/", {"nome": "hack"}, format="json"), 404)
        self.erro(self.api_b.delete(f"{V1}/insumos/{insumo_a.pk}/"), 404)
        self.assertEqual(self.ok(self.api_b.get(f"{V1}/insumos/"))["data"]["paginacao"]["total"], 0)
        insumo_a.refresh_from_db()
        self.assertEqual((insumo_a.nome, insumo_a.ativo), ("Do A", True))

    def test_sem_permissao_403_e_modulo_desabilitado_403(self):
        apenas_ver = User.objects.create_user(username="leitor", email="leitor@teste.local", password=SENHA)
        apenas_ver.profile.cliente = self.cliente_a
        apenas_ver.profile.save()
        apenas_ver.user_permissions.add(Permission.objects.get(codename="view_insumo"))
        api = cliente_api(apenas_ver)
        self.ok(api.get(f"{V1}/insumos/"))
        self.erro(api.post(f"{V1}/insumos/", self.payload(), format="json"), 403)
        ClienteModulo.objects.filter(cliente=self.cliente_a, modulo__slug="insumo").update(ativo=False)
        self.erro(self.api_a.get(f"{V1}/insumos/"), 403)

    def test_patch_recalcula_e_put_exige_campos(self):
        insumo_id = self.ok(self.criar(), 201)["data"]["id"]
        corpo = self.ok(self.api_a.patch(f"{V1}/insumos/{insumo_id}/", {"preco_pago": 800}, format="json"))["data"]
        self.assertEqual(corpo["preco_unidade"], 8000)
        self.erro(self.api_a.put(f"{V1}/insumos/{insumo_id}/", {"nome": "so nome"}, format="json"), 400)
        corpo = self.ok(self.api_a.put(f"{V1}/insumos/{insumo_id}/", self.payload(quantidade="200", medida=medida("kg").pk), format="json"))["data"]
        self.assertEqual(corpo["preco_unidade"], 2)  # R$ 8,00 / 200 kg = R$ 0,04/kg

    def test_delete_e_exclusao_logica(self):
        insumo_id = self.ok(self.criar(), 201)["data"]["id"]
        self.assertEqual(self.api_a.delete(f"{V1}/insumos/{insumo_id}/").status_code, 204)
        self.assertFalse(Insumo.objects.get(pk=insumo_id).ativo)
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/insumos/"))["data"]["paginacao"]["total"], 0)
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/insumos/", {"ativo": "todos"}))["data"]["paginacao"]["total"], 1)

    def test_crud_e_auditado_com_antes_e_depois(self):
        insumo_id = self.ok(self.criar(), 201)["data"]["id"]
        self.api_a.patch(f"{V1}/insumos/{insumo_id}/", {"preco_pago": 800}, format="json")
        self.api_a.delete(f"{V1}/insumos/{insumo_id}/")
        registros = {r.acao: r for r in RegistroAuditoria.objects.filter(modulo="insumo")}
        self.assertEqual(set(registros), {"criar", "editar", "excluir"})
        self.assertEqual(registros["criar"].descricao, f"Criado insumo ID {insumo_id} - Queijo.")
        alteracao = registros["editar"].alteracoes[0]
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("R$ 4,00", "R$ 8,00"))
        self.assertIn("foi inativado", registros["excluir"].descricao)
        self.assertEqual(registros["criar"].usuario, self.usuario_a)

    def test_listagem_pesquisa_ordenacao_paginacao(self):
        for i, nome in enumerate(["Arroz", "Banana", "Cebola"]):
            novo_insumo(self.cliente_a, nome, "kg", "1", (i + 1) * 100)
        data = lambda **p: self.ok(self.api_a.get(f"{V1}/insumos/", p))["data"]
        self.assertEqual([i["nome"] for i in data()["itens"]], ["Arroz", "Banana", "Cebola"])
        self.assertEqual([i["nome"] for i in data(ordenar="-preco_pago")["itens"]], ["Cebola", "Banana", "Arroz"])
        self.assertEqual([i["nome"] for i in data(q="ban")["itens"]], ["Banana"])
        pagina = data(por_pagina=2, pagina=2)
        self.assertEqual([i["nome"] for i in pagina["itens"]], ["Cebola"])
        self.assertEqual(pagina["paginacao"], {"pagina": 2, "por_pagina": 2, "total": 3, "total_paginas": 2})
        self.assertEqual(data(por_pagina=100000)["paginacao"]["por_pagina"], 100)  # teto
        self.erro(self.api_a.get(f"{V1}/insumos/", {"pagina": 99}), 404)

    def test_entradas_maliciosas_nao_quebram(self):
        for q in ["' OR 1=1 --", "%", "'; DROP TABLE insumo_insumo; --"]:
            self.ok(self.api_a.get(f"{V1}/insumos/", {"q": q}))
        self.ok(self.api_a.get(f"{V1}/insumos/", {"ordenar": "cliente__cpf_cnpj"}))  # fora da whitelist: ignorado
        self.assertTrue(Insumo._meta.db_table in __import__("django.db", fromlist=["connection"]).connection.introspection.table_names())

    def test_nao_ha_categoria_no_insumo(self):
        corpo = self.ok(self.criar(), 201)["data"]
        self.assertNotIn("categoria", corpo)


class MedidaAPITests(BaseAPITests):
    def payload(self, **extra):
        dados = {"medida": "Pitada", "sigla": "pit", "dimensao": "massa", "fator_base": "0.5", "nome_singular": "pitada", "nome_plural": "pitadas"}
        dados.update(extra)
        return dados

    def test_qualquer_usuario_autenticado_le(self):
        corpo = self.ok(self.api_a.get(f"{V1}/medidas/", {"por_pagina": 100}))["data"]
        self.assertEqual(corpo["paginacao"]["total"], 17)
        self.assertEqual(corpo["itens"][0]["medida"], "Gramas")

    def test_so_administrador_escreve(self):
        self.erro(self.api_a.post(f"{V1}/medidas/", self.payload(), format="json"), 403)
        self.erro(self.api_a.delete(f"{V1}/medidas/1/"), 403)
        self.erro(self.api_a.post(f"{V1}/medidas/1/inativar/"), 403)
        corpo = self.ok(self.api_admin.post(f"{V1}/medidas/", self.payload(), format="json"), 201)["data"]
        self.assertEqual(Medida.objects.get(pk=corpo["id"]).usuario, self.admin)

    def test_validacoes_e_duplicidade(self):
        self.erro(self.api_admin.post(f"{V1}/medidas/", self.payload(medida="litro"), format="json"), 409)
        self.erro(self.api_admin.post(f"{V1}/medidas/", self.payload(sigla="KG"), format="json"), 409)
        for fator in ("0", "-1", "abc"):
            self.erro(self.api_admin.post(f"{V1}/medidas/", self.payload(fator_base=fator), format="json"), 400)
        self.erro(self.api_admin.post(f"{V1}/medidas/", self.payload(dimensao="inexistente"), format="json"), 400)

    def test_medida_em_uso_nao_muda_fator_422(self):
        novo_insumo(self.cliente_a, "Queijo", "g", "100", 400)
        g = medida("g")
        self.erro(self.api_admin.patch(f"{V1}/medidas/{g.pk}/", {"fator_base": "2"}, format="json"), 422)
        self.ok(self.api_admin.patch(f"{V1}/medidas/{g.pk}/", {"descricao": "ok"}, format="json"))
        g.refresh_from_db()
        self.assertEqual(g.fator_base, Decimal("1"))

    def test_delete_em_uso_inativa_e_sem_uso_apaga(self):
        novo_insumo(self.cliente_a, "Queijo", "g", "100", 400)
        em_uso = self.ok(self.api_admin.delete(f"{V1}/medidas/{medida('g').pk}/"))
        self.assertFalse(em_uso["data"]["ativo"])
        self.assertIn("inativada", em_uso["message"])
        nova = self.ok(self.api_admin.post(f"{V1}/medidas/", self.payload(), format="json"), 201)["data"]
        self.assertEqual(self.api_admin.delete(f"{V1}/medidas/{nova['id']}/").status_code, 204)
        self.assertFalse(Medida.objects.filter(pk=nova["id"]).exists())

    def test_ativar_inativar_e_filtros(self):
        pk = medida("g").pk
        self.assertFalse(self.ok(self.api_admin.post(f"{V1}/medidas/{pk}/inativar/"))["data"]["ativo"])
        self.assertTrue(self.ok(self.api_admin.post(f"{V1}/medidas/{pk}/ativar/"))["data"]["ativo"])
        self.api_admin.post(f"{V1}/medidas/{pk}/inativar/")
        inativas = self.ok(self.api_admin.get(f"{V1}/medidas/", {"ativo": "false"}))["data"]["itens"]
        self.assertEqual([m["sigla"] for m in inativas], ["g"])
        massa = self.ok(self.api_admin.get(f"{V1}/medidas/", {"dimensao": "massa"}))["data"]["itens"]
        self.assertEqual({m["sigla"] for m in massa}, {"g", "kg"})

    def test_medida_e_auditada(self):
        self.api_admin.post(f"{V1}/medidas/", self.payload(), format="json")
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="medida", acao="criar", usuario=self.admin).exists())


class CategoriaAPITests(BaseAPITests):
    def test_crud_isolamento_e_duplicidade(self):
        corpo = self.ok(self.api_a.post(f"{V1}/categorias/", {"nome": "Laticínios"}, format="json"), 201)["data"]
        self.erro(self.api_a.post(f"{V1}/categorias/", {"nome": "laticínios"}, format="json"), 409)
        self.ok(self.api_b.post(f"{V1}/categorias/", {"nome": "Laticínios"}, format="json"), 201)
        self.erro(self.api_b.patch(f"{V1}/categorias/{corpo['id']}/", {"nome": "x"}, format="json"), 404)
        self.ok(self.api_a.patch(f"{V1}/categorias/{corpo['id']}/", {"nome": "Frios"}, format="json"))
        self.assertEqual(Categoria.objects.get(pk=corpo["id"]).nome, "Frios")
        self.assertEqual(self.ok(self.api_a.get(f"{V1}/categorias/"))["data"]["paginacao"]["total"], 1)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="categoria", acao="editar").exists())


class UsuarioAPITests(BaseAPITests):
    def setUp(self):
        super().setUp()
        self.operador = Group.objects.get(name="Operador")
        self.cliente_a.perfis.set([self.operador])
        self.cliente_b.perfis.set([Group.objects.get(name="Gestor")])

    def payload(self, **extra):
        dados = {
            "username": "maria", "first_name": "Maria", "email": "maria@teste.local", "cliente": self.cliente_a.pk,
            "perfil": self.operador.pk, "senha": SENHA_NOVA, "confirmar_senha": SENHA_NOVA,
        }
        dados.update(extra)
        return dados

    def test_criar_nunca_devolve_senha(self):
        resposta = self.api_admin.post(f"{V1}/usuarios/", self.payload(), format="json")
        corpo = self.ok(resposta, 201)["data"]
        texto = resposta.content.decode()
        for proibido in (SENHA_NOVA, "pbkdf2", "senha", "password"):
            self.assertNotIn(proibido, texto)
        self.assertEqual(corpo["cliente"]["id"], self.cliente_a.pk)
        self.assertEqual(corpo["perfil"]["nome"], "Operador")
        usuario = User.objects.get(username="maria")
        self.assertTrue(usuario.check_password(SENHA_NOVA))

    def test_validacoes_de_negocio(self):
        self.erro(self.api_admin.post(f"{V1}/usuarios/", self.payload(perfil=Group.objects.get(name="Gestor").pk), format="json"), 400)  # perfil não liberado
        self.erro(self.api_admin.post(f"{V1}/usuarios/", self.payload(senha="123", confirmar_senha="123"), format="json"), 400)
        self.erro(self.api_admin.post(f"{V1}/usuarios/", self.payload(email="invalido"), format="json"), 400)
        self.ok(self.api_admin.post(f"{V1}/usuarios/", self.payload(), format="json"), 201)
        self.erro(self.api_admin.post(f"{V1}/usuarios/", self.payload(username="MARIA", email="outro@teste.local"), format="json"), 409)
        self.erro(self.api_admin.post(f"{V1}/usuarios/", self.payload(username="m2", email="MARIA@teste.local"), format="json"), 409)

    def test_privilegios_nao_sao_aceitos(self):
        self.ok(self.api_admin.post(f"{V1}/usuarios/", self.payload(is_superuser=True, is_staff=True), format="json"), 201)
        usuario = User.objects.get(username="maria")
        self.assertFalse(usuario.is_superuser or usuario.is_staff)

    def test_usuario_comum_nao_acessa(self):
        for metodo, url in [("get", "usuarios/"), ("post", "usuarios/"), ("get", f"usuarios/{self.admin.pk}/")]:
            self.erro(getattr(self.api_a, metodo)(f"{V1}/{url}"), 403)

    def test_patch_parcial_e_auditoria_sem_hash(self):
        criado = self.ok(self.api_admin.post(f"{V1}/usuarios/", self.payload(), format="json"), 201)["data"]
        corpo = self.ok(self.api_admin.patch(f"{V1}/usuarios/{criado['id']}/", {"first_name": "Mariana"}, format="json"))["data"]
        self.assertEqual(corpo["first_name"], "Mariana")
        self.assertEqual(corpo["email"], "maria@teste.local")  # campos não enviados ficam como estavam
        registro = RegistroAuditoria.objects.get(acao="editar", modulo="usuario")
        self.assertEqual([a["campo"] for a in registro.alteracoes], ["first_name"])
        self.assertNotIn("pbkdf2", str(registro.alteracoes) + registro.descricao)

    def test_inativar_ativar_e_nao_inativa_a_si_mesmo(self):
        criado = self.ok(self.api_admin.post(f"{V1}/usuarios/", self.payload(), format="json"), 201)["data"]
        self.assertFalse(self.ok(self.api_admin.post(f"{V1}/usuarios/{criado['id']}/inativar/"))["data"]["is_active"])
        self.assertTrue(self.ok(self.api_admin.post(f"{V1}/usuarios/{criado['id']}/ativar/"))["data"]["is_active"])
        self.erro(self.api_admin.post(f"{V1}/usuarios/{self.admin.pk}/inativar/"), 400)
        self.assertEqual(self.api_admin.delete(f"{V1}/usuarios/{criado['id']}/").status_code, 405)

    def test_escopo_por_cliente_para_quem_nao_e_administrador_global(self):
        grupo = Group.objects.create(name="Gerente")
        grupo.permissions.set(Permission.objects.filter(content_type__app_label="auth", content_type__model="user"))
        self.cliente_a.perfis.add(grupo)
        gerente = User.objects.create_user(username="gerente", email="gerente@teste.local", password=SENHA)
        gerente.groups.add(grupo)
        gerente.profile.cliente = self.cliente_a
        gerente.profile.save()
        api = cliente_api(gerente)
        self.ok(self.api_admin.post(f"{V1}/usuarios/", self.payload(), format="json"), 201)
        nomes = {u["username"] for u in self.ok(api.get(f"{V1}/usuarios/"))["data"]["itens"]}
        self.assertTrue(nomes <= {"gerente", "maria", "usuario_a"})
        self.assertNotIn("usuario_b", nomes)
        self.assertNotIn("admin", nomes)
        self.erro(api.get(f"{V1}/usuarios/{self.usuario_b.pk}/"), 404)
        self.erro(api.post(f"{V1}/usuarios/", self.payload(cliente=self.cliente_b.pk, username="x", email="x@t.local"), format="json"), 400)


class ClientePerfilModuloAPITests(BaseAPITests):
    def cliente_payload(self, **extra):
        dados = {
            "tipo_pessoa": "PJ", "cpf_cnpj": "11.222.333/0001-81", "nome_razao_social": "Nova Empresa",
            "email_gestor": "gestor.nova@teste.local",
        }
        dados.update(extra)
        return dados

    def test_so_administrador_gerencia_clientes(self):
        self.erro(self.api_a.get(f"{V1}/clientes/"), 403)
        self.erro(self.api_a.post(f"{V1}/clientes/", self.cliente_payload(cpf_cnpj="52998224725", tipo_pessoa="PF"), format="json"), 403)

    def test_cpf_cnpj_validado_normalizado_e_unico(self):
        self.erro(self.api_admin.post(f"{V1}/clientes/", self.cliente_payload(cpf_cnpj="11.222.333/0001-82"), format="json"), 400)
        self.erro(self.api_admin.post(f"{V1}/clientes/", self.cliente_payload(tipo_pessoa="PF"), format="json"), 400)
        self.erro(self.api_admin.post(f"{V1}/clientes/", self.cliente_payload(tipo_pessoa="XX"), format="json"), 400)
        self.erro(self.api_admin.post(f"{V1}/clientes/", self.cliente_payload(cpf_cnpj="11.222.333/0001-81"), format="json"), 409)  # já existe (cliente A)
        corpo = self.ok(self.api_admin.post(f"{V1}/clientes/", self.cliente_payload(cpf_cnpj="52998224725", tipo_pessoa="PF"), format="json"), 201)["data"]
        self.assertEqual(corpo["cpf_cnpj"], "52998224725")
        self.assertEqual(corpo["cpf_cnpj_formatado"], "529.982.247-25")

    def test_perfis_do_cliente_e_auditoria_do_m2m(self):
        gestor = Group.objects.get(name="Gestor")
        self.ok(self.api_admin.patch(f"{V1}/clientes/{self.cliente_a.pk}/", {"perfis": [gestor.pk]}, format="json"))
        self.assertEqual(list(self.cliente_a.perfis.all()), [gestor])
        alteracao = RegistroAuditoria.objects.get(modulo="cliente", acao="editar").alteracoes[0]
        self.assertEqual(alteracao["rotulo"], "Perfis disponíveis")

    def test_modulos_do_cliente(self):
        corpo = self.ok(self.api_admin.get(f"{V1}/clientes/{self.cliente_a.pk}/modulos/"))["data"]["modulos"]
        self.assertTrue(any(m["slug"] == "insumo" and m["habilitado"] for m in corpo))
        categoria = Modulo.objects.get(slug="categoria")
        self.ok(self.api_admin.put(f"{V1}/clientes/{self.cliente_a.pk}/modulos/", {"modulos": [categoria.pk]}, format="json"))
        self.erro(self.api_a.get(f"{V1}/insumos/"), 403)  # insumo foi desabilitado
        self.ok(self.api_a.get(f"{V1}/categorias/"))
        self.erro(self.api_admin.put(f"{V1}/clientes/{self.cliente_a.pk}/modulos/", {"modulos": [99999]}, format="json"), 400)
        self.assertTrue(RegistroAuditoria.objects.filter(modulo="modulo", acao="editar").exists())

    def test_ativar_inativar_cliente(self):
        self.assertFalse(self.ok(self.api_admin.post(f"{V1}/clientes/{self.cliente_a.pk}/inativar/"))["data"]["ativo"])

    def test_perfis_crud_e_permissoes_validas(self):
        catalogo = self.ok(self.api_admin.get(f"{V1}/permissoes/"))["data"]
        ids = [a["permissao_id"] for linha in catalogo for a in linha["acoes"] if linha["app_label"] == "insumo"]
        corpo = self.ok(self.api_admin.post(f"{V1}/perfis/", {"name": "Estoque", "permissoes": ids}, format="json"), 201)["data"]
        self.assertEqual(sorted(corpo["permissoes"]), sorted(ids))
        self.assertIn("insumo.view_insumo", corpo["permissoes_codigos"])
        self.erro(self.api_admin.post(f"{V1}/perfis/", {"name": "estoque"}, format="json"), 409)
        # permissão de gerenciar papéis NÃO pode ser concedida por aqui
        proibida = Permission.objects.get(content_type__app_label="auth", codename="delete_group").pk
        self.erro(self.api_admin.post(f"{V1}/perfis/", {"name": "Hack", "permissoes": [proibida]}, format="json"), 400)
        self.ok(self.api_admin.patch(f"{V1}/perfis/{corpo['id']}/", {"name": "Estoque 2"}, format="json"))
        self.assertEqual(sorted(Group.objects.get(pk=corpo["id"]).permissions.values_list("pk", flat=True)), sorted(ids))  # PATCH sem permissoes mantém
        self.assertEqual(self.api_admin.delete(f"{V1}/perfis/{corpo['id']}/").status_code, 204)
        acoes = list(RegistroAuditoria.objects.filter(modulo="acl").order_by("id").values_list("acao", flat=True))
        self.assertEqual(acoes, ["criar", "editar", "excluir"])

    def test_perfis_e_permissoes_so_para_administrador(self):
        self.erro(self.api_a.get(f"{V1}/perfis/"), 403)
        self.erro(self.api_a.get(f"{V1}/permissoes/"), 403)
        self.erro(self.api_a.get(f"{V1}/modulos/"), 403)
        self.ok(self.api_admin.get(f"{V1}/modulos/"))


class AuditoriaAPITests(BaseAPITests):
    def setUp(self):
        super().setUp()
        self.api_a.post(f"{V1}/insumos/", {"nome": "A1", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": 100}, format="json")
        self.api_b.post(f"{V1}/insumos/", {"nome": "B1", "medida": medida("kg").pk, "quantidade": "1", "preco_pago": 100}, format="json")

    def test_administrador_ve_tudo_com_filtros(self):
        corpo = self.ok(self.api_admin.get(f"{V1}/auditoria/", {"modulo": "insumo", "acao": "criar"}))["data"]
        self.assertEqual({r["registro_nome"] for r in corpo["itens"]}, {"A1", "B1"})
        corpo = self.ok(self.api_admin.get(f"{V1}/auditoria/", {"usuario": self.usuario_a.pk, "modulo": "insumo"}))["data"]
        self.assertEqual([r["registro_nome"] for r in corpo["itens"]], ["A1"])

    def test_usuario_comum_nao_acessa_e_com_permissao_ve_so_o_proprio_cliente(self):
        self.erro(self.api_a.get(f"{V1}/auditoria/"), 403)
        self.usuario_a.user_permissions.add(Permission.objects.get(codename="view_registroauditoria"))
        api = cliente_api(self.usuario_a)
        itens = self.ok(api.get(f"{V1}/auditoria/"))["data"]["itens"]
        self.assertTrue(itens)
        self.assertNotIn("B1", {r["registro_nome"] for r in itens})
        registro_b = RegistroAuditoria.objects.get(registro_nome="B1")
        self.erro(api.get(f"{V1}/auditoria/{registro_b.pk}/"), 404)

    def test_somente_leitura(self):
        registro = RegistroAuditoria.objects.first()
        self.erro(self.api_admin.post(f"{V1}/auditoria/", {}, format="json"), 405)
        self.erro(self.api_admin.patch(f"{V1}/auditoria/{registro.pk}/", {"descricao": "x"}, format="json"), 405)
        self.erro(self.api_admin.delete(f"{V1}/auditoria/{registro.pk}/"), 405)


class DocumentacaoTests(BaseAPITests):
    def test_swagger_exige_login_e_administrador(self):
        web = self.client
        for caminho in ("/api/docs/", "/api/schema/", "/api/redoc/"):
            self.assertEqual(web.get(caminho).status_code, 302)  # anônimo -> login
        web.login(username="usuario_a", password=SENHA)
        for caminho in ("/api/docs/", "/api/schema/"):
            self.assertEqual(web.get(caminho).status_code, 403)
        web.logout()
        web.login(username="admin", password=SENHA)
        self.assertEqual(web.get("/api/docs/").status_code, 200)

    def test_schema_descreve_envelope_endpoints_e_seguranca(self):
        self.client.login(username="admin", password=SENHA)
        resposta = self.client.get("/api/schema/", {"format": "json"})
        self.assertEqual(resposta.status_code, 200)
        schema = resposta.json()
        for caminho in ("/api/v1/auth/login/", "/api/v1/insumos/", "/api/v1/insumos/{id}/", "/api/v1/medidas/", "/api/v1/auditoria/", "/api/v1/usuarios/"):
            self.assertIn(caminho, schema["paths"])
        resposta_get = schema["paths"]["/api/v1/insumos/{id}/"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(set(resposta_get["properties"]), {"success", "message", "data"})
        self.assertIn("404", schema["paths"]["/api/v1/insumos/{id}/"]["get"]["responses"])
        self.assertIn("BearerAuth", schema["components"]["securitySchemes"])
        self.assertEqual(schema["paths"]["/api/v1/auth/login/"]["post"]["security"], [])

    def test_todo_endpoint_de_recurso_exige_autenticacao(self):
        """Varre as rotas da API: nenhuma (exceto as públicas de auth) responde sem token."""
        from django.urls import get_resolver

        publicas = {"auth/login/", "auth/register/", "auth/password/forgot/", "auth/password/reset/"}
        rotas = []

        def coletar(padroes, prefixo=""):
            for padrao in padroes:
                texto = prefixo + str(padrao.pattern)
                if hasattr(padrao, "url_patterns"):
                    coletar(padrao.url_patterns, texto)
                else:
                    rotas.append(texto)

        coletar(get_resolver().url_patterns)
        api = [r for r in rotas if r.startswith("api/v1/")]
        self.assertGreater(len(api), 30)
        for rota in api:
            caminho = "/" + rota.replace("(?P<pk>[^/.]+)", "1").replace("<int:pk>", "1").replace("<str:tipo>", "insumo").replace("^", "").replace("$", "").replace("\\.(?P<format>[a-z0-9]+)/?", "").rstrip("/") + "/"
            if "(" in caminho or any(caminho.endswith("v1/" + p) for p in publicas):
                continue
            resposta = self.anonimo.get(caminho)
            self.assertIn(resposta.status_code, (401, 405), f"{caminho} -> {resposta.status_code}")


class DesempenhoAPITests(BaseAPITests):
    """Listas não podem fazer uma consulta por linha (N+1): o número de
    consultas é o mesmo com 1 ou com 12 registros."""

    def consultas(self, api, caminho):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as contexto:
            resposta = api.get(f"{V1}/{caminho}/", {"por_pagina": 100})
        self.assertEqual(resposta.status_code, 200, resposta.content[:300])
        return len(contexto)

    def assert_constante(self, api, caminho, criar_mais):
        antes = self.consultas(api, caminho)
        criar_mais()
        depois = self.consultas(api, caminho)
        self.assertEqual(antes, depois, f"{caminho}: {antes} consultas -> {depois} (N+1)")

    def test_insumos(self):
        novo_insumo(self.cliente_a, "Semente")  # com 0 registros a consulta da lista nem roda
        self.assert_constante(self.api_a, "insumos", lambda: [novo_insumo(self.cliente_a, f"I{i}") for i in range(12)])

    def test_medidas(self):
        novo_insumo(self.cliente_a, "Em uso", "g", "1", 100)
        self.assert_constante(self.api_a, "medidas", lambda: [
            Medida.objects.create(medida=f"M{i}", sigla=f"m{i}", dimensao="massa", fator_base=Decimal("1")) for i in range(12)
        ])

    def test_usuarios(self):
        operador = Group.objects.get(name="Operador")

        def criar():
            for i in range(12):
                u = User.objects.create_user(username=f"u{i}", email=f"u{i}@t.local", password="x")
                u.profile.cliente = self.cliente_a
                u.profile.save()
                u.groups.add(operador)

        self.assert_constante(self.api_admin, "usuarios", criar)

    def test_clientes(self):
        def criar():
            for i, doc in enumerate(["52998224725", "11144477735", "39053344705"]):
                c = Cliente.objects.create(tipo_pessoa="PF", cpf_cnpj=doc, nome_razao_social=f"PF{i}")
                c.perfis.add(Group.objects.get(name="Gestor"))

        self.assert_constante(self.api_admin, "clientes", criar)

    def test_perfis(self):
        def criar():
            for i in range(8):
                g = Group.objects.create(name=f"G{i}")
                g.permissions.add(*Permission.objects.filter(content_type__app_label="insumo"))

        self.assert_constante(self.api_admin, "perfis", criar)

    def test_auditoria(self):
        self.api_a.post(f"{V1}/categorias/", {"nome": "Semente"}, format="json")

        def criar():
            for i in range(10):
                self.api_a.post(f"{V1}/categorias/", {"nome": f"Cat{i}"}, format="json")

        self.assert_constante(self.api_admin, "auditoria", criar)
