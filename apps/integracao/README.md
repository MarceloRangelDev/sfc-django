# Integrações e agenda (`apps/integracao`)

Conexões com **WhatsApp**, **Instagram**, **Google Agenda** e **iCloud (CalDAV)**, mais a **agenda** de compromissos do sistema. iFood e 99Food: **somente a estrutura** (basta criar um adaptador novo, ver abaixo).

> **IMPORTANTE — nada aqui foi homologado contra conta real.** Os conectores foram escritos a partir da documentação pública dos provedores e testados com o adaptador simulado (`teste`, só em DEBUG). A tela mostra "não homologado" em cada provedor. Antes de produção: testar com contas de homologação, credenciais próprias e os fluxos de aprovação de cada provedor (Meta para WhatsApp/Instagram, verificação OAuth do Google). Nenhuma integração deve ser anunciada como funcional sem essa validação.

## Como funciona

- **Credenciais**: cifradas (Fernet, biblioteca `cryptography`) com a chave de `INTEGRACAO_CRYPTO_KEYS`; nunca são exibidas, registradas em log, auditoria ou mensagem de erro. Em produção, sem a chave, o sistema recusa gravar credenciais. Rotação: coloque a chave nova na frente (separe por vírgula), rode `manage.py rotacionar_credenciais` e depois remova a antiga.
- **Rede (anti-SSRF)**: só HTTPS para host público, com limite de tamanho e tempo (`rede.py`).
- **Webhooks** (`/webhooks/<token>`, público): autenticados por assinatura HMAC + token da URL, **idempotentes** (`EventoWebhook`), com limite de chamadas por conexão/IP.
- **Mensagens**: fila de saída com reenvio e espera crescente (`manage.py enviar_mensagens_pendentes`, agende a cada minuto). Sem garantia de entrega instantânea. Idempotente por `chave`/`Idempotency-Key` na API. O contato (dado pessoal) nunca vai para log.
- **Agenda**: `manage.py sincronizar_integracoes` (ex.: a cada 15 min). Vencimentos de contas a pagar/receber vão do sistema para o calendário (valores em R$ só se "incluir valores" estiver ligado); **compromissos** sincronizam nos dois sentidos, e em conflito vence a alteração **mais recente**. Desativar um compromisso remove o evento externo.
- **OAuth Google**: `GOOGLE_OAUTH_CLIENT_ID` e `GOOGLE_OAUTH_CLIENT_SECRET` no `.env`; sem eles a conexão fica indisponível. `renovar_credenciais` renova tokens a vencer.
- Registro de log por conexão (`LogIntegracao`) e auditoria de conectar, alterar credenciais, desconectar e mensagens.

## Permissões

| Perfil | Pode |
|---|---|
| Administrador | tudo |
| Gestor | conectar, alterar credenciais, testar, sincronizar, desconectar, enviar e ver mensagens, ver o log |
| Operador | agenda (ver, criar, alterar compromissos); **não** vê conexões nem mensagens |
| Visualizador | ver a agenda |

Módulos: `integracoes` (Configurações) e `agenda` (menu principal), ambos "essenciais" (dependem só da permissão). Tudo é da unidade do usuário; conexão de outra unidade devolve 404.

## API v1

`/integracoes/` (+ `provedores/`, `{id}/testar|sincronizar|desconectar/`, `{id}/credenciais/`), `/mensagens/` (POST idempotente), `/compromissos/` (CRUD). Credenciais são recebidas e **nunca devolvidas**.

## Provedor novo (ex.: iFood, 99Food)

Crie a classe em `adaptadores/` herdando de `Adaptador` (`base.py`), implemente o que o provedor suporta (testar, enviar mensagem, webhook, eventos) e acrescente-a ao `REGISTRO` em `adaptadores/__init__.py`. O núcleo não muda.

## Tabelas

`integracao_conexao`, `integracao_eventowebhook`, `integracao_mensagem`, `integracao_compromisso`, `integracao_itemsincronizado`, `integracao_logintegracao` (migração `integracao.0001`, aditiva; módulos em `modulo.0017`).

## Variáveis de ambiente

`INTEGRACAO_CRYPTO_KEYS`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `BASE_URL` (para montar o endereço de retorno OAuth e dos webhooks).
