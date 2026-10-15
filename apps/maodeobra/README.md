# Mão de obra por hora e por dia (`apps/maodeobra`)

Colaboradores (valor da hora e, opcionalmente, da diária) e **apontamentos** de trabalho por hora (início, fim, pausa) ou por dia, calculados **no servidor** (tela e API usam o mesmo cálculo, `calculo.py`, funções puras com `Decimal` e centavos inteiros).

## Quem usa

Somente empresas de segmento **Serviço** ou **Serviço e Produto** (decisão do cliente). Segmento de produto puro não recebe o módulo (`seed_modulos.SOMENTE_SERVICO`, migração `modulo.0016`), e o servidor recusa gravar apontamento fora dessa regra.

## Regras de cálculo (configuráveis por unidade em *Mão de obra → Regras*)

| Regra | Padrão | Observação |
|---|---|---|
| Arredondamento | por **minuto** (sem arredondar) | opções: para cima a cada 15 min ou à hora cheia |
| Jornada normal | 8 h/dia | o que passa disso é hora extra |
| Adicional de hora extra | **50 %** | sobre o valor da hora |
| Adicional de domingo | 0 % (desligado) | quando > 0, vale para todas as horas do domingo |

- Horas = (fim − início) − pausas; fim menor que início passa da meia-noite (máx. 24 h).
- Valor da hora × tempo normal + valor da hora × (1 + extra%) × tempo extra; cada valor arredondado **uma vez** ao centavo.
- Por dia: `dias × valor_dia` (vazio = valor da hora × jornada). Sem hora extra.
- Exemplo (hora R$ 20,00, 8 h, 50 %): 9 h 30 min → R$ 160,00 + R$ 45,00 = **R$ 205,00**.
- Cada apontamento guarda um **snapshot** dos parâmetros e do resultado: mudar valor ou regras depois não altera lançamentos antigos.

## Telas e API

`/mao-de-obra` (apontamentos, filtros), `/mao-de-obra/relatorio` (por colaborador e período), `/mao-de-obra/colaboradores`, `/mao-de-obra/regras`.
API v1: `/mao-de-obra/colaboradores/`, `/mao-de-obra/apontamentos/` (CRUD paginado), `/mao-de-obra/configuracao/` (GET/PATCH), `/mao-de-obra/relatorio/`.

## Segurança e auditoria

Tudo é da unidade do usuário (outra unidade devolve 404); colaborador e apontamento de outra unidade não são alcançados. Todo CRUD é auditado (valores monetários nos campos `AUDITORIA_MONETARIOS`). Colaborador com apontamentos é desativado, não excluído.

## Tabelas

`maodeobra_colaborador`, `maodeobra_apontamento`, `maodeobra_configuracaomaodeobra` (migração `maodeobra.0001`, aditiva).

## Limites

O custo calculado **não** alimenta automaticamente a Precificação de serviços (o preço do serviço continua sendo definido pela Precificação existente); o relatório serve de base para esse custo.
