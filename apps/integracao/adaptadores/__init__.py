"""Registro dos adaptadores. Para um provedor novo (ex.: iFood, 99Food): crie a classe em adaptadores/, herde de `Adaptador`,
implemente os métodos que ele suporta e acrescente-a ao REGISTRO abaixo. Nada mais no núcleo precisa mudar."""
from django.conf import settings

from .agenda import GoogleCalendar, ICloudCalendar
from .base import Adaptador, ErroIntegracao, NaoSuportado  # noqa: F401
from .instagram import InstagramGraph
from .teste import AdaptadorTeste
from .whatsapp import WhatsAppCloud, WhatsAppIntermediario

REGISTRO = {a.chave: a for a in (WhatsAppCloud(), WhatsAppIntermediario(), GoogleCalendar(), ICloudCalendar(), InstagramGraph(), AdaptadorTeste())}


def obter(chave):
    try:
        return REGISTRO[chave]
    except KeyError:
        raise ErroIntegracao("Provedor desconhecido.") from None


def disponiveis():
    """Provedores oferecidos na tela (o de teste só em DEBUG)."""
    return [a for a in REGISTRO.values() if a.chave != "teste" or settings.DEBUG]


CHOICES = [(a.chave, a.rotulo) for a in REGISTRO.values()]
