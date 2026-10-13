from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.modulo.models import Segmento
from auth.models import Profile
from web_project.acesso import administrador_global, clientes_acessiveis, perfis_visiveis
from web_project.regras.validadores import validar_email

from .models import Cliente


class ClienteForm(forms.ModelForm):
    """A validação/normalização real do CPF/CNPJ e das regras de Matriz/Filial
    acontece em Cliente.clean() (chamado por ModelForm.is_valid() ->
    instance.full_clean()), não aqui — assim ela vale também para quem gravar
    um Cliente fora deste formulário (Django Admin, um script, etc.).

    Quem não é administrador global só enxerga a própria estrutura: só pode
    cadastrar FILIAIS da sua matriz, que herdam o segmento dela.
    """

    email_gestor = forms.EmailField(
        required=False, label="E-mail do usuário Gestor",
        help_text="Um usuário Gestor com o nome do cliente é criado automaticamente; ele define a senha por 'Esqueci minha senha'.",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Cliente
        fields = ["tipo_pessoa", "cpf_cnpj", "nome_razao_social", "nome_fantasia", "ativo",
                  "tipo_estabelecimento", "matriz", "segmento", "perfis"]
        widgets = {
            "tipo_pessoa": forms.Select(attrs={"class": "form-select"}),
            "cpf_cnpj": forms.TextInput(attrs={"class": "form-control", "placeholder": "000.000.000-00 ou 00.000.000/0000-00", "data-mask": "cpf-cnpj"}),
            "nome_razao_social": forms.TextInput(attrs={"class": "form-control"}),
            "nome_fantasia": forms.TextInput(attrs={"class": "form-control"}),
            "ativo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "tipo_estabelecimento": forms.Select(attrs={"class": "form-select"}),
            "matriz": forms.Select(attrs={"class": "form-select"}),
            "segmento": forms.Select(attrs={"class": "form-select"}),
            "perfis": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, solicitante, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitante = solicitante
        self.global_ = administrador_global(solicitante)
        self.criando = self.instance.pk is None

        self.fields["matriz"].queryset = clientes_acessiveis(solicitante).filter(
            tipo_pessoa=Cliente.TIPO_PJ, tipo_estabelecimento=Cliente.TIPO_MATRIZ
        ).exclude(pk=self.instance.pk)
        self.fields["matriz"].empty_label = "---"
        self.fields["segmento"].queryset = Segmento.objects.filter(ativo=True)
        self.fields["perfis"].queryset = perfis_visiveis(solicitante)
        self.fields["email_gestor"].required = self.criando
        self.fields["tipo_estabelecimento"].required = False  # vazio = mantém o atual (padrão: matriz)

        if not self.global_:
            # Quem não é Admin só cria filiais da própria estrutura, com o segmento da matriz.
            del self.fields["segmento"]
            if self.criando:
                self.fields["tipo_estabelecimento"].choices = [(Cliente.TIPO_FILIAL, "Filial")]
                self.fields["tipo_estabelecimento"].initial = Cliente.TIPO_FILIAL
                self.fields["matriz"].required = True
        if not self.criando:
            del self.fields["email_gestor"]

    def clean_tipo_estabelecimento(self):
        return self.cleaned_data.get("tipo_estabelecimento") or self.instance.tipo_estabelecimento or Cliente.TIPO_MATRIZ

    def clean_email_gestor(self):
        email = (self.cleaned_data.get("email_gestor") or "").strip().lower()
        if not email:
            return email
        if not validar_email(email):
            raise ValidationError("Informe um e-mail válido.")
        if User.objects.filter(email__iexact=email).exists() or Profile.objects.filter(email__iexact=email).exists():
            raise ValidationError("Este e-mail já está cadastrado.")
        return email

    def _post_clean(self):
        # Uma filial herda o segmento da matriz (não editável por quem não é Admin).
        matriz = self.cleaned_data.get("matriz")
        if not self.global_ and self.criando and matriz is not None:
            self.instance.segmento = matriz.segmento
        super()._post_clean()
