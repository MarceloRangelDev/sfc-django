"""Telas da precificação de serviços. Segmentos Serviço e Serviço e Produto apenas (os demais recebem 403). Escopo SEMPRE da unidade do
usuário (`clientes_dos_itens`): id de outra unidade = 404. Nada que o navegador manda (totais, status, unidade, retrato) é confiado."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.auditoria.mixins import AuditoriaFormMixin
from apps.auditoria.utils import tirar_foto
from apps.maodeobra.models import segmento_permite
from web_project import TemplateLayout
from web_project.acesso import clientes_dos_itens
from web_project.mixins import ClienteScopedMixin, ExcluirOuDesativarView, ListaPaginadaMixin, ListaPorUnidadeMixin, SalvarEAdicionarMixin
from web_project.regras import RegraNegocioError

from . import services as s
from .forms import ConfiguracaoForm, DatasForm, DespesaForm, LancamentoForm, ModeloForm, PagamentoForm, PropostaForm, linhas_do_json
from .models import ConfiguracaoServico, DespesaFixa, Lancamento, ModeloServico, Pagamento, Proposta, PropostaCusto


class SegmentoMixin:
    """O módulo só existe para empresas que precificam serviço (Serviço e Serviço e Produto): as demais recebem 403, mesmo pela URL."""

    def dispatch(self, request, *args, **kwargs):
        cliente = request.user.profile.cliente if request.user.is_authenticated else None
        if cliente is not None and not segmento_permite(cliente):
            raise PermissionDenied("O segmento da sua empresa não usa a precificação de serviços.")
        return super().dispatch(request, *args, **kwargs)


def _unidade(request):
    cliente = request.user.profile.cliente
    if cliente is None:
        raise PermissionDenied
    return cliente


def _pagina(view, request, template, contexto):
    return render(request, template, TemplateLayout.init(view, contexto))


def _proposta(request, pk, acao="view"):
    return get_object_or_404(Proposta.objects.select_related("cliente", "consumidor"), pk=pk, cliente__in=clientes_dos_itens(request.user, acao))


# ---------------------------------------------------------------------------------------------------------------------------
# Configuração e custo-hora
# ---------------------------------------------------------------------------------------------------------------------------

class ConfiguracaoView(SegmentoMixin, View):
    """Premissas da PRÓPRIA unidade + composição do custo-hora (valores calculados, separados dos informados)."""

    def _contexto(self, request, form, cfg):
        cliente = cfg.cliente
        try:
            capacidade, custo = s.calcular_custo_hora(cliente, cfg)
            # Valores em centavos (o filtro |moeda); horas faturáveis ficam de fora por não serem dinheiro.
            custo = {k: round(v) for k, v in custo.items() if k != "horas_faturaveis"}
            erro = None
        except RegraNegocioError as e:
            capacidade = custo = None
            erro = str(e)
        despesas = DespesaFixa.objects.filter(cliente=cliente, ativo=True)
        return {"form": form, "cfg": cfg, "capacidade": capacidade, "custo": custo, "erro_calculo": erro, "despesas_ativas": despesas.count(),
                "pode_alterar": request.user.has_perm("precificacaoservico.change_configuracaoservico")}

    def get(self, request):
        cfg = ConfiguracaoServico.da_unidade(_unidade(request))
        return _pagina(self, request, "ps_config.html", self._contexto(request, ConfiguracaoForm(instance=cfg), cfg))

    def post(self, request):
        if not request.user.has_perm("precificacaoservico.change_configuracaoservico"):
            raise PermissionDenied
        cfg = ConfiguracaoServico.da_unidade(_unidade(request))
        foto = tirar_foto(cfg)  # antes do formulário alterar a instância
        form = ConfiguracaoForm(request.POST, instance=cfg)
        if form.is_valid():
            try:
                s.salvar_configuracao(request, form.save(commit=False), foto)
            except RegraNegocioError as e:
                form.add_error(None, str(e))
            else:
                messages.success(request, "Configuração salva. Propostas já feitas mantêm as premissas da época.")
                return redirect("ps-config")
        return _pagina(self, request, "ps_config.html", self._contexto(request, form, cfg))


# ---------------------------------------------------------------------------------------------------------------------------
# Despesas e provisões
# ---------------------------------------------------------------------------------------------------------------------------

class _Lista(SegmentoMixin, ListaPaginadaMixin, ListaPorUnidadeMixin, ListView):
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class _Form(SegmentoMixin):
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class DespesaListView(_Lista):
    model = DespesaFixa
    template_name = "ps_despesa.html"
    context_object_name = "despesas"
    colunas_ordenaveis = {"descricao": "descricao", "valor": "valor"}
    ordenacao_padrao = "descricao"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(descricao__icontains=termo)
        return self.ordenar(self.filtrar_status(queryset))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Total mensal das despesas ATIVAS da própria unidade (o que entra no custo-hora).
        context["total_mensal"] = {}
        try:
            despesas, provisoes = s.totais_de_despesas(_unidade(self.request))
            context["total_mensal"] = {"despesas": round(despesas), "provisoes": round(provisoes)}
        except RegraNegocioError:
            pass
        return context


class DespesaCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = DespesaFixa, DespesaForm, "ps_despesa_form.html"
    success_url = reverse_lazy("ps-despesa-list")
    acao_escopo, nome_url_novo = "add", "ps-despesa-create"

    def form_valid(self, form):
        messages.success(self.request, "Despesa cadastrada.")
        return super().form_valid(form)


class DespesaUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = DespesaFixa, DespesaForm, "ps_despesa_form.html"
    success_url = reverse_lazy("ps-despesa-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Despesa atualizada. Propostas já feitas mantêm o custo-hora da época.")
        return super().form_valid(form)


class DespesaExcluirView(SegmentoMixin, ExcluirOuDesativarView):
    model, url_lista, rotulo, artigo = DespesaFixa, "ps-despesa-list", "despesa", "a"


# ---------------------------------------------------------------------------------------------------------------------------
# Modelos de serviço
# ---------------------------------------------------------------------------------------------------------------------------

class ModeloListView(_Lista):
    model = ModeloServico
    template_name = "ps_modelo.html"
    context_object_name = "modelos"
    colunas_ordenaveis = {"nome": "nome", "categoria": "categoria"}

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            queryset = queryset.filter(Q(nome__icontains=termo) | Q(categoria__icontains=termo))
        return self.ordenar(self.filtrar_status(queryset))


class ModeloCreateView(_Form, SalvarEAdicionarMixin, AuditoriaFormMixin, ClienteScopedMixin, CreateView):
    model, form_class, template_name = ModeloServico, ModeloForm, "ps_modelo_form.html"
    success_url = reverse_lazy("ps-modelo-list")
    acao_escopo, nome_url_novo = "add", "ps-modelo-create"

    def form_valid(self, form):
        messages.success(self.request, "Modelo cadastrado.")
        return super().form_valid(form)


class ModeloUpdateView(_Form, AuditoriaFormMixin, ClienteScopedMixin, UpdateView):
    model, form_class, template_name = ModeloServico, ModeloForm, "ps_modelo_form.html"
    success_url = reverse_lazy("ps-modelo-list")
    acao_escopo = "change"

    def form_valid(self, form):
        messages.success(self.request, "Modelo atualizado. Propostas criadas a partir dele não mudam.")
        return super().form_valid(form)


class ModeloExcluirView(SegmentoMixin, ExcluirOuDesativarView):
    model, url_lista, rotulo, artigo = ModeloServico, "ps-modelo-list", "modelo", "o"


# ---------------------------------------------------------------------------------------------------------------------------
# Propostas
# ---------------------------------------------------------------------------------------------------------------------------

class PropostaListView(_Lista):
    model = Proposta
    template_name = "ps_proposta.html"
    context_object_name = "propostas"
    colunas_ordenaveis = {"numero": "numero", "cliente": "destinatario_nome", "total": "total", "criado_em": "criado_em"}
    ordenacao_padrao = "numero"
    direcao_padrao = "desc"
    status_padrao = "todos"

    def get_queryset(self):
        queryset = super().get_queryset()
        termo = self.request.GET.get("q", "").strip()
        if termo:
            filtro = Q(destinatario_nome__icontains=termo) | Q(descricao__icontains=termo)
            if termo.isdigit():
                filtro |= Q(numero=int(termo))
            queryset = queryset.filter(filtro)
        situacao = self.request.GET.get("situacao")
        if situacao in dict(Proposta.STATUS_CHOICES):
            queryset = queryset.filter(status=situacao)
        return self.ordenar(queryset)

    def filtrar_status(self, queryset):  # a situação da proposta é outro filtro (`situacao`); não há "ativo"
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(situacoes=Proposta.STATUS_CHOICES, situacao=self.request.GET.get("situacao", ""))
        return context


def _linhas_iniciais(proposta):
    """Itens e custos salvos -> JSON da tela (valores em reais, como o usuário digita)."""
    from web_project.regras.dinheiro import DinheiroService

    def reais(c):
        return None if c is None else DinheiroService.formatar(c).replace("R$ ", "")

    itens = [{"descricao": i.descricao, "modo": i.modo, "quantidade": f"{i.quantidade.normalize():f}".replace(".", ","), "unidade": i.unidade,
              "horas_por_unidade": None if i.horas_por_unidade is None else f"{i.horas_por_unidade.normalize():f}".replace(".", ","), "preco_unitario": reais(i.preco_unitario)}
             for i in proposta.itens.all()]
    custos = [{"descricao": k.descricao, "categoria": k.categoria, "quantidade": f"{k.quantidade.normalize():f}".replace(".", ","), "unidade": k.unidade,
               "custo_unitario": reais(k.custo_unitario), "repasse": k.repasse} for k in proposta.custos.all()]
    return itens, custos


class PropostaFormView(SegmentoMixin, View):
    """Criar (pk=None) e editar. Só rascunho/enviada é editável. `?modelo=<id>` inicia a proposta com um item do modelo (valores copiados)."""

    def _render(self, request, form, itens, custos, proposta=None, erro_calculo=None):
        import json

        return _pagina(self, request, "ps_proposta_form.html", {
            "form": form, "proposta": proposta, "itens_json": json.dumps(itens), "custos_json": json.dumps(custos), "erro_calculo": erro_calculo,
            "categorias_json": json.dumps(PropostaCusto.CATEGORIA_CHOICES), "repasses_json": json.dumps(PropostaCusto.REPASSE_CHOICES),
            "modos_json": json.dumps(Proposta._meta.get_field("modo").choices),
        })

    def get(self, request, pk=None):
        if pk is None:
            cliente = _unidade(request)
            inicial, itens = {}, []
            modelo_id = request.GET.get("modelo", "")
            modelo = ModeloServico.objects.filter(pk=modelo_id, cliente=cliente, ativo=True).first() if modelo_id.isdigit() else None
            if modelo:
                inicial = {"modo": modelo.modo, "descricao": modelo.nome, "escopo_incluido": modelo.descricao, "margem_pct": modelo.margem_pct}
                itens = [{"descricao": modelo.nome, "modo": modelo.modo, "quantidade": "1", "unidade": modelo.unidade,
                          "horas_por_unidade": None if modelo.horas_por_unidade is None else str(modelo.horas_por_unidade).replace(".", ","),
                          "preco_unitario": None if modelo.preco_base is None else f"{modelo.preco_base // 100},{modelo.preco_base % 100:02d}"}]
            return self._render(request, PropostaForm(cliente=cliente, initial=inicial), itens, [])
        proposta = _proposta(request, pk, "change")
        if not proposta.editavel:
            messages.error(request, "Esta proposta já foi fechada e não pode ser alterada.")
            return redirect("ps-proposta-detalhe", pk=proposta.pk)
        itens, custos = _linhas_iniciais(proposta)
        return self._render(request, PropostaForm(instance=proposta, cliente=proposta.cliente), itens, custos, proposta)

    def post(self, request, pk=None):
        proposta = _proposta(request, pk, "change") if pk else None
        if proposta is not None and not proposta.editavel:
            messages.error(request, "Esta proposta já foi fechada e não pode ser alterada.")
            return redirect("ps-proposta-detalhe", pk=proposta.pk)
        cliente = proposta.cliente if proposta else _unidade(request)
        if proposta is None and not request.user.has_perm("precificacaoservico.add_proposta"):
            raise PermissionDenied
        foto = tirar_foto(proposta) if proposta else None
        form = PropostaForm(request.POST, instance=proposta, cliente=cliente)
        itens_json, custos_json = request.POST.get("itens_json", "[]"), request.POST.get("custos_json", "[]")
        erro = None
        if form.is_valid():
            novo = form.save(commit=False)
            if proposta is None:
                novo.cliente, novo.criado_por = cliente, request.user  # dono e autor vêm do usuário autenticado
            try:
                itens, custos = linhas_do_json(itens_json, custos_json)
                s.salvar_proposta(request, novo, itens, custos, foto_antes=foto, atualizar_premissas="atualizar_premissas" in request.POST)
            except RegraNegocioError as e:
                erro = str(e)
                form.add_error(None, erro)
            else:
                messages.success(request, "Proposta salva.")
                return redirect("ps-proposta-detalhe", pk=novo.pk)
        try:
            import json

            itens_l, custos_l = json.loads(itens_json), json.loads(custos_json)
        except ValueError:
            itens_l, custos_l = [], []
        return self._render(request, form, itens_l, custos_l, proposta, erro)


class PropostaCalcularView(SegmentoMixin, View):
    """Prévia do cálculo (JSON) enquanto o usuário digita: usa o mesmo serviço da gravação e NÃO grava nada. Em proposta existente usa o retrato
    dela; em proposta nova, as premissas de hoje."""

    def post(self, request):
        cliente = _unidade(request)
        existente = None
        if request.POST.get("proposta", "").isdigit():
            existente = _proposta(request, int(request.POST["proposta"]))
        form = PropostaForm(request.POST, cliente=cliente, instance=existente)
        if not form.is_valid():
            return JsonResponse({"ok": False, "erros": {k: [str(m) for m in v] for k, v in form.errors.items()}}, status=422)
        try:
            itens, custos = linhas_do_json(request.POST.get("itens_json"), request.POST.get("custos_json"))
            itens, custos = s.normalizar_linhas(itens, custos)
            resultado, premissas = s.simular(cliente, form.save(commit=False), itens, custos, existente.premissas if existente and existente.premissas else None)
        except RegraNegocioError as e:
            return JsonResponse({"ok": False, "erros": {"__all__": [str(e)]}}, status=422)
        return JsonResponse({"ok": True, "resultado": resultado, "custo_hora": premissas["custo_hora_total"], "versao": premissas["versao_configuracao"]})


class PropostaDetalheView(SegmentoMixin, View):
    def get(self, request, pk):
        proposta = _proposta(request, pk)
        escopo_alterar = set(clientes_dos_itens(request.user, "change").values_list("pk", flat=True))
        pode_alterar = request.user.has_perm("precificacaoservico.change_proposta") and proposta.cliente_id in escopo_alterar
        em_execucao = proposta.status in Proposta.EM_EXECUCAO
        return _pagina(self, request, "ps_proposta_detalhe.html", {
            "proposta": proposta, "r": proposta.resultado, "p": proposta.premissas, "itens": proposta.itens.all(), "custos": proposta.custos.all(),
            "pode_alterar": pode_alterar, "pode_editar": pode_alterar and proposta.editavel,
            "pode_excluir": request.user.has_perm("precificacaoservico.delete_proposta") and proposta.status == Proposta.RASCUNHO,
            "transicoes": [(n, dict(Proposta.STATUS_CHOICES)[n]) for n in Proposta.TRANSICOES.get(proposta.status, ())] if pode_alterar else [],
            "em_execucao": em_execucao, "rel": s.relatorio(proposta) if em_execucao else None,
            "lancamentos": proposta.lancamentos.all(), "pagamentos": proposta.pagamentos.all(),
            "form_lancamento": LancamentoForm(), "form_pagamento": PagamentoForm(),
            "form_datas": DatasForm(initial={"data_inicio": proposta.data_inicio, "data_conclusao": proposta.data_conclusao}),
        })


class PropostaImprimirView(SegmentoMixin, View):
    """Versão legível para imprimir ou salvar como PDF pelo navegador. Respeita `detalhar_custos`; custos internos, lucro e margem não saem."""

    def get(self, request, pk):
        proposta = _proposta(request, pk)
        return _pagina(self, request, "ps_proposta_imprimir.html", {
            "proposta": proposta, "r": proposta.resultado, "empresa": proposta.cliente, "itens": proposta.itens.all(),
            "custos_discriminados": proposta.custos.filter(repasse="discriminado") if proposta.detalhar_custos else [],
        })


class PropostaAcaoView(SegmentoMixin, View):
    """POST: mudar situação | excluir (rascunho) | atualizar premissas | datas | lançamentos e pagamentos (criar/excluir)."""

    def post(self, request, pk, acao):
        proposta = _proposta(request, pk, "delete" if acao == "excluir" else "change")
        permissao = "delete" if acao == "excluir" else "change"
        if not request.user.has_perm(f"precificacaoservico.{permissao}_proposta"):
            raise PermissionDenied
        try:
            if acao == "excluir":
                s.excluir_proposta(request, proposta)
                messages.success(request, "Rascunho excluído.")
                return redirect("ps-proposta-list")
            if acao == "status":
                s.mudar_status(request, proposta, request.POST.get("novo", ""), request.POST.get("aceite_nome", "").strip())
                messages.success(request, "Situação atualizada.")
            elif acao == "premissas":
                if not proposta.editavel:
                    raise RegraNegocioError("Esta proposta já foi fechada e não pode ser alterada.")
                itens, custos = s.linhas_salvas(proposta)
                s.salvar_proposta(request, proposta, itens, custos, foto_antes=tirar_foto(proposta), atualizar_premissas=True)
                messages.success(request, "Premissas atualizadas para os valores de hoje e a proposta foi recalculada.")
            elif acao == "datas":
                form = DatasForm(request.POST)
                if form.is_valid():
                    s.atualizar_datas(request, proposta, form.cleaned_data["data_inicio"], form.cleaned_data["data_conclusao"])
                    messages.success(request, "Datas atualizadas.")
                else:
                    raise RegraNegocioError("Datas inválidas.")
            elif acao == "lancamento":
                form = LancamentoForm(request.POST)
                if not form.is_valid():
                    raise RegraNegocioError("Confira os dados do lançamento: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
                s.registrar_lancamento(request, proposta, form.cleaned_data)
                messages.success(request, "Lançamento registrado.")
            elif acao == "pagamento":
                form = PagamentoForm(request.POST)
                if not form.is_valid():
                    raise RegraNegocioError("Confira os dados do pagamento: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
                s.registrar_pagamento(request, proposta, form.cleaned_data)
                messages.success(request, "Pagamento registrado.")
            else:
                raise Http404
        except RegraNegocioError as e:
            messages.error(request, str(e))
        return redirect("ps-proposta-detalhe", pk=proposta.pk)


class RegistroExcluirView(SegmentoMixin, View):
    """POST: exclui um lançamento ou pagamento da proposta (conferindo que ele é da unidade e que o usuário pode alterar a proposta)."""

    def post(self, request, pk, tipo, registro_id):
        modelo = {"lancamento": Lancamento, "pagamento": Pagamento}.get(tipo)
        if modelo is None:
            raise Http404
        if not request.user.has_perm("precificacaoservico.change_proposta"):
            raise PermissionDenied
        proposta = _proposta(request, pk, "change")
        registro = get_object_or_404(modelo, pk=registro_id, proposta=proposta)
        try:
            s.excluir_registro(request, registro)
            messages.success(request, "Registro excluído.")
        except RegraNegocioError as e:
            messages.error(request, str(e))
        return redirect("ps-proposta-detalhe", pk=proposta.pk)
