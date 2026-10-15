"""Adaptador de TESTE (em memória): exercita toda a cadeia (fila, webhook, agenda, erros e reenvio) sem rede e sem conta.

Útil para homologar o fluxo do sistema e para os testes automáticos; aparece na tela só em DEBUG. Falhas simuláveis pela config:
`falhar_envio` ("temporario"/"definitivo"), `falhar_agenda`.
"""
import itertools
from datetime import datetime

from .base import Adaptador, ErroIntegracao, comparar, hmac_sha256


class AdaptadorTeste(Adaptador):
    chave = "teste"
    rotulo = "Provedor de teste (desenvolvimento)"
    tipo = "misto"  # exercita mensagens e agenda
    descricao = "Provedor simulado em memória, só para validar o fluxo sem conta real."
    validado = True
    campos_credenciais = (("segredo", "Segredo do webhook", True),)
    campos_config = ()
    suporta_webhook = True

    ENVIADAS = []   # (destino, texto)
    EVENTOS = {}    # id_externo -> dict
    _ids = itertools.count(1)

    @classmethod
    def limpar(cls):
        cls.ENVIADAS.clear()
        cls.EVENTOS.clear()
        cls._ids = itertools.count(1)

    def testar(self, conexao, credenciais):
        if conexao.config.get("falhar_teste"):
            raise ErroIntegracao("Falha simulada no teste.")

    def renovar(self, conexao, credenciais):
        if conexao.config.get("renovavel"):
            return {**credenciais, "token": f"novo-{next(self._ids)}"}, datetime.now().astimezone()
        return None

    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        falha = conexao.config.get("falhar_envio")
        if falha:
            raise ErroIntegracao("Falha simulada no envio.", temporario=falha == "temporario", espera=1 if falha == "temporario" else None)
        self.ENVIADAS.append((destino, texto))
        return f"msg-{len(self.ENVIADAS)}"

    def desafio_webhook(self, conexao, credenciais, parametros):
        return parametros.get("desafio")

    def assinatura_valida(self, conexao, credenciais, cabecalhos, corpo):
        return comparar(cabecalhos.get("X-Teste-Assinatura"), hmac_sha256(credenciais.get("segredo", "x"), corpo))

    def interpretar_webhook(self, conexao, payload):
        return [{"id": e["id"], "tipo": "mensagem", "de": e.get("de", ""), "texto": e.get("texto", ""), "quando": e.get("quando")} for e in payload.get("eventos", [])]

    def criar_evento(self, conexao, credenciais, evento):
        if conexao.config.get("falhar_agenda"):
            raise ErroIntegracao("Falha simulada na agenda.", temporario=True)
        id_externo = f"ev-{next(self._ids)}"
        self.EVENTOS[id_externo] = {**evento, "id": id_externo, "atualizado_em": datetime.now().astimezone(), "etag": "1"}
        return id_externo, "1"

    def atualizar_evento(self, conexao, credenciais, id_externo, evento):
        if id_externo not in self.EVENTOS:
            raise ErroIntegracao("Evento inexistente no provedor.")
        etag = str(int(self.EVENTOS[id_externo]["etag"]) + 1)
        self.EVENTOS[id_externo] = {**evento, "id": id_externo, "atualizado_em": datetime.now().astimezone(), "etag": etag}
        return etag

    def remover_evento(self, conexao, credenciais, id_externo):
        self.EVENTOS.pop(id_externo, None)

    def listar_eventos(self, conexao, credenciais, desde, ate):
        return list(self.EVENTOS.values())
