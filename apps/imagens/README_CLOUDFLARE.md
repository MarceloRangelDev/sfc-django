# Configuração e operação do Cloudflare (R2 + Image Transformations)

Este guia separa o que **a aplicação já faz** do que **você precisa fazer no painel Cloudflare / no ambiente de implantação**. Nada abaixo foi executado por nós: nenhuma conta, bucket ou credencial foi criada ou validada.

Fontes oficiais consultadas em 2026-10-17 (reconfira antes de usar, a Cloudflare muda produtos e preços):
* Preços do R2 — <https://developers.cloudflare.com/r2/pricing/>
* Tokens de API/credenciais S3 do R2 — <https://developers.cloudflare.com/r2/api/tokens/>
* Preços das Image Transformations — <https://developers.cloudflare.com/images/pricing/>
* Habilitar Transformations e origens permitidas — <https://developers.cloudflare.com/images/transform-images/>
* Demais páginas citadas (não relidas nesta data; abra-as para os passos atuais): R2 <https://developers.cloudflare.com/r2/>, API S3 <https://developers.cloudflare.com/r2/api/s3/api/>, domínios <https://developers.cloudflare.com/r2/buckets/public-buckets/> (não usamos bucket público), URL de transformação <https://developers.cloudflare.com/images/transform-images/transform-via-url/>.

## O que a aplicação faz / o que é externo
| Já feito na aplicação | Você faz fora |
|---|---|
| Cliente S3 (boto3) com região `auto`, timeouts, erros sem segredos; validação do arquivo; variantes; contadores; painel de custos; tarifas editáveis | Criar conta, bucket, token; definir variáveis/secrets; (opcional) habilitar Transformations e origens permitidas; agendar `limpar_imagens` |

## Passo a passo

1. **Conta e R2** — no painel: *R2 object storage*, ative o R2 (exige método de pagamento mesmo na franquia gratuita; confira a página de preços).
2. **Bucket privado** — crie um bucket (ex.: um por ambiente: `sfc-imagens-dev`, `-hml`, `-prod`). **Não** habilite acesso público nem domínio `r2.dev`. Anote a jurisdição, se houver (EU/FedRAMP exigem endpoint próprio, ver abaixo).
3. **Credenciais com permissão mínima** — *R2 → Overview → Manage API Tokens*: token **Object Read & Write limitado a UM bucket**. Guarde *Access Key ID* e *Secret Access Key* (o segredo só aparece uma vez). Um token por ambiente.
4. **Variáveis de ambiente** (nunca no repositório, nunca no chat):

   | Variável | Valor |
   |---|---|
   | `R2_ACCOUNT_ID` | ID da conta (gera o endpoint `https://<id>.r2.cloudflarestorage.com`) |
   | `R2_ENDPOINT` | opcional; obrigatório para jurisdição (`https://<id>.eu.r2.cloudflarestorage.com`) |
   | `R2_BUCKET` | nome do bucket do ambiente |
   | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | do token do passo 3 |
   | `IMAGENS_ENTREGA` | `local` (padrão) ou `cloudflare` |
   | `IMAGENS_CF_BASE_URL` | só no modo cloudflare: `https://seudominio` (zona Cloudflare) |
   | `IMAGENS_MAX_BYTES`, `IMAGENS_MAX_LADO`, `IMAGENS_RETENCAO_DIAS`… | opcionais, ver README.md |

   Desenvolvimento: arquivo `.env` local (já ignorado pelo git). Homologação/produção: o mecanismo de *secrets* do ambiente (variáveis do Docker/compose, do orquestrador ou do provedor). Confirme que `.env` está no `.gitignore` antes de criar.
5. **Teste a conexão** — entre como superusuário, menu do usuário → *Imagens e custos (R2)* → **Testar conexão com o R2** (faz `HeadBucket`). Mensagem de erro nunca mostra segredos; detalhe técnico fica no log (`apps.imagens.armazenamento`, só o tipo do erro).
6. **Modo `local` (recomendado para começar)** — nada mais a configurar: o Django gera as variantes. Não depende de zona Cloudflare nem gera cobrança de transformação; consome CPU do servidor e leituras (Classe B) do R2.
7. **Modo `cloudflare` (opcional, só imagens de catálogo)**
   * O domínio precisa estar numa **zona Cloudflare**. Em *Images → Transformations* selecione a zona e **habilite**. Por padrão a Cloudflare só aceita imagens de origem **da mesma zona**; se o app roda em outro host/zona, adicione o host do app às **origens permitidas** — e somente ele (evita proxy aberto).
   * Defina `IMAGENS_ENTREGA=cloudflare` e `IMAGENS_CF_BASE_URL`. A aplicação redireciona para `…/cdn-cgi/image/width=…,height=…,fit=…,quality=…,format=auto,metadata=none/<origem>`, onde `<origem>` é `/imagens/o/<token>` do próprio app (token assinado, 1 imagem, só catálogo). `format=auto` negocia AVIF/WebP; `metadata=none` remove EXIF.
   * Imagens de **usuário e cliente nunca** usam este modo.
   * **Não verificado**: o formato exato da URL e a aceitação da origem com a sua zona devem ser testados (passo 9). Se `/cdn-cgi/image` não responder, volte a `local`.
8. **Cache** — o app envia `private, max-age=86400` (local) e a origem `public, max-age=86400`. Não crie regra de cache que ignore `Cookie` para `/imagens/<uuid>/…` (privadas!). Cache de CDN só vale para a origem de catálogo.
9. **Validação (checklist manual)**: (a) testar conexão; (b) enviar imagem JPEG num insumo → aparece a miniatura; (c) conferir no R2 que o objeto está em `t<cliente>/insumo/<uuid>.jpg` e que o bucket **não** é público (abrir a URL do objeto sem assinatura deve falhar); (d) abrir a imagem logado como **outro tenant** → 404; (e) substituir → objeto antigo continua por 7 dias; (f) remover → objeto some; (g) no modo cloudflare, `curl -I` na URL redirecionada e checar `cf-resized`/formato; (h) painel mostra contadores.
10. **Tarefa de limpeza** — agende diariamente `python manage.py limpar_imagens` (cron/Agendador do Windows/serviço do orquestrador).

## Rotação e revogação de credenciais
Crie um **novo** token (mesmo escopo) → atualize os secrets do ambiente → reinicie a aplicação → teste a conexão → **revogue** o token antigo no painel. Se vazou: revogue primeiro, depois recrie. A aplicação não guarda credenciais no banco.

## Atualizar tarifas e conferir o painel
Quando a Cloudflare mudar preços: abra as páginas de preço acima, e no painel do proprietário edite preço, franquia, **fonte** e **data da consulta** de cada tarifa (alteração é auditada com antes/depois). Confira as premissas: GB = 10⁹ bytes, unidade começada é cobrada inteira após a franquia, armazenamento contado ao fim do mês (a Cloudflare usa média de picos diários), transformações só no modo cloudflare. Compare com a fatura real: o painel é **estimativa**. A franquia de Image Transformations (5 mil únicas) aplica-se conforme o seu plano — ajuste a franquia se estiver no plano gratuito/pago.

## Diagnóstico
| Sintoma | Causa provável |
|---|---|
| “Armazenamento de imagens não configurado” | faltam `R2_BUCKET`/chaves/endpoint (ver painel: “R2 NÃO configurado”) |
| “Indisponível no momento” | credenciais erradas/revogadas, bucket inexistente, endpoint/jurisdição incorretos, rede; veja o log (`R2 head_bucket falhou: ClientError`) |
| Upload 400 | formato fora de JPEG/PNG/WebP, > 5 MB, > 8000 px, arquivo corrompido |
| Imagem “antiga” continua aparecendo | cache do navegador (até 24 h) ou da CDN no modo cloudflare; a URL muda quando a imagem é trocada |
| Erro de transformação no modo cloudflare | Transformations não habilitado, origem não permitida, limite do plano (a Cloudflare retorna erro `9422` além da franquia no plano gratuito, segundo a página de preços) |
| Erro de CORS | **não se aplica**: o upload passa pelo Django; só surgirá se você passar a enviar direto do navegador ao R2 |
| 429 | limite por minuto/hora (`IMAGENS_LIMITE_*`, `API_THROTTLE_IMAGENS`) |

## Rollback sem perder dados
1. `IMAGENS_ENTREGA=local` e reinicie (volta a servir pelo Django; originais e metadados intactos).
2. Para desligar o R2 temporariamente, remova as variáveis `R2_*`: telas continuam funcionando, uploads/leituras respondem “não configurado/indisponível”, **sem apagar metadados**.
3. Não rode `migrate imagens zero` nem apague o bucket: isso perde a referência dos originais. Para voltar de versão do código, basta manter as tabelas (são aditivas).
