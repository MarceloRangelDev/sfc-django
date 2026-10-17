"""Estimativa de custo do R2 e das Image Transformations — EXCLUSIVA do proprietário da plataforma.

É uma ESTIMATIVA calculada com os contadores da própria aplicação e as tarifas cadastradas (TarifaImagens). Não é a fatura da Cloudflare:
a fatura real depende do plano, de acessos feitos fora da aplicação, de cache e de arredondamentos da conta. Limites conhecidos:
- armazenamento = soma dos originais ainda no R2 ao FIM do mês (a Cloudflare cobra a média mensal dos picos diários);
- operações = o que a aplicação executou (upload = Classe A; leitura do original = Classe B; DeleteObject é gratuito);
- transformações únicas só existem no modo de entrega `cloudflare` (primeira entrega do mês de cada imagem+variante); no modo `local`
  as variantes são geradas pelo servidor e NÃO há cobrança de transformação;
- 1 GB = 10^9 bytes (convenção da Cloudflare); cada unidade faturável começada é cobrada inteira, depois de descontada a franquia.
"""
import math
from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Imagem, TarifaImagens, TransformacaoUnica, UsoImagens


def _fim_do_mes(ano, mes):
    proximo = date(ano + (mes == 12), mes % 12 + 1, 1)
    return timezone.make_aware(datetime.combine(proximo, time.min))


def _faturavel(uso, franquia, preco):
    """Unidades cobradas = ceil(uso - franquia), nunca negativo (uso e franquia já estão na unidade de cobrança)."""
    excedente = max(Decimal(0), Decimal(uso) - franquia)
    unidades = math.ceil(excedente)
    return unidades, Decimal(unidades) * preco


def relatorio(ano, mes):
    """Indicadores globais do mês. Só totais da plataforma (sem detalhe por tenant)."""
    inicio = timezone.make_aware(datetime(ano, mes, 1))
    fim = _fim_do_mes(ano, mes)
    tarifas = {t.chave: t for t in TarifaImagens.objects.all()}

    # Originais que ocupavam o R2 ao fim do mês: criados antes do fim e não removidos (ou removidos depois do fim).
    no_r2 = Imagem.objects.filter(criado_em__lt=fim).exclude(estado=Imagem.PENDENTE).filter(Q(removida_em__isnull=True) | Q(removida_em__gte=fim))
    armazenado = no_r2.aggregate(bytes=Sum("tamanho"), objetos=Count("id"))
    bytes_total = armazenado["bytes"] or 0
    uso = {m["metrica"]: m["total"] for m in UsoImagens.objects.filter(dia__gte=inicio.date(), dia__lt=fim.date()).values("metrica").annotate(total=Sum("quantidade"))}
    transformacoes = TransformacaoUnica.objects.filter(mes=f"{ano:04d}-{mes:02d}").count()

    linhas = []

    def linha(chave, rotulo, quantidade, unidade, em_unidade_cobranca):
        t = tarifas.get(chave)
        if t is None:
            linhas.append({"rotulo": rotulo, "quantidade": quantidade, "unidade": unidade, "custo": None})
            return
        unidades, custo = _faturavel(em_unidade_cobranca, t.franquia, t.preco)
        linhas.append({
            "rotulo": rotulo, "quantidade": quantidade, "unidade": unidade, "franquia": t.franquia, "preco": t.preco, "moeda": t.moeda,
            "unidades_cobradas": unidades, "custo": custo, "fonte": t.fonte, "consultado_em": t.consultado_em,
        })

    gb = Decimal(bytes_total) / Decimal(10**9)
    linha("armazenamento", "R2 - armazenamento (fim do mês)", f"{gb:.4f}", "GB", gb)
    linha("classe_a", "R2 - operações Classe A (escritas)", uso.get("classe_a", 0), "operações", Decimal(uso.get("classe_a", 0)) / Decimal(10**6))
    linha("classe_b", "R2 - operações Classe B (leituras)", uso.get("classe_b", 0), "operações", Decimal(uso.get("classe_b", 0)) / Decimal(10**6))
    linha("transformacao", "Images - transformações únicas (estimadas)", transformacoes, "transformações", Decimal(transformacoes) / Decimal(1000))
    moedas = {l["moeda"] for l in linhas if l["custo"] is not None}
    total = sum((l["custo"] for l in linhas if l["custo"] is not None), Decimal(0))
    return {
        "periodo": f"{ano:04d}-{mes:02d}", "atualizado_em": timezone.now(), "linhas": linhas, "total": total.quantize(Decimal("0.01")),
        "moeda": moedas.pop() if len(moedas) == 1 else "mista", "objetos": armazenado["objetos"], "bytes": bytes_total,
        "imagens_ativas": Imagem.objects.filter(estado=Imagem.ATIVA).count(),
    }
