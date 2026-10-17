# Módulo de imagens (Cloudflare R2)

Armazena, valida, entrega e audita imagens de **insumos, produtos, usuários e clientes** (e exibe, somente leitura, a foto do produto no **PDV** e a do insumo na **cotação**). Os bytes ficam no **Cloudflare R2** (bucket privado); o banco guarda **apenas metadados**.
Configuração do Cloudflare e operação: [README_CLOUDFLARE.md](README_CLOUDFLARE.md).

> **Estado:** implementado e testado com um R2 **simulado em memória**. Nenhuma conexão real com a Cloudflare foi feita ou validada — isso depende das etapas externas do README_CLOUDFLARE.md.

## Arquitetura

```
Navegador/app ──(multipart)──▶ Django ──valida (Pillow) + autoriza──▶ R2 (original, bucket privado)
      ▲                           │  └─ metadados ─▶ tabela imagens_imagem (+ auditoria)
      │  GET /imagens/<uuid>/<variante>   (login + tenant + permissão em TODO acesso)
      └──────────────── variante WEBP/AVIF ◀── modo "local": Django lê o original e gera a variante (cache 1 h)
                                              modo "cloudflare" (só CATÁLOGO): 302 → /cdn-cgi/image/... → origem assinada /imagens/o/<token>
```

* **Upload passa pelo Django** (decisão do projeto): o servidor valida o conteúdo real antes de gravar. Não há URL pré-assinada de *upload*; por isso não há CORS no bucket. A URL pré-assinada de **leitura** existe em `armazenamento.url_leitura()` (curta, 1 objeto, 1 operação) mas **não é usada nas telas** — a leitura passa sempre pela view autorizada. Se um dia usá-la, nunca persista nem registre a URL.
* **Original preservado**: nunca é recomprimido. Só as **variantes** têm perdas (WebP/AVIF com qualidade 80–85). Compressão com perdas, mesmo em qualidade alta, **não é sem perdas**.
* **Variantes fixas** (cliente não escolhe tamanho/qualidade): `avatar` 96 px, `miniatura` 160 px (ambas recorte quadrado), `lista` 400 px, `detalhe` 1200 px. Nunca amplia. EXIF/GPS é descartado (re-codificação) e a rotação EXIF é aplicada antes.
* **Negociação de formato**: AVIF se o `Accept` pedir **e** o Pillow instalado suportar; senão WebP. Entrada aceita: JPEG, PNG, WebP.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `servico.py` | **Único ponto** usado pelos módulos: `enviar`, `remover`, `ativa`, `url`, `url_da_entidade`, `desvincular`, `limpar`, entrega local/Cloudflare, contadores de uso, limite por minuto. |
| `registro.py` | Quais entidades têm imagem e **quem pode ver/alterar** cada uma. Para um módulo novo ganhar imagem, acrescente em `ENTIDADES`. |
| `armazenamento.py` | Cliente R2 (boto3, API S3, região `auto`, timeouts 5 s/15 s, 2 tentativas) + `ArmazenamentoMemoria` para testes. |
| `processamento.py` | Validação do conteúdo, limites, variantes, negociação de formato. |
| `custos.py` | Estimativa mensal de custo (só proprietário). |
| `views.py` / `urls.py` | Envio, exclusão, entrega, origem assinada e painel do proprietário. |
| `signals.py` | Excluiu a entidade ⇒ imagem desvinculada (segue a retenção). |
| `templatetags/imagens.py` | `{% imagem_url 'produto' p.pk 'miniatura' %}`. |
| `templates/imagens/_campo.html` | Campo de upload reutilizável (`{% include "imagens/_campo.html" with tipo="produto" objeto=object %}`), usado depois do `</form>` na edição. |
| `management/commands/limpar_imagens.py` | Limpeza diária (agende no cron/Agendador). |
| `apps/api/imagens.py` | Endpoints da API. |

## Sequências

**Upload / substituição** (`servico.enviar`): valida arquivo → autoriza (entidade do usuário, tenant derivado da própria entidade) → se o SHA-256 é igual ao da imagem atual, devolve a atual (**idempotente**) → grava linha `pendente` → envia ao R2 → numa transação: a anterior vira `para_excluir`, a nova `ativa`, auditoria.
**Leitura**: `GET /imagens/<uuid>/<variante>` → login → variante permitida → imagem `ativa` → `registro.pode_ver` → entrega. “Não existe” e “não pode” respondem **404 idêntico**.
**Exclusão**: estado `para_excluir`, auditoria, `DeleteObject` imediato (se o R2 falhar, a próxima limpeza conclui).
**Entidade excluída**: imagem vira `para_excluir` e fica pelo prazo de retenção (inativar o registro não mexe na imagem).

### Falhas parciais
| Falha | Resultado |
|---|---|
| R2 recusa o envio | linha descartada, nada auditado, erro 503 sem detalhes |
| Banco falha depois do envio | objeto removido do R2 (compensação); se isso também falhar, a linha `pendente` é limpa por `limpar_imagens` após 1 h |
| R2 fora na exclusão | `para_excluir` com retenção vencida; `limpar_imagens` conclui |
| R2 fora na leitura | 503 sem detalhes (nada de stack/credenciais) |

## Limites (configuráveis por variável de ambiente)
`IMAGENS_MAX_BYTES` (5 MB), `IMAGENS_MAX_LADO` (8000 px), `IMAGENS_MAX_PIXELS` (40 mi — barra bomba de descompressão **antes** de decodificar), `IMAGENS_RETENCAO_DIAS` (7), `IMAGENS_LIMITE_LEITURA_MIN` (600/usuário/min), `IMAGENS_LIMITE_ENVIO_MIN` (20/usuário/min), `API_THROTTLE_IMAGENS` (300/h). SVG, GIF, animadas e arquivos corrompidos são recusados.

## Segurança multi-tenant
* O **tenant nunca vem do cliente HTTP**: é o `Cliente` dono da entidade, resolvido no servidor por `registro.py`, que reaproveita `web_project/acesso.py` (`clientes_dos_itens`, `usuarios_acessiveis`, `clientes_acessiveis`) e exige permissão **e** módulo habilitado.
* Chave no R2: `t<cliente>/<tipo>/<uuid>.<ext>` (aleatória). O caminho **não substitui** a autorização. A chave nunca sai do servidor (nem na API).
* **Categorias**: `insumo` e `produto` = *catálogo* (podem sair pelo Cloudflare e ter cache de CDN); `usuario` e `cliente` = *privada* (sempre servidas pelo Django, `Cache-Control: private`, nunca pela origem pública).
* Regras: usuário altera a **própria** foto ou (com `auth.change_user`) a de usuários da sua estrutura; cliente exige `cliente.view/change_cliente` e tenant; insumo/produto exigem a permissão do módulo; PDV (`pdv.add_venda`/`view_venda`) e cotação (`cotacao.view_cotacao`) **só leem**.
* Origem do Cloudflare (`/imagens/o/<token>`): token assinado (HMAC do `SECRET_KEY`) de **uma** imagem de catálogo; não recebe URL, então **não é proxy aberto**. Atenção: o token não expira (precisa ser estável para o cache/contagem de transformações) — quem o obtiver lê o original daquela imagem de catálogo; a troca de imagem gera outro uuid/token e invalida o antigo, e a exclusão faz o token dar 404.
* Cache/invalidação: URL do app muda (uuid novo) a cada troca; imagem excluída/trocada dá 404 imediatamente no servidor. Cópias já baixadas pelo navegador (até 24 h) e variantes em cache de CDN (modo cloudflare, até 24 h) podem persistir até expirar.

## Tabelas (migrações `imagens.0001` e `0002`; **nenhuma tabela existente foi alterada**)
Reversíveis (`migrate imagens zero` remove tudo; os objetos no R2 **não** são apagados). Impacto: apenas cria tabelas novas; a `0002` insere 4 tarifas iniciais (idempotente).

**`imagens_imagem`** — metadados do original. `id` bigint PK; `uuid` uuid único (URL pública); `cliente_id` FK→`cliente_cliente` PROTECT (tenant); `entidade_tipo` varchar(20); `entidade_id` bigint (sem FK: serve a vários modelos); `chave` varchar(255) único; `mime` varchar(30); `tamanho` int; `largura`,`altura` int; `checksum` varchar(64) SHA-256; `estado` varchar(14) `pendente|ativa|para_excluir|excluida` (índice); `criado_por_id` FK→`auth_user` SET NULL; `criado_em`, `atualizado_em` datetime; `removida_em` datetime NULL. Restrição `imagem_uma_ativa_por_entidade` (única por `entidade_tipo+entidade_id` quando `estado='ativa'`). Índices `imagem_entidade_idx`, `imagem_cliente_estado_idx`. Todas NOT NULL salvo `criado_por_id` e `removida_em`; sem valores padrão no banco (tabela nova).
**`imagens_tarifaimagens`** — tarifas da estimativa: `chave` (única: `armazenamento|classe_a|classe_b|transformacao`), `preco` decimal(12,6), `franquia` decimal(14,2) (default 0), `moeda` char(3) (USD), `fonte` URL, `consultado_em` date, `atualizado_em`, `atualizado_por_id` FK→`auth_user` SET NULL.
**`imagens_usoimagens`** — contador diário: `dia`, `cliente_id` FK CASCADE, `metrica`, `quantidade` bigint; única (`dia`,`cliente`,`metrica`).
**`imagens_transformacaounica`** — `imagem_id` FK CASCADE, `variante`, `mes` (AAAA-MM), `cliente_id` FK CASCADE; única (`imagem`,`variante`,`mes`).

## Auditoria
Criação/substituição/exclusão de imagem e alteração de tarifa vão para `auditoria_registroauditoria` (módulo `imagens`: ator, unidade, entidade e ID). Nada de URLs assinadas, credenciais ou conteúdo.

## Endpoints (API v1, Swagger em `/api/docs/`, tags `imagens` e `imagens-plataforma`)
| Método e rota | Quem |
|---|---|
| `GET /api/v1/imagens/<tipo>/<id>/` — metadados e URLs das 4 variantes | quem pode ver a entidade |
| `PUT /api/v1/imagens/<tipo>/<id>/` — multipart, campo `imagem` (idempotente) | quem pode alterar |
| `DELETE /api/v1/imagens/<tipo>/<id>/` | quem pode alterar |
| `GET/POST /api/v1/plataforma/imagens/configuracao/` — estado (sem segredos) / testa a conexão | **proprietário** |
| `GET /api/v1/plataforma/imagens/custos/?mes=AAAA-MM` | **proprietário** |
| `GET/PATCH /api/v1/plataforma/imagens/tarifas/` | **proprietário** |

`<tipo>` ∈ `insumo | produto | usuario | cliente`. As URLs de variante apontam para o **app** (exigem o token Bearer da API ou a sessão), nunca para o R2. Telas web equivalentes: `POST /imagens/<tipo>/<id>/enviar|excluir`, painel em `/plataforma/imagens` (menu do usuário, só superusuário).

## Proprietário da plataforma
É o **superusuário** (`is_superuser`). O grupo “Administrador” (global) e administradores de tenant **não** acessam painel, configuração, custos nem tarifas — validado na rota/view **e** no serviço (`PATCH` de tarifas), com 403. Não foi criado papel novo.

## Custos
Ver docstring de `custos.py` e o painel: fórmulas, franquias, arredondamento (unidade começada é cobrada), premissas e limites. É **estimativa**, não a fatura. Tarifas iniciais (consulta em 2026-10-17): R2 Standard US$ 0,015/GB-mês (10 GB grátis), Classe A US$ 4,50/milhão (1 mi grátis), Classe B US$ 0,36/milhão (10 mi grátis), saída de dados grátis — <https://developers.cloudflare.com/r2/pricing/>; Image Transformations US$ 0,50/mil únicas (5 mil incluídas; origem remota cobra só “Images Transformed”) — <https://developers.cloudflare.com/images/pricing/>. Tarifas **não** estão no código: ficam na tabela e se editam no painel.

## Testes e verificação
```
.venv/Scripts/python.exe manage.py test apps.imagens
```
42 testes: validação (SVG, corrompido, limites, bomba), EXIF removido, variantes, ciclo de vida, falhas parciais e compensação, retenção e órfãos, isolamento entre tenants, módulo desabilitado, origem assinada, limite por minuto, cálculo de custo, painel/API só do proprietário, auditoria, schema OpenAPI. **Não cobrem** a Cloudflare real (ver README_CLOUDFLARE.md, seção de validação).

## Limitações conhecidas
* Cada leitura “local” decodifica o original (cache de 1 h por processo; com vários processos use um cache compartilhado em `CACHES`).
* Armazenamento histórico é estimado por datas de criação/remoção, não por medição diária.
* Sem listagem/exportação de imagens e sem detalhamento de custo por tenant (apenas totais globais).
* Modo `cloudflare` e AVIF não foram exercitados contra uma zona real.
