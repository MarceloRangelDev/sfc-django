from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.conf import settings


def _html_wrapper(title, message_html):
    return f"""
    <div style="font-family: Arial, Helvetica, sans-serif; max-width: 480px; margin: 0 auto; color: #4b465c;">
      <h2 style="color: #0D9394;">{title}</h2>
      <div style="font-size: 15px; line-height: 1.6;">{message_html}</div>
      <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
      <p style="font-size: 12px; color: #a5a3ae;">
        {settings.MAIL_FROM_NAME} &middot; Este é um e-mail automático, não responda.
      </p>
    </div>
    """


def send_email(subject, email, message, html_message=None):
    try:
        recipient_list = [email]
        email_message = EmailMultiAlternatives(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipient_list,
            reply_to=[settings.MAIL_REPLY_TO] if settings.MAIL_REPLY_TO else None,
        )
        if html_message:
            email_message.attach_alternative(html_message, "text/html")
        email_message.send()
    except Exception as e:
        print(f"Failed to send email: {e}")


def get_absolute_url(path):
    return settings.BASE_URL + path


def send_verification_email(email, token):
    subject = "Verifique seu e-mail - Sistema Faturando Certo"
    verification_url = get_absolute_url(reverse('verify-email', kwargs={'token': token}))
    message = f"Olá,\n\nPor favor, verifique seu e-mail usando este link: {verification_url}"
    html_message = _html_wrapper(
        "Verifique seu e-mail",
        f"""
        <p>Olá,</p>
        <p>Confirme seu cadastro no Sistema Faturando Certo clicando no botão abaixo:</p>
        <p style="text-align: center; margin: 24px 0;">
          <a href="{verification_url}" style="background: #0D9394; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none;">Verificar e-mail</a>
        </p>
        <p>Ou copie e cole este link no navegador:<br /><a href="{verification_url}">{verification_url}</a></p>
        """,
    )
    send_email(subject, email, message, html_message)


def send_password_reset_email(email, token):
    subject = "Redefinição de senha - Sistema Faturando Certo"
    reset_url = get_absolute_url(reverse('reset-password', kwargs={'token': token}))
    message = f"Olá,\n\nRedefina sua senha usando este link: {reset_url}\n\nEste link expira em 24 horas. Se você não solicitou essa alteração, ignore este e-mail."
    html_message = _html_wrapper(
        "Redefinição de senha",
        f"""
        <p>Olá,</p>
        <p>Recebemos uma solicitação para redefinir a senha da sua conta. Clique no botão abaixo para continuar:</p>
        <p style="text-align: center; margin: 24px 0;">
          <a href="{reset_url}" style="background: #0D9394; color: #fff; padding: 10px 24px; border-radius: 6px; text-decoration: none;">Redefinir senha</a>
        </p>
        <p>Este link expira em 24 horas. Se você não solicitou essa alteração, pode ignorar este e-mail com segurança.</p>
        """,
    )
    send_email(subject, email, message, html_message)
