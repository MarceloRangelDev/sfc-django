"""Cliente mínimo da API v5 do Pagar.me (https://docs.pagar.me — consultada em 18/10/2026; ver README_PAGARME.md).

- Autenticação: HTTP Basic com a CHAVE SECRETA como usuário e senha vazia. A chave vem do ambiente (`PAGARME_SECRET_KEY`),
  nunca do banco/código/log. Chave `sk_test_...` = sandbox (simulador); `sk_...` = produção; a URL base é a mesma.
- Toda chamada passa por `apps.integracao.rede.requisitar` (https, anti-SSRF, timeout, teto de resposta). Os testes substituem
  `obter_cliente()` por um falso: nenhum teste fala com o provedor real.
- Só os endpoints cuja forma foi confirmada na documentação estão aqui (planos, assinaturas, cancelamento, cobrança).
"""
import base64

from django.conf import settings

from apps.integracao.rede import ErroDeRede, requisitar


class ErroPagarme(Exception):
    """Falha ao falar com o provedor (mensagem segura, sem credenciais)."""


class NaoConfigurado(ErroPagarme):
    """PAGARME_SECRET_KEY ausente: a integração fica indisponível (nunca simulamos cobrança)."""


class ClientePagarme:
    def __init__(self, chave_secreta, url_base):
        self._auth = "Basic " + base64.b64encode(f"{chave_secreta}:".encode()).decode()
        self._base = url_base.rstrip("/")

    def _chamar(self, metodo, caminho, corpo=None):
        try:
            _, _, dados = requisitar(metodo, f"{self._base}{caminho}", {"Authorization": self._auth, "Accept": "application/json"}, corpo)
        except ErroDeRede as erro:
            raise ErroPagarme(str(erro)) from None
        return dados

    def criar_plano(self, plano):
        """POST /plans. Corpo conforme a documentação (valores em centavos; recorrência sempre à vista)."""
        return self._chamar("POST", "/plans", {
            "name": plano.nome[:64], "currency": "BRL", "interval": plano.intervalo, "interval_count": plano.intervalo_qtd,
            "billing_type": "prepaid", "installments": [1], "payment_methods": ["credit_card", "boleto"],
            "items": [{"name": plano.nome[:64], "quantity": 1, "pricing_scheme": {"scheme_type": "unit", "price": plano.valor}}],
            "metadata": {"plano": plano.slug},
        })

    def criar_assinatura(self, plano_id, cliente, forma, card_token=None, codigo=""):
        """POST /subscriptions. `cliente`: dict {name, email, document, type}. Cartão: SÓ `card_token` (nunca número)."""
        corpo = {"plan_id": plano_id, "payment_method": forma, "customer": cliente, "code": codigo, "installments": 1}
        if forma == "credit_card":
            corpo["card_token"] = card_token
        return self._chamar("POST", "/subscriptions", corpo)

    def obter_assinatura(self, subscription_id):
        return self._chamar("GET", f"/subscriptions/{subscription_id}")

    def cancelar_assinatura(self, subscription_id):
        """DELETE /subscriptions/{id}; cancela também as faturas pendentes (padrão do provedor)."""
        return self._chamar("DELETE", f"/subscriptions/{subscription_id}", {"cancel_pending_invoices": True})

    def obter_cobranca(self, charge_id):
        return self._chamar("GET", f"/charges/{charge_id}")


def obter_cliente():
    """Cliente configurado pelo ambiente. Levanta NaoConfigurado sem chave (a tela mostra "indisponível")."""
    chave = getattr(settings, "PAGARME_SECRET_KEY", "")
    if not chave:
        raise NaoConfigurado("Integração com o Pagar.me não configurada.")
    return ClientePagarme(chave, settings.PAGARME_API_URL)
