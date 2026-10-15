from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.auditoria.utils import registrar_edicao, tirar_foto
from apps.cliente.models import Cliente
from apps.pdv.models import Venda
from web_project import TemplateLayout
from web_project.acesso import cliente_do_usuario, clientes_dos_itens
from web_project.regras import RegraNegocioError

from . import services as s
from .forms import CertificadoForm, ClienteFiscalForm, ConfiguracaoFiscalForm, SolicitarForm
from .models import ConfiguracaoFiscal, DocumentoFiscal


def documentos_visiveis(user):
    return DocumentoFiscal.objects.filter(cliente__in=clientes_dos_itens(user, "view"))


class ConfiguracaoView(View):
    """Configuração fiscal da unidade do usuário: regime/opção (cadastro do cliente), provedor, inscrições e certificado A1."""

    template = "fiscal_config.html"

    def _contexto(self, request, cliente, configuracao, **forms):
        return TemplateLayout.init(self, {
            "cliente": cliente, "configuracao": configuracao, "prontidao": s.prontidao(cliente),
            "form": forms.get("form") or ConfiguracaoFiscalForm(instance=configuracao), "form_cliente": forms.get("form_cliente") or ClienteFiscalForm(instance=cliente),
            "form_cert": forms.get("form_cert") or CertificadoForm(),
        })

    def get(self, request):
        cliente = cliente_do_usuario(request.user)
        return render(request, self.template, self._contexto(request, cliente, ConfiguracaoFiscal.da_unidade(cliente)))

    def post(self, request):
        cliente = cliente_do_usuario(request.user)
        configuracao = ConfiguracaoFiscal.da_unidade(cliente)
        acao = request.POST.get("acao", "salvar")
        if acao == "certificado":
            form_cert = CertificadoForm(request.POST, request.FILES)
            if form_cert.is_valid():
                try:
                    s.salvar_certificado(request, configuracao, form_cert.cleaned_data["arquivo"].read(), form_cert.cleaned_data["senha"])
                except RegraNegocioError as erro:
                    form_cert.add_error(None, str(erro))
                else:
                    messages.success(request, "Certificado digital guardado com segurança.")
                    return redirect("fiscal-config")
            return render(request, self.template, self._contexto(request, cliente, configuracao, form_cert=form_cert))
        if acao == "remover_certificado":
            s.remover_certificado(request, configuracao)
            messages.success(request, "Certificado removido.")
            return redirect("fiscal-config")
        form, form_cliente = ConfiguracaoFiscalForm(request.POST, instance=configuracao), ClienteFiscalForm(request.POST, instance=cliente)
        if form.is_valid() and form_cliente.is_valid():
            with transaction.atomic():
                # fotos do banco ANTES de gravar (o ModelForm já alterou as instâncias em memória)
                foto_cfg, foto_cli = tirar_foto(ConfiguracaoFiscal.objects.get(pk=configuracao.pk)), tirar_foto(Cliente.objects.get(pk=cliente.pk))
                form.save()
                form_cliente.save()
                registrar_edicao(request, configuracao, foto_cfg, modulo="fiscal")
                registrar_edicao(request, cliente, foto_cli, modulo="fiscal")
            messages.success(request, "Configuração fiscal salva.")
            return redirect("fiscal-config")
        return render(request, self.template, self._contexto(request, cliente, configuracao, form=form, form_cliente=form_cliente))


class DocumentoListView(View):
    def get(self, request):
        consulta = documentos_visiveis(request.user).select_related("venda", "cliente")
        status = request.GET.get("status")
        if status in dict(DocumentoFiscal.STATUS_CHOICES):
            consulta = consulta.filter(status=status)
        pagina = Paginator(consulta, 25).get_page(request.GET.get("page"))
        return render(request, "fiscal_documentos.html", TemplateLayout.init(self, {
            "documentos": pagina, "page_obj": pagina, "status": status or "", "status_opcoes": DocumentoFiscal.STATUS_CHOICES,
            "pode_emitir": request.user.has_perm("fiscal.emitir_nota"),
        }))


class DocumentoView(View):
    def get(self, request, pk):
        documento = get_object_or_404(documentos_visiveis(request.user).select_related("venda", "cliente"), pk=pk)
        return render(request, "fiscal_documento.html", TemplateLayout.init(self, {"documento": documento, "pode_emitir": request.user.has_perm("fiscal.emitir_nota")}))


class SolicitarView(View):
    """POST (a partir da venda): pede o documento fiscal. Idempotente; a venda e o comprovante não fiscal não mudam."""

    def post(self, request, venda_id):
        venda = get_object_or_404(Venda.objects.select_related("cliente"), pk=venda_id, cliente__in=clientes_dos_itens(request.user, "view"))
        form = SolicitarForm(request.POST)
        try:
            if not form.is_valid():
                raise RegraNegocioError("Tipo de documento inválido.")
            documento, criado = s.solicitar_emissao(request, venda, form.cleaned_data["tipo"])
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
            return redirect("pdv-venda", pk=venda.pk)
        messages.info(request, f"Documento fiscal registrado como {documento.get_status_display().lower()}: {documento.motivo or 'sem pendências'}") if criado else messages.info(request, "Já existe um documento fiscal ativo para esta venda.")
        return redirect("fiscal-documento", pk=documento.pk)


class CancelarView(View):
    def post(self, request, pk):
        documento = get_object_or_404(documentos_visiveis(request.user), pk=pk)
        try:
            s.cancelar_documento(request, documento, request.POST.get("motivo", ""))
            messages.success(request, "Documento cancelado.")
        except RegraNegocioError as erro:
            messages.error(request, str(erro))
        return redirect("fiscal-documento", pk=documento.pk)


class ProcessarView(View):
    """POST: processa a fila de pendentes da(s) unidade(s) do usuário agora (o comando `processar_fiscal` faz o mesmo em lote)."""

    def post(self, request):
        resumo = s.processar_pendentes(clientes=clientes_dos_itens(request.user, "view"))
        messages.info(request, f"Fila processada: {resumo['autorizadas']} autorizada(s), {resumo['rejeitadas']} rejeitada(s), {resumo['aguardando']} aguardando condições, {resumo['erros']} com erro.")
        return redirect("fiscal-documentos")
