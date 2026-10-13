from django import forms

from apps.modulo.models import Segmento
from web_project.acesso import clientes_acessiveis, usuarios_acessiveis

from .models import Notificacao


class NotificacaoForm(forms.ModelForm):
    """Criação de notificação. Segmentos e usuários oferecidos (e aceitos)
    são só os da estrutura do solicitante: o queryset do campo é o filtro de
    segurança, não a tela."""

    segmentos = forms.ModelMultipleChoiceField(
        queryset=Segmento.objects.none(), required=False, label="Segmentos",
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 5}),
    )
    usuarios = forms.ModelMultipleChoiceField(
        queryset=None, required=False, label="Usuários",
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
    )

    class Meta:
        model = Notificacao
        fields = ["titulo", "mensagem", "link", "tipo_destinatario"]
        widgets = {
            "titulo": forms.TextInput(attrs={"class": "form-control"}),
            "mensagem": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "link": forms.TextInput(attrs={"class": "form-control", "placeholder": "/insumo"}),
            "tipo_destinatario": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, solicitante, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitante = solicitante
        self.fields["tipo_destinatario"].choices = Notificacao.DESTINO_CHOICES  # sem opção em branco
        self.fields["tipo_destinatario"].initial = Notificacao.DESTINO_TODOS
        self.fields["segmentos"].queryset = Segmento.objects.filter(
            ativo=True, clientes__in=clientes_acessiveis(solicitante)
        ).distinct()
        self.fields["usuarios"].queryset = usuarios_acessiveis(solicitante).filter(
            is_active=True, profile__cliente__isnull=False
        ).order_by("username")

    def clean_link(self):
        link = self.cleaned_data["link"].strip()
        # Só caminho interno ("/insumo"): "//site.com" e "http://..." viram redirecionamento externo.
        if link and (not link.startswith("/") or link.startswith("//") or "\\" in link):
            raise forms.ValidationError("Informe um caminho interno do sistema, começando com '/'.")
        return link

    def clean(self):
        dados = super().clean()
        tipo = dados.get("tipo_destinatario")
        if tipo == Notificacao.DESTINO_SEGMENTO and not dados.get("segmentos"):
            self.add_error("segmentos", "Selecione ao menos um segmento.")
        if tipo == Notificacao.DESTINO_USUARIOS and not dados.get("usuarios"):
            self.add_error("usuarios", "Selecione ao menos um usuário.")
        return dados
