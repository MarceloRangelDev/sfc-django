"""Recursos de cadastro da API v1: produtos, canais de venda, custos operacionais, processados, fichas
técnicas e precificações. Validação e gravação reaproveitam formulários e serviços das telas (ver negocio.py).
Dinheiro SEMPRE em centavos inteiros; quantidades e percentuais como texto decimal com ponto."""
from decimal import Decimal

from django.db.models import Prefetch, Q
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers

from apps.canal.forms import CanalForm
from apps.canal.models import CanalTaxa, CanalVenda
from apps.canal.services import salvar_canal
from apps.custo.forms import CustoForm
from apps.custo.models import TIPO_CHOICES, CustoOperacional
from apps.custo.services import salvar_custo
from apps.ficha.forms import FichaForm, ItemFormSet as FichaItemFormSet
from apps.ficha.models import Ficha, FichaItem
from apps.ficha.services import salvar_ficha
from apps.precificacao.forms import PrecificacaoForm
from apps.precificacao.models import Precificacao
from apps.precificacao.services import salvar_precificacao
from apps.processado.forms import ItemFormSet as ProcessadoItemFormSet, ProcessadoForm
from apps.processado.models import Processado, ProcessadoItem
from apps.processado.services import excluir_processado, salvar_processado
from apps.produto.forms import ProdutoForm
from apps.produto.models import Produto
from web_project.regras.dinheiro import DinheiroService

from .negocio import RecursoViewSet, dados_do_formset, documentar, erros_de
from .views import resposta

QUANTIDADE = dict(max_digits=15, decimal_places=4, min_value=Decimal("0.0001"))


def _moeda(**extra):
    """Campo de dinheiro da API: inteiro em centavos (R$ 4,00 = 400)."""
    return serializers.IntegerField(min_value=0, max_value=10**12, **extra)


def _formatar(centavos):
    return DinheiroService.formatar(centavos)


# ===================================================================================================
# Produtos
# ===================================================================================================

class ProdutoSerializer(serializers.ModelSerializer):
    categoria_nome = serializers.CharField(source="categoria.nome", read_only=True)

    class Meta:
        model = Produto
        fields = ["id", "nome", "descricao", "categoria", "categoria_nome", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class ProdutoEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    categoria = serializers.IntegerField(help_text="Id da categoria ATIVA da sua empresa (ver /categorias/).")
    descricao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ativo = serializers.BooleanField(required=False)


@documentar("produtos", ProdutoSerializer, ProdutoEntrada, [OpenApiParameter("categoria", int), OpenApiParameter("ordenar", str, description="nome, criado_em (prefixo - = decrescente)")])
class ProdutoViewSet(RecursoViewSet):
    """Produtos da empresa do usuário. DELETE apaga o que não tem uso e apenas DESATIVA o que já tem fichas/precificações."""

    modelo, serializer_class, entrada_class, form_class = Produto, ProdutoSerializer, ProdutoEntrada, ProdutoForm
    permissao_app, permissao_model, modulo_slug = "produto", "produto", "produto"
    campos_busca = ("nome", "descricao")
    rotulo = "Produto"

    def consulta_base(self):
        return Produto.objects.select_related("categoria")

    def filtrar(self, queryset, params):
        queryset = super().filtrar(queryset, params)
        return queryset.filter(categoria_id=params["categoria"]) if params.get("categoria", "").isdigit() else queryset

    def dados_atuais(self, p):
        return {"nome": p.nome, "categoria": p.categoria_id, "descricao": p.descricao, "ativo": p.ativo}


# ===================================================================================================
# Canais de venda
# ===================================================================================================

class TaxaEntrada(serializers.Serializer):
    descricao = serializers.CharField(max_length=100, required=False, allow_blank=True)
    percentual = serializers.DecimalField(max_digits=7, decimal_places=4, min_value=0, max_value=Decimal("99.9999"), required=False, allow_null=True)
    valor_fixo = _moeda(required=False, allow_null=True, help_text="Taxa fixa por venda, em centavos.")

    def validate(self, dados):
        if (dados.get("percentual") is None) == (dados.get("valor_fixo") is None):
            raise serializers.ValidationError("Informe o percentual OU o valor fixo da taxa (apenas um dos dois).")
        return dados


class TaxaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CanalTaxa
        fields = ["id", "descricao", "percentual", "valor_fixo"]
        read_only_fields = fields


class CanalSerializer(serializers.ModelSerializer):
    taxas = TaxaSerializer(many=True, read_only=True)
    liquido_percentual = serializers.DecimalField(max_digits=7, decimal_places=4, read_only=True, help_text="100% menos as taxas percentuais.")

    class Meta:
        model = CanalVenda
        fields = ["id", "nome", "descricao", "taxa_percentual_total", "taxa_fixa_total", "liquido_percentual", "taxas", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CanalEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    descricao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ativo = serializers.BooleanField(required=False)
    taxas = TaxaEntrada(many=True, required=False, help_text="Substitui TODAS as taxas do canal (omitida na edição = mantém).")


@documentar("canais-venda", CanalSerializer, CanalEntrada)
class CanalViewSet(RecursoViewSet):
    """Canais de venda e suas taxas. Alterar as taxas refaz as precificações que usam o canal; se alguma ficasse
    inviável (margem + taxas ≥ 100%) a edição é recusada. DELETE apaga o canal sem uso ou apenas o desativa."""

    modelo, serializer_class, entrada_class, form_class = CanalVenda, CanalSerializer, CanalEntrada, CanalForm
    permissao_app, permissao_model, modulo_slug = "canal", "canalvenda", "canal-venda"
    campos_busca = ("nome", "descricao")
    colunas_ordenaveis = {"nome": "nome", "taxas": "taxa_percentual_total", "criado_em": "criado_em"}
    rotulo = "Canal de venda"

    def consulta_base(self):
        return CanalVenda.objects.prefetch_related("taxas")

    def dados_atuais(self, c):
        taxas = [{"descricao": t.descricao, "percentual": t.percentual, "valor_fixo": t.valor_fixo} for t in c.taxas.all()]
        return {"nome": c.nome, "descricao": c.descricao, "ativo": c.ativo, "taxas": taxas}

    def gravar(self, request, contexto, dados, instancia, foto):
        canal = contexto["form"].instance
        if instancia is None:
            canal.cliente = self.cliente()
        taxas = [CanalTaxa(descricao=t.get("descricao", ""), percentual=t.get("percentual"), valor_fixo=t.get("valor_fixo")) for t in dados.get("taxas", [])]
        return salvar_canal(request, canal, taxas, foto_antes=foto)  # audita e refaz as precificações


# ===================================================================================================
# Custos operacionais
# ===================================================================================================

class CustoSerializer(serializers.ModelSerializer):
    valor_formatado = serializers.CharField(read_only=True)
    modelo_nome = serializers.CharField(source="modelo.nome", read_only=True, default=None)

    class Meta:
        model = CustoOperacional
        fields = ["id", "nome", "tipo", "valor", "valor_formatado", "descricao", "modelo", "modelo_nome", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class CustoEntrada(serializers.Serializer):
    modelo = serializers.IntegerField(required=False, allow_null=True, help_text="Id de um custo comum (ver /custos-comuns/); usa o tipo dele.")
    nome = serializers.CharField(max_length=150, required=False, allow_blank=True, help_text="Em branco com um custo comum: usa o nome dele.")
    tipo = serializers.ChoiceField(choices=TIPO_CHOICES, required=False)
    valor = _moeda(help_text="Valor MENSAL em centavos (R$ 350,00 = 35000).")
    descricao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    ativo = serializers.BooleanField(required=False)


@documentar("custos-operacionais", CustoSerializer, CustoEntrada, [OpenApiParameter("tipo", str, enum=["fixo", "variavel"]), OpenApiParameter("ordenar", str, description="nome, tipo, valor, criado_em")])
class CustoViewSet(RecursoViewSet):
    """Custos operacionais (fixos e variáveis, mensais). A distribuição por categoria é calculada sozinha
    (ver /custo-operacional/categorias/). Criar, editar, ativar/desativar ou apagar um custo refaz as precificações automáticas."""

    modelo, serializer_class, entrada_class, form_class = CustoOperacional, CustoSerializer, CustoEntrada, CustoForm
    permissao_app, permissao_model, modulo_slug = "custo", "custooperacional", "custo-operacional"
    campos_busca = ("nome", "descricao")
    colunas_ordenaveis = {"nome": "nome", "tipo": "tipo", "valor": "valor", "criado_em": "criado_em"}
    campos_moeda = ("valor",)
    padroes = {"ativo": True, "tipo": "fixo", "nome": "", "modelo": None, "descricao": ""}
    rotulo = "Custo operacional"

    def consulta_base(self):
        return CustoOperacional.objects.select_related("modelo")

    def filtrar(self, queryset, params):
        queryset = super().filtrar(queryset, params)
        return queryset.filter(tipo=params["tipo"]) if params.get("tipo") in dict(TIPO_CHOICES) else queryset

    def dados_atuais(self, c):
        return {"modelo": c.modelo_id, "nome": c.nome, "tipo": c.tipo, "valor": c.valor, "descricao": c.descricao, "ativo": c.ativo}

    def gravar(self, request, contexto, dados, instancia, foto):
        custo = contexto["form"].instance
        if instancia is None:
            custo.cliente = self.cliente()
        return salvar_custo(request, custo, foto_antes=foto)


# ===================================================================================================
# Processados e fichas técnicas (itens)
# ===================================================================================================

class ItemEntrada(serializers.Serializer):
    insumo = serializers.IntegerField(help_text="Id de um insumo ATIVO da sua empresa (ver /insumos/).")
    medida = serializers.IntegerField(help_text="Id da unidade em que a quantidade foi informada (ver /medidas/).")
    quantidade = serializers.DecimalField(**QUANTIDADE, help_text='Quantidade usada, com ponto decimal (ex.: "0.5").')


class ItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    insumo = serializers.IntegerField(source="insumo_id")
    insumo_nome = serializers.CharField(source="insumo.nome")
    medida = serializers.IntegerField(source="medida_id")
    medida_nome = serializers.CharField(source="medida.medida")
    quantidade = serializers.DecimalField(max_digits=15, decimal_places=4)
    custo = serializers.IntegerField(help_text="Custo desta quantidade, em centavos (calculado).")


def _itens_atuais(itens):
    return [{"insumo": i.insumo_id, "medida": i.medida_id, "quantidade": i.quantidade} for i in itens]


class ProcessadoSerializer(serializers.ModelSerializer):
    rendimento_medida_nome = serializers.CharField(source="rendimento_medida.medida", read_only=True)
    custo_total_formatado = serializers.CharField(read_only=True)
    insumo_gerado = serializers.IntegerField(source="insumo_id", read_only=True, help_text="Id do insumo criado a partir do processado (1 unidade da maior grandeza).")
    itens = ItemSerializer(many=True, read_only=True)

    class Meta:
        model = Processado
        fields = [
            "id", "nome", "rendimento_quantidade", "rendimento_medida", "rendimento_medida_nome", "custo_total", "custo_total_formatado",
            "fator_rendimento", "insumo_gerado", "itens", "ativo", "criado_em", "atualizado_em",
        ]
        read_only_fields = fields


class ProcessadoEntrada(serializers.Serializer):
    nome = serializers.CharField(max_length=150)
    rendimento_quantidade = serializers.DecimalField(**QUANTIDADE)
    rendimento_medida = serializers.IntegerField(help_text="Id da unidade de saída (massa, volume, unidade ou comprimento).")
    ativo = serializers.BooleanField(required=False)
    itens = ItemEntrada(many=True, help_text="Ficha técnica: substitui TODOS os itens (ao menos um; sem insumo repetido).")


@documentar("processados", ProcessadoSerializer, ProcessadoEntrada)
class ProcessadoViewSet(RecursoViewSet):
    """Processados/receitas com a ficha técnica de insumos. O custo, o fator de rendimento e o insumo gerado são calculados
    no servidor e refeitos em cascata quando um insumo muda. DELETE é lógico: inativa o processado e o insumo gerado
    (recusado se outra receita usa o insumo gerado)."""

    modelo, serializer_class, entrada_class, form_class = Processado, ProcessadoSerializer, ProcessadoEntrada, ProcessadoForm
    permissao_app, permissao_model, modulo_slug = "processado", "processado", "processado"
    colunas_ordenaveis = {"nome": "nome", "custo": "custo_total", "criado_em": "criado_em"}
    rotulo = "Processado"

    def consulta_base(self):
        return Processado.objects.select_related("rendimento_medida").prefetch_related(Prefetch("itens", ProcessadoItem.objects.select_related("insumo", "medida")))

    def dados_atuais(self, p):
        return {
            "nome": p.nome, "rendimento_quantidade": p.rendimento_quantidade, "rendimento_medida": p.rendimento_medida_id,
            "ativo": p.ativo, "itens": _itens_atuais(p.itens.all()),
        }

    def preparar(self, dados, instancia):
        contexto = super().preparar(dados, instancia)
        # formset sobre uma instância NOVA (sem itens gravados): a gravação substitui todos os itens, e o
        # insumo gerado fica de fora das opções para não criar ciclo direto.
        base = Processado(cliente=instancia.cliente if instancia is not None else self.cliente(), insumo_id=getattr(instancia, "insumo_id", None))
        formset = ProcessadoItemFormSet(dados_do_formset(dados.get("itens", []), ("insumo", "medida", "quantidade")), instance=base, cliente=base.cliente)
        if not formset.is_valid():
            raise serializers.ValidationError(erros_de(contexto["form"], formset))
        contexto["formset"] = formset
        return contexto

    def gravar(self, request, contexto, dados, instancia, foto):
        processado = contexto["form"].instance
        if instancia is None:
            processado.cliente = self.cliente()
        itens = [ProcessadoItem(insumo=f.cleaned_data["insumo"], medida=f.cleaned_data["medida"], quantidade=f.cleaned_data["quantidade"]) for f in contexto["formset"].itens_validos()]
        return salvar_processado(request, processado, itens, foto_antes=foto)

    def destroy(self, request, *args, **kwargs):
        from rest_framework.response import Response

        excluir_processado(request, self.get_object())
        return Response(status=204)


class FichaSerializer(serializers.ModelSerializer):
    produto_nome = serializers.CharField(source="produto.nome", read_only=True)
    custo_total_formatado = serializers.CharField(read_only=True)
    itens = ItemSerializer(many=True, read_only=True)

    class Meta:
        model = Ficha
        fields = ["id", "nome", "produto", "produto_nome", "custo_total", "custo_total_formatado", "itens", "ativo", "criado_em", "atualizado_em"]
        read_only_fields = fields


class FichaEntrada(serializers.Serializer):
    produto = serializers.IntegerField(help_text="Id de um produto ATIVO da sua empresa (ver /produtos/). A ficha usa o nome do produto.")
    ativo = serializers.BooleanField(required=False)
    itens = ItemEntrada(many=True, help_text="Substitui TODOS os itens (ao menos um; sem insumo repetido).")


@documentar("fichas-tecnicas", FichaSerializer, FichaEntrada, [OpenApiParameter("produto", int)])
class FichaViewSet(RecursoViewSet):
    """Fichas técnicas dos produtos (insumos e quantidades). O custo é calculado no servidor e refeito quando um insumo muda.
    DELETE apaga a ficha sem precificações; com precificações apenas a desativa."""

    modelo, serializer_class, entrada_class, form_class = Ficha, FichaSerializer, FichaEntrada, FichaForm
    permissao_app, permissao_model, modulo_slug = "ficha", "ficha", "ficha"
    campos_busca = ("nome", "produto__nome")
    colunas_ordenaveis = {"nome": "nome", "produto": "produto__nome", "custo": "custo_total", "criado_em": "criado_em"}
    rotulo = "Ficha técnica"

    def consulta_base(self):
        return Ficha.objects.select_related("produto").prefetch_related(Prefetch("itens", FichaItem.objects.select_related("insumo", "medida")))

    def filtrar(self, queryset, params):
        queryset = super().filtrar(queryset, params)
        return queryset.filter(produto_id=params["produto"]) if params.get("produto", "").isdigit() else queryset

    def dados_atuais(self, f):
        return {"produto": f.produto_id, "ativo": f.ativo, "itens": _itens_atuais(f.itens.all())}

    def preparar(self, dados, instancia):
        contexto = super().preparar(dados, instancia)
        base = Ficha(cliente=instancia.cliente if instancia is not None else self.cliente())
        formset = FichaItemFormSet(dados_do_formset(dados.get("itens", []), ("insumo", "medida", "quantidade")), instance=base, cliente=base.cliente)
        if not formset.is_valid():
            raise serializers.ValidationError(erros_de(contexto["form"], formset))
        contexto["formset"] = formset
        return contexto

    def gravar(self, request, contexto, dados, instancia, foto):
        ficha = contexto["form"].instance
        if instancia is None:
            ficha.cliente = self.cliente()
        itens = [FichaItem(insumo=f.cleaned_data["insumo"], medida=f.cleaned_data["medida"], quantidade=f.cleaned_data["quantidade"]) for f in contexto["formset"].itens_validos()]
        return salvar_ficha(request, ficha, itens, foto_antes=foto)


# ===================================================================================================
# Precificações
# ===================================================================================================

class PrecificacaoSerializer(serializers.ModelSerializer):
    produto_nome = serializers.CharField(source="produto.nome", read_only=True)
    ficha_nome = serializers.CharField(source="ficha.nome", read_only=True)
    canal_descricao_completa = serializers.CharField(source="nome_do_canal", read_only=True)
    preco_venda_formatado = serializers.CharField(read_only=True)

    class Meta:
        model = Precificacao
        fields = [
            "id", "nome", "ficha", "ficha_nome", "produto", "produto_nome", "canal", "canal_nome", "canal_descricao", "canal_descricao_completa",
            "taxa_percentual", "taxa_fixa", "custo_operacional", "custo_operacional_manual", "metodo_lucro", "percentual_lucro", "valor_lucro",
            "custo_ficha", "custo_total", "taxas_percentuais_valor", "lucro", "preco_venda", "preco_venda_formatado", "cmv_percentual",
            "ativo", "criado_em", "atualizado_em",
        ]
        read_only_fields = fields


class PrecificacaoEntrada(serializers.Serializer):
    ficha = serializers.IntegerField(help_text="Id de uma ficha técnica ATIVA da sua empresa (ver /fichas-tecnicas/).")
    canal = serializers.IntegerField(required=False, allow_null=True, help_text="Id de um canal de venda cadastrado; null = canal personalizado.")
    canal_nome = serializers.CharField(max_length=150, required=False, allow_blank=True, help_text="Obrigatório sem canal cadastrado.")
    canal_descricao = serializers.CharField(max_length=255, required=False, allow_blank=True)
    taxa_percentual = serializers.DecimalField(max_digits=7, decimal_places=4, min_value=0, max_value=Decimal("99.9999"), required=False, allow_null=True)
    taxa_fixa = _moeda(required=False, allow_null=True, help_text="Taxas em reais, em centavos.")
    custo_operacional = _moeda(required=False, allow_null=True, help_text="Em centavos por unidade. Omitido/null = automático (distribuição do Custo operacional); outro valor = ajuste manual.")
    metodo_lucro = serializers.ChoiceField(choices=Precificacao.METODO_CHOICES)
    percentual_lucro = serializers.DecimalField(max_digits=9, decimal_places=4, min_value=0, required=False, allow_null=True, help_text="Margem ou markup (%).")
    valor_lucro = _moeda(required=False, allow_null=True, help_text="Lucro em reais, em centavos (método 'valor').")
    ativo = serializers.BooleanField(required=False)


@documentar("precificacoes", PrecificacaoSerializer, PrecificacaoEntrada, [OpenApiParameter("produto", int), OpenApiParameter("canal", int), OpenApiParameter("metodo", str, enum=["margem", "markup", "valor"]), OpenApiParameter("ordenar", str, description="produto, canal, preco, lucro, cmv, criado_em")])
class PrecificacaoViewSet(RecursoViewSet):
    """Precificações (custo da ficha + custo operacional + taxas do canal + lucro = preço de venda), calculadas pelo servidor com a mesma
    fórmula das telas e refeitas quando a ficha, o canal ou os custos mudam. DELETE apaga a precificação."""

    modelo, serializer_class, entrada_class, form_class = Precificacao, PrecificacaoSerializer, PrecificacaoEntrada, PrecificacaoForm
    permissao_app, permissao_model, modulo_slug = "precificacao", "precificacao", "precificacao"
    campos_busca = ("ficha__nome", "produto__nome", "canal_nome")
    colunas_ordenaveis = {"produto": "produto__nome", "canal": "canal_nome", "preco": "preco_venda", "lucro": "lucro", "cmv": "cmv_percentual", "criado_em": "criado_em"}
    ordenacao_padrao = ["produto__nome", "pk"]
    campos_moeda = ("taxa_fixa", "custo_operacional", "valor_lucro")
    padroes = {"ativo": True, "canal": None, "canal_nome": "", "canal_descricao": "", "taxa_percentual": None, "taxa_fixa": None,
               "custo_operacional": None, "percentual_lucro": None, "valor_lucro": None}
    rotulo = "Precificação"

    def consulta_base(self):
        return Precificacao.objects.select_related("produto", "ficha")

    def filtrar(self, queryset, params):
        queryset = super().filtrar(queryset, params)
        if params.get("produto", "").isdigit():
            queryset = queryset.filter(produto_id=params["produto"])
        if params.get("canal", "").isdigit():
            queryset = queryset.filter(canal_id=params["canal"])
        if params.get("metodo") in dict(Precificacao.METODO_CHOICES):
            queryset = queryset.filter(metodo_lucro=params["metodo"])
        return queryset

    def dados_atuais(self, p):
        return {
            "ficha": p.ficha_id, "canal": p.canal_id, "canal_nome": "" if p.canal_id else p.canal_nome, "canal_descricao": "" if p.canal_id else p.canal_descricao,
            "taxa_percentual": None if p.canal_id else p.taxa_percentual, "taxa_fixa": None if p.canal_id else p.taxa_fixa,
            "custo_operacional": p.custo_operacional if p.custo_operacional_manual else None,  # automático continua automático
            "metodo_lucro": p.metodo_lucro, "percentual_lucro": p.percentual_lucro, "valor_lucro": p.valor_lucro, "ativo": p.ativo,
        }

    def gravar(self, request, contexto, dados, instancia, foto):
        precificacao = contexto["form"].instance
        if instancia is None:
            precificacao.cliente = self.cliente()
        return salvar_precificacao(request, precificacao, foto_antes=foto)  # calcula, grava e audita
