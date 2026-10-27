# Atualizar o servidor (DigitalOcean) via SSH

Guia para trazer ao droplet **somente o que mudou** no GitHub. O `git pull` já baixa apenas as diferenças entre o que está no servidor e o repositório; nada é recopiado por inteiro.

Premissas (do guia de publicação): usuário `django_user`, projeto em `/home/django_user/sfc-django`, ambiente virtual em `venv/`, serviço `gunicorn` (systemd) e Nginx. Ajuste os caminhos se os seus forem outros.

> **Não é preciso instalar Node/npm no servidor.** Os arquivos compilados do tema (`src/assets/vendor`) agora vêm no próprio Git; `src/node_modules` continua fora (só serve para recompilar o tema na sua máquina).

## 1. Conectar

```bash
ssh django_user@SEU_IP_OU_DOMINIO
cd /home/django_user/sfc-django
source venv/bin/activate
```

## 2. Backup antes de atualizar (banco SQLite e arquivos enviados)

O `db.sqlite3` **não** está no Git; o `git pull` não o toca, mas faça a cópia antes de migrar.

```bash
mkdir -p ~/backups
cp db.sqlite3 ~/backups/db-$(date +%F-%H%M).sqlite3
[ -d media ] && tar czf ~/backups/media-$(date +%F-%H%M).tar.gz media
```

## 3. Ver o que mudou e baixar

```bash
sudo -u django_user git status                      # deve estar limpo; se houver alterações locais, não use pull às cegas (ver "Problemas")
sudo -u django_user git fetch origin
sudo -u django_user git log --oneline HEAD..origin/main    # commits novos que serão aplicados
sudo -u django_user git diff --stat HEAD origin/main       # arquivos que mudarão
sudo -u django_user git pull --ff-only origin main         # aplica somente as diferenças
```

`--ff-only` recusa a atualização se o servidor tiver commits próprios, em vez de criar um merge sem querer.

## 4. Aplicar a atualização

```bash
pip install -r requirements.txt            # só instala o que for novo
python manage.py migrate                   # cria/ajusta tabelas, módulos e permissões
python manage.py collectstatic --noinput   # copia os estáticos (inclui src/assets/vendor)
python manage.py check
sudo systemctl restart gunicorn
sudo systemctl reload nginx
```

### Atalho
```bash
sudo -u django_user git pull --ff-only origin main 
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn
sudo systemctl reload nginx
```

## 5. Variáveis de ambiente novas (somente se usar o recurso)

Edite o arquivo de ambiente que o serviço usa (`.env` na pasta do projeto ou `Environment=`/`EnvironmentFile=` em `/etc/systemd/system/gunicorn.service`); depois `sudo systemctl daemon-reload && sudo systemctl restart gunicorn`. **Nunca** coloque estas chaves no Git.

| Variável | Para quê |
|---|---|
| `PAGARME_SECRET_KEY`, `PAGARME_PUBLIC_KEY`, `PAGARME_WEBHOOK_TOKEN` | Assinatura/cobrança (ver `apps/assinatura/README_PAGARME.md`) |
| `ASSINATURA_CARENCIA_DIAS` (7), `ASSINATURA_OBRIGATORIA` (0) | Regras da assinatura (opcionais) |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google Agenda / Google Tarefas |
| `INTEGRACAO_CRYPTO_KEYS` | Obrigatória em produção para guardar credenciais de integrações |

## 6. Tarefas agendadas (cron) — uma vez só

```bash
crontab -e
```

```cron
*/5  * * * * cd /home/django_user/sfc-django && venv/bin/python manage.py enviar_lembretes_agenda >> ~/cron.log 2>&1
*/15 * * * * cd /home/django_user/sfc-django && venv/bin/python manage.py sincronizar_integracoes   >> ~/cron.log 2>&1
0    * * * * cd /home/django_user/sfc-django && venv/bin/python manage.py verificar_assinaturas      >> ~/cron.log 2>&1
```

## 7. Conferir

```bash
sudo systemctl status gunicorn --no-pager
sudo journalctl -u gunicorn -n 50 --no-pager     # erros recentes
sudo nginx -t
```

Abra o sistema no navegador e confira o login, o Painel, a **Agenda**, o **Kanban** (administrador) e **Assinatura**.

## Voltar atrás (rollback)

```bash
git log --oneline -5                         # anote o commit anterior
git checkout <commit_anterior>               # volta o código
cp ~/backups/db-AAAA-MM-DD-HHMM.sqlite3 db.sqlite3   # só se a migração mexeu em dados e algo deu errado
sudo systemctl restart gunicorn
```

Para voltar de vez à última versão: `git checkout main && git pull --ff-only origin main`.

## Problemas comuns

- **`git pull` reclama de alterações locais:** veja com `git status`/`git diff`. Para guardar e seguir: `git stash`, `git pull --ff-only origin main`, `git stash pop`. Não use `git reset --hard` sem backup.
- **`Permission denied (publickey)` / pede senha no GitHub (repositório privado):** use uma chave de deploy (`ssh-keygen`, cadastre a chave pública em *Settings → Deploy keys* do repositório e use a URL `git@github.com:MarceloRangelDev/sfc-django.git`) ou um token pessoal como senha.
- **Estilo/JS sem atualizar no navegador:** rode `collectstatic` de novo e force o recarregamento (Ctrl+F5).
- **502 Bad Gateway:** o Gunicorn caiu; veja `journalctl -u gunicorn -n 50`.
- **Aviso de migração pendente de `webpush`:** é do pacote de terceiros, pode ignorar.
