"""
Auditoria reutilizável (PROMPT_MESTRE, seções 8 a 10).

Todo o código que grava na tabela `auditoria_registroauditoria` passa por
aqui — nenhum módulo precisa reimplementar auditoria:

- registrar()            genérico (login, logout, exportação...)
- registrar_criacao()    CREATE  -> "Criado insumo ID 123 - Queijo Mussarela."
- registrar_edicao()     UPDATE  -> só os campos que mudaram, com antes/depois
- registrar_exclusao()   DELETE  -> "Excluído ..." ou "... foi inativado."
- registrar_alteracoes() alterações já calculadas à mão (ex.: M2M de papéis)

Em views de CRUD baseadas em CreateView/UpdateView basta usar o
AuditoriaFormMixin (apps/auditoria/mixins.py). Nas views de exclusão, chame
registrar_exclusao() dentro de `transaction.atomic()`, junto com a exclusão.
"""
import logging
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from auth.ratelimit import get_client_ip
from web_project.regras.dinheiro import DinheiroService
from web_project.regras.extenso import formatar_numero_br

from .models import ACAO_CRIAR, ACAO_EDITAR, ACAO_EXCLUIR, RegistroAuditoria

logger = logging.getLogger(__name__)


def _obter_ip(request):
    if request is None:
        return None
    return get_client_ip(request)  # mesma regra (e mesma confiança em proxy) do rate limit


def registrar(
    request, acao, modulo="", registro_id="", descricao="", resultado=RegistroAuditoria.RESULTADO_SUCESSO,
    usuario=None, tabela="", registro_nome="", alteracoes=None, silencioso=True,
):
    """Grava um evento no log de auditoria e devolve o registro criado.

    `silencioso=True` (padrão, eventos como login/logout): uma falha ao
    auditar nunca derruba a requisição — só é logada. CRUD usa
    `silencioso=False`: a auditoria é parte da operação (rodando na mesma
    transação), então se não der para auditar, a operação inteira é
    desfeita — nunca existe alteração de dados sem rastro.
    """
    try:
        usuario_final = usuario
        if usuario_final is None and request is not None and request.user.is_authenticated:
            usuario_final = request.user

        cliente = None
        if usuario_final is not None:
            cliente = getattr(getattr(usuario_final, "profile", None), "cliente", None)

        return RegistroAuditoria.objects.create(
            usuario=usuario_final,
            cliente=cliente,
            acao=acao,
            modulo=modulo,
            tabela=tabela,
            registro_id=str(registro_id) if registro_id else "",
            registro_nome=registro_nome[:255],
            descricao=descricao,
            alteracoes=alteracoes or [],
            ip=_obter_ip(request),
            resultado=resultado,
        )
    except Exception:
        logger.exception("Falha ao gravar registro de auditoria (ação=%s, módulo=%s)", acao, modulo)
        if not silencioso:
            raise
        return None


# --------------------------------------------------------------------------
# Foto (snapshot) e comparação de objetos
# --------------------------------------------------------------------------

def _campos_auditaveis(objeto):
    """Campos editáveis do model (ignora a PK e campos automáticos como
    criado_em/atualizado_em, que são editable=False)."""
    return [c for c in objeto._meta.concrete_fields if c.editable and not c.primary_key]


def _texto_do_valor(objeto, campo, valor):
    """Texto de exibição de um valor (o que o usuário veria na tela)."""
    if valor is None or valor == "":
        return None
    # Models com valores em centavos declaram quais campos são dinheiro:
    #   AUDITORIA_MONETARIOS = ("preco_pago",)
    if campo.name in getattr(objeto, "AUDITORIA_MONETARIOS", ()):
        return DinheiroService.formatar(valor)
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if campo.choices:
        return str(dict(campo.flatchoices).get(valor, valor))
    if campo.is_relation:
        relacionado = getattr(objeto, campo.name, None)
        return str(relacionado) if relacionado is not None else str(valor)
    if isinstance(valor, Decimal):
        return formatar_numero_br(valor)
    if isinstance(valor, datetime):
        return timezone.localtime(valor).strftime("%d/%m/%Y %H:%M") if timezone.is_aware(valor) else valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def tirar_foto(objeto, ignorar=()):
    """Foto dos campos do objeto: {campo: {"rotulo", "valor", "texto"}}.

    `valor` é o valor bruto (para detectar mudança de verdade: Decimal
    '10.0' e '10' são iguais) e `texto` é o que aparece na auditoria.
    Tire a foto ANTES de salvar uma edição.

    `ignorar`: nomes de campos que NÃO devem ser auditados (ex.: "password":
    o hash da senha jamais deve ir para o log). Campos ManyToMany editáveis
    entram na foto como conjunto de ids (ex.: perfis de um cliente).
    """
    foto = {}
    for campo in _campos_auditaveis(objeto):
        if campo.name in ignorar:
            continue
        valor = campo.value_from_object(objeto)
        foto[campo.name] = {
            "rotulo": str(campo.verbose_name)[:1].upper() + str(campo.verbose_name)[1:],
            "valor": valor,
            "texto": _texto_do_valor(objeto, campo, valor),
        }
    if objeto.pk:
        for campo in objeto._meta.many_to_many:
            if not campo.editable or campo.name in ignorar:
                continue
            relacionados = list(getattr(objeto, campo.name).all())
            foto[campo.name] = {
                "rotulo": str(campo.verbose_name)[:1].upper() + str(campo.verbose_name)[1:],
                "valor": frozenset(r.pk for r in relacionados),
                "texto": ", ".join(sorted(str(r) for r in relacionados)) or None,
            }
    return foto


def comparar(antes, depois):
    """Lista só os campos que mudaram: [{"campo","rotulo","antes","depois"}]."""
    mudancas = []
    for nome, novo in depois.items():
        velho = antes.get(nome)
        if velho is None or velho["valor"] == novo["valor"]:
            continue
        mudancas.append(
            {"campo": nome, "rotulo": novo["rotulo"], "antes": velho["texto"], "depois": novo["texto"]}
        )
    return mudancas


def _vazio(texto):
    return "(vazio)" if texto is None else texto


def _descricao_com_alteracoes(titulo, alteracoes):
    if not alteracoes:
        return titulo
    linhas = [f"- {a['rotulo']}: {_vazio(a['antes'])} → {_vazio(a['depois'])}" for a in alteracoes]
    return f"{titulo}\n\nAlterações:\n" + "\n".join(linhas)


# --------------------------------------------------------------------------
# CREATE / UPDATE / DELETE
# --------------------------------------------------------------------------

def _identificacao(objeto):
    """('insumo', 'insumo', 'insumo_insumo', 123, 'Queijo Mussarela')."""
    meta = objeto._meta
    return meta.verbose_name.lower(), meta.app_label, meta.db_table, objeto.pk, str(objeto)


def registrar_criacao(request, objeto, modulo=None):
    nome_modelo, app, tabela, pk, nome = _identificacao(objeto)
    return registrar(
        request, acao=ACAO_CRIAR, modulo=modulo or app, tabela=tabela, registro_id=pk, registro_nome=nome,
        descricao=f"Criado {nome_modelo} ID {pk} - {nome}.", silencioso=False,
    )


def registrar_edicao(request, objeto, foto_antes, modulo=None):
    """UPDATE: compara a foto de antes com o estado atual. Se nada mudou,
    não grava nada (não existe 'atualização' sem alteração)."""
    alteracoes = comparar(foto_antes, tirar_foto(objeto))
    if not alteracoes:
        return None
    return registrar_alteracoes(request, objeto, alteracoes, modulo=modulo)


def registrar_alteracoes(request, objeto, alteracoes, modulo=None):
    """UPDATE com a lista de alterações já pronta (usada quando a mudança
    não é um campo simples, ex.: permissões de um papel)."""
    nome_modelo, app, tabela, pk, nome = _identificacao(objeto)
    titulo = f"Atualizado {nome_modelo} ID {pk} - {nome}."
    return registrar(
        request, acao=ACAO_EDITAR, modulo=modulo or app, tabela=tabela, registro_id=pk, registro_nome=nome,
        descricao=_descricao_com_alteracoes(titulo, alteracoes), alteracoes=alteracoes, silencioso=False,
    )


def registrar_exclusao(request, objeto, logica=False, modulo=None):
    """DELETE. Chame ANTES de apagar/inativar (depois do delete() o objeto
    perde o id), dentro da mesma `transaction.atomic()` da exclusão.

    logica=True: o registro só foi inativado (soft delete) — a mensagem diz
    isso claramente: "Insumo ID 123 - Queijo foi inativado."
    """
    nome_modelo, app, tabela, pk, nome = _identificacao(objeto)
    if logica:
        descricao = f"{nome_modelo.capitalize()} ID {pk} - {nome} foi inativado."
    else:
        descricao = f"Excluído {nome_modelo} ID {pk} - {nome}."
    return registrar(
        request, acao=ACAO_EXCLUIR, modulo=modulo or app, tabela=tabela, registro_id=pk, registro_nome=nome,
        descricao=descricao, silencioso=False,
    )
