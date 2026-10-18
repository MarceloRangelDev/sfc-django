# Insumos globais (cadastro padrão) e não conformidade

Cadastro **global** (de toda a plataforma) de insumos padronizados. O usuário encontra o insumo já padronizado; se não existir, cadastra o dele e o administrador padroniza depois.

## Fluxo
1. **Cadastro de insumo** (tela e formulário do app `insumo`): o nome digitado é comparado ao cadastro global *sem acento, caixa e espaços repetidos* (`models.normalizar`).
   * **Existe** (ex.: `acucar` → “Açúcar”): o insumo usa o **nome padrão** e fica vinculado (`Insumo.insumo_global`).
   * **Não existe, mas lembra um padrão** (similaridade ≥ 0,8, `difflib`; ex.: `Asucar`): o formulário bloqueia e lista os padrões; o usuário usa o nome padrão **ou** marca “Cadastrar mesmo assim”.
   * **Novo de verdade** (ou confirmado): é **salvo** com `Insumo.nao_conforme = True` e os administradores recebem uma **notificação** (sino) com link para a fila.
   * Editar sem trocar o nome não reavalia a padronização.
   * O campo nome tem autocompletar (`<datalist>`) com os padrões ativos.
2. **Administrador** (permissões abaixo) abre **Insumos fora do padrão** e, para cada insumo:
   * **Redirecionar** para um padrão existente: se a unidade do cliente **não** tem o padrão, o insumo é **renomeado** e vinculado; se **já tem**, as referências (fichas, estoque, cotações etc., todas as FKs para `Insumo`) são **movidas** para o insumo padronizado e o duplicado é **excluído** (ou **inativado**, se houver histórico imutável de preços/estoque que impede a exclusão). Se mover causar duplicidade (ex.: mesma ficha com os dois), a operação é desfeita com mensagem.
   * **Padronizar como novo**: o nome vira um novo padrão global (com a unidade do insumo) e o insumo passa a conforme.
3. **Insumos globais**: CRUD do padrão (nome único, unidade sugerida, descrição, **foto** via módulo de imagens, ativar/inativar; nunca se apaga). A foto do padrão aparece, como miniatura, nos insumos do cliente que não têm foto própria.

## Permissões (Papéis e Permissões especiais do usuário)
| Módulo (Configurações) | Permissão | Padrão |
|---|---|---|
| Insumos globais | `insumoglobal.view/add/change/delete_insumoglobal` | só **Administrador** |
| Insumos fora do padrão | `insumoglobal.view_naoconformes` | só **Administrador** |
| (extra) Padronizar insumos | `insumoglobal.padronizar_insumo` (promover/redirecionar) | só **Administrador** |

Quem recebe `view_naoconformes` (por papel ou permissão individual) também é notificado. Ver ≠ padronizar: redirecionar/promover exige `padronizar_insumo`. As telas usam `module_required` (rota + permissão) — esconder o menu não é o controle.

## Dados (migrações `insumoglobal.0001/0002`, `insumo.0011`)
* `insumoglobal_insumoglobal`: `id`, `uuid` (único), `nome`, `nome_normalizado` (**único**, calculado no `save`), `medida_id` (FK `medida`, PROTECT, null), `descricao`, `ativo`, `criado_por_id` (SET NULL), `criado_em`, `atualizado_em`.
* `insumo_insumo` (aditiva): `insumo_global_id` (FK, PROTECT, **null**) e `nao_conforme` (bool, `db_default=False`). Insumos existentes ficam sem vínculo e conformes (nada é alterado); reversível.

## Limitações
* A API REST de insumos **não** aplica a padronização (só o formulário web); insumos criados por ela ficam sem vínculo/conformes.
* A similaridade é textual (não entende sinônimos, como “sacarose” ≈ “açúcar”): o administrador resolve na fila.
* Não há importação em lote do cadastro global.

## Testes
`manage.py test apps.insumoglobal` (padronização no cadastro, aviso, permissões, redirecionar/mesclar, promover, thumbs, exportações, logo nos PDFs, foto de perfil, menu lateral por papel).
