from datetime import datetime, time

from django import forms
from django.utils import timezone

from .models import Compromisso

CONTROLE = {"class": "form-control"}


def formulario_conexao(adaptador, editando=False):
    """Formulário dinâmico da conexão manual, montado a partir dos campos que o ADAPTADOR declara. Campos secretos usam
    `PasswordInput` SEM valor inicial (nunca são exibidos); ao editar, vazio = manter o atual."""

    campos = {"nome": forms.CharField(max_length=80, label="Nome da conexão", widget=forms.TextInput(attrs=CONTROLE))}
    for nome, rotulo, secreto in adaptador.campos_credenciais:
        widget = forms.PasswordInput(attrs={**CONTROLE, "autocomplete": "new-password"}, render_value=False) if secreto else forms.TextInput(attrs=CONTROLE)
        campos[f"cred_{nome}"] = forms.CharField(label=rotulo, required=not editando, widget=widget, max_length=2000,
                                                 help_text="Deixe vazio para manter o atual." if editando and secreto else "")
    for nome, rotulo in adaptador.campos_config:
        campos[f"cfg_{nome}"] = forms.CharField(label=rotulo, required=not editando, max_length=300, widget=forms.TextInput(attrs=CONTROLE))
    for nome, rotulo in adaptador.campos_config_opcionais:
        campos[f"cfg_{nome}"] = forms.CharField(label=rotulo, required=False, max_length=300, widget=forms.TextInput(attrs=CONTROLE))
    if adaptador.sincroniza_agenda:
        campos["cfg_incluir_valores"] = forms.BooleanField(required=False, label="Incluir valores em R$ nos títulos dos vencimentos (dado financeiro vai para o provedor)", widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    campos["notificar_usuarios"] = forms.BooleanField(required=False, initial=True, label="Notificar a equipe quando chegar mensagem", widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    return type("ConexaoForm", (forms.Form,), campos)


def separar(adaptador, dados):
    """cleaned_data do formulário dinâmico -> (credenciais, config)."""
    credenciais = {n: dados.get(f"cred_{n}", "") for n, _, _ in adaptador.campos_credenciais if dados.get(f"cred_{n}")}
    config = {n: dados.get(f"cfg_{n}", "") for n, _ in (*adaptador.campos_config, *adaptador.campos_config_opcionais)}
    if "cfg_incluir_valores" in dados:
        config["incluir_valores"] = "1" if dados["cfg_incluir_valores"] else "0"
    return credenciais, config


class MensagemForm(forms.Form):
    contato = forms.CharField(max_length=30, label="Número/ID do contato", widget=forms.TextInput(attrs={**CONTROLE, "inputmode": "tel"}))
    texto = forms.CharField(max_length=2000, label="Mensagem", widget=forms.Textarea(attrs={**CONTROLE, "rows": 3, "maxlength": 2000}))


LEMBRETES = [("", "Sem lembrete"), ("0", "Na hora"), ("15", "15 minutos antes"), ("60", "1 hora antes"), ("1440", "1 dia antes")]


class CompromissoForm(forms.ModelForm):
    lembrete_minutos = forms.TypedChoiceField(label="Lembrete", required=False, choices=LEMBRETES, coerce=int, empty_value=None, widget=forms.Select(attrs={"class": "form-select"}))
    inicio = forms.DateTimeField(label="Início", input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"], widget=forms.DateTimeInput(attrs={**CONTROLE, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    fim = forms.DateTimeField(label="Fim", input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"], widget=forms.DateTimeInput(attrs={**CONTROLE, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))

    class Meta:
        model = Compromisso
        fields = ["tipo", "titulo", "descricao", "inicio", "fim", "todo_o_dia", "lembrete_minutos", "concluido", "ativo"]
        widgets = {
            "tipo": forms.Select(attrs={"class": "form-select"}), "titulo": forms.TextInput(attrs=CONTROLE), "descricao": forms.TextInput(attrs={**CONTROLE, "maxlength": 500}),
            "todo_o_dia": forms.CheckboxInput(attrs={"class": "form-check-input"}), "concluido": forms.CheckboxInput(attrs={"class": "form-check-input"}), "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cliente=None, unidades=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cliente = self.instance.cliente if self.instance.pk else cliente

    def clean_titulo(self):
        titulo = " ".join((self.cleaned_data.get("titulo") or "").split())
        if not titulo:
            raise forms.ValidationError("Informe o título.")
        return titulo

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get("inicio"), dados.get("fim")
        if inicio is None or fim is None:
            return dados
        if dados.get("todo_o_dia"):
            # Dia inteiro: só valem as DATAS. Guardamos sempre meia-noite local no início e 23:59 local no fim, assim a data nunca "anda" por
            # causa do fuso e o evento sincroniza com o dia certo (ver Compromisso._dia_inteiro).
            fuso = timezone.get_current_timezone()
            primeiro, ultimo = timezone.localtime(inicio, fuso).date(), timezone.localtime(fim, fuso).date()
            inicio = timezone.make_aware(datetime.combine(primeiro, time.min), fuso)
            fim = timezone.make_aware(datetime.combine(ultimo, time(23, 59)), fuso)
            dados["inicio"], dados["fim"] = inicio, fim
        if fim < inicio:
            self.add_error("fim", "O fim não pode ser anterior ao início.")
        return dados

    def save(self, commit=True):
        # Mudou o início ou a antecedência: o lembrete volta a valer (será enviado de novo no novo horário).
        if self.instance.pk and ({"inicio", "lembrete_minutos"} & set(self.changed_data)):
            self.instance.lembrete_enviado_em = None
        return super().save(commit)


BOTOES_NA_TELA = 3  # a tela oferece 3 botões; a validação (e a API) aceitam até 10


class TemplateWhatsAppForm(forms.Form):
    """Só coleta e converte os campos da tela. A validação de verdade (formato do nome, variáveis, limites da Meta) é feita no servidor por
    `templates_whatsapp.validar_dados`, a mesma da API."""

    conexao = forms.ChoiceField(label="Conexão (WhatsApp oficial)", widget=forms.Select(attrs={"class": "form-select"}))
    nome = forms.CharField(label="Nome do template", max_length=512, help_text="Minúsculas, números e sublinhado. Ex.: pedido_confirmado", widget=forms.TextInput(attrs=CONTROLE))
    idioma = forms.ChoiceField(label="Idioma", widget=forms.Select(attrs={"class": "form-select"}))
    categoria = forms.ChoiceField(label="Categoria", choices=[("MARKETING", "Marketing"), ("UTILITY", "Utilidade")], widget=forms.Select(attrs={"class": "form-select"}))
    cabecalho = forms.CharField(label="Cabeçalho (texto, opcional)", max_length=60, required=False, widget=forms.TextInput(attrs=CONTROLE))
    exemplo_cabecalho = forms.CharField(label="Exemplo da variável do cabeçalho", max_length=200, required=False, widget=forms.TextInput(attrs=CONTROLE))
    corpo = forms.CharField(label="Corpo", max_length=1024, widget=forms.Textarea(attrs={**CONTROLE, "rows": 5}), help_text="Use {{1}}, {{2}}... para variáveis (não no início nem no fim).")
    exemplos_corpo = forms.CharField(label="Exemplos das variáveis do corpo (um por linha)", required=False, widget=forms.Textarea(attrs={**CONTROLE, "rows": 3}))
    rodape = forms.CharField(label="Rodapé (opcional)", max_length=60, required=False, widget=forms.TextInput(attrs=CONTROLE))

    def __init__(self, *args, conexoes=(), travado=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["conexao"].choices = [(c.pk, c.nome) for c in conexoes]
        from .templates_whatsapp import IDIOMAS

        self.fields["idioma"].choices = IDIOMAS
        if travado:  # já enviado à Meta: conexão, nome, idioma e categoria não mudam
            for nome in ("conexao", "nome", "idioma", "categoria"):
                self.fields[nome].disabled = True
        for i in range(1, BOTOES_NA_TELA + 1):
            self.fields[f"botao{i}_tipo"] = forms.ChoiceField(required=False, label=f"Botão {i}", widget=forms.Select(attrs={"class": "form-select"}),
                                                              choices=[("", "— sem botão —"), ("QUICK_REPLY", "Resposta rápida"), ("URL", "Abrir site (URL https)"), ("PHONE_NUMBER", "Ligar (telefone)")])
            self.fields[f"botao{i}_texto"] = forms.CharField(required=False, max_length=25, label="Texto do botão", widget=forms.TextInput(attrs=CONTROLE))
            self.fields[f"botao{i}_valor"] = forms.CharField(required=False, max_length=2000, label="URL ou telefone (+DDI)", widget=forms.TextInput(attrs=CONTROLE))
            self.fields[f"botao{i}_exemplo"] = forms.CharField(required=False, max_length=200, label="Exemplo da variável da URL", widget=forms.TextInput(attrs=CONTROLE))

    def dados(self):
        """cleaned_data -> dicionário no formato que `templates_whatsapp.validar_dados` espera."""
        d = self.cleaned_data
        botoes = []
        for i in range(1, BOTOES_NA_TELA + 1):
            tipo = d.get(f"botao{i}_tipo")
            if not tipo:
                continue
            botao = {"tipo": tipo, "texto": d.get(f"botao{i}_texto", "")}
            if tipo == "URL":
                botao.update(url=d.get(f"botao{i}_valor", ""), exemplo=d.get(f"botao{i}_exemplo", ""))
            elif tipo == "PHONE_NUMBER":
                botao["telefone"] = d.get(f"botao{i}_valor", "")
            botoes.append(botao)
        return {
            "nome": d.get("nome"), "idioma": d.get("idioma"), "categoria": d.get("categoria"), "cabecalho": d.get("cabecalho"), "corpo": d.get("corpo"), "rodape": d.get("rodape"),
            "botoes": botoes, "exemplos": {"cabecalho": d.get("exemplo_cabecalho"), "corpo": [l for l in (d.get("exemplos_corpo") or "").splitlines() if l.strip()]},
        }

    @staticmethod
    def inicial(t):
        """Template salvo -> valores iniciais do formulário."""
        inicial = {"conexao": t.conexao_id, "nome": t.nome, "idioma": t.idioma, "categoria": t.categoria, "cabecalho": t.cabecalho, "corpo": t.corpo, "rodape": t.rodape,
                   "exemplo_cabecalho": t.exemplos.get("cabecalho", ""), "exemplos_corpo": "\n".join(t.exemplos.get("corpo", []))}
        for i, b in enumerate(t.botoes[:BOTOES_NA_TELA], 1):
            inicial.update({f"botao{i}_tipo": b["tipo"], f"botao{i}_texto": b["texto"], f"botao{i}_valor": b.get("url") or b.get("telefone") or "", f"botao{i}_exemplo": b.get("exemplo", "")})
        return inicial
