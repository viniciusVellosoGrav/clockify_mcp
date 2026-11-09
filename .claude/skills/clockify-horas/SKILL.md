---
name: clockify-horas
description: "Use ao ligar/parar cronômetro ou somar horas no servidor da Leia (GRAV)."
---

# Cronômetro e horas no servidor da Leia (GRAV)

Este servidor (`clockify_mcp`) fala a API compatível com Clockify do backend da Leia. O **workspace
vem da credencial** (`workspaceId`; no GRAV é o quadro "Tarefas Leia") e o projeto é **"LeIA"**;
reunião vai em **"Atividades GRAV"** com `reuniao=True`. Nunca monte chamada HTTP na mão: use as
ferramentas.

## Regra de ouro: o título é o vínculo com o card

A `descricao` do cronômetro tem que ser o **título completo e idêntico** de um card do quadro
"Tarefas Leia". É por esse texto que o relatório do time liga a hora ao card.

- Antes de iniciar, **leia o card** (`card` do servidor `leia-tarefas`) ou peça o título. Nunca
  parafraseie ("Otimização app" ✗), nunca invente título que não existe em card nenhum.
- Título com aspas chega escapado (`\"...\"`) e deixa de ser idêntico: se o card tiver aspas,
  `renomear_cronometro` para a forma sem aspas na mesma rodada.

## Começar

- **`iniciar_cronometro_paralelo` é a ferramenta do dia a dia.** No servidor da Leia a concorrência
  está ligada (`concorrente: true`): existem vários cronômetros ao mesmo tempo, porque várias
  sessões do agente trabalham em paralelo e cada timer é hora real de alguém.
- **`iniciar_cronometro` NÃO é acionável nesse servidor**: ela fecharia o timer em andamento, então
  responde erro e não inicia nem para nada.
- **Antes de iniciar, olhe `cronometros_em_andamento`**: pode já haver um timer para aquele card
  (o plugin `dsh-clockify-leia` liga sozinho quando o card entra em "Fazendo", com início =
  `updatedAt` do card). Timer existente com o título do card não ganha um segundo.
- Reunião: `reuniao=True`, nunca como trabalho de projeto. Na dúvida se é reunião, pergunte.

## Encerrar

- **Feche quando o trabalho acabou de verdade**, não quando o usuário vai validar: espera de
  validação, aprovação ou deploy em produção não é trabalho, e timer aberto faz a hora do time
  mentir. Relate início → fim e a duração.
- Encerrar é `parar_cronometro` **pelo id**. Antes de considerar feito, olhe a `descricao` que ele
  devolve: se não for o seu card, o relógio que você fechou era de outra sessão — diga isso.
- Trabalho que continua depois de um fechamento por engano se corrige com `editar_lancamento`
  (`novo_fim` = quando o trabalho realmente parou) ou religando com o mesmo título. Não invente um
  lançamento curto começando depois para "tapar o buraco".
- **Sessão que continua trabalhando = lançamento ABERTO.** Fechar no horário da última mensagem
  inventa um fim que não aconteceu.

## Vários cronômetros

`cronometros_em_andamento` lista todos (id, descrição, projeto, início, decorrido). Com 2+ rodando,
`parar_cronometro` e `renomear_cronometro` **se recusam a agir sem saber qual**: devolvem a lista —
repasse ao usuário e repita com `cronometro` (id ou trecho da descrição).

## Corrigir

- Título errado no que está rodando (ou, se nada roda, no último lançamento): `renomear_cronometro`.
- Horário errado num lançamento **encerrado** (esqueceu ligado, início atrasado):
  `editar_lancamento`, achando a entrada por `horas_trabalhadas`. Ela **recusa entrada em
  andamento** — a ordem é parar → editar → religar.
- Registro retroativo (trabalho feito antes de ligar o timer) usa o horário **real**, nunca o
  relógio de agora: começo do trabalho, não o momento do pedido.

## Somar horas

`horas_trabalhadas` aceita `periodo` = `hoje` | `ontem` | `semana` | `mes`, ou intervalo
`AAAA-MM-DD`. Fusos e limites de dia/semana/mês são **America/Sao_Paulo**; mostre os totais como
vierem ("1h 18min").

## Escopo

Só trabalho de **projeto LEIA** entra no relatório do time. Tarefa de outro projeto não vira
lançamento aqui — diga isso em uma linha em vez de lançar.
