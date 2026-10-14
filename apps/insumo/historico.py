"""Histórico de alterações de um insumo, montado a partir da trilha de auditoria (`RegistroAuditoria`).

Não cria tabela nova: reaproveita o que a auditoria já grava (criação, edições de preço/quantidade/unidade,
mudança de estoque mínimo, inativação...). Só registros DESTE insumo (tabela + id) entram; o chamador já
garantiu que o insumo está no escopo (tenant) do usuário. Registros sem alteração útil (login, exportações
de etiqueta) não aparecem.
"""
import re
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date

from apps.auditoria.models import RegistroAuditoria

LIMITE_REGISTROS = 500


def _numero(texto):
    """'R$ 1.234,56' / '10,5 kg' / '10' -> Decimal; None se o texto não for numérico."""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,.\-]", "", texto.replace("R$", ""))
    if not limpo or not re.search(r"\d", limpo):
        return None
    limpo = limpo.replace(".", "").replace(",", ".") if "," in limpo else limpo
    try:
        return Decimal(limpo)
    except InvalidOperation:
        return None


def variacao_percentual(antes, depois):
    """'+12,5%' / '-3,0%' entre dois textos numéricos; '' se não der para comparar (ou antes = 0)."""
    a, d = _numero(antes), _numero(depois)
    if a is None or d is None or a == 0:
        return ""
    pct = ((d - a) / abs(a) * 100).quantize(Decimal("0.1"))
    return f"{'+' if pct > 0 else ''}{pct}%".replace(".", ",")


def montar_historico(insumo, params):
    """Contexto da tela: eventos (mais recentes primeiro), campos disponíveis para o filtro e a evolução do campo
    escolhido. `params`: querystring (`campo`, `de`, `ate`)."""
    registros = RegistroAuditoria.objects.filter(
        tabela=insumo._meta.db_table, registro_id=str(insumo.pk), resultado=RegistroAuditoria.RESULTADO_SUCESSO,
    ).select_related("usuario").order_by("-criado_em", "-pk")
    de, ate = parse_date(params.get("de", "") or ""), parse_date(params.get("ate", "") or "")
    if de:
        registros = registros.filter(criado_em__date__gte=de)
    if ate:
        registros = registros.filter(criado_em__date__lte=ate)
    registros = list(registros.exclude(acao="exportar")[:LIMITE_REGISTROS])

    campos = {}
    for registro in registros:
        for alteracao in registro.alteracoes or []:
            campos.setdefault(alteracao.get("campo", ""), alteracao.get("rotulo", ""))
    campo = params.get("campo", "")
    if campo not in campos:
        campo = ""

    eventos, evolucao = [], []
    for registro in registros:
        alteracoes = []
        for alteracao in registro.alteracoes or []:
            if campo and alteracao.get("campo") != campo:
                continue
            item = {**alteracao, "variacao": variacao_percentual(alteracao.get("antes"), alteracao.get("depois"))}
            alteracoes.append(item)
            if campo:
                evolucao.append({"quando": registro.criado_em, "usuario": registro.usuario, **item})
        if campo and not alteracoes:
            continue
        eventos.append({"registro": registro, "alteracoes": alteracoes})
    return {
        "eventos": eventos, "campos": sorted(campos.items(), key=lambda c: c[1]), "campo": campo, "evolucao": list(reversed(evolucao)),
        "de": params.get("de", ""), "ate": params.get("ate", ""), "limitado": len(registros) >= LIMITE_REGISTROS,
    }
