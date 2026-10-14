"""Consumidores (clientes finais): validação, máscara, tenant, permissões, auditoria sem dados pessoais,
anonimização (LGPD), consultas externas (com a rede simulada) e API."""
from unittest import mock

from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.auditoria.models import RegistroAuditoria
from apps.cliente.models import Cliente
from web_project.testing import habilitar_modulo

from . import services
from .models import Consumidor, mascarar_documento

CPF_VALIDO = "52998224725"
CPF_VALIDO_2 = "11144477735"
CNPJ_VALIDO = "11222333000181"
V1 = "/api/v1"


def novo_usuario(sufixo, cnpj, permissoes=None):
    """Cliente (tenant) com o módulo habilitado e um usuário com as permissões de consumidor indicadas
    (padrão: todas)."""
    cliente = Cliente.objects.create(tipo_pessoa=Cliente.TIPO_PJ, cpf_cnpj=cnpj, nome_razao_social=f"Empresa {sufixo}")
    habilitar_modulo(cliente, "consumidor")
    grupo = Group.objects.create(name=f"Grupo Consumidor {sufixo}")
    consulta = Permission.objects.filter(content_type__app_label="consumidor")
    if permissoes is not None:
        consulta = consulta.filter(codename__in=permissoes)
    grupo.permissions.set(consulta)
    usuario = User.objects.create_user(username=f"u_{sufixo}", email=f"{sufixo}@teste.local", password="SenhaForte123!")
    usuario.profile.cliente = cliente
    usuario.profile.save()
    usuario.groups.add(grupo)
    return cliente, usuario


def dados(**extra):
    base = {"nome": "Maria Souza", "tipo_pessoa": "PF", "documento": "", "ativo": "on"}
    base.update(extra)
    return base


class ModeloTests(TestCase):
    def setUp(self):
        self.cliente, _ = novo_usuario("a", CNPJ_VALIDO)
        self.outro, _ = novo_usuario("b", "11444777000161")

    def criar(self, cliente=None, **campos):
        c = Consumidor(cliente=cliente or self.cliente, nome=campos.pop("nome", "Maria"), **campos)
        c.full_clean()
        c.save()
        return c

    def test_normaliza_e_guarda_so_digitos(self):
        c = self.criar(documento="529.982.247-25", telefone="(11) 99999-1234", cep="01310-100", uf="sp", instagram="@Maria.Souza", email="A@B.COM")
        c.refresh_from_db()
        self.assertEqual((c.documento, c.telefone, c.cep, c.uf, c.instagram, c.email), (CPF_VALIDO, "11999991234", "01310100", "SP", "maria.souza", "a@b.com"))

    def test_instagram_aceita_url(self):
        self.assertEqual(self.criar(instagram="https://www.instagram.com/loja_boa/").instagram, "loja_boa")

    def test_documento_invalido_e_tipo_errado(self):
        for tipo, doc in (("PF", "12345678900"), ("PF", CNPJ_VALIDO), ("PJ", CPF_VALIDO), ("PJ", "11111111111111")):
            with self.assertRaises(ValidationError, msg=(tipo, doc)) as ctx:
                self.criar(tipo_pessoa=tipo, documento=doc)
            self.assertIn("documento", ctx.exception.message_dict)

    def test_documento_unico_por_unidade_mas_igual_em_outra(self):
        self.criar(documento=CPF_VALIDO)
        with self.assertRaises(ValidationError):
            self.criar(documento=CPF_VALIDO, nome="Outra")
        self.criar(cliente=self.outro, documento=CPF_VALIDO)  # outra unidade pode
        self.criar(nome="Sem doc 1")
        self.criar(nome="Sem doc 2")  # vários sem documento

    def test_telefone_uf_cep_e_nascimento_invalidos(self):
        for campo, valor in (("telefone", "123"), ("whatsapp", "1" * 14), ("uf", "XX"), ("cep", "123"), ("instagram", "nome inválido!"), ("data_nascimento", __import__("datetime").date(2999, 1, 1))):
            with self.assertRaises(ValidationError, msg=campo) as ctx:
                self.criar(**{campo: valor})
            self.assertIn(campo, ctx.exception.message_dict)

    def test_consentimento_registra_data_e_retirar_limpa(self):
        c = self.criar(consentimento_lgpd=True)
        self.assertIsNotNone(c.consentimento_em)
        c.consentimento_lgpd = False
        c.full_clean()
        self.assertIsNone(c.consentimento_em)

    def test_mascara(self):
        self.assertEqual(mascarar_documento(CPF_VALIDO), "***.982.247-**")
        self.assertEqual(mascarar_documento(CNPJ_VALIDO), "**.222.333/****-**")
        self.assertEqual(mascarar_documento(""), "—")


class TelasTests(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente_a, self.ua = novo_usuario("a", CNPJ_VALIDO)
        self.cliente_b, self.ub = novo_usuario("b", "11444777000161")
        self.client.force_login(self.ua)

    def criar_via_tela(self, **extra):
        return self.client.post(reverse("consumidor-create"), dados(**extra))

    def test_criar_define_unidade_do_usuario_e_audita_sem_dados_pessoais(self):
        resposta = self.criar_via_tela(documento=CPF_VALIDO, email="maria@x.com", telefone="11999991234", cidade="Santos", uf="SP",
                                       cliente=self.cliente_b.pk)  # tentativa de forjar a unidade
        self.assertEqual(resposta.status_code, 302)
        c = Consumidor.objects.get()
        self.assertEqual(c.cliente_id, self.cliente_a.pk)
        registro = RegistroAuditoria.objects.get(modulo="consumidor", acao="criar")
        texto = f"{registro.descricao} {registro.registro_nome} {registro.alteracoes}"
        for segredo in (CPF_VALIDO, "maria@x.com", "11999991234"):
            self.assertNotIn(segredo, texto)

    def test_edicao_audita_que_mudou_sem_o_conteudo(self):
        self.criar_via_tela(documento=CPF_VALIDO, email="velho@x.com")
        c = Consumidor.objects.get()
        self.client.post(reverse("consumidor-edit", args=[c.pk]), dados(documento=CPF_VALIDO, email="novo@x.com"))
        registro = RegistroAuditoria.objects.filter(modulo="consumidor", acao="editar").latest("id")
        alteracao = next(a for a in registro.alteracoes if a["campo"] == "email")
        self.assertEqual((alteracao["antes"], alteracao["depois"]), ("(conteúdo omitido)", "(conteúdo omitido)"))
        self.assertNotIn("novo@x.com", registro.descricao)

    def test_documento_repetido_na_unidade_e_rejeitado(self):
        self.criar_via_tela(documento=CPF_VALIDO)
        resposta = self.criar_via_tela(documento=CPF_VALIDO, nome="Outra")
        self.assertEqual(resposta.status_code, 200)  # volta ao formulário com erro
        self.assertEqual(Consumidor.objects.count(), 1)

    def test_lista_mascara_documento_e_nao_mostra_contatos(self):
        self.criar_via_tela(documento=CPF_VALIDO, email="privado@x.com", telefone="11999991234", cidade="Santos", uf="SP")
        pagina = self.client.get(reverse("consumidor")).content.decode()
        self.assertIn("***.982.247-**", pagina)
        self.assertNotIn(CPF_VALIDO, pagina)
        self.assertNotIn("privado@x.com", pagina)
        self.assertNotIn("11999991234", pagina)
        self.assertIn("Santos / SP", pagina)

    def test_pesquisa_por_trecho_do_documento(self):
        self.criar_via_tela(documento=CPF_VALIDO)
        self.criar_via_tela(documento=CPF_VALIDO_2, nome="João")
        pagina = self.client.get(reverse("consumidor"), {"q": "9982"}).content.decode()
        self.assertIn("Maria Souza", pagina)
        self.assertNotIn("João", pagina)

    def test_outro_tenant_nao_ve_edita_nem_anonimiza(self):
        self.criar_via_tela(documento=CPF_VALIDO)
        c = Consumidor.objects.get()
        self.client.force_login(self.ub)
        self.assertNotIn("Maria Souza", self.client.get(reverse("consumidor")).content.decode())
        self.assertEqual(self.client.get(reverse("consumidor-edit", args=[c.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("consumidor-edit", args=[c.pk]), dados(nome="Hack")).status_code, 404)
        self.assertEqual(self.client.post(reverse("consumidor-anonimizar", args=[c.pk])).status_code, 404)
        c.refresh_from_db()
        self.assertEqual((c.nome, c.anonimizado_em), ("Maria Souza", None))

    def test_sem_permissao_nao_cria_nem_anonimiza(self):
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_consumidor"])
        self.criar_via_tela()
        c = Consumidor.objects.get()
        self.client.force_login(so_ver)
        self.assertEqual(self.client.get(reverse("consumidor")).status_code, 200)
        self.assertEqual(self.client.post(reverse("consumidor-create"), dados()).status_code, 403)
        self.assertEqual(self.client.post(reverse("consumidor-anonimizar", args=[c.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310100"}).status_code, 403)

    def test_anonimizar_apaga_dados_audita_e_bloqueia_edicao(self):
        self.criar_via_tela(documento=CPF_VALIDO, email="maria@x.com", telefone="11999991234", cep="01310100", cidade="Santos", uf="SP", consentimento_lgpd="on")
        c = Consumidor.objects.get()
        self.assertEqual(self.client.post(reverse("consumidor-anonimizar", args=[c.pk])).status_code, 302)
        c.refresh_from_db()
        self.assertEqual((c.nome, c.documento, c.email, c.telefone, c.cep, c.cidade, c.consentimento_lgpd, c.ativo),
                         (f"Consumidor anonimizado #{c.pk}", "", "", "", "", "", False, False))
        self.assertIsNotNone(c.anonimizado_em)
        registro = RegistroAuditoria.objects.filter(modulo="consumidor", acao="excluir").latest("id")
        self.assertIn("anonimizado", registro.descricao)
        self.assertNotIn("Maria", f"{registro.descricao}{registro.registro_nome}")
        self.assertEqual(self.client.get(reverse("consumidor-edit", args=[c.pk])).status_code, 404)

    def test_anonimizar_duas_vezes_nao_duplica_auditoria(self):
        self.criar_via_tela()
        c = Consumidor.objects.get()
        for _ in range(2):
            self.client.post(reverse("consumidor-anonimizar", args=[c.pk]))
        self.assertEqual(RegistroAuditoria.objects.filter(modulo="consumidor", acao="excluir").count(), 1)


class ConsultasExternasTests(TestCase):
    def setUp(self):
        cache.clear()
        _, self.usuario = novo_usuario("a", CNPJ_VALIDO)
        self.client.force_login(self.usuario)

    def test_cep_devolve_sugestao_com_aviso(self):
        with mock.patch.object(services, "_buscar_json", return_value={"logradouro": "Av. Paulista", "bairro": "Bela Vista", "localidade": "São Paulo", "uf": "SP"}) as fake:
            r = self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310-100"})
        self.assertEqual(r.status_code, 200)
        corpo = r.json()
        self.assertEqual((corpo["logradouro"], corpo["cidade"], corpo["uf"]), ("Av. Paulista", "São Paulo", "SP"))
        self.assertIn("confira", corpo["aviso"])
        fake.assert_called_once_with("https://viacep.com.br/ws/01310100/json/")  # só dígitos validados na URL
        self.assertFalse(Consumidor.objects.exists())  # nada é gravado

    def test_cep_invalido_nao_chama_a_rede(self):
        with mock.patch.object(services, "_buscar_json") as fake:
            for valor in ("123", "abc", "../../etc", "0131010a"):
                self.assertEqual(self.client.get(reverse("consumidor-consulta-cep"), {"cep": valor}).status_code, 400, valor)
        fake.assert_not_called()

    def test_cep_inexistente_404(self):
        with mock.patch.object(services, "_buscar_json", return_value={"erro": True}):
            self.assertEqual(self.client.get(reverse("consumidor-consulta-cep"), {"cep": "99999999"}).status_code, 404)

    def test_provedor_indisponivel_503_sem_vazar_detalhes(self):
        with mock.patch.object(services, "_buscar_json", side_effect=services.ConsultaIndisponivel("O serviço de consulta está indisponível no momento.")):
            r = self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310100"})
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("Traceback", r.content.decode())

    def test_falha_de_rede_real_vira_indisponivel(self):
        import urllib.error

        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(services.ConsultaIndisponivel):
                services._buscar_json("https://viacep.com.br/ws/01310100/json/")

    def test_cnpj_valida_digitos_antes_de_consultar_e_devolve_so_o_necessario(self):
        with mock.patch.object(services, "_buscar_json") as fake:
            self.assertEqual(self.client.get(reverse("consumidor-consulta-cnpj"), {"cnpj": "11222333000182"}).status_code, 400)
            fake.assert_not_called()
        bruto = {"razao_social": "EMPRESA X LTDA", "nome_fantasia": "X", "cep": "01310100", "logradouro": "Av", "numero": "1", "bairro": "B",
                 "municipio": "SANTOS", "uf": "SP", "ddd_telefone_1": "1333334444", "email": "contato@x.com", "qsa": [{"nome_socio": "Fulano"}]}
        with mock.patch.object(services, "_buscar_json", return_value=bruto):
            corpo = self.client.get(reverse("consumidor-consulta-cnpj"), {"cnpj": "11.222.333/0001-81"}).json()
        self.assertEqual((corpo["nome"], corpo["cidade"], corpo["telefone"]), ("EMPRESA X LTDA", "SANTOS", "1333334444"))
        self.assertNotIn("qsa", corpo)  # sócios e e-mail não são repassados
        self.assertNotIn("email", corpo)

    def test_resultado_vai_para_cache(self):
        with mock.patch.object(services, "_buscar_json", return_value={"logradouro": "R", "localidade": "L", "uf": "SP"}) as fake:
            self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310100"})
            self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310100"})
        self.assertEqual(fake.call_count, 1)

    def test_rate_limit_por_usuario(self):
        with mock.patch.object(services, "_buscar_json", return_value={"localidade": "L", "uf": "SP"}):
            codigos = [self.client.get(reverse("consumidor-consulta-cep"), {"cep": "01310100"}).status_code for _ in range(services.LIMITE_CONSULTAS + 2)]
        self.assertEqual(codigos[0], 200)
        self.assertEqual(codigos[-1], 429)

    def test_resposta_gigante_e_recusada(self):
        class Resposta:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"x" * n
        with mock.patch("urllib.request.urlopen", return_value=Resposta()):
            with self.assertRaises(services.ConsultaIndisponivel):
                services._buscar_json("https://viacep.com.br/ws/01310100/json/")


class APITests(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente_a, self.ua = novo_usuario("a", CNPJ_VALIDO)
        self.cliente_b, self.ub = novo_usuario("b", "11444777000161")
        self.api_a, self.api_b = self.api(self.ua), self.api(self.ub)

    def api(self, usuario):
        api = APIClient()
        token, _ = Token.objects.get_or_create(user=usuario)
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
        return api

    def criar(self, api=None, **extra):
        corpo = {"nome": "Maria Souza", "documento": CPF_VALIDO, "email": "m@x.com", "telefone": "11999991234", "cidade": "Santos", "uf": "SP", **extra}
        r = (api or self.api_a).post(f"{V1}/consumidores/", corpo, format="json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        return r.json()["data"]

    def test_lista_mascara_e_omite_contatos_detalhe_traz_tudo(self):
        criado = self.criar()
        item = self.api_a.get(f"{V1}/consumidores/").json()["data"]["itens"][0]
        self.assertEqual(item["documento_mascarado"], "***.982.247-**")
        for campo in ("documento", "email", "telefone", "logradouro"):
            self.assertNotIn(campo, item)
        detalhe = self.api_a.get(f"{V1}/consumidores/{criado['id']}/").json()["data"]
        self.assertEqual((detalhe["documento"], detalhe["email"], detalhe["telefone"]), (CPF_VALIDO, "m@x.com", "11999991234"))

    def test_validacao_e_conflito_de_documento(self):
        r = self.api_a.post(f"{V1}/consumidores/", {"nome": "X", "documento": "12345678900"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("documento", r.json()["errors"])
        self.criar()
        r = self.api_a.post(f"{V1}/consumidores/", {"nome": "Y", "documento": CPF_VALIDO}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_patch_preserva_demais_campos_e_dono_vem_do_usuario(self):
        criado = self.criar(cliente=self.cliente_b.pk)
        self.assertEqual(Consumidor.objects.get(pk=criado["id"]).cliente_id, self.cliente_a.pk)
        r = self.api_a.patch(f"{V1}/consumidores/{criado['id']}/", {"cidade": "Santos II"}, format="json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        dado = r.json()["data"]
        self.assertEqual((dado["cidade"], dado["documento"], dado["email"]), ("Santos II", CPF_VALIDO, "m@x.com"))

    def test_outro_tenant_recebe_404_em_tudo(self):
        criado = self.criar()
        url = f"{V1}/consumidores/{criado['id']}/"
        self.assertEqual(self.api_b.get(url).status_code, 404)
        self.assertEqual(self.api_b.patch(url, {"nome": "Hack"}, format="json").status_code, 404)
        self.assertEqual(self.api_b.delete(url).status_code, 404)
        self.assertEqual(self.api_b.get(f"{V1}/consumidores/").json()["data"]["itens"], [])
        self.assertEqual(self.api_b.get(f"{V1}/consumidores/", {"q": "9982"}).json()["data"]["itens"], [])

    def test_delete_anonimiza_e_nao_edita_depois(self):
        criado = self.criar()
        self.assertEqual(self.api_a.delete(f"{V1}/consumidores/{criado['id']}/").status_code, 204)
        c = Consumidor.objects.get(pk=criado["id"])
        self.assertEqual((c.documento, c.email, c.ativo), ("", "", False))
        self.assertIsNotNone(c.anonimizado_em)
        self.assertEqual(self.api_a.patch(f"{V1}/consumidores/{criado['id']}/", {"nome": "Volta"}, format="json").status_code, 404)

    def test_permissoes_e_autenticacao(self):
        _, so_ver = novo_usuario("c", "11555666000100", permissoes=["view_consumidor"])
        api_ver = self.api(so_ver)
        self.assertEqual(api_ver.get(f"{V1}/consumidores/").status_code, 200)
        self.assertEqual(api_ver.post(f"{V1}/consumidores/", {"nome": "X"}, format="json").status_code, 403)
        self.assertEqual(APIClient().get(f"{V1}/consumidores/").status_code, 401)

    def test_consultas_na_api(self):
        with mock.patch.object(services, "_buscar_json", return_value={"logradouro": "R", "localidade": "L", "uf": "SP"}):
            r = self.api_a.get(f"{V1}/consumidores/consulta-cep/01310100/")
        self.assertEqual((r.status_code, r.json()["data"]["cidade"]), (200, "L"))
        self.assertEqual(self.api_a.get(f"{V1}/consumidores/consulta-cep/123/").status_code, 400)
        with mock.patch.object(services, "_buscar_json", side_effect=services.ConsultaIndisponivel("fora")):
            self.assertEqual(self.api_a.get(f"{V1}/consumidores/consulta-cep/99999999/").status_code, 503)

    def test_auditoria_da_api_sem_dados_pessoais(self):
        self.criar()
        registro = RegistroAuditoria.objects.filter(modulo="consumidor", acao="criar").latest("id")
        self.assertNotIn(CPF_VALIDO, f"{registro.descricao}{registro.alteracoes}")
