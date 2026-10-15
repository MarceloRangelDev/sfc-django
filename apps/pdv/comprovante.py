"""Comprovante NÃO FISCAL da venda: página HTML imprimível (qualquer dispositivo), PDF e ESC/POS.

Formatos: `termica80` (80 mm, 48 colunas), `termica58` (58 mm, 32 colunas) e `a4`. Os três usam os mesmos dados
(`dados_comprovante`), então o conteúdo é idêntico. ESC/POS é o texto cru para impressoras térmicas (via agente de
impressão/app que aceite bytes brutos); o layout foi gerado e testado por software, NÃO em hardware.
O comprovante não tem valor fiscal e diz isso; a emissão fiscal é outro fluxo (apps/fiscal).
"""
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.cliente.validators import formatar_documento
from apps.pdvconfig.models import ConfiguracaoPdv
from web_project.regras.dinheiro import DinheiroService

FORMATOS = {"termica80": 48, "termica58": 32, "a4": 64}
LARGURAS_MM = {"termica80": 80, "termica58": 58}
AVISO = "COMPROVANTE NAO FISCAL - SEM VALOR FISCAL"
CORTE = b"\x1d\x56\x42\x00"  # GS V 66 0: corte parcial com avanço


def _reais(centavos):
    return DinheiroService.formatar(centavos)


def dados_comprovante(venda):
    """Tudo que o comprovante mostra, já formatado (nenhum dado sensível além do necessário: o consumidor aparece só pelo nome)."""
    configuracao = ConfiguracaoPdv.da_unidade(venda.cliente)
    itens = [
        {"descricao": i.descricao, "quantidade": f"{i.quantidade.normalize():f}".replace(".", ","), "preco": _reais(i.preco_unitario), "total": _reais(i.total), "observacao": i.observacao}
        for i in venda.itens.all()
    ]
    pagamentos = [
        {"forma": p.forma_nome, "valor": _reais(p.valor), "recebido": _reais(p.valor_recebido) if p.valor_recebido else "", "pagador": p.pagador}
        for p in venda.pagamentos.all()
    ]
    cliente = venda.cliente
    return {
        "empresa": cliente.nome_fantasia or cliente.nome_razao_social, "documento": formatar_documento(cliente.cpf_cnpj, cliente.tipo_pessoa),
        "numero": venda.numero, "emitido_em": timezone.localtime(venda.finalizada_em or venda.criado_em).strftime("%d/%m/%Y %H:%M"),
        "operador": (venda.criado_por.get_full_name() or venda.criado_por.get_username()) if venda.criado_por_id else "",
        "consumidor": venda.consumidor.nome if venda.consumidor_id else "", "mesa": venda.mesa.nome if venda.mesa_id else "",
        "itens": itens, "pagamentos": pagamentos, "subtotal": _reais(venda.subtotal), "desconto": _reais(venda.desconto) if venda.desconto else "",
        "total": _reais(venda.total), "troco": _reais(venda.troco) if venda.troco else "", "cancelada": venda.status == venda.CANCELADA,
        "aberta": venda.status == venda.ABERTA, "rodape": configuracao.rodape_comprovante, "aviso": AVISO, "formato_padrao": configuracao.formato_comprovante,
    }


def _duas_colunas(esquerda, direita, largura):
    espaco = max(1, largura - len(esquerda) - len(direita))
    if len(esquerda) + len(direita) >= largura:
        esquerda = esquerda[: max(1, largura - len(direita) - 1)]
        espaco = max(1, largura - len(esquerda) - len(direita))
    return esquerda + " " * espaco + direita


def linhas_texto(dados, largura):
    """Comprovante em texto de largura fixa (base do PDF e do ESC/POS)."""
    traco = "-" * largura
    linhas = [dados["empresa"][:largura].center(largura), dados["documento"].center(largura), traco, dados["aviso"][:largura].center(largura), traco]
    linhas.append(_duas_colunas(f"Venda #{dados['numero']}", dados["emitido_em"], largura))
    if dados["mesa"]:
        linhas.append(f"Mesa: {dados['mesa']}"[:largura])
    if dados["consumidor"]:
        linhas.append(f"Cliente: {dados['consumidor']}"[:largura])
    if dados["operador"]:
        linhas.append(f"Operador: {dados['operador']}"[:largura])
    if dados["cancelada"]:
        linhas.append("*** VENDA CANCELADA ***".center(largura))
    if dados["aberta"]:
        linhas.append("*** CONTA ABERTA - NAO PAGA ***".center(largura))
    linhas.append(traco)
    for item in dados["itens"]:
        linhas.append(item["descricao"][:largura])
        linhas.append(_duas_colunas(f"  {item['quantidade']} x {item['preco']}", item["total"], largura))
        if item["observacao"]:
            linhas.append(f"  obs: {item['observacao']}"[:largura])
    linhas.append(traco)
    linhas.append(_duas_colunas("Subtotal", dados["subtotal"], largura))
    if dados["desconto"]:
        linhas.append(_duas_colunas("Desconto", "-" + dados["desconto"], largura))
    linhas.append(_duas_colunas("TOTAL", dados["total"], largura))
    for pagamento in dados["pagamentos"]:
        rotulo = pagamento["forma"] + (f" ({pagamento['pagador']})" if pagamento["pagador"] else "")
        linhas.append(_duas_colunas(rotulo, pagamento["valor"], largura))
        if pagamento["recebido"]:
            linhas.append(_duas_colunas("  Recebido", pagamento["recebido"], largura))
    if dados["troco"]:
        linhas.append(_duas_colunas("Troco", dados["troco"], largura))
    if dados["rodape"]:
        linhas += [traco, dados["rodape"][:largura].center(largura)]
    return linhas


def resposta_pdf(dados, formato):
    buffer = BytesIO()
    largura = FORMATOS[formato]
    linhas = linhas_texto(dados, largura)
    if formato == "a4":
        c = canvas.Canvas(buffer, pagesize=A4)
        tamanho, y = 10, A4[1] - 25 * mm
        c.setFont("Courier", tamanho)
        for linha in linhas:
            if y < 20 * mm:
                c.showPage()
                c.setFont("Courier", tamanho)
                y = A4[1] - 25 * mm
            c.drawString(25 * mm, y, linha)
            y -= tamanho + 3
    else:
        # Fonte monoespaçada cabendo na largura útil; altura da página proporcional ao número de linhas (um recibo contínuo).
        pagina_l = LARGURAS_MM[formato] * mm
        tamanho = (pagina_l - 6 * mm) / (largura * 0.6)
        altura = len(linhas) * (tamanho + 2) + 10 * mm
        c = canvas.Canvas(buffer, pagesize=(pagina_l, altura))
        c.setFont("Courier", tamanho)
        y = altura - 5 * mm - tamanho
        for linha in linhas:
            c.drawString(3 * mm, y, linha)
            y -= tamanho + 2
    c.setTitle(f"Venda {dados['numero']}")
    c.save()
    resposta = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    resposta["Content-Disposition"] = f'inline; filename="venda-{dados["numero"]}.pdf"'
    return resposta


def resposta_escpos(dados, formato):
    """Bytes ESC/POS (cp850) prontos para uma impressora térmica; `Content-Disposition: attachment` (.prn)."""
    largura = FORMATOS["termica58" if formato == "termica58" else "termica80"]
    corpo = b"\x1b@" + b"\x1bt\x02"  # inicializa; página de códigos 850
    texto = "\n".join(linhas_texto(dados, largura)) + "\n\n\n"
    corpo += texto.encode("cp850", errors="replace") + CORTE
    resposta = HttpResponse(corpo, content_type="application/octet-stream")
    resposta["Content-Disposition"] = f'attachment; filename="venda-{dados["numero"]}.prn"'
    return resposta
