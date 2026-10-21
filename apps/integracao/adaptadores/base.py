"""Contrato dos adaptadores de provedor.

Cada provedor (WhatsApp, Google Agenda, iCloud, Instagram, e no futuro iFood/99Food) é UMA classe que herda de `Adaptador` e é
registrada em `adaptadores/__init__.py`. O núcleo (apps/integracao/services.py) só conversa com esta interface: conexão, fila de
mensagens, webhook e sincronização de agenda não conhecem detalhes de nenhum provedor. Para um provedor novo basta criar a classe,
declarar `campos` (o que o usuário informa) e implementar os métodos que ele suporta.

`validado = False` significa: implementado segundo a documentação pública do provedor, mas NÃO homologado contra a conta real
(faltam credenciais/ambiente). A tela e o README mostram esse estado; não se promete funcionamento antes da homologação.
"""
import hmac
from hashlib import sha256


class ErroIntegracao(Exception):
    """Erro do provedor/adaptador com mensagem segura para o usuário (nunca contém segredo)."""

    def __init__(self, mensagem, temporario=False, espera=None):
        super().__init__(mensagem)
        self.temporario, self.espera = temporario, espera


class NaoSuportado(ErroIntegracao):
    pass


class Adaptador:
    chave = ""
    rotulo = ""
    tipo = "mensagem"  # mensagem | agenda | social | misto (mensagens E agenda)
    @property
    def envia_mensagens(self):
        return self.tipo in ("mensagem", "social", "misto")

    @property
    def sincroniza_agenda(self):
        return self.tipo in ("agenda", "misto")

    descricao = ""
    validado = False
    escopos = ()  # escopos MÍNIMOS pedidos ao provedor
    # [(nome, rótulo, secreto)]: credenciais pedidas ao usuário (secretas ficam cifradas e nunca são exibidas)
    campos_credenciais = ()
    # [(nome, rótulo)]: configuração NÃO secreta (id do calendário, número, URL base...)
    campos_config = ()
    # Como `campos_config`, mas NÃO obrigatórios (recursos extras do provedor; conexões antigas continuam válidas sem eles).
    campos_config_opcionais = ()
    usa_oauth = False
    suporta_webhook = False
    so_tarefas = False  # publica/importa APENAS compromissos do tipo tarefa (sem vencimentos de contas) — ex.: Google Tarefas
    sincroniza_conclusao = False  # propaga "concluído" nos dois sentidos

    # --- ciclo de vida ---------------------------------------------------------------
    def validar(self, credenciais, config):
        """Confere se os campos obrigatórios foram informados (levanta ErroIntegracao)."""
        faltando = [r for n, r, _ in self.campos_credenciais if not credenciais.get(n)]
        faltando += [r for n, r in self.campos_config if not config.get(n)]
        if faltando:
            raise ErroIntegracao("Informe: " + ", ".join(faltando) + ".")

    def testar(self, conexao, credenciais):
        """Verifica a conexão chamando o provedor (levanta ErroIntegracao se falhar)."""
        raise NaoSuportado("Este provedor não tem teste de conexão.")

    def renovar(self, conexao, credenciais):
        """Renova tokens que expiram. Devolve (credenciais_atualizadas, expira_em) ou None se não se aplica."""
        return None

    def revogar(self, conexao, credenciais):
        """Revoga o acesso no provedor (melhor esforço; o chamador apaga as credenciais locais de qualquer forma)."""

    # --- mensagens -------------------------------------------------------------------
    def enviar_mensagem(self, conexao, credenciais, destino, texto):
        """Envia texto; devolve o id externo da mensagem."""
        raise NaoSuportado("Este provedor não envia mensagens.")

    # --- webhook ---------------------------------------------------------------------
    def desafio_webhook(self, conexao, credenciais, parametros):
        """Resposta ao desafio de verificação (GET) do provedor, ou None."""
        return None

    def assinatura_valida(self, conexao, credenciais, cabecalhos, corpo):
        """Valida origem/assinatura do webhook. Sem validação possível = False (NUNCA aceita sem assinatura)."""
        return False

    def interpretar_webhook(self, conexao, payload):
        """payload JSON -> [{"id", "tipo", "de", "texto", "quando"}] (um por evento recebido)."""
        return []

    # --- templates de mensagem (WhatsApp oficial) ------------------------------------
    def listar_templates(self, conexao, credenciais):
        raise NaoSuportado("Este provedor não tem templates de mensagem.")

    def criar_template(self, conexao, credenciais, template):
        raise NaoSuportado("Este provedor não tem templates de mensagem.")

    def atualizar_template(self, conexao, credenciais, id_externo, template):
        raise NaoSuportado("Este provedor não tem templates de mensagem.")

    def obter_template(self, conexao, credenciais, id_externo):
        raise NaoSuportado("Este provedor não tem templates de mensagem.")

    def excluir_template(self, conexao, credenciais, nome, id_externo=None):
        raise NaoSuportado("Este provedor não tem templates de mensagem.")

    # --- agenda ----------------------------------------------------------------------
    def criar_evento(self, conexao, credenciais, evento):
        """evento: dict(titulo, descricao, inicio, fim, todo_o_dia). Devolve (id_externo, etag)."""
        raise NaoSuportado("Este provedor não tem agenda.")

    def atualizar_evento(self, conexao, credenciais, id_externo, evento):
        raise NaoSuportado("Este provedor não tem agenda.")

    def remover_evento(self, conexao, credenciais, id_externo):
        raise NaoSuportado("Este provedor não tem agenda.")

    def listar_eventos(self, conexao, credenciais, desde, ate):
        """Eventos externos no intervalo: [dict(id, titulo, descricao, inicio, fim, todo_o_dia, atualizado_em, etag)]."""
        raise NaoSuportado("Este provedor não lista eventos.")


def hmac_sha256(segredo, corpo):
    return hmac.new(segredo.encode(), corpo, sha256).hexdigest()


def comparar(a, b):
    """Comparação em tempo constante de assinaturas."""
    return hmac.compare_digest(str(a or ""), str(b or ""))
