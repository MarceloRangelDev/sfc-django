Para publicar um SaaS desenvolvido em Django utilizando o SQLite em um DigitalOcean Droplet, a grande vantagem é que, diferentemente de plataformas PaaS (como a App Platform, que apaga arquivos locais a cada novo deploy), o Droplet possui armazenamento persistente. Isso significa que seu arquivo do SQLite (db.sqlite3) não será deletado.
A arquitetura padrão mais recomendada e segura para esse deploy é: Nginx (como servidor web de proxy reverso) → Gunicorn (servidor de aplicação WSGI) → Django.

# Passo 1: Preparar o Projeto Django (Localmente)

## 1. Ajuste o settings.py:
	• Certifique-se de que o caminho do banco de dados está apontando corretamente para a raiz ou uma pasta segura do projeto:python
```bash
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
```
Use o código com cuidado.
	• Configure os arquivos estáticos e de mídia:python

```bash
ALLOWED_HOSTS = ['app.cdfiscal.com', '165.232.56.183'] #
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles' #
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media' #
```
Use o código com cuidado.

## 2. Gere o arquivo de dependências:bash
```bash
pip freeze > requirements.txt
```
Use o código com cuidado.

## 3. Envie seu código para um repositório privado no GitHub ou GitLab.
---
# Passo 2: Configurar o Droplet da DigitalOcean

## 1. Crie um Droplet na DigitalOcean (um plano básico com Ubuntu 24.04 LTS ou 22.04 LTS é suficiente para começar).
## 2. Acesse o seu servidor via terminal (SSH):bash
```bash
ssh root@165.232.56.183
```
Use o código com cuidado.

## 3. Atualize o sistema e instale as dependências principais:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv nginx git curl -y
```
Use o código com cuidado.
---
# Passo 3: Clonar o Projeto e Configurar o Ambiente Virtual

## 1. Recomenda-se criar um usuário comum para não rodar a aplicação como root:
```bash
sudo adduser django_user
sudo usermod -aG sudo django_user
su - django_user
```
Use o código com cuidado.

## 2. Clone o seu repositório:
```bash
git clone https://github.com/MarceloRangelDev/sfc-django.git
cd sfc-django
```
Use o código com cuidado.

## 3. Crie e ative o ambiente virtual (venv):bash
```bash
python3 -m venv venv
source venv/bin/activate
```
Use o código com cuidado.

## 4. Instale as dependências do projeto e o Gunicorn:bash
```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```
Use o código com cuidado.

## 5. Rode as migrações, crie o superusuário e colete os arquivos estáticos:bash
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```
Use o código com cuidado.
⚠️ Atenção crucial sobre as permissões do SQLite: O Nginx/Gunicorn precisará ler e escrever no arquivo db.sqlite3 e na pasta onde ele está. Conceda as permissões corretas para evitar erros de Database is locked ou Permission denied:
bash
```bash
chmod 664 db.sqlite3
chmod 775 .
```
Use o código com cuidado.
--- 
# Passo 4: Configurar o Gunicorn (Systemd)

Para que o Django rode em segundo plano e inicialize automaticamente com o servidor, crie um serviço do sistema:
## 1. Abra o arquivo de configuração do serviço:bash
```bash
sudo nano /etc/systemd/system/gunicorn.service
```
Use o código com cuidado.

## 2. Cole a configuração abaixo (ajuste os caminhos para o seu usuário e pasta):

```ini
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=django_user
Group=www-data
WorkingDirectory=/home/django_user/sfc-django
ExecStart=/home/django_user/sfc-django/venv/bin/gunicorn \
          --access-logfile - \
          --workers 3 \
          --bind unix:/run/gunicorn.sock \
          config.wsgi:application

[Install]
WantedBy=multi-user.target
```
Use o código com cuidado.

(Substitua saas_project.wsgi pelo nome do diretório do seu arquivo wsgi.py).

## 3. Inicie e habilite o serviço:bash
```bash
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
```
Use o código com cuidado.

# Passo 5: Configurar o Nginx como Proxy Reverso

O Nginx vai receber as requisições web externas (porta 80/443), servir as pastas de arquivos estáticos (staticfiles) diretamente e encaminhar o restante para o Gunicorn.
## 1. Crie um novo arquivo de bloco de servidor:bash
```bash
sudo nano /etc/nginx/sites-available/sfc-django
```
Use o código com cuidado.

## 2. Adicione a seguinte estrutura:
```nginx
server {
    listen 80;
    server_name app.cdfiscal.com ://app.cdfiscal.com 165.232.56.183;

    location = /favicon.ico { access_log off; log_not_found off; }

    # Caminho para os arquivos estáticos gerados pelo collectstatic
    # IMPORTANTE: STATIC_ROOT é a pasta 'staticfiles' (não 'static'); com 'root' o Nginx procuraria .../static/... e daria 404.
    location /static/ {
        alias /home/django_user/sfc-django/staticfiles/;
    }

    # Caminho para uploads de mídia (se houver)
    location /media/ {
        root /home/django_user/sfc-django;
    }

    # Encaminha o restante das requisições para o socket do Gunicorn
    location / {
        include proxy_params;
        proxy_pass http://unix:/home/django_user/sfc-django/gunicorn.sock;
    }
}
```
Use o código com cuidado.

## 3. Ative a configuração criando um link simbólico e reinicie o Nginx:bash
```bash
sudo ln -s /etc/nginx/sites-available/sfc-django /etc/nginx/sites-enabled/
sudo nginx -t  # Testa se não há erros de sintaxe
sudo systemctl restart nginx
```
Use o código com cuidado.

## 4. Ajuste o Firewall para permitir conexões:bash
```bash
sudo ufw allow 'Nginx Full'
```
Use o código com cuidado.

# Passo 6: Segurança e SSL (HTTPS) com Let's Encrypt

Como se trata de um SaaS, criptografia é indispensável para transações e segurança de dados.
## 1. Instale o Certbot para o Nginx:bash
```bash
sudo apt install certbot python3-certbot-nginx -y
```
Use o código com cuidado.

## 2. Obtenha e instale o certificado SSL (substitua pelo seu domínio configurado no DNS):bash
```bash
sudo certbot --nginx -d app.cdfiscal.com -d ://app.cdfiscal.com
sudo certbot --nginx -d cdfiscal.com -d www.cdfiscal.com
```
Use o código com cuidado.

## 3. O Certbot configurará automaticamente o redirecionamento automático de HTTP para HTTPS.

💡 Recomendações Críticas para SaaS com SQLite

• Backups Automáticos: O SQLite fica salvo num arquivo único (db.sqlite3). Configure uma tarefa agendada (Cron job) para copiar esse arquivo diariamente para um serviço externo, como o DigitalOcean Spaces (Object Storage) ou AWS S3, garantindo que você não perca os dados do SaaS em caso de pane no Droplet.
• Concorrência: Se o seu SaaS começar a receber muitos acessos simultâneos de escrita, você poderá começar a notar travamentos de banco de dados. Nesse momento, migrar para um banco gerenciado como o PostgreSQL será o próximo passo natural.



Corrigir as permissões da pasta do projeto

Para que o Nginx consiga ler e interagir com o arquivo socket que o Gunicorn vai criar dentro da sua pasta de usuário, rode estes comandos de permissão:
```bash
sudo usermod -aG django_user www-data
chmod 755 /home/django_user
chmod 755 /home/django_user/sfc-django
```
Recarregar e Reiniciar os Serviços

Agora aplique as mudanças no sistema e reinicie ambos os serviços:
```bash
# Recarrega as configurações de serviços do sistema
sudo systemctl daemon-reload

# Reinicia e valida o Gunicorn
sudo systemctl restart gunicorn
sudo systemctl status gunicorn

# Reinicia e valida o Nginx
sudo nginx -t
sudo systemctl restart nginx
```
