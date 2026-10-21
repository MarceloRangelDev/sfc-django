import re

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

    # Usuário inicial (Gestor): nada é criado "por conta própria"; o cadastro informa quem é. A senha NÃO é digitada aqui:
    # o responsável recebe por e-mail um link para defini-la (mesmo fluxo do "Esqueci minha senha").
    usuario_nome = forms.CharField(
        required=False, max_length=150, label="Nome do responsável", widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    usuario_username = forms.CharField(
        required=False, max_length=150, label="Usuário (login)", widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    email_gestor = forms.EmailField(
        required=False, label="E-mail do responsável",
        help_text="Cria o usuário Gestor deste cliente. Ele recebe um link por e-mail para definir a senha.",
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Cliente
        fields = ["tipo_pessoa", "cpf_cnpj", "nome_razao_social", "nome_fantasia", "ativo",
                  "tipo_estabelecimento", "matriz", "segmento", "perfis", "endereco", "cidade", "uf", "email_contato", "whatsapp"]
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
            "endereco": forms.TextInput(attrs={"class": "form-control"}),
            "cidade": forms.TextInput(attrs={"class": "form-control"}),
            "uf": forms.TextInput(attrs={"class": "form-control", "maxlength": 2, "style": "text-transform:uppercase"}),
            "email_contato": forms.EmailInput(attrs={"class": "form-control"}),
            "whatsapp": forms.TextInput(attrs={"class": "form-control", "placeholder": "+5511999990000", "inputmode": "tel"}),
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
        for campo in ("email_gestor", "usuario_nome", "usuario_username"):
            self.fields[campo].required = self.criando
        self.fields["tipo_estabelecimento"].required = False  # vazio = mantém o atual (padrão: matriz)

        if not self.global_:
            # Quem não é Admin só cria filiais da própria estrutura, com o segmento da matriz.
            del self.fields["segmento"]
            if self.criando:
                self.fields["tipo_estabelecimento"].choices = [(Cliente.TIPO_FILIAL, "Filial")]
                self.fields["tipo_estabelecimento"].initial = Cliente.TIPO_FILIAL
                self.fields["matriz"].required = True
        if not self.criando:
            for campo in ("email_gestor", "usuario_nome", "usuario_username"):
                del self.fields[campo]

    # Agrupamentos usados pelo template (a ordem das seções na tela).
    @property
    def contato_campos(self):
        return [self[c] for c in ("endereco", "cidade", "uf", "email_contato", "whatsapp")]

    @property
    def usuario_campos(self):
        return [self[c] for c in ("usuario_nome", "usuario_username", "email_gestor") if c in self.fields]

    def clean_tipo_estabelecimento(self):
        return self.cleaned_data.get("tipo_estabelecimento") or self.instance.tipo_estabelecimento or Cliente.TIPO_MATRIZ

    def clean_uf(self):
        return (self.cleaned_data.get("uf") or "").strip().upper()

    def clean_whatsapp(self):
        # Só dígitos e "+": é o formato que os conectores de mensagem esperam.
        numero = re.sub(r"[^\d+]", "", self.cleaned_data.get("whatsapp") or "")
        if numero and not re.fullmatch(r"\+?\d{10,15}", numero):
            raise ValidationError("Informe o WhatsApp com DDI e DDD (10 a 15 dígitos).")
        return numero

    def clean_usuario_username(self):
        username = (self.cleaned_data.get("usuario_username") or "").strip()
        if username and User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Este usuário já existe.")
        return username

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
