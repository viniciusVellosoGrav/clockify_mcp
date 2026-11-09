# clockify_mcp

Servidor MCP (stdio) de **cronômetro e horas trabalhadas**: inicia/para o relógio e soma as horas
de quem é dono da chave. Fala a API do Clockify oficial **ou** a API compatível embutida no backend
da Leia — **no time, todo mundo usa o servidor da GRAV** (ambiente `grav`), que é o caminho descrito
aqui; o Clockify oficial fica só como alternativa.

Ferramentas: `iniciar_cronometro`, `iniciar_cronometro_paralelo`, `parar_cronometro`,
`renomear_cronometro`, `cronometro_atual`, `cronometros_em_andamento`, `editar_lancamento`,
`horas_trabalhadas`.

## Independência (importante)

Este servidor **não depende de mais nada**: não conhece o CRM, não importa código do
`leia-tarefas` e funciona sozinho. O único elo com o quadro "Tarefas Leia" é um **contrato de
texto**: a `descricao` do cronômetro é o título exato do card — a ponte é feita pelo agente, lendo
o card no outro servidor e passando o título para cá.

Consequência prática: dá para usar só este servidor (reunião, trabalho sem card). O contrário não
vale — a rotina completa do LEIA precisa dos dois: `leia-tarefas` para achar/mover o card e este
para o relógio.

## Instalação numa máquina nova

### Pré-requisitos

- `uv` no PATH — instalador oficial por sistema:
  - macOS: `brew install uv`
  - Linux / WSL: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `winget install --id=astral-sh.uv -e` (ou `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- Um cliente MCP: **Claude Code** (`claude`) ou **Hermes**.
- Python **não** precisa estar instalado: o `server.py` é um script PEP 723 (`requires-python
  >=3.10`, dependência `mcp`) e o `uv run --script` baixa um Python gerenciado quando o da máquina é
  antigo — o do macOS ainda vem 3.9, e várias distros Linux vêm 3.8/3.9.

### 1. Gerar a chave pessoal no aplicativo

No **aplicativo LEIA** → **Configurações** (seção **Segurança**) → **"Chave da API do controle de
horas"** → **Gerar chave**.

- A chave aparece **uma única vez** ("Copie agora; ela não aparece de novo") e começa com `leia_tt_`.
- A mesma seção mostra a **URL base** para copiar — é a base da API do ambiente + `/clockify/api/v1`
  (no GRAV: `https://grav-lightsail.grav.digital/leia_prod/clockify/api/v1`).
- "Gerar nova chave" derruba a anterior na hora; "Revogar" corta o acesso de quem usa a chave.
- A seção só aparece onde o controle de horas está ligado no ambiente; não apareceu, é isso.

A chave é **pessoal**: as horas lançadas são as do dono dela. Não compartilhe a sua nem use a de
outra pessoa.

### 2. Criar a credencial

`~/.config/clockify-mcp/credentials.json` — o `~` vale nos três sistemas (no Windows é
`C:\Users\<você>`, então o arquivo fica em `C:\Users\<você>\.config\clockify-mcp\credentials.json`).
Ou o caminho que estiver em `$CLOCKIFY_MCP_CRED` / `%CLOCKIFY_MCP_CRED%`:

```json
{
  "baseUrl": "https://grav-lightsail.grav.digital/leia_prod/clockify/api/v1",
  "apiKey": "leia_tt_...",
  "workspaceId": "tarefas-leia",
  "concorrente": true
}
```

- `baseUrl`: o do servidor **GRAV**, exatamente o acima (é o mesmo que a seção "Chave da API do
  controle de horas" mostra em "URL base"). Sem ele o servidor cai no Clockify **oficial** em
  silêncio (horas no lugar errado).
- `workspaceId`: no backend da Leia o "workspace" é um **quadro do CRM** — o de horas. No ambiente
  `grav`, o quadro "Tarefas Leia" responde pelo slug `tarefas-leia`. Com `baseUrl` da Leia ele é
  **obrigatório**: sem isso o servidor procura o workspace "Felipe Cerqueira" do Clockify oficial e
  falha com "workspace não encontrado".
- `concorrente: true`: declara que o servidor aceita **vários cronômetros ao mesmo tempo** (o da
  Leia aceita; o oficial não). Nunca ligar apontando para o Clockify oficial.

Dica: restrinja a leitura ao seu usuário (`chmod 600 ~/.config/clockify-mcp/credentials.json` no
macOS/Linux; no Windows o arquivo já fica sob o seu perfil). Os projetos "LeIA" e "Atividades GRAV"
já existem nesse quadro — sem eles o cronômetro não acha o projeto.

### 3. Registrar no Claude Code

```
claude mcp add --scope user clockify -- uv run --script /caminho/absoluto/clockify_mcp/server.py
```

- `--scope user` = disponível em todos os projetos; `--scope local` (padrão) = só no diretório onde
  o comando foi rodado; `--scope project` grava no `.mcp.json` do repositório.
- Caminho **absoluto** (o Claude sobe o servidor fora do diretório do comando): macOS/Linux
  `/Users/voce/clockify_mcp/server.py`; Windows `C:\Users\voce\clockify_mcp\server.py` (entre aspas se
  houver espaço).
- Conferir: `claude mcp get clockify` → `Status: ✔ Connected`, `Type: stdio`, `Command: uv`,
  `Args: run --script .../server.py`. Remover: `claude mcp remove clockify -s user`.
- O servidor novo só aparece em sessão nova (as ferramentas são lidas quando o processo sobe).

No **Hermes** o registro equivalente é:

```
hermes mcp add clockify --command uv --args run --script /caminho/absoluto/clockify_mcp/server.py
```

O prompt "Enable all N tools?" só obedece a um TTY de verdade (pipe com `printf 'Y'` é cancelado em
silêncio). Num Hermes já aberto, `/reload-mcp` carrega o servidor novo.

### 4. Conferir antes de usar

Em sessão nova, pedir **só leitura**: "quais cronômetros estão em andamento". A resposta traz
`em_andamento` com descrição, início, tempo decorrido e **projeto** — o projeto prova contra qual
backend a chamada foi. Vazio é resposta boa; erro de workspace/chave manda de volta ao passo 2.

## Como usar no contexto da Leia

1. **O card vem primeiro.** Para trabalho de um card do "Tarefas Leia", o título do card é a
   `descricao` do cronômetro — completo e **idêntico**, sem parafrasear, sem aspas e sem inventar
   título que não existe em card nenhum. É por esse texto que o relatório do time liga hora e card.
2. **Começar = `iniciar_cronometro_paralelo`.** No servidor da Leia (`concorrente: true`) a
   `iniciar_cronometro` **responde erro e não faz nada**: ela fecharia o timer em andamento, e cada
   timer é hora real de alguém (várias sessões do agente rodam em paralelo). A paralela começa ao
   lado das que já rodam e devolve `continuam_rodando`.
3. **Reunião** entra com `reuniao=True` (projeto "Atividades GRAV"), nunca como trabalho de projeto.
4. **Fechar quando o trabalho acabou de verdade**, com `parar_cronometro` pelo **id** — e relatar
   início → fim e duração. Deixar aberto "até validar" faz a hora do time mentir. Antes de parar,
   olhar a `descricao` devolvida: se não for o seu card, o relógio que você fechou era de outra
   sessão.
5. **Corrigir** lançamento encerrado é `editar_lancamento` (início/fim). Ela recusa entrada em
   andamento: pare antes, edite, religue.
6. **Somar horas**: `horas_trabalhadas` com `periodo` = `hoje` | `ontem` | `semana` | `mes`, ou
   intervalo explícito `AAAA-MM-DD`. Fusos e limites de dia/semana/mês são America/Sao_Paulo.
7. **Escopo**: só trabalho de projeto LEIA vira lançamento no relatório do time. Tarefa de outro
   projeto não entra aqui.

Nunca montar chamada HTTP na mão para isso: as ferramentas fazem a chamada certa.

## Vários cronômetros ao mesmo tempo

`cronometros_em_andamento` lista todos. Com 2+ rodando, `parar_cronometro` e `renomear_cronometro`
**se recusam a agir sem saber qual** (devolvem a lista): repita passando `cronometro` (id ou trecho
da descrição). `renomear_cronometro` sem nada rodando renomeia o último lançamento.

Card que entra em "Fazendo" com o plugin `dsh-clockify-leia` aberto em `~/projects/grav` ganha timer
sozinho (título do card, início = `updatedAt` do card, só se o card tem menos de 15 min): antes de
iniciar, olhe `cronometros_em_andamento` — pode já haver um para aquele card.

## Trocar entre Leia e Clockify oficial

O backend é escolhido **só** pela credencial:

- **Leia (GRAV, o padrão do time):** `baseUrl` da tela do app, `workspaceId` obrigatório,
  `concorrente: true`, chave `leia_tt_`.
- **Clockify oficial:** tirar `baseUrl` e `concorrente`, pôr a chave do Clockify (Profile settings →
  API) e, se quiser, `workspaceId`. Sem `workspaceId` vale o workspace "Felipe Cerqueira". Nesse
  modo `iniciar_cronometro` volta a fechar o timer anterior, porque não há como ter dois.

## Passar para outra pessoa

Vai no repositório: `server.py` + este README. **Não** vai: `credentials.json`, chave `leia_tt_`,
token ou qualquer segredo — cada integrante gera a própria chave no app (passo 1). Repositório
privado.
