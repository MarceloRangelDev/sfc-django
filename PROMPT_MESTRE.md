# PROMPT MESTRE — ATUALIZAÇÃO E REESTRUTURAÇÃO DO PROJETO

## 1. OBJETIVO GERAL

Atualize e refatore o projeto existente mantendo todas as funcionalidades atuais que já estejam funcionando.

O objetivo é transformar o projeto em uma aplicação:

* mais organizada;
* mais simples de manter;
* mais segura;
* mais enxuta;
* com código limpo;
* com menor duplicação de código;
* com regras de negócio centralizadas;
* preparada para futuras aplicações mobile;
* com API documentada;
* com auditoria completa das operações;
* com controle de acesso por perfil;
* com tratamento correto de idioma, acentuação e UTF-8;
* com documentação suficiente para que um desenvolvedor iniciante consiga compreender e manter o sistema.

### REGRA PRINCIPAL

**NÃO REESCREVA O SISTEMA DESNECESSARIAMENTE.**

Antes de criar qualquer coisa:

1. Analise a estrutura atual do projeto.
2. Identifique as funcionalidades existentes.
3. Identifique modelos, views, forms, services, templates, URLs, APIs e JavaScript existentes.
4. Reaproveite o que já estiver correto.
5. Corrija somente o que for necessário.
6. Evite criar estruturas duplicadas.
7. Evite abstrações desnecessárias.
8. Prefira soluções simples, claras e fáceis de entender.
9. Não remova funcionalidades existentes sem justificativa técnica.
10. Preserve compatibilidade com os dados existentes sempre que possível.

---

# 2. PRINCÍPIOS DE DESENVOLVIMENTO

Todo o desenvolvimento deverá seguir os seguintes princípios:

### Código

Utilizar:

* Clean Code;
* DRY;
* KISS;
* SOLID quando realmente fizer sentido;
* baixo acoplamento;
* alta reutilização;
* funções pequenas;
* classes com responsabilidades claras;
* nomes descritivos;
* código simples.

Evitar:

* código duplicado;
* funções gigantes;
* lógica de negócio dentro de templates;
* regras de negócio espalhadas em várias views;
* JavaScript duplicado;
* consultas ao banco desnecessárias;
* abstrações excessivamente complexas;
* criação de classes apenas para "seguir padrão" sem necessidade.

### REGRA DE SIMPLICIDADE

Se uma solução simples resolver o problema, NÃO crie uma solução complexa.

Exemplo:

Preferir:

```python
resultado = converter_medida(valor, origem, destino)
```

em vez de criar várias classes desnecessárias para realizar uma conversão simples.

---

# 3. ANÁLISE OBRIGATÓRIA ANTES DAS ALTERAÇÕES

Antes de modificar o código, faça uma análise completa do projeto.

Identifique:

* framework;
* versão;
* banco de dados;
* aplicações instaladas;
* modelos;
* relacionamentos;
* URLs;
* views;
* forms;
* templates;
* JavaScript;
* CSS;
* APIs existentes;
* autenticação;
* autorização;
* middleware;
* arquivos de configuração;
* variáveis de ambiente;
* sistema de auditoria existente;
* tabela `auditoria_registroauditoria`;
* tabela `cliente_cliente`;
* tabela `auth_user`;
* sistema atual de perfis/ACL;
* módulo atual de insumos;
* sistema atual de unidades de medida.

Antes de alterar estruturas existentes, verifique como elas são utilizadas.

---

# 4. API COMPLETA DO SISTEMA

Criar/habilitar uma API para todas as funcionalidades existentes no sistema que possam ser utilizadas futuramente por aplicações mobile.

A API deverá contemplar, quando aplicável:

* cadastro;
* login;
* logout;
* recuperação de senha;
* alteração de senha;
* usuários;
* clientes;
* perfis;
* permissões;
* insumos;
* medidas;
* produtos;
* categorias;
* fichas técnicas;
* receitas;
* demais módulos existentes no sistema;
* consultas;
* criação;
* atualização;
* exclusão;
* ativação/inativação;
* demais operações existentes.

### IMPORTANTE

Não criar endpoints fictícios.

Primeiro identifique os recursos realmente existentes no sistema e crie os endpoints correspondentes.

---

# 5. API REST

Estruturar a API de maneira RESTful.

Utilizar:

```text
GET     consulta
POST    criação
PUT     atualização completa
PATCH   atualização parcial
DELETE  exclusão
```

Utilizar respostas HTTP adequadas:

```text
200 OK
201 Created
204 No Content
400 Bad Request
401 Unauthorized
403 Forbidden
404 Not Found
409 Conflict
422 Unprocessable Entity
500 Internal Server Error
```

As respostas deverão possuir estrutura consistente.

Exemplo:

```json
{
    "success": true,
    "message": "Insumo criado com sucesso.",
    "data": {}
}
```

Para erros:

```json
{
    "success": false,
    "message": "Não foi possível criar o insumo.",
    "errors": {}
}
```

---

# 6. SEGURANÇA DA API

A API deverá ser preparada para utilização futura em aplicativos mobile.

Implementar, conforme a arquitetura existente:

* autenticação segura;
* autorização;
* controle de permissões;
* proteção contra acesso não autorizado;
* validação de dados;
* proteção contra mass assignment;
* proteção contra SQL Injection;
* proteção contra CSRF quando aplicável;
* rate limiting para endpoints sensíveis;
* proteção de endpoints administrativos;
* não expor informações sensíveis;
* não retornar senhas;
* não retornar hashes de senha;
* não retornar tokens de terceiros;
* não confiar em IDs enviados pelo cliente sem validação de autorização;
* validar propriedade dos registros;
* utilizar HTTPS em produção;
* utilizar variáveis de ambiente para informações sensíveis.

Endpoints administrativos deverão exigir autenticação e permissão apropriadas.

---

# 7. DOCUMENTAÇÃO DA API

Criar documentação visual da API semelhante ao Swagger.

Preferencialmente utilizar:

**OpenAPI + Swagger UI**

A documentação deverá apresentar:

* endpoints;
* métodos;
* parâmetros;
* autenticação;
* exemplos de requisição;
* exemplos de resposta;
* códigos HTTP;
* schemas;
* erros possíveis.

Criar uma área/documentação que permita ao administrador testar os endpoints.

Exemplo:

```text
/api/docs/
```

ou estrutura equivalente conforme a arquitetura atual.

### IMPORTANTE

A documentação deverá ser gerada a partir do código sempre que possível, evitando duplicação manual.

---

# 8. AUDITORIA COMPLETA

Toda operação de CRUD deverá ser registrada na tabela:

```text
auditoria_registroauditoria
```

Não criar outro sistema de auditoria se a tabela existente puder ser reutilizada.

Auditar:

* CREATE;
* UPDATE;
* DELETE.

Também auditar alterações administrativas relevantes quando fizer sentido.

---

# 9. REGRA DA AUDITORIA

## CREATE

Quando um registro for criado, registrar:

* usuário;
* data/hora;
* módulo;
* tabela;
* ID do registro;
* nome/descrição do registro;
* ação realizada;
* descrição.

Exemplo:

```text
Criado insumo ID 123 - Queijo Mussarela.
```

---

## UPDATE

Quando um registro for atualizado, registrar:

* ID;
* nome;
* usuário;
* data/hora;
* campos alterados;
* valor anterior;
* valor novo.

Exemplo:

```text
Atualizado insumo ID 123 - Queijo Mussarela.

Alterações:
- Preço: R$ 35,00 → R$ 40,00
- Quantidade: 10 → 15
- Unidade: Quilo → Gramas
```

Não registrar campos que não sofreram alteração.

---

## DELETE

Quando um registro for excluído, registrar:

```text
Excluído insumo ID 123 - Queijo Mussarela.
```

Se a exclusão for lógica, informar claramente:

```text
Insumo ID 123 - Queijo Mussarela foi inativado.
```

---

# 10. AUDITORIA REUTILIZÁVEL

Não duplicar código de auditoria em cada CRUD.

Criar uma solução reutilizável.

Exemplo conceitual:

```python
registrar_auditoria(
    usuario=usuario,
    acao="UPDATE",
    objeto=objeto,
    alteracoes=alteracoes
)
```

A implementação deverá ser compatível com a estrutura atual do projeto.

---

# 11. TEMPLATE DE AUDITORIA

Criar um módulo/template administrativo para consulta das auditorias.

Permitir:

* listagem;
* paginação;
* pesquisa;
* filtro por usuário;
* filtro por módulo;
* filtro por ação;
* filtro por período;
* visualização dos detalhes.

Exemplo:

```text
Auditoria
------------------------------------------
Data | Usuário | Módulo | Ação | Registro
```

Ao abrir o registro:

```text
Detalhes da Auditoria

Usuário:
Data:
Módulo:
Tabela:
Registro:
Ação:

Descrição:
...

Alterações:
Campo | Antes | Depois
```

---

# 12. CLASSE CENTRAL DE REGRAS DE NEGÓCIO

Criar uma classe/service centralizada para regras de negócio globais que possam ser reutilizadas em todo o sistema.

Não espalhar regras matemáticas por views, templates ou JavaScript.

Criar uma estrutura simples e reutilizável.

Exemplo conceitual:

```python
class RegraNegocio:
    ...
```

ou, caso a arquitetura atual seja melhor atendida por services:

```python
class ConversaoService:
    ...
```

A estrutura final deverá respeitar a arquitetura existente.

---

# 13. CONVERSÃO DE UNIDADES

Todas as conversões deverão utilizar a tabela:

```text
medidas
```

Não criar conversões "hardcoded" espalhadas pelo código.

As funções deverão suportar, quando aplicável:

* grama → quilo;
* quilo → grama;
* mililitro → litro;
* litro → mililitro;
* centímetro → metro;
* metro → centímetro;
* polegada;
* colher de sopa;
* colher de chá;
* colher de café;
* copo americano;
* xícara;
* unidade;
* metro quadrado;
* metro cúbico;
* segundos;
* quilômetro.

As conversões devem respeitar as equivalências cadastradas no banco.

---

# 14. VALIDAÇÃO MATEMÁTICA

Toda conversão deverá:

* validar a unidade de origem;
* validar a unidade de destino;
* validar quantidade;
* impedir valores inválidos;
* impedir divisão por zero;
* respeitar fator de conversão;
* respeitar casas decimais;
* utilizar Decimal quando apropriado para valores monetários;
* evitar erros de ponto flutuante em cálculos financeiros.

Exemplo:

```text
1000 g = 1 kg
100 g = 0,1 kg
500 ml = 0,5 L
```

---

# 15. VALOR POR UNIDADE DE MEDIDA

Criar função capaz de determinar o valor equivalente de uma unidade.

Exemplo:

Compra:

```text
100 gramas
R$ 4,00
```

Resultado:

```text
1 kg = R$ 40,00
```

Outro exemplo:

```text
500 ml
R$ 10,00
```

Resultado:

```text
1 litro = R$ 20,00
```

A lógica deverá utilizar a unidade comprada e a quantidade informada.

---

# 16. FORMATAÇÃO POR EXTENSO

Criar função reutilizável que receba:

```text
unidade
quantidade
```

e apresente o valor por extenso.

Exemplo:

```text
100 gramas
```

```text
1 quilo
```

```text
500 mililitros
```

```text
2 litros
```

A função deverá respeitar singular/plural.

Também deverá existir função equivalente para valores monetários.

Exemplo:

```text
R$ 100,00
```

poderá ser apresentado como:

```text
Cem reais
```

---

# 17. MÁSCARAS E VALIDAÇÕES

Criar componentes/funções reutilizáveis para:

* CPF;
* CNPJ;
* telefone;
* celular;
* e-mail;
* moeda brasileira;
* data;
* data e hora;
* números decimais;
* unidades de medida.

As máscaras deverão ser reutilizáveis no sistema inteiro.

Não duplicar a implementação em cada formulário.

Além da máscara visual no frontend, deverá existir validação no backend.

### IMPORTANTE

Máscara NÃO substitui validação no backend.

---

# 18. INDICADORES E REGRAS FINANCEIRAS

Centralizar funções para cálculos utilizados pelo sistema.

Implementar/revisar, conforme aplicabilidade:

* ticket médio;
* markup;
* margem de lucro;
* ponto de equilíbrio;
* CMV;
* precificação por markup;
* precificação por margem de lucro;
* lucro do produto;
* fator de redução;
* custo unitário;
* custo por KG;
* custo por litro;
* custo por unidade;
* outros indicadores de gestão já utilizados pelo sistema.

Todas as fórmulas deverão:

* ser matematicamente coerentes;
* validar entradas;
* impedir divisão por zero;
* utilizar valores monetários adequadamente;
* possuir comentários explicativos;
* ser reutilizáveis.

---

# 19. MÓDULO DE INSUMOS

Atualizar o módulo de insumos.

## IMPORTANTE

**INSUMOS NÃO TERÃO CATEGORIA.**

As categorias serão utilizadas pelos produtos.

Remover a dependência de categoria dos insumos somente se isso não quebrar dados existentes.

Antes de alterar o banco:

1. analisar os relacionamentos atuais;
2. identificar dependências;
3. criar migration;
4. preservar dados quando necessário;
5. garantir que funcionalidades existentes continuem funcionando.

---

# 20. CÁLCULO DO PREÇO DO INSUMO

No cadastro do insumo deverão existir:

* unidade de medida comprada;
* quantidade comprada;
* valor pago.

O sistema deverá calcular automaticamente o valor equivalente.

Exemplo:

```text
Quantidade comprada: 100
Unidade: Gramas
Valor pago: R$ 4,00
```

Resultado:

```text
Preço por KG: R$ 40,00
```

Outro exemplo:

```text
Quantidade: 500
Unidade: Mililitros
Valor: R$ 10,00
```

Resultado:

```text
Preço por L: R$ 20,00
```

---

# 21. LABEL DE PREÇO

Adicionar um label para apresentar o resultado.

Sugestão:

```text
PREÇO (por KG / UN / L)
```

O texto deverá ser dinâmico conforme a unidade aplicável.

Exemplos:

```text
PREÇO (por KG)
```

```text
PREÇO (por L)
```

```text
PREÇO (por UN)
```

---

# 22. QUANTIDADE POR EXTENSO NO INSUMO

Ao selecionar a unidade de medida e digitar a quantidade, apresentar imediatamente abaixo do input:

```text
100 gramas
```

Utilizar a função global criada anteriormente.

Não criar uma função específica somente para o módulo de insumos.

---

# 23. TABELA DE MEDIDAS

Criar tabela:

```text
medidas
```

Campos:

```text
id
medida
sigla
descricao
equivale
ativo
id_usuario
criado_em
atualizado_em
```

### Significado

`medida`

Nome da unidade por extenso.

Exemplo:

```text
Gramas
```

`sigla`

Exemplo:

```text
g
```

`descricao`

Descrição da unidade.

`equivale`

Representação da equivalência.

Exemplo:

```text
1000 g = 1 Kg
```

`ativo`

Booleano:

```text
true
false
```

`id_usuario`

Usuário responsável pelo cadastro.

`criado_em`

Data/hora de criação.

`atualizado_em`

Data/hora de atualização.

---

# 24. CARGA INICIAL DA TABELA MEDIDAS

Criar migration/data migration/fixture apropriada para cadastrar inicialmente, exatamente nesta ordem:

1. Gramas
2. Quilo
3. Unidade
4. Mililitro
5. Litro
6. Colher de chá
7. Colher de sopa
8. Colher de café
9. Copo americano
10. Xícara
11. Polegadas
12. Centímetro
13. Metro
14. Metro quadrado
15. Metro Cúbico
16. Segundos
17. Quilômetro

Não inserir duplicidades caso a migration seja executada novamente.

---

# 25. MÓDULO ADMINISTRATIVO DE MEDIDAS

Criar módulo exclusivo para administradores.

Permitir:

* listar medidas;
* cadastrar;
* editar;
* excluir;
* ativar;
* inativar;
* pesquisar;
* filtrar.

Esse módulo deverá possuir ACL.

Somente usuários autorizados poderão acessá-lo.

---

# 26. EXCLUSÃO DE MEDIDAS

A exclusão deverá respeitar os relacionamentos existentes.

O usuário solicitou exclusão/edição em cascata, porém:

**NÃO implementar CASCADE indiscriminadamente.**

Antes de aplicar:

1. verificar todas as tabelas que utilizam medidas;
2. verificar impacto da exclusão;
3. impedir perda acidental de dados;
4. utilizar transação;
5. aplicar cascade somente onde for matematicamente e funcionalmente seguro.

Caso a unidade esteja sendo utilizada por registros históricos, considerar inativação em vez de exclusão física.

Documentar essa decisão no README.

---

# 27. MÓDULO DE USUÁRIOS

Criar módulo administrativo para criação de usuários.

Os usuários deverão ser criados utilizando:

```text
auth_user
```

Cada usuário deverá ser associado a:

```text
cliente_cliente
```

e a um:

```text
perfil
```

já configurado para aquele cliente.

---

# 28. REGRAS DE USUÁRIO

No cadastro:

* validar CPF/CNPJ quando aplicável;
* validar e-mail;
* impedir e-mail duplicado quando a regra atual exigir;
* definir cliente;
* definir perfil;
* validar se o perfil pertence ao cliente;
* controlar permissões;
* nunca armazenar senha em texto puro;
* utilizar o sistema de hashing nativo do framework.

---

# 29. SEGURANÇA DE ACESSO

Garantir:

```text
Usuário → Cliente → Perfil → Permissões
```

Um usuário não poderá acessar dados de outro cliente sem autorização.

Implementar isolamento de dados por cliente onde aplicável.

Não confiar apenas em filtros enviados pelo frontend.

A validação deverá ocorrer no backend.

---

# 30. CORREÇÃO DO ERRO DO VUEXY

Corrigir:

```text
Uncaught Error: Language "pt-br" not found!
```

Origem:

```text
template-customizer.js
```

O idioma `pt-br` deverá ser corretamente registrado/carregado antes de ser utilizado.

Verificar:

* arquivo de tradução;
* import;
* ordem de carregamento;
* nome do locale;
* build;
* webpack;
* assets;
* configuração do TemplateCustomizer.

Não simplesmente esconder o erro.

A causa deverá ser corrigida.

---

# 31. CORREÇÃO DA CODIFICAÇÃO UTF-8

Corrigir definitivamente problemas como:

```text
PapÃ©is
MÃ³dulos
InformaÃ§Ã£o
```

Garantir UTF-8 em toda a aplicação.

Verificar:

* banco de dados;
* charset;
* collation;
* conexão;
* backend;
* templates;
* HTML;
* HTTP headers;
* arquivos JavaScript;
* arquivos JSON;
* arquivos CSS;
* AJAX/fetch;
* API;
* banco;
* exportações.

Utilizar:

```html
<meta charset="UTF-8">
```

quando aplicável.

Garantir:

```text
UTF-8
```

de ponta a ponta.

### IMPORTANTE

Não corrigir os textos individualmente.

Corrigir a origem do problema de encoding.

---

# 32. COMENTÁRIOS NO CÓDIGO

Comentar o código de maneira didática.

Os comentários devem explicar:

* por que determinada regra existe;
* cálculos;
* decisões importantes;
* regras de negócio;
* integrações;
* comportamentos que não são óbvios.

Não comentar coisas óbvias.

Evitar:

```python
# Soma 1 + 1
resultado = 1 + 1
```

Preferir:

```python
# O cálculo utiliza Decimal para evitar erros de precisão
# em operações financeiras.
resultado = ...
```

---

# 33. DOCUMENTAÇÃO README

Criar/atualizar:

```text
README.md
```

O README deverá explicar detalhadamente o projeto.

Incluir:

## Visão geral

O que o sistema faz.

## Tecnologias

* framework;
* linguagem;
* banco;
* bibliotecas;
* frontend;
* API;
* autenticação.

## Instalação

Passo a passo para Windows e, quando aplicável, Linux.

## Ambiente virtual

Explicar como criar e ativar.

## Dependências

Explicar instalação.

## Variáveis de ambiente

Explicar cada variável.

## Banco de dados

Explicar configuração.

## Migrations

Explicar:

```bash
python manage.py makemigrations
python manage.py migrate
```

ou equivalente.

## Usuário administrador

Explicar criação.

## Execução

Explicar como iniciar o projeto.

## Estrutura de pastas

Explicar cada pasta.

## Autenticação

Explicar login e permissões.

## ACL

Explicar:

```text
Cliente
Perfil
Usuário
Permissão
```

## API

Explicar todos os endpoints.

## Swagger

Explicar onde acessar.

## Auditoria

Explicar como funciona.

## Insumos

Explicar cálculos.

## Medidas

Explicar cadastro e conversões.

## Regras de negócio

Explicar onde estão.

## Máscaras

Explicar onde estão.

## Indicadores

Explicar cada cálculo.

## Encoding

Explicar que o sistema utiliza UTF-8.

## Manutenção

Explicar como um desenvolvedor deve adicionar:

* novo módulo;
* novo endpoint;
* novo cálculo;
* nova medida;
* nova auditoria;
* nova permissão.

---

# 34. DOCUMENTAÇÃO PARA LEIGOS

O README deverá ser escrito de forma didática.

Não assumir conhecimento avançado.

Quando utilizar termos técnicos, explicar.

Exemplo:

```text
Migration:
É o mecanismo utilizado pelo Django para registrar alterações
na estrutura do banco de dados.
```

---

# 35. TESTES

Criar ou atualizar testes para as funcionalidades críticas.

Priorizar:

### Conversões

```text
1000 g = 1 kg
1 kg = 1000 g
500 ml = 0,5 L
```

### Valores

```text
100 g = R$ 4
1 kg = R$ 40
```

### Auditoria

Testar:

```text
CREATE
UPDATE
DELETE
```

### API

Testar:

```text
login
autorização
CRUD
permissões
erros
```

### Segurança

Testar acesso de um cliente aos dados de outro cliente.

### Validações

Testar:

* CPF;
* CNPJ;
* e-mail;
* moeda;
* datas;
* unidades;
* valores inválidos.

---

# 36. TRANSAÇÕES

Operações críticas deverão utilizar transações quando necessário.

Especialmente:

* exclusões;
* operações em cascata;
* alterações que envolvam múltiplas tabelas;
* criação de usuário + cliente/perfil;
* operações de auditoria;
* cálculos que dependam de múltiplas alterações.

Nunca deixar o banco em estado parcialmente atualizado.

---

# 37. PERFORMANCE

Revisar consultas existentes.

Evitar:

* N+1 queries;
* consultas repetidas;
* carregamento desnecessário;
* processamento pesado no template.

Utilizar adequadamente:

* `select_related`;
* `prefetch_related`;
* filtros no banco;
* paginação;
* índices;
* cache quando realmente necessário.

Não adicionar cache apenas por adicionar.

---

# 38. FRONTEND

Reaproveitar componentes.

Criar componentes reutilizáveis para:

* CPF;
* CNPJ;
* telefone;
* celular;
* moeda;
* data;
* data/hora;
* quantidade;
* unidade de medida;
* mensagens;
* confirmações;
* tabelas;
* formulários.

Evitar JavaScript duplicado.

---

# 39. VALIDAÇÃO DUPLA

Toda informação importante deverá ser validada:

```text
Frontend
+
Backend
```

O frontend melhora a experiência.

O backend garante segurança e integridade.

Nunca confiar somente no frontend.

---

# 40. REGRA DE PERSISTÊNCIA DE VALORES MONETÁRIOS

Todos os valores monetários persistidos no banco de dados deverão utilizar a menor unidade monetária, ou seja, centavos, armazenados preferencialmente como `BIGINT`.

Exemplo:

```text
R$ 4,00       → 400
R$ 40,00      → 4000
R$ 1.250,75   → 125075
R$ 0,05       → 5
```

O sistema NÃO deverá armazenar valores monetários utilizando `float`.

## REGRA

* Persistência: `BIGINT` em centavos.
* Cálculos financeiros: `Decimal`.
* Apresentação: conversão para reais e formatação brasileira.
* API: definir padrão consistente para valores monetários.
* Frontend: utilizar máscara monetária apenas para apresentação/entrada.
* Backend: sempre validar e converter o valor recebido antes de persistir.

Criar uma função/service centralizado para conversão entre:

```text
centavos → Decimal
Decimal → centavos
```

Exemplo conceitual:

```python
class DinheiroService:

    @staticmethod
    def para_decimal(centavos):
        ...

    @staticmethod
    def para_centavos(valor):
        ...
```

Não espalhar operações como:

```python
valor / 100
valor * 100
```

pelo projeto.

Toda conversão monetária deverá utilizar o service centralizado.

## IMPORTANTE

Não utilizar `float` em cálculos financeiros.

Utilizar `Decimal` para cálculos intermediários, especialmente:

* preço;
* custo;
* lucro;
* CMV;
* markup;
* margem;
* ticket médio;
* ponto de equilíbrio;
* descontos;
* impostos;
* precificação;
* conversões de preço por unidade de medida.

Percentuais, quantidades, pesos, volumes, fatores de conversão e demais valores que não representam dinheiro deverão manter o tipo numérico apropriado, preferencialmente `Decimal` quando houver necessidade de casas decimais.

Não converter indiscriminadamente todas as quantidades para inteiros.

## EXEMPLO

Se o usuário informar:

```text
Quantidade: 100
Unidade: Gramas
Preço pago: R$ 4,00
```

Persistir:

```text
quantidade = 100
preco_pago = 400
```

Calcular internamente:

```text
R$ 4,00 ÷ 100 g = R$ 0,04/g
```

e:

```text
R$ 0,04 × 1000 = R$ 40,00/kg
```

Persistir:

```text
preco_kg = 4000
```

Apresentar ao usuário:

```text
R$ 40,00
```

## BANCO DE DADOS

Para campos monetários existentes, analisar individualmente antes de alterar o tipo.

Não realizar conversões automáticas sem verificar:

* valores existentes;
* casas decimais atuais;
* precisão;
* relacionamentos;
* cálculos existentes;
* relatórios;
* APIs;
* integrações.

Criar migrations seguras e preservar os valores existentes.

## COMPATIBILIDADE

Caso existam dados monetários atualmente armazenados como `DecimalField`, realizar a migração de forma segura.

Exemplo:

```text
4.00 → 400
40.00 → 4000
1250.75 → 125075
```

Validar a migração antes de remover o formato anterior.

## API

Definir um padrão único para valores monetários.

Preferencialmente disponibilizar o valor em centavos de forma explícita e, quando necessário para facilitar o consumo pelo frontend/mobile, disponibilizar também uma representação decimal/string.

Exemplo:

```json
{
    "preco": 400,
    "preco_decimal": "4.00",
    "preco_formatado": "R$ 4,00"
}
```

Nunca utilizar `float` para representar valores monetários na API.

## OBJETIVO

A arquitetura deverá garantir:

* precisão financeira;
* ausência de erros de ponto flutuante;
* segurança para valores elevados;
* consistência entre banco, backend, API e frontend;
* facilidade de manutenção;
* compatibilidade futura com aplicativos mobile.


---

# 41. BANCO DE DADOS

Antes de alterar tabelas existentes:

* verificar foreign keys;
* verificar índices;
* verificar constraints;
* verificar dados existentes;
* verificar dependências;
* verificar impacto nos usuários atuais.

Não apagar dados de produção.

Criar migrations reversíveis sempre que possível.

---

# 42. COMPATIBILIDADE

Preservar:

* URLs existentes quando possível;
* nomes de rotas existentes;
* funcionalidades;
* dados;
* relacionamentos;
* permissões;
* comportamento atual.

Se alguma alteração incompatível for realmente necessária:

1. documentar;
2. explicar o motivo;
3. criar estratégia de migração;
4. evitar perda de dados.

---

# 43. RESULTADO FINAL ESPERADO

Ao finalizar, o projeto deverá possuir:

### Código

* limpo;
* simples;
* enxuto;
* organizado;
* reutilizável.

### API

* REST;
* segura;
* documentada;
* preparada para mobile;
* Swagger/OpenAPI.

### Auditoria

* CREATE;
* UPDATE;
* DELETE;
* detalhes das alterações;
* template administrativo.

### Medidas

* tabela `medidas`;
* CRUD administrativo;
* ACL;
* conversões;
* cálculo de preço por unidade.

### Insumos

* sem categoria;
* cálculo por unidade;
* preço por KG/L/UN;
* quantidade por extenso.

### Usuários

```text
auth_user
    ↓
cliente
    ↓
perfil
    ↓
permissões
```

### Regras globais

Centralizadas e reutilizáveis.

### Máscaras

Centralizadas e reutilizáveis.

### Indicadores

Centralizados e reutilizáveis.

### Encoding

UTF-8 funcionando corretamente.

### Vuexy

Erro:

```text
Language "pt-br" not found!
```

corrigido na origem.

### Documentação

README completo e didático.

---

# 44. ORDEM DE EXECUÇÃO

Execute o trabalho preferencialmente nesta ordem:

1. Analisar o projeto atual.
2. Mapear estrutura e dependências.
3. Identificar funcionalidades existentes.
4. Identificar banco e relacionamentos.
5. Corrigir encoding UTF-8.
6. Corrigir idioma `pt-br` do Vuexy.
7. Criar/organizar regras globais.
8. Criar sistema de medidas.
9. Criar funções de conversão.
10. Criar funções financeiras.
11. Atualizar módulo de insumos.
12. Criar módulo administrativo de medidas.
13. Criar módulo de usuários.
14. Integrar ACL.
15. Implementar auditoria.
16. Criar template de auditoria.
17. Criar API.
18. Criar autenticação da API.
19. Criar autorização da API.
20. Criar documentação Swagger/OpenAPI.
21. Criar testes.
22. Revisar performance.
23. Revisar segurança.
24. Atualizar README.
25. Fazer revisão final completa.

---

# 45. REVISÃO FINAL OBRIGATÓRIA

Antes de considerar o trabalho concluído, faça uma revisão procurando:

* código duplicado;
* funções desnecessárias;
* imports não utilizados;
* variáveis não utilizadas;
* consultas desnecessárias;
* problemas de segurança;
* problemas de permissão;
* endpoints sem autenticação;
* endpoints sem autorização;
* problemas de encoding;
* erros de tradução;
* problemas de validação;
* problemas matemáticos;
* problemas de conversão;
* problemas de arredondamento;
* problemas de auditoria;
* problemas de relacionamento;
* problemas de migration;
* problemas de performance.

Execute os testes.

Corrija os problemas encontrados.

---

# 46. REGRA FINAL — NÃO COMPLICAR

A prioridade do projeto será:

```text
FUNCIONAR
↓
SER SEGURO
↓
SER CORRETO
↓
SER SIMPLES
↓
SER REUTILIZÁVEL
↓
SER FÁCIL DE MANTER
```

Não criar arquitetura complexa apenas para parecer sofisticada.

Não criar abstrações desnecessárias.

Não duplicar código.

Não criar funcionalidades que não foram solicitadas.

Não alterar funcionalidades existentes sem analisar o impacto.

Sempre que houver duas soluções tecnicamente válidas, escolher a solução:

**mais simples, mais segura, mais clara e mais fácil de manter.**

---

# 47. ENTREGA FINAL

Ao terminar, apresentar um relatório contendo:

### 1. O que foi alterado

Lista objetiva de todas as alterações.

### 2. Arquivos criados

Listar todos.

### 3. Arquivos modificados

Listar todos.

### 4. Migrations criadas

Listar todas.

### 5. Endpoints criados

Listar:

```text
Método
Endpoint
Descrição
Autenticação
Permissão
```

### 6. Regras de negócio

Listar as funções criadas.

### 7. Auditoria

Explicar como funciona.

### 8. Medidas

Listar medidas cadastradas.

### 9. Segurança

Informar as medidas implementadas.

### 10. Testes

Informar quais testes foram executados e os resultados.

### 11. Problemas encontrados

Informar problemas encontrados durante a implementação.

### 12. Decisões técnicas

Explicar decisões importantes.

### 13. README

Confirmar que foi atualizado.

### 14. Pendências

Se alguma funcionalidade não puder ser implementada, informar:

* o que ficou pendente;
* motivo;
* impacto;
* o que precisa ser feito posteriormente.

**NÃO declarar uma tarefa como concluída se ela não tiver sido realmente implementada e testada.**
