from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class Profile(models.Model):
    SEGMENTO_SERVICO = "servico"
    SEGMENTO_PRODUTO = "produto"
    SEGMENTO_AMBOS = "ambos"

    SEGMENTO_CHOICES = [
        (SEGMENTO_SERVICO, "Serviço"),
        (SEGMENTO_PRODUTO, "Produto"),
        (SEGMENTO_AMBOS, "Serviço e Produto"),
    ]

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
    segmento_trabalho = models.CharField(
        max_length=10,
        choices=SEGMENTO_CHOICES,
        default=SEGMENTO_PRODUTO,
        verbose_name="Segmento de trabalho",
        help_text="Define se as regras de precificação usadas são as de serviço, de produto ou ambas.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.username

    @property
    def precifica_servico(self):
        return self.segmento_trabalho in (self.SEGMENTO_SERVICO, self.SEGMENTO_AMBOS)

    @property
    def precifica_produto(self):
        return self.segmento_trabalho in (self.SEGMENTO_PRODUTO, self.SEGMENTO_AMBOS)

    @receiver(post_save, sender=User)
    def create_profile(sender, instance, created, **kwargs):
        if created:
            Profile.objects.create(user=instance, email=instance.email)


    class Meta:
        verbose_name = "Perfil do usuário"
        verbose_name_plural = "Perfis de usuário"
