"""Templates do WhatsApp Business (API oficial da Meta): validação, montagem dos componentes e troca com a Meta.

Regras que valem para TODAS as entradas (telas e API), porque a validação mora aqui e nunca no navegador:
- a conexão precisa ser `whatsapp_cloud`, estar ativa e pertencer à unidade do usuário (quem chama escolhe a conexão já filtrada por
  `clientes_dos_itens`; as funções deste módulo não aceitam `cliente` vindo do cliente HTTP);
- a Meta é a fonte da verdade do STATUS: nada é marcado como aprovado/enviado sem uma resposta real dela. Se a chamada falhar, o estado
  local fica como estava e o erro (sem credenciais) é guardado em `ultimo_erro` e no log;
- só MARKETING e UTILITY podem ser criados/editados aqui; AUTHENTICATION (OTP), mídia, carrossel etc. são importados como somente leitura.
"""
import re

from django.db import transaction
from django.utils import timezone

from apps.auditoria.utils import registrar, registrar_criacao, registrar_edicao, tirar_foto
from web_project.regras import RegraNegocioError

from . import services as s
from .adaptadores import ErroIntegracao
from .models import Conexao, LogIntegracao, TemplateWhatsApp

T = TemplateWhatsApp
RE_NOME = re.compile(r"^[a-z0-9_]{1,512}$")  # exigência da Meta: minúsculas, números e sublinhado
RE_IDIOMA = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")  # pt_BR, en_US, es...
RE_VARIAVEL = re.compile(r"\{\{(\d+)\}\}")
TIPOS_BOTAO = ("QUICK_REPLY", "URL", "PHONE_NUMBER")
MAX_BOTOES, MAX_URL, MAX_TELEFONE = 10, 2, 1
IDIOMAS = [("pt_BR", "Português (Brasil)"), ("pt_PT", "Português (Portugal)"), ("en_US", "Inglês (EUA)"), ("en_GB", "Inglês (Reino Unido)"),
           ("es", "Espanhol"), ("es_AR", "Espanhol (Argentina)"), ("es_ES", "Espanhol (Espanha)"), ("es_MX", "Espanhol (México)")]
# Status da Meta em que o conteúdo pode ser reenviado (editar). Em análise (PENDING) ou desativado a Meta recusa a edição.
STATUS_EDITAVEIS = ("APPROVED", "REJECTED", "PAUSED")


def _variaveis(texto):
    return [int(n) for n in RE_VARIAVEL.findall(texto or "")]


def _checar_sequencia(texto, rotulo, maximo, erros):
    """Variáveis {{1}}, {{2}}... em sequência, sem repetição fora de ordem, até `maximo` (None = sem limite)."""
    numeros = _variaveis(texto)
    unicos = sorted(set(numeros))
    if unicos != list(range(1, len(unicos) + 1)):
        erros.append(f"{rotulo}: as variáveis devem ser {{{{1}}}}, {{{{2}}}}... em sequência, sem pular números.")
    if maximo is not None and len(unicos) > maximo:
        erros.append(f"{rotulo}: no máximo {maximo} variável(is).")
    return unicos


def validar_dados(dados, parcial_de=None):
    """Valida e normaliza o conteúdo. `dados`: nome, idioma, categoria, cabecalho, corpo, rodape, botoes, exemplos.
    `parcial_de`: template existente (já enviado) — nome, idioma e categoria ficam travados nele, pois a Meta não os aceita alterar.
    Devolve o dicionário limpo ou levanta RegraNegocioError com TODAS as mensagens."""
    erros = []
    limpo = {
        "nome": str(dados.get("nome") or "").strip(), "idioma": str(dados.get("idioma") or "").strip(), "categoria": str(dados.get("categoria") or "").strip(),
        "cabecalho": str(dados.get("cabecalho") or "").strip(), "corpo": str(dados.get("corpo") or "").strip(), "rodape": str(dados.get("rodape") or "").strip(),
    }
    if parcial_de is not None and parcial_de.enviado:
        for campo in ("nome", "idioma", "categoria"):
            if limpo[campo] and limpo[campo] != getattr(parcial_de, campo):
                erros.append(f"{campo.capitalize()} não pode ser alterado depois que o template foi enviado à Meta. Crie outro template.")
            limpo[campo] = getattr(parcial_de, campo)
    if not RE_NOME.match(limpo["nome"]):
        erros.append("Nome: use só letras minúsculas sem acento, números e sublinhado (até 512 caracteres).")
    if not RE_IDIOMA.match(limpo["idioma"]):
        erros.append("Idioma inválido. Exemplos: pt_BR, en_US, es.")
    if limpo["categoria"] not in (T.MARKETING, T.UTILITY):
        erros.append("Categoria: escolha Marketing ou Utilidade (autenticação não é criada por aqui).")

    exemplos_in = dados.get("exemplos") if isinstance(dados.get("exemplos"), dict) else {}
    exemplos = {"cabecalho": str(exemplos_in.get("cabecalho") or "").strip(), "corpo": [str(x).strip() for x in (exemplos_in.get("corpo") or []) if isinstance(x, (str, int))]}

    if len(limpo["cabecalho"]) > 60:
        erros.append("Cabeçalho: no máximo 60 caracteres.")
    var_cab = _checar_sequencia(limpo["cabecalho"], "Cabeçalho", 1, erros)
    if var_cab and not exemplos["cabecalho"]:
        erros.append("Cabeçalho: informe um exemplo para a variável {{1}}.")
    if not var_cab:
        exemplos["cabecalho"] = ""

    if not limpo["corpo"]:
        erros.append("Corpo: obrigatório.")
    if len(limpo["corpo"]) > 1024:
        erros.append("Corpo: no máximo 1024 caracteres.")
    var_corpo = _checar_sequencia(limpo["corpo"], "Corpo", None, erros)
    if var_corpo and re.match(r"^\s*\{\{\d+\}\}|\{\{\d+\}\}\s*$", limpo["corpo"]):
        erros.append("Corpo: a Meta não aceita variável no início nem no fim do texto.")
    if var_corpo:
        if len(exemplos["corpo"]) < len(var_corpo) or not all(exemplos["corpo"][: len(var_corpo)]):
            erros.append(f"Corpo: informe um exemplo para cada uma das {len(var_corpo)} variável(is).")
        exemplos["corpo"] = exemplos["corpo"][: len(var_corpo)]
    else:
        exemplos["corpo"] = []

    if len(limpo["rodape"]) > 60:
        erros.append("Rodapé: no máximo 60 caracteres.")
    if _variaveis(limpo["rodape"]):
        erros.append("Rodapé: não aceita variáveis.")

    botoes, vistos = [], {"URL": 0, "PHONE_NUMBER": 0}
    brutos = dados.get("botoes") if isinstance(dados.get("botoes"), list) else []
    if len(brutos) > MAX_BOTOES:
        erros.append(f"No máximo {MAX_BOTOES} botões.")
    for i, bruto in enumerate(brutos[:MAX_BOTOES], 1):
        bruto = bruto if isinstance(bruto, dict) else {}
        tipo, texto = str(bruto.get("tipo") or "").upper(), str(bruto.get("texto") or "").strip()
        item = {"tipo": tipo, "texto": texto}
        if tipo not in TIPOS_BOTAO:
            erros.append(f"Botão {i}: tipo inválido.")
            continue
        if not texto or len(texto) > 25:
            erros.append(f"Botão {i}: o texto deve ter de 1 a 25 caracteres.")
        if tipo == "URL":
            url = str(bruto.get("url") or "").strip()
            vistos["URL"] += 1
            if not re.match(r"^https://[^\s]{1,2000}$", url):
                erros.append(f"Botão {i}: a URL deve começar com https://.")
            variaveis = _checar_sequencia(url, f"Botão {i}", 1, erros)
            if variaveis and not url.endswith("{{1}}"):
                erros.append(f"Botão {i}: a variável da URL deve ficar no final ({{{{1}}}}).")
            exemplo = str(bruto.get("exemplo") or "").strip()
            if variaveis and not exemplo:
                erros.append(f"Botão {i}: informe um exemplo para a variável da URL.")
            item.update(url=url, exemplo=exemplo if variaveis else "")
        elif tipo == "PHONE_NUMBER":
            telefone = re.sub(r"[\s().-]", "", str(bruto.get("telefone") or ""))
            vistos["PHONE_NUMBER"] += 1
            if not re.match(r"^\+\d{8,15}$", telefone):
                erros.append(f"Botão {i}: informe o telefone com + e DDI (ex.: +5511999990000).")
            item.update(telefone=telefone)
        botoes.append(item)
    if vistos["URL"] > MAX_URL:
        erros.append(f"No máximo {MAX_URL} botões de URL.")
    if vistos["PHONE_NUMBER"] > MAX_TELEFONE:
        erros.append(f"No máximo {MAX_TELEFONE} botão de telefone.")
    # A Meta exige as respostas rápidas agrupadas antes dos demais botões: ordenamos em vez de recusar (a ordem relativa é mantida).
    botoes.sort(key=lambda b: b["tipo"] != "QUICK_REPLY")

    if erros:
        raise RegraNegocioError(" ".join(erros))
    return {**limpo, "botoes": botoes, "exemplos": exemplos}


def montar_componentes(t):
    """Conteúdo local -> `components` da Graph API."""
    componentes = []
    if t.cabecalho:
        cab = {"type": "HEADER", "format": "TEXT", "text": t.cabecalho}
        if _variaveis(t.cabecalho):
            cab["example"] = {"header_text": [t.exemplos.get("cabecalho", "")]}
        componentes.append(cab)
    corpo = {"type": "BODY", "text": t.corpo}
    if _variaveis(t.corpo):
        corpo["example"] = {"body_text": [list(t.exemplos.get("corpo", []))]}
    componentes.append(corpo)
    if t.rodape:
        componentes.append({"type": "FOOTER", "text": t.rodape})
    if t.botoes:
        botoes = []
        for b in t.botoes:
            if b["tipo"] == "QUICK_REPLY":
                botoes.append({"type": "QUICK_REPLY", "text": b["texto"]})
            elif b["tipo"] == "URL":
                item = {"type": "URL", "text": b["texto"], "url": b["url"]}
                if b.get("exemplo"):
                    item["example"] = [b["exemplo"]]
                botoes.append(item)
            else:
                botoes.append({"type": "PHONE_NUMBER", "text": b["texto"], "phone_number": b["telefone"]})
        componentes.append({"type": "BUTTONS", "buttons": botoes})
    return componentes


def ler_componentes(componentes):
    """`components` da Meta -> campos locais. Devolve (campos, somente_leitura): qualquer recurso fora do que montamos
    (cabeçalho de mídia, botão OTP/Flow/copiar código, carrossel...) marca o template como somente leitura."""
    campos = {"cabecalho": "", "corpo": "", "rodape": "", "botoes": [], "exemplos": {"cabecalho": "", "corpo": []}}
    somente_leitura = False
    for c in componentes or []:
        tipo = str(c.get("type", "")).upper()
        if tipo == "HEADER" and str(c.get("format", "")).upper() == "TEXT":
            campos["cabecalho"] = str(c.get("text", ""))[:60]
            exemplo = (c.get("example") or {}).get("header_text") or [""]
            campos["exemplos"]["cabecalho"] = str(exemplo[0])[:200]
        elif tipo == "BODY":
            campos["corpo"] = str(c.get("text", ""))[:1024]
            exemplo = ((c.get("example") or {}).get("body_text") or [[]])[0]
            campos["exemplos"]["corpo"] = [str(x)[:200] for x in exemplo]
        elif tipo == "FOOTER":
            campos["rodape"] = str(c.get("text", ""))[:60]
        elif tipo == "BUTTONS":
            for b in c.get("buttons") or []:
                bt = str(b.get("type", "")).upper()
                if bt == "QUICK_REPLY":
                    campos["botoes"].append({"tipo": bt, "texto": str(b.get("text", ""))[:25]})
                elif bt == "URL":
                    campos["botoes"].append({"tipo": bt, "texto": str(b.get("text", ""))[:25], "url": str(b.get("url", ""))[:2000], "exemplo": str((b.get("example") or [""])[0])[:200]})
                elif bt == "PHONE_NUMBER":
                    campos["botoes"].append({"tipo": bt, "texto": str(b.get("text", ""))[:25], "telefone": str(b.get("phone_number", ""))[:20]})
                else:
                    somente_leitura = True
        else:
            somente_leitura = True
    return campos, somente_leitura


# --------------------------------------------------------------------------
# Operações (cada uma fala com a Meta e só então altera o estado local)
# --------------------------------------------------------------------------

def _conexao_apta(conexao):
    if conexao.provedor != "whatsapp_cloud":
        raise RegraNegocioError("Templates existem só na API oficial do WhatsApp Business (Meta).")
    if conexao.status != Conexao.CONECTADA:
        raise RegraNegocioError("A conexão não está ativa.")
    return conexao.adaptador, s.credenciais_de(conexao)


def _falha(template, conexao, evento, erro):
    """Guarda o erro (já sem credenciais) e propaga como regra de negócio. O estado de conteúdo/Meta não muda."""
    mensagem = s._mascarar(erro)
    if template is not None:
        T.objects.filter(pk=template.pk).update(ultimo_erro=mensagem)
    s.log(conexao, LogIntegracao.AVISO, evento, mensagem)
    raise RegraNegocioError(mensagem) from None


def _gravar_status_meta(t, resposta):
    """Copia o que a Meta devolveu (status/categoria/motivo) — NUNCA inventa valores."""
    t.status_meta = str(resposta.get("status") or t.status_meta)[:30]
    if resposta.get("category") in dict(T.CATEGORIA_CHOICES):
        t.categoria = resposta["category"]
    t.motivo_rejeicao = str(resposta.get("rejected_reason") or "")[:255] if str(resposta.get("rejected_reason") or "").upper() not in ("", "NONE") else ""
    t.sincronizado_em = timezone.now()


def criar_local(request, conexao, dados):
    """Salva como RASCUNHO (nada é enviado à Meta). Duplicado (mesmo nome+idioma na conexão) é recusado."""
    if conexao.provedor != "whatsapp_cloud":
        raise RegraNegocioError("Templates existem só na API oficial do WhatsApp Business (Meta).")
    limpo = validar_dados(dados)
    if T.objects.filter(conexao=conexao, nome=limpo["nome"], idioma=limpo["idioma"]).exists():
        raise RegraNegocioError("Já existe um template com este nome e idioma nesta conexão.")
    with transaction.atomic():
        t = T.objects.create(conexao=conexao, criado_por=request.user, situacao_local=T.RASCUNHO, **limpo)
        registrar_criacao(request, t, modulo="integracao")
    return t


def editar_local(request, t, dados):
    """Altera o conteúdo aqui. Se já foi enviado, passa a 'alterado' até ser reenviado; a Meta não é consultada."""
    if t.somente_leitura:
        raise RegraNegocioError("Este template tem recursos que o sistema não edita (veja no painel da Meta). Só consulta e exclusão.")
    limpo = validar_dados(dados, parcial_de=t)
    if T.objects.filter(conexao=t.conexao, nome=limpo["nome"], idioma=limpo["idioma"]).exclude(pk=t.pk).exists():
        raise RegraNegocioError("Já existe um template com este nome e idioma nesta conexão.")
    with transaction.atomic():
        foto = tirar_foto(t)
        for campo, valor in limpo.items():
            setattr(t, campo, valor)
        if t.enviado:
            t.situacao_local = T.ALTERADO
        t.save()
        registrar_edicao(request, t, foto, modulo="integracao")
    return t


def enviar_para_meta(request, t):
    """Rascunho -> cria na Meta; já enviado e alterado -> atualiza na Meta. O status volta DA META: depois de criar vem o da resposta; depois de
    atualizar, o template é relido. Falha de rede/validação da Meta mantém tudo como estava."""
    if t.somente_leitura:
        raise RegraNegocioError("Template somente leitura: edite-o no painel da Meta.")
    conexao = t.conexao
    adaptador, credenciais = _conexao_apta(conexao)
    if t.enviado and t.status_meta and t.status_meta not in STATUS_EDITAVEIS:
        raise RegraNegocioError(f"A Meta não aceita alterar um template com status {t.status_meta}. Aguarde a análise ou sincronize.")
    if t.enviado and t.situacao_local != T.ALTERADO:
        raise RegraNegocioError("Não há alterações para enviar.")
    try:
        if not t.enviado:
            resposta = adaptador.criar_template(conexao, credenciais, {"name": t.nome, "language": t.idioma, "category": t.categoria, "components": montar_componentes(t)})
            t.id_meta = str(resposta["id"])[:40]
            _gravar_status_meta(t, resposta)
            acao = "criado"
        else:
            adaptador.atualizar_template(conexao, credenciais, t.id_meta, {"components": montar_componentes(t)})
            _gravar_status_meta(t, adaptador.obter_template(conexao, credenciais, t.id_meta) or {})
            acao = "atualizado"
    except ErroIntegracao as erro:
        _falha(t, conexao, "template", erro)
    t.situacao_local, t.ultimo_erro = T.SINCRONIZADO, ""
    with transaction.atomic():
        t.save()
        registrar(request, acao="editar", modulo="integracao", tabela=t._meta.db_table, registro_id=t.pk, registro_nome=str(t),
                  descricao=f"Template ID {t.pk} - {t} {acao} na Meta (status informado pela Meta: {t.status_meta or 'sem retorno'}).", silencioso=False)
        s.log(conexao, LogIntegracao.INFO, "template", f"Template ID {t.pk} {acao} na Meta.")
    return t


def sincronizar(request, conexao):
    """Traz da Meta todos os templates da conta: atualiza status/motivo (e o conteúdo, se não há edição local pendente) e importa os que só
    existem lá. Devolve {"importados", "atualizados", "ausentes"}. Templates locais já enviados que a Meta não lista ficam marcados com aviso
    (não são apagados: a exclusão é decisão de quem usa)."""
    adaptador, credenciais = _conexao_apta(conexao)
    try:
        remotos = adaptador.listar_templates(conexao, credenciais)
    except ErroIntegracao as erro:
        _falha(None, conexao, "template", erro)
    resumo = {"importados": 0, "atualizados": 0, "ausentes": 0}
    vistos = set()
    agora = timezone.now()
    with transaction.atomic():
        for r in remotos:
            nome, idioma = str(r.get("name") or "")[:512], str(r.get("language") or "")[:10]
            if not nome or not idioma:
                continue
            vistos.add((nome, idioma))
            campos, somente_leitura = ler_componentes(r.get("components"))
            categoria = r.get("category") if r.get("category") in dict(T.CATEGORIA_CHOICES) else T.UTILITY
            if categoria == T.AUTHENTICATION:
                somente_leitura = True
            t = T.objects.filter(conexao=conexao, nome=nome, idioma=idioma).first()
            novo = t is None
            t = t or T(conexao=conexao, nome=nome, idioma=idioma, criado_por=request.user)
            t.id_meta, t.ultimo_erro = str(r.get("id") or t.id_meta)[:40], ""
            if novo or t.situacao_local != T.ALTERADO:  # edição local pendente não é sobrescrita; só o status da Meta é atualizado
                t.categoria, t.somente_leitura, t.situacao_local = categoria, somente_leitura, T.SINCRONIZADO
                for campo, valor in campos.items():
                    setattr(t, campo, valor)
            _gravar_status_meta(t, r)
            t.save()
            resumo["importados" if novo else "atualizados"] += 1
        for t in T.objects.filter(conexao=conexao).exclude(id_meta=""):
            if (t.nome, t.idioma) not in vistos:
                T.objects.filter(pk=t.pk).update(ultimo_erro="Não encontrado na Meta na última sincronização.", sincronizado_em=agora)
                resumo["ausentes"] += 1
        s.log(conexao, LogIntegracao.INFO, "template", f"Sincronização de templates: {resumo['importados']} novo(s), {resumo['atualizados']} atualizado(s).")
        registrar(request, acao="editar", modulo="integracao", tabela=T._meta.db_table, registro_id=conexao.pk, registro_nome=conexao.nome,
                  descricao=f"Templates da conexão ID {conexao.pk} sincronizados com a Meta.", silencioso=False)
    return resumo


def excluir(request, t):
    """Já enviado: exclui NA META (apenas este idioma) e, só com a confirmação dela, apaga o registro local. Nunca enviado: apaga só o local."""
    if t.enviado:
        adaptador, credenciais = _conexao_apta(t.conexao)
        try:
            adaptador.excluir_template(t.conexao, credenciais, t.nome, t.id_meta)
        except ErroIntegracao as erro:
            _falha(t, t.conexao, "template", erro)
    with transaction.atomic():
        registrar(request, acao="excluir", modulo="integracao", tabela=t._meta.db_table, registro_id=t.pk, registro_nome=str(t),
                  descricao=f"Excluído template ID {t.pk} - {t}" + (" (também na Meta)." if t.enviado else " (rascunho local)."), silencioso=False)
        s.log(t.conexao, LogIntegracao.INFO, "template", f"Template ID {t.pk} excluído.")
        t.delete()
