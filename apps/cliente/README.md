# apps/cliente — Clientes, Matriz e Filiais

## Objetivo

Um **Cliente** é a empresa (ou pessoa física) dona dos dados — o *tenant* do sistema. Este app cadastra clientes (CPF/CNPJ), organiza **Matriz/Filiais**, define o **Segmento de Trabalho** e, ao criar um cliente, cria automaticamente o seu **usuário Gestor**.

## Estrutura

| Arquivo | Papel |
|---|---|
| `models.py` | `Cliente` e as regras de Matriz/Filial (`Cliente.clean`). |
| `forms.py` | `ClienteForm`: campos por tipo de solicitante (Admin x demais). |
| `services.py` | `criar_cliente()`: cliente + Gestor + módulos + auditoria, numa transação. Usado pela tela **e** pela API. |
| `views.py` | Lista (filtros `q` nome/fantasia/CPF-CNPJ, `tipo_pessoa`, `tipo_estabelecimento`, `segmento`, `status`, `per_page`; paginação no banco, página fora do intervalo cai na última), criação, edição e vínculo de usuários — sempre dentro de `clientes_acessiveis`. |
| `urls.py` | `clientes`, `clientes/novo`, `clientes/<id>/editar`, vincular/desvincular usuário. |
| `validators.py` | CPF/CNPJ (dígitos verificadores) e máscara. |
| `admin.py` | Registro no Django Admin. |
| `templates/` | `cliente_list.html`, `cliente_form.html`. |
| `tests_listagem.py` | Filtros, paginação e isolamento entre tenants na lista. |
| `tests.py`, `tests_estrutura.py` | Validadores/modelo e regras de estrutura, isolamento, Gestor padrão e Admin. |

## Relacionamentos

```text
Cliente ──matriz──▶ Cliente (auto-relacionamento; filiais = related_name "filiais")
Cliente ──segmento─▶ modulo.Segmento ──modulos──▶ modulo.Modulo
Cliente ◀──cliente── accounts.Profile (usuários)   (PROTECT)
Cliente ◀──cliente── ClienteModulo (habilitação por módulo)
Cliente ──perfis──▶ auth.Group (perfis liberados para os usuários dele)
```

## Fluxo de criação de cliente

```text
POST /clientes/novo
 ↓ ClienteCreateView.form_valid
 ↓ services.criar_cliente(request, cliente, perfis, email_gestor)   [transaction.atomic]
     1. cliente.save() + perfis (Gestor e Operador sempre incluídos)
     2. ClienteModulo ativo para cada módulo do segmento
     3. User "<nome do cliente>" (senha inutilizável) + Profile.cliente + grupo Gestor
     4. registrar_criacao (cliente) e registrar_criacao (usuário)
 ↓ erro em qualquer passo => nada é gravado
```

**Usuário inicial (atualizado)**: o cadastro agora pede **nome do responsável, usuário (login) e e-mail** (obrigatórios; `usuario_nome`, `usuario_username`, `email_gestor` na tela e na API) e **nada é criado “por conta própria”**. O login é validado (único, sem diferenciar maiúsculas, também no serviço, contra corrida). Nenhuma senha é digitada: a senha continua inutilizável e o responsável recebe, **depois do commit**, o e-mail com o link de definição de senha (o mesmo do “Esqueci minha senha”, válido 24 h). Falha no envio do e-mail **não desfaz** o cadastro (fica registrada no log; o link pode ser pedido em “Esqueci minha senha”). Chamadas antigas de `criar_cliente(request, cliente, perfis, email_gestor)` ainda funcionam e caem no padrão anterior (nome/login derivados da razão social), mas a tela e a API exigem os novos campos. O usuário nasce no grupo **Gestor**, vinculado ao cliente.

**Contato da empresa (novo)**: `endereco`, `cidade`, `uf`, `email_contato`, `whatsapp` (todos opcionais) aparecem no formulário e saem no cabeçalho dos documentos emitidos (PDF do orçamento). A logo continua sendo enviada pelo módulo de imagens (entidade `cliente`). O WhatsApp é normalizado (`+` e dígitos, 10 a 15) e a UF vira maiúscula.

## Banco de dados — tabela `cliente_cliente`

| Coluna | Tipo | Obrigatório | Descrição | Relacionamento |
|---|---|---|---|---|
| `id` | bigint PK | sim | Identificador | — |
| `tipo_pessoa` | char(2) | sim | `PF` ou `PJ` | — |
| `cpf_cnpj` | varchar(18) | sim | Documento **normalizado** (só dígitos) | **unique**, índice |
| `nome_razao_social` | varchar(200) | sim | Nome / razão social | — |
| `nome_fantasia` | varchar(200) | não | Nome fantasia | — |
| `ativo` | bool | sim | Cliente ativo | — |
| `tipo_estabelecimento` | varchar(10) | sim | `matriz` (padrão) ou `filial` | — |
| `matriz_id` | bigint | só filial | Matriz da filial | FK `cliente_cliente` (PROTECT) |
| `segmento_id` | bigint | não* | Segmento de trabalho | FK `modulo_segmento` (PROTECT) |
| `sistema` | bool | sim | Marca o cliente dono do sistema (único onde o Admin pode existir). **Não editável em telas** | — |
| `endereco` | varchar(255) | não (`''`) | Endereço da empresa (documentos emitidos) | — |
| `cidade` | varchar(100) | não (`''`) | Cidade | — |
| `uf` | varchar(2) | não (`''`) | UF (maiúscula) | — |
| `email_contato` | varchar(100) | não (`''`) | E-mail de contato da empresa (≠ e-mail de login) | — |
| `whatsapp` | varchar(20) | não (`''`) | WhatsApp da empresa (`+` e dígitos) | — |
| `criado_em` / `atualizado_em` | datetime | sim | Controle | — |

\* Sem segmento o cliente só usa módulos essenciais (Configurações).

Tabela `cliente_cliente_perfis` (M2M): `cliente_id` + `group_id`, única por par.

### Regras (em `Cliente.clean`, valem para qualquer forma de gravação)

- CPF/CNPJ validado matematicamente e gravado sem máscara.
- **Filial**: só CNPJ; exige `matriz`; a matriz precisa ser CNPJ do tipo *matriz*; não pode ser matriz de si mesma; uma matriz que tem filiais não vira filial.
- **Matriz** não pode ter `matriz`.
- Quem **não é Admin** só cadastra **filiais da própria matriz** (herdam o segmento dela) e nunca vê outra estrutura.

## Código complexo

- **Isolamento**: `ClienteFormKwargsMixin.get_queryset` devolve `clientes_acessiveis(request.user)`. Trocar o `pk` na URL de `/clientes/10/editar` para outro cliente fora da estrutura resulta em **404** (view e API).
- **Cliente do sistema** (`sistema=True`): marcado pela migration `0006` quando o nome é `SISTEMA FATURANDO CERTO SERVIÇOS LTDA`. Para marcar manualmente: `Cliente.objects.filter(pk=...).update(sistema=True)` (o campo não aparece em formulários de propósito).

## Manutenção

| Se eu precisar... | Altere... |
|---|---|
| nova regra de Matriz/Filial | `Cliente._validar_estrutura` |
| mudar o que o Gestor padrão recebe | `services.criar_cliente` e `seed_acl` (permissões do grupo Gestor) |
| campo novo no cadastro | `models.py` → `makemigrations` → `forms.py` → `cliente_form.html` → `api/serializers.py::ClienteSerializer` |
| quem enxerga quais clientes | `web_project/acesso.py::clientes_acessiveis` |

## Migração desta atualização

`cliente.0010_contato_da_empresa`: acrescenta as cinco colunas acima, todas **opcionais com padrão vazio** (`db_default`), sem efeito nos dados existentes; reversível. Clientes existentes continuam como estão (sem contato preenchido até alguém editar). `python manage.py migrate cliente`.