"""Exportação da cotação para PDF (também serve para imprimir) e Excel.

O conteúdo é o comparativo: uma linha por oferta (item x loja) com o preço aplicado, o total e o preço normalizado
(R$ por KG/L/UN), marcando o menor preço e o vencedor escolhido; depois o resumo dos vencedores por loja.
O chamador entrega uma cotação JÁ dentro do escopo (tenant/permissão) do usuário. Texto do usuário é escapado (PDF)
ou neutralizado contra fórmulas (Excel).
"""
from apps.auditoria.utils import registrar as registrar_auditoria
from web_project.exportacao import exportar_excel, exportar_pdf, sanitizar_texto_excel
from web_project.regras.dinheiro import DinheiroService

from .services import comparativo, resumo_escolhidos

CABECALHO = ["Item", "Qtd. do item", "Loja", "Marca", "Qtd. ofertada", "Preço aplicado", "Tipo", "Total", "Preço por KG/L/UN", "Situação", "Observações"]


def _qtd(valor, sigla):
    return f"{valor.normalize():f} {sigla}".replace(".", ",")


def linhas_da_cotacao(cotacao):
    dados = comparativo(cotacao)
    linhas = []
    for bloco in dados:
        item = bloco["item"]
        if not bloco["ofertas"]:
            linhas.append([item.descricao, _qtd(item.quantidade, item.medida.sigla), "—", "", "", "", "", "", "", "Sem preços", item.observacao])
        for oferta in bloco["ofertas"]:
            preco = oferta["preco"]
            situacao = "VENCEDOR" if preco.escolhido else ("Menor preço" if oferta["melhor"] else ("Não atende" if not oferta["atende"] else ""))
            observacoes = " | ".join(t for t in (preco.observacao, oferta["motivo"]) if t)
            linhas.append([
                item.descricao, _qtd(item.quantidade, item.medida.sigla), preco.loja.nome, preco.marca, _qtd(preco.quantidade, preco.medida.sigla),
                f"{DinheiroService.formatar(int(oferta['valor_unitario']))} / {preco.medida.sigla}", oferta["tipo"].capitalize(),
                DinheiroService.formatar(oferta["total"]), f"{DinheiroService.formatar(oferta['por_base_centavos'])} / {oferta['unidade_base']}",
                situacao, observacoes,
            ])
    return linhas, dados


def _resumo(dados):
    blocos, total = resumo_escolhidos(dados)
    linhas = [[b["loja"].nome, len(b["linhas"]), DinheiroService.formatar(b["total"])] for b in blocos]
    return linhas, DinheiroService.formatar(total)


def exportar_cotacao(request, cotacao, formato):
    linhas, dados = linhas_da_cotacao(cotacao)
    resumo, total = _resumo(dados)
    registrar_auditoria(
        request, acao="exportar", modulo="cotacao", tabela=cotacao._meta.db_table, registro_id=str(cotacao.pk), registro_nome=cotacao.titulo,
        descricao=f"Exportação da cotação ID {cotacao.pk} - {cotacao.titulo} em {formato}.", silencioso=False,
    )
    nome = f"cotacao-{cotacao.pk}"
    if formato == "xlsx":
        planilha = [[sanitizar_texto_excel(c) if isinstance(c, str) else c for c in linha] for linha in linhas]
        planilha += [[], [sanitizar_texto_excel("RESUMO DOS VENCEDORES")], ["Loja", "Itens", "Total"]]
        planilha += [[sanitizar_texto_excel(loja), n, valor] for loja, n, valor in resumo]
        planilha += [["Total geral", "", total]]
        if cotacao.observacoes:
            planilha += [[], [sanitizar_texto_excel("Observações gerais")], [sanitizar_texto_excel(cotacao.observacoes)]]
        return exportar_excel(nome, "Cotação", CABECALHO, planilha)
    textos = []
    if resumo:
        textos.append(("Resumo dos vencedores", "\n".join(f"{loja}: {n} item(ns) — {valor}" for loja, n, valor in resumo) + f"\nTotal geral: {total}"))
    if cotacao.observacoes:
        textos.append(("Observações gerais", cotacao.observacoes))
    return exportar_pdf(nome, f"Cotação: {cotacao.titulo}", request.user, CABECALHO, linhas, f"status={cotacao.get_status_display()}", textos)
