"""Autorização por SENHA no PDV: excluir item lançado, cancelar venda/comanda e devolver (extornar) itens.

Quem executa a ação digita o usuário e a senha de alguém COM a permissão da operação (o próprio usuário, se a tem, ou um supervisor). O servidor:
  1. autentica a senha (nunca é gravada nem logada);
  2. exige que o autorizador esteja ativo, tenha a permissão e alcance a unidade da venda (ou seja administrador global);
  3. limita tentativas erradas por usuário (anti força bruta);
  4. registra na auditoria QUEM autorizou.
Mensagens de erro são genéricas: não revelam se o usuário existe nem o que falhou.
"""
from django.contrib.auth import authenticate
from django.core.cache import cache

from apps.auditoria.models import ACAO_EDITAR
from apps.auditoria.utils import registrar
from web_project.acesso import administrador_global, clientes_acessiveis
from web_project.regras import RegraNegocioError

MAX_FALHAS, JANELA_SEGUNDOS = 5, 900
NEGADO = "Senha de autorização inválida ou sem permissão para esta operação."


def autorizar(request, permissao, cliente, usuario, senha):
    """Devolve o usuário autorizador ou levanta RegraNegocioError. `usuario` vazio = o próprio usuário logado."""
    chave = f"pdv:autorizacao:{request.user.pk}"
    if cache.get(chave, 0) >= MAX_FALHAS:
        raise RegraNegocioError("Muitas tentativas de autorização. Aguarde alguns minutos.")
    autorizador = authenticate(request, username=(usuario or request.user.get_username()).strip(), password=senha or "") if senha else None
    permitido = bool(
        autorizador is not None and autorizador.is_active and autorizador.has_perm(permissao)
        and (administrador_global(autorizador) or clientes_acessiveis(autorizador).filter(pk=cliente.pk).exists())
    )
    if not permitido:
        cache.add(chave, 0, JANELA_SEGUNDOS)
        cache.incr(chave)
        raise RegraNegocioError(NEGADO)
    cache.delete(chave)
    return autorizador


def auditar(request, autorizador, acao, venda):
    """Trilha da autorização (ator = quem executou; autorizador no texto). Sem senha."""
    registrar(
        request, ACAO_EDITAR, modulo="pdv", registro_id=venda.pk if venda is not None else "", tabela="pdv_venda",
        registro_nome=f"Venda #{venda.numero}" if venda is not None else "Venda em andamento",
        descricao=f"Autorização por senha: {acao}. Autorizado por {autorizador.get_username()}.", silencioso=False,
    )
