# Insumos globais (cadastro padrão) e não conformidade

Cadastro **global** (de toda a plataforma) de insumos padronizados. O usuário encontra o insumo já padronizado; se não existir, cadastra o dele e o administrador padroniza depois.

## Fluxo
1. **Cadastro de insumo** (tela e formulário do app `insumo`): o campo **Insumo** é uma lista dos insumos globais do segmento do cliente, com a opção **“Personalizar (não está na lista)”**. Escolher da lista usa o nome e o vínculo do padrão; as marcas (fabricantes) dele são sugeridas no campo Marca e a foto vem da marca escolhida. **Personalizar** exige digitar o nome. Antes (versão anterior) valia a regra abaixo, que continua para o nome personalizado: o nome digitado é comparado ao cadastro global *sem acento, caixa e espaços repetidos* (`models.normalizar`).
   * **Existe** (ex.: `acucar` → “Açúcar”): o insumo usa o **nome padrão** e fica vinculado (`Insumo.insumo_global`).
   * **Personalizado** (não existe no cadastro): é **salvo** com `Insumo.nao_conforme = True` e os administradores recebem uma **notificação** (sino) com link para a fila.
   * Editar sem trocar o nome não reavalia a padronização.
   * Na lista de insumos do cliente, o personalizado aparece com o selo “personalizado”.
2. **Administrador** (permissões abaixo) abre **Insumos fora do padrão** e, para cada insumo:
   * **Redirecionar** para um padrão existente: se a unidade do cliente **não** tem o padrão, o insumo é **renomeado** e vinculado; se **já tem**, as referências (fichas, estoque, cotações etc., todas as FKs para `Insumo`) são **movidas** para o insumo padronizado e o duplicado é **excluído** (ou **inativado**, se houver histórico imutável de preços/estoque que impede a exclusão). Se mover causar duplicidade (ex.: mesma ficha com os dois), a operação é desfeita com mensagem.
   * **Padronizar como novo**: o nome vira um novo padrão global (com a unidade do insumo) e o insumo passa a conforme.
3. **Insumos globais**: CRUD do padrão (nome único, unidade sugerida, descrição, segmentos, ativar/inativar; nunca se apaga). **O insumo global NÃO tem foto própria: a foto é só a da marca (variação)**, e aparece na lista de globais e na tela de insumos do cliente.

## Marcas (1 insumo → N marcas, com foto por marca)
* `MarcaGlobal`: a marca padronizada (nome único sem acento/caixa; serve a vários insumos).
* `InsumoGlobalMarca` (**variação**): insumo padrão + marca (única por par), ativa/inativa, com a **sua foto** (módulo de imagens, tipo `insumo_global_marca`).
* O administrador gerencia as variações na **edição do insumo global**, card “Marcas deste insumo”: digitar a marca cria a `MarcaGlobal` (se nova) e a variação; cada variação recebe a foto; inativar só deixa de oferecê-la.
* No **insumo do cliente**, o campo Marca continua texto livre (com autocompletar das marcas globais). Se o insumo é padrão e a marca digitada (sem acento/caixa) é uma variação ativa dele, o insumo ganha `insumo_global_marca`, a marca vira o nome padrão e a **foto da marca** aparece na tabela. Marca desconhecida segue como texto livre e **não** torna o insumo não conforme. Redirecionar/promover também tentam vincular a marca.
* **Cadastro com marca**: o formulário do insumo global tem o campo Marca. Cadastrar um nome que já existe (sem acento/caixa) **com** marca não duplica o padrão: acrescenta a marca como nova variação do existente; **sem** marca continua recusado. Na edição, o campo Marca adiciona uma variação. A lista mostra as marcas de cada padrão.
* Ordem da miniatura: foto da variação (marca) → ícone “sem foto” (fotos antigas enviadas diretamente num insumo ainda aparecem, mas o formulário não permite mais enviar).
* Tabelas: `insumoglobal_marcaglobal` (`nome`, `nome_normalizado` único, `ativo`), `insumoglobal_insumoglobalmarca` (`insumo_global_id` CASCADE, `marca_id` PROTECT, `ativo`; único por par), e `insumo_insumo.insumo_global_marca_id` (FK null, SET NULL; migrações `insumoglobal.0003/0004`, `insumo.0012`).
* Fora do escopo por ora: tela própria de marcas globais, fila de marcas fora do padrão para o administrador e preço/embalagem por marca.

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
