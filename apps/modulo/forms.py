from django import forms
from django.contrib.auth.models import Permission
from django.urls import NoReverseMatch, reverse

from .models import Modulo, Segmento

CONTROLE = {"class": "form-control"}


class ModuloForm(forms.ModelForm):
    """Cadastro de módulo. Valida `rota` e `permissao` contra o que realmente
    existe: uma rota inválida quebraria o menu de todas as telas."""

    class Meta:
        model = Modulo
        fields = ["nome", "slug", "descricao", "icone", "rota", "app", "permissao", "secao", "ordem",
                  "essencial", "delegavel", "ativo", "instrucoes"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "slug": forms.TextInput(attrs=CONTROLE),
            "descricao": forms.TextInput(attrs=CONTROLE), "icone": forms.TextInput(attrs=CONTROLE),
            "rota": forms.TextInput(attrs=CONTROLE), "app": forms.TextInput(attrs=CONTROLE),
            "permissao": forms.TextInput(attrs=CONTROLE), "secao": forms.Select(attrs={"class": "form-select"}),
            "ordem": forms.NumberInput(attrs=CONTROLE),
            "essencial": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "delegavel": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "instrucoes": forms.Textarea(attrs={**CONTROLE, "rows": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["slug"].disabled = True  # identificador estável, usado no código

    def clean_rota(self):
        rota = self.cleaned_data["rota"].strip()
        if rota:
            try:
                reverse(rota)
            except NoReverseMatch:
                raise forms.ValidationError("Esta rota não existe no urls.py (ou exige parâmetros).") from None
        return rota

    def clean_permissao(self):
        permissao = self.cleaned_data["permissao"].strip()
        if permissao:
            app, _, codigo = permissao.partition(".")
            if not Permission.objects.filter(content_type__app_label=app, codename=codigo).exists():
                raise forms.ValidationError("Permissão inexistente. Use o formato 'app.view_modelo'.")
            if not codigo.startswith("view_") and self.data.get("delegavel"):
                raise forms.ValidationError("Use a permissão 'view_' do modelo (as demais ações são derivadas dela).")
        return permissao


class SegmentoForm(forms.ModelForm):
    class Meta:
        model = Segmento
        fields = ["nome", "slug", "descricao", "tipo_precificacao", "modulos", "ativo"]
        widgets = {
            "nome": forms.TextInput(attrs=CONTROLE), "slug": forms.TextInput(attrs=CONTROLE),
            "descricao": forms.TextInput(attrs=CONTROLE),
            "tipo_precificacao": forms.Select(attrs={"class": "form-select"}),
            "modulos": forms.CheckboxSelectMultiple(),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    # Insumos globais oferecidos a este segmento (o vínculo fica em InsumoGlobal.segmentos). Sem nenhum marcado em NENHUM segmento = visível a todos.
    insumos_globais = forms.ModelMultipleChoiceField(queryset=None, required=False, label="Insumos globais do segmento", widget=forms.CheckboxSelectMultiple())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.insumoglobal.models import InsumoGlobal

        # Módulos essenciais (Configurações) valem para todos os segmentos.
        self.fields["modulos"].queryset = Modulo.objects.filter(essencial=False)
        self.fields["insumos_globais"].queryset = InsumoGlobal.objects.filter(ativo=True).order_by("nome")
        if self.instance.pk:
            self.fields["insumos_globais"].initial = list(self.instance.insumos_globais.values_list("pk", flat=True))
        if self.instance.pk:
            self.fields["slug"].disabled = True

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.insumos_globais.set(self.cleaned_data.get("insumos_globais", []))  # vínculo guardado em InsumoGlobal.segmentos
