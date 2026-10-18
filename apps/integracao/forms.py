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
    if adaptador.sincroniza_agenda:
        campos["cfg_incluir_valores"] = forms.BooleanField(required=False, label="Incluir valores em R$ nos títulos dos vencimentos (dado financeiro vai para o provedor)", widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    campos["notificar_usuarios"] = forms.BooleanField(required=False, initial=True, label="Notificar a equipe quando chegar mensagem", widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    return type("ConexaoForm", (forms.Form,), campos)


def separar(adaptador, dados):
    """cleaned_data do formulário dinâmico -> (credenciais, config)."""
    credenciais = {n: dados.get(f"cred_{n}", "") for n, _, _ in adaptador.campos_credenciais if dados.get(f"cred_{n}")}
    config = {n: dados.get(f"cfg_{n}", "") for n, _ in adaptador.campos_config}
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

    def save(self, commit=True):
        # Mudou o início ou a antecedência: o lembrete volta a valer (será enviado de novo no novo horário).
        if self.instance.pk and ({"inicio", "lembrete_minutos"} & set(self.changed_data)):
            self.instance.lembrete_enviado_em = None
        return super().save(commit)

    def clean(self):
        dados = super().clean()
        if dados.get("inicio") and dados.get("fim") and dados["fim"] < dados["inicio"]:
            self.add_error("fim", "O fim não pode ser anterior ao início.")
        return dados
