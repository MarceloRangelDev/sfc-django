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

O usuário Gestor define a senha pelo fluxo **"Esqueci minha senha"** (não há senha padrão). Se o `username` (nome do cliente) já existir, o CPF/CNPJ é acrescentado.

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
