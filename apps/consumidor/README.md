# Consumidores (clientes finais)

Cadastro dos **clientes finais** de cada empresa (quem compra no PDV, faz pedidos e recebe cotações). Não confundir com `apps/cliente`, que é o **tenant** (a empresa que usa o sistema): o campo `cliente` do consumidor aponta para a empresa/unidade dona do cadastro.

## Regras

- **Tenant:** cada consumidor pertence a uma unidade (matriz ou filial) e segue o escopo de `web_project/acesso.py` (a matriz só alcança as filiais com `cliente.<ação>_itensfilial`). A unidade vem do usuário autenticado, nunca do corpo da requisição. Fora do escopo = 404.
- **Validação no model** (`Consumidor.clean`, vale para tela, API e qualquer gravação): nome obrigatório; CPF (PF) ou CNPJ (PJ) opcional, com dígitos verificadores, gravado só com dígitos e **único por unidade** (vários consumidores sem documento são permitidos); telefone 10–11 dígitos; WhatsApp 10–13 (DDI 55 opcional); Instagram sem `@` (aceita URL), `[a-z0-9._]{1,30}`; CEP 8 dígitos; UF válida; nascimento entre 1900 e hoje.
- **LGPD**
  - *Consentimento:* caixa `consentimento_lgpd`; a data é gravada ao marcar e limpa ao retirar. É um registro, **não bloqueia** o cadastro (decisão de negócio a confirmar).
  - *Minimização:* só dados necessários às funções previstas; documento é **mascarado em listas** (`***.982.247-**`) e a lista/API-lista não trazem contatos nem endereço completo.
  - *Auditoria:* documento, e-mail, telefones, Instagram, nascimento e endereço **não vão para a trilha** (`AUDITORIA_SEM_CONTEUDO`): registra-se *que* mudaram, `(conteúdo omitido)`.
  - *Exclusão = anonimização:* a ação (tela: ícone de usuário riscado; API: `DELETE`) apaga documento, contatos, endereço, nascimento e consentimento, troca o nome por `Consumidor anonimizado #id`, desativa e grava `anonimizado_em`. É **irreversível** e mantém o id para vendas/pedidos futuros. Registros de auditoria *anteriores* (criação/edição) guardam o nome da época: aplique a política de retenção da empresa. **Prazo de retenção automático não foi definido** (pendente).
  - Não há exportação em massa de consumidores nesta fase.
- **Consultas externas** (`services.py`): CEP → **ViaCEP**; CNPJ → **BrasilAPI**. CPF não tem consulta pública legítima (só validação dos dígitos). Regras: só dígitos validados entram na URL; resultado é **sugestão** que pré-preenche o formulário (nunca grava sozinho, com aviso de conferência); timeout de 5 s, teto de 200 KB, cache de 1 h e limite de **30 consultas/min por usuário** (429); indisponibilidade → 503 e o cadastro segue manual. Do CNPJ só vêm razão social, fantasia, endereço, telefone e situação (sem sócios nem e-mail). O CNPJ consultado é enviado ao provedor; nenhum dado do tenant é enviado. Exige permissão de **criar**.

## Telas e rotas (`module_required("consumidor", ...)`)

`consumidor` (lista: pesquisa por nome/cidade/trecho do documento, status, ordenação, paginação, filtro de unidade), `consumidor/novo`, `consumidor/<id>/editar` (não abre anonimizado), `consumidor/<id>/anonimizar` (POST), `consumidor/consulta-cep?cep=` e `consumidor/consulta-cnpj?cnpj=` (JSON).

## API (`/api/v1/consumidores/`, módulo `consumidor`)

| Método | Rota | Descrição | Permissão |
|---|---|---|---|
| GET | `/consumidores/` | Lista paginada (`q`, `ativo`, `ordenar`). **Sem contatos; documento mascarado** | `view_consumidor` |
| GET | `/consumidores/{id}/` | Detalhe completo | `view_consumidor` |
| POST | `/consumidores/` | Cria (unidade = a do usuário) | `add_consumidor` |
| PUT/PATCH | `/consumidores/{id}/` | Atualiza (PATCH mescla com o atual) | `change_consumidor` |
| DELETE | `/consumidores/{id}/` | **Anonimiza** (204) | `delete_consumidor` |
| GET | `/consumidores/consulta-cep/{cep}/` | Sugestão de endereço (ViaCEP) | `add_consumidor` |
| GET | `/consumidores/consulta-cnpj/{cnpj}/` | Sugestão de dados do CNPJ (BrasilAPI) | `add_consumidor` |

Erros: 400 validação/CEP ou CNPJ inválido, 403 sem permissão, 404 fora do tenant ou não encontrado, 409/400 documento repetido, 429 limite de consultas, 503 provedor indisponível.

## Banco de dados

Migration `consumidor.0001_consumidores` (cria a tabela; nada existente é alterado) e `modulo.0011_modulo_consumidor` (catálogo: módulo `consumidor`, ordem 45, nos segmentos padrão e habilitado para clientes que já têm segmento; as permissões dos perfis são completadas pelo `post_migrate` do app `acl`).

**`consumidor_consumidor`** — consumidores (clientes finais) por unidade.

| Coluna | Tipo | Nulo | Padrão | Observação |
|---|---|---|---|---|
| `id` | bigint PK | não | auto | |
| `cliente_id` | FK `cliente_cliente` (PROTECT) | não | — | Unidade dona (tenant) |
| `nome` | varchar(150) | não | — | Nome/razão social; anonimizado vira `Consumidor anonimizado #id` |
| `tipo_pessoa` | varchar(2) | não | `PF` | `PF`/`PJ` |
| `documento` | varchar(18) | não | `''` | Só dígitos (CPF 11/CNPJ 14). Único por (`cliente_id`, `documento`) quando não vazio |
| `email` | varchar(254) | não | `''` | minúsculo |
| `telefone`, `whatsapp` | varchar(20) | não | `''` | só dígitos |
| `instagram` | varchar(40) | não | `''` | sem `@` |
| `data_nascimento` | date | **sim** | NULL | |
| `cep` | varchar(9) | não | `''` | 8 dígitos |
| `logradouro`, `bairro`, `cidade` | varchar(150/100/100) | não | `''` | |
| `numero` | varchar(20) | não | `''` | |
| `complemento` | varchar(100) | não | `''` | |
| `uf` | varchar(2) | não | `''` | |
| `consentimento_lgpd` | bool | não | `false` | |
| `consentimento_em` | datetime | **sim** | NULL | preenchido pelo sistema |
| `ativo` | bool | não | `true` | |
| `anonimizado_em` | datetime | **sim** | NULL | preenchido ao anonimizar |
| `criado_em`, `atualizado_em` | datetime | não | auto | |

Índices: (`cliente_id`, `ativo`), (`cliente_id`, `nome`); restrição única parcial `consumidor_documento_unico_por_unidade`. Impacto em dados existentes: nenhum (tabela nova). Origem dos dados: digitação do usuário e, opcionalmente, sugestão de ViaCEP/BrasilAPI conferida por ele.

## Permissões

`consumidor.view/add/change/delete_consumidor`, entregues pelo `seed_acl`/`post_migrate` conforme os padrões dos perfis (`apps/acl/padroes.py`, app em `SCREEN_APPS`): Administrador (tudo), Gestor (ver/criar/editar), Operador (ver/criar), Visualizador (ver). **Anonimizar exige `delete_consumidor`** (só Administrador por padrão).

## Arquivos

`models.py` (campos e validação), `forms.py`, `services.py` (anonimização e consultas externas), `views.py`, `urls.py`, `templates/`, `tests.py`; API em `apps/api/consumidores.py`.
