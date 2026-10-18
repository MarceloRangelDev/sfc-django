from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    email = models.EmailField(max_length=100, unique=True)  # E-mail único por usuário
    email_token = models.CharField(max_length=100, blank=True, null=True)
    forget_password_token = models.CharField(max_length=100, blank=True, null=True)
    # Validade do token de redefinição de senha (forget_password_token).
    # Sem este campo o token gerado em "Esqueci minha senha" nunca era
    # persistido e, na prática, o link de redefinição nunca expirava —
    # falha de segurança corrigida junto com a tela de reset de senha.
    forget_password_token_expiration = models.DateTimeField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    # Cliente (empresa/CPF-CNPJ) ao qual este usuário pertence. Nullable
    # porque usuários criados antes da Fase 2 (multi-tenant) ainda não têm
    # Cliente até a migration de dados vinculá-los a um Cliente padrão;
    # PROTECT porque um Cliente com usuários vinculados não deve poder ser
    # apagado sem antes desvincular (ou migrar) esses usuários.
    cliente = models.ForeignKey(
        "cliente.Cliente",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios",
        verbose_name="Cliente",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username

    # O segmento de trabalho pertence ao Cliente (ver cliente.segmento); o
    # usuário apenas herda o contexto através dele.
    @property
    def segmento(self):
        return self.cliente.segmento if self.cliente_id else None

    @property
    def precifica_servico(self):
        return bool(self.segmento and self.segmento.precifica_servico)

    @property
    def precifica_produto(self):
        return bool(self.segmento and self.segmento.precifica_produto)

    @receiver(post_save, sender=User)
    def create_profile(sender, instance, created, **kwargs):
        if created:
            Profile.objects.create(user=instance, email=instance.email)


    class Meta:
        verbose_name = "Perfil do usuário"
        verbose_name_plural = "Perfis de usuário"
        # Libera o usuário a trocar a PRÓPRIA foto em Meu perfil (Papéis e Permissões especiais).
        permissions = [("change_fotoperfil", "Alterar a própria foto do perfil")]
