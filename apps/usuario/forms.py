from django import forms
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.cliente.models import Cliente
from auth.models import Profile
from web_project.acesso import administrador_global, cliente_do_usuario
from web_project.regras.validadores import validar_email


class UsuarioForm(forms.ModelForm):
    """Cadastro/edição de usuário (auth_user) vinculado a Cliente e Perfil.

    Cadeia validada no backend: Usuário -> Cliente -> Perfil -> Permissões.
    O perfil escolhido precisa estar entre os perfis liberados para o
    cliente (Cliente.perfis). Quem não é administrador global só pode
    cadastrar usuários do próprio cliente — o campo é travado no servidor,
    não apenas escondido na tela.
    """

    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), label="Cliente", empty_label="Selecione...",
                                     widget=forms.Select(attrs={"class": "form-select"}))
    perfil = forms.ModelChoiceField(queryset=Group.objects.none(), label="Perfil", empty_label="Selecione...",
                                    widget=forms.Select(attrs={"class": "form-select"}))
    segmento_trabalho = forms.ChoiceField(choices=Profile.SEGMENTO_CHOICES, label="Segmento de trabalho",
                                          widget=forms.Select(attrs={"class": "form-select"}))
    senha = forms.CharField(required=False, label="Senha", strip=False,
                            widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}))
    confirmar_senha = forms.CharField(required=False, label="Confirmar senha", strip=False,
                                      widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {"username": "Usuário", "first_name": "Nome", "last_name": "Sobrenome", "email": "E-mail", "is_active": "Ativo"}
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, solicitante, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitante = solicitante
        self.fields["email"].required = True
        if administrador_global(solicitante):
            clientes = Cliente.objects.filter(ativo=True)
        else:
            clientes = Cliente.objects.filter(pk=getattr(cliente_do_usuario(solicitante), "pk", None))
        if self.instance.pk and getattr(self.instance.profile, "cliente_id", None):
            clientes = clientes | Cliente.objects.filter(pk=self.instance.profile.cliente_id)
        self.fields["cliente"].queryset = clientes.order_by("nome_razao_social").distinct()
        self.fields["perfil"].queryset = Group.objects.filter(clientes__in=self.fields["cliente"].queryset).distinct().order_by("name")

        if self.instance.pk:
            profile = self.instance.profile
            grupo = self.instance.groups.first()
            self.initial.update(
                cliente=profile.cliente_id, segmento_trabalho=profile.segmento_trabalho, perfil=grupo.pk if grupo else None
            )

    # Tabela cliente -> perfis liberados, usada pelo JavaScript da tela para
    # filtrar o select de perfil (a validação de verdade é em clean()).
    @property
    def perfis_por_cliente(self):
        return {
            str(c.pk): [{"id": g.pk, "nome": g.name} for g in c.perfis.order_by("name")]
            for c in self.fields["cliente"].queryset
        }

    def clean_username(self):
        username = self.cleaned_data["username"]
        duplicado = User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk)
        if duplicado.exists():
            raise ValidationError("Este nome de usuário já está em uso.", code="unique")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if not validar_email(email):
            raise ValidationError("Informe um e-mail válido.")
        # O e-mail é único no sistema: User.email e Profile.email (que tem
        # restrição unique no banco).
        usuarios = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        perfis = Profile.objects.filter(email__iexact=email).exclude(user_id=self.instance.pk)
        if usuarios.exists() or perfis.exists():
            raise ValidationError("Este e-mail já está cadastrado.", code="unique")
        return email

    def clean(self):
        dados = super().clean()
        cliente, perfil = dados.get("cliente"), dados.get("perfil")
        if cliente and perfil and not cliente.perfis.filter(pk=perfil.pk).exists():
            self.add_error("perfil", "Este perfil não está disponível para o cliente selecionado.")

        senha, confirmar = dados.get("senha") or "", dados.get("confirmar_senha") or ""
        if not self.instance.pk and not senha:
            self.add_error("senha", "Informe uma senha.")
        if senha or confirmar:
            if senha != confirmar:
                self.add_error("confirmar_senha", "As senhas não conferem.")
            else:
                try:
                    # Mesmas regras de senha do sistema (AUTH_PASSWORD_VALIDATORS).
                    validate_password(senha, user=User(username=dados.get("username", ""), email=dados.get("email", "")))
                except ValidationError as erro:
                    self.add_error("senha", erro)

        if self.instance.pk:
            if self.instance.is_superuser and not self.solicitante.is_superuser:
                raise ValidationError("Somente um superusuário pode alterar outro superusuário.")
            if self.instance.pk == self.solicitante.pk and dados.get("is_active") is False:
                self.add_error("is_active", "Você não pode inativar a sua própria conta.")
        return dados
