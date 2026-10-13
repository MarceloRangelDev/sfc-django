"""Presença de usuários ("online"), sem WebSocket: o deploy é gunicorn síncrono,
então "online" significa "fez alguma requisição nos últimos 5 minutos".

- `PresencaMiddleware` grava a atividade, no máximo UMA vez por minuto por sessão
  (o instante da última gravação fica na própria sessão), para não pesar no banco.
- A atualização automática do sino de notificações (a cada 20 s, só com a aba
  visível) também conta como atividade: aba aberta e visível = usuário presente.
- `usuarios_online(solicitante)` só devolve quem o solicitante pode ver
  (`web_project.acesso.usuarios_acessiveis`: a própria estrutura matriz/filiais;
  Admin: todos; superusuários só para superusuários) e só dados mínimos.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from web_project.acesso import usuarios_acessiveis

logger = logging.getLogger(__name__)

MINUTOS_ONLINE = 5
INTERVALO_GRAVACAO = 60  # segundos entre duas gravações da mesma sessão
CHAVE_SESSAO = "presenca_gravada_em"
LIMITE_LISTA = 200


def registrar_atividade(usuario, agora=None):
    from .models import Presenca

    Presenca.objects.update_or_create(usuario=usuario, defaults={"ultima_atividade": agora or timezone.now()})


def registrar_saida(sender, request, user, **kwargs):
    """Sinal de logout: o usuário some da lista de online imediatamente."""
    from .models import Presenca

    if user is not None:
        Presenca.objects.filter(usuario=user).delete()


def usuarios_online(solicitante, agora=None):
    """[{nome, unidade, minutos}] dos usuários ATIVOS com atividade recente que o
    solicitante pode ver, do mais recente ao mais antigo. Sem e-mail, IP ou outros dados."""
    from .models import Presenca

    agora = agora or timezone.now()
    limite = agora - timedelta(minutes=MINUTOS_ONLINE)
    registros = (
        Presenca.objects.filter(ultima_atividade__gte=limite, usuario__is_active=True, usuario__in=usuarios_acessiveis(solicitante).values("pk"))
        .select_related("usuario", "usuario__profile__cliente").order_by("-ultima_atividade")
    )
    total = registros.count()
    lista = []
    for registro in registros[:LIMITE_LISTA]:
        usuario = registro.usuario
        cliente = getattr(getattr(usuario, "profile", None), "cliente", None)
        lista.append({
            "nome": usuario.get_full_name() or usuario.get_username(),
            "unidade": cliente.nome_razao_social if cliente else "—",
            "minutos": int((agora - registro.ultima_atividade).total_seconds() // 60),
        })
    return total, lista
