#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""Servidor MCP do Clockify — cronômetro e horas trabalhadas de quem é dono da chave.

Ferramentas: iniciar_cronometro, iniciar_cronometro_paralelo, parar_cronometro, renomear_cronometro,
editar_lancamento, cronometro_atual, cronometros_em_andamento, horas_trabalhadas.

A credencial vive em `~/.config/clockify-mcp/credentials.json` (ou no caminho de
$CLOCKIFY_MCP_CRED): `{"apiKey": "...", "workspaceId": "..."}` — o workspaceId é opcional
(sem ele vale o workspace padrão do usuário). A chave nunca é escrita em log nem devolvida.
Campos opcionais para apontar a outro servidor compatível (ex.: a API da Leia): `"baseUrl"` (padrão: a
API oficial do Clockify; com outra baseUrl o `workspaceId` passa a ser obrigatório) e `"concorrente": true`
(declara que o servidor aceita vários cronômetros ao mesmo tempo; padrão false, sem autodetecção).
Não há acoplamento com o CRM: quem liga um card ao cronômetro é o Claude, passando o título.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

CRED_PATH = os.path.expanduser(os.environ.get('CLOCKIFY_MCP_CRED', '~/.config/clockify-mcp/credentials.json'))
BASE_URL = 'https://api.clockify.me/api/v1'  # padrão; a credencial pode trocar por "baseUrl"
TZ = ZoneInfo('America/Sao_Paulo')
TIMEOUT = 25
PAGE_SIZE = 1000
MAX_PAGES = 50
PERIODOS = ('hoje', 'ontem', 'semana', 'mes')
WORKSPACE_PADRAO = 'Felipe Cerqueira'  # todas as tarefas vão neste workspace
PROJETO_PADRAO = 'LeIA'
PROJETO_REUNIAO = 'Atividades GRAV'
DIAS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']

_lock = threading.Lock()
_cred_cache: dict | None = None
_cred_mtime: float | None = None
_eu_cache: dict | None = None
_projetos_cache: dict[str, str] | None = None


class ClockifyError(RuntimeError):
    """Falha que deve chegar ao usuário como texto, não como stack trace."""


# ── credencial e HTTP ─────────────────────────────────────────────────────────


def _cred() -> dict:
    global _cred_cache, _cred_mtime, _eu_cache, _projetos_cache
    with _lock:
        try:
            mtime = os.path.getmtime(CRED_PATH)
        except OSError:
            mtime = None
        if _cred_cache is not None and mtime != _cred_mtime:
            # Arquivo trocado (ex.: chave nova): relê e esquece o que dependia da chave antiga.
            _cred_cache = _eu_cache = _projetos_cache = None
        if _cred_cache is None:
            _cred_mtime = mtime
            try:
                with open(CRED_PATH) as f:
                    dados = json.load(f)
            except FileNotFoundError as exc:
                raise ClockifyError(
                    f'Chave do Clockify não encontrada em {CRED_PATH}. Gere a chave em Clockify → '
                    'Profile settings → API e grave um JSON {"apiKey": "..."} nesse arquivo '
                    '(opcional: "workspaceId").'
                ) from exc
            except (json.JSONDecodeError, OSError) as exc:
                raise ClockifyError(f'Credencial ilegível em {CRED_PATH}: {exc}') from exc
            if not isinstance(dados, dict) or not str(dados.get('apiKey') or '').strip():
                raise ClockifyError(f'O arquivo {CRED_PATH} não tem o campo "apiKey".')
            _cred_cache = dados
        return _cred_cache


def base_url() -> str:
    return str(_cred().get('baseUrl') or '').strip().rstrip('/') or BASE_URL


def concorrente() -> bool:
    """Declaração manual (credencial `"concorrente": true`) de que o servidor aceita vários cronômetros."""
    return _cred().get('concorrente') is True


def api(path: str, params: dict | None = None, method: str = 'GET', body: dict | None = None):
    key = str(_cred()['apiKey']).strip()
    url = base_url() + path
    if params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})
    headers = {'X-Api-Key': key, 'accept': 'application/json'}
    if body is not None:
        headers['content-type'] = 'application/json'
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as exc:
        corpo = exc.read().decode(errors='replace')
        try:
            msg = json.loads(corpo).get('message') or corpo
        except Exception:
            msg = corpo or f'HTTP {exc.code}'
        if exc.code in (401, 403):
            raise ClockifyError(f'Clockify recusou a chave ({exc.code}): confira o apiKey em {CRED_PATH}.') from exc
        raise ClockifyError(f'Clockify respondeu {exc.code}: {msg[:300]}') from exc
    except urllib.error.URLError as exc:
        raise ClockifyError(f'Clockify inalcançável: {exc.reason}') from exc


def me() -> dict:
    """Usuário dono da chave + workspace a usar, uma vez por processo."""
    global _eu_cache
    if _eu_cache:
        return _eu_cache
    u = api('/user') or {}
    if not u.get('id'):
        raise ClockifyError('O Clockify não devolveu o usuário da chave.')
    ws = str(_cred().get('workspaceId') or '').strip()
    if not ws and base_url() != BASE_URL:
        raise ClockifyError(f'A credencial usa outro servidor ("baseUrl" = {base_url()}): informe também '
                            '"workspaceId" em ' + CRED_PATH + ', pois lá o workspace não é achado pelo nome.')
    if not ws:
        ws = next((w['id'] for w in api('/workspaces') or [] if (w.get('name') or '').strip() == WORKSPACE_PADRAO), '')
    if not ws:
        raise ClockifyError(f'Workspace "{WORKSPACE_PADRAO}" não encontrado para esta chave: informe "workspaceId" na credencial.')
    if not ws:
        raise ClockifyError('Não achei workspace: informe "workspaceId" na credencial.')
    _eu_cache = {'id': u['id'], 'nome': u.get('name'), 'workspace': ws}
    return _eu_cache


def projeto_id(nome: str) -> str:
    """Id do projeto (não arquivado) pelo nome, sem diferenciar maiúsculas."""
    global _projetos_cache
    if _projetos_cache is None:
        lista = api(f'/workspaces/{me()["workspace"]}/projects', {'page-size': 500, 'archived': 'false'}) or []
        _projetos_cache = {(p.get('name') or '').strip().lower(): p['id'] for p in lista}
    pid = _projetos_cache.get((nome or '').strip().lower())
    if not pid:
        raise ClockifyError(f'Projeto "{nome}" não existe no workspace. Disponíveis: {", ".join(sorted(_projetos_cache))}.')
    return pid


def _garantir_projeto(entrada: dict) -> None:
    """O workspace exige projeto: entrada em andamento sem projeto recebe o padrão antes de parar."""
    if entrada.get('projectId'):
        return
    u = me()
    api(f'/workspaces/{u["workspace"]}/time-entries/{entrada["id"]}', method='PUT', body={
        'start': entrada['timeInterval']['start'], 'description': entrada.get('description') or '',
        'projectId': projeto_id(PROJETO_PADRAO), 'billable': entrada.get('billable', False),
        'tagIds': entrada.get('tagIds') or []})


# ── tempo ─────────────────────────────────────────────────────────────────────


def agora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def hora_local(dt: datetime) -> str:
    return local(dt).strftime('%d/%m/%Y %H:%M')


def fmt_dur(segundos: float) -> str:
    minutos = int(round(max(0, segundos) / 60))
    return f'{minutos // 60}h {minutos % 60:02d}min'


def _meia_noite(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc)


def _data(s: str, nome: str) -> date:
    try:
        return date.fromisoformat(s.strip())
    except ValueError as exc:
        raise ClockifyError(f'"{nome}" inválido: use AAAA-MM-DD (recebi "{s}").') from exc


def intervalo(periodo: str, inicio: str | None, fim: str | None, ref: datetime | None = None):
    """(rótulo, início UTC, fim UTC exclusivo) nos limites do dia local em São Paulo."""
    hoje = local(ref or agora()).date()
    if inicio or fim:
        d0 = _data(inicio or fim, 'inicio')
        d1 = _data(fim or inicio, 'fim')
        if d1 < d0:
            raise ClockifyError('"fim" é anterior a "inicio".')
        return f'{d0.strftime("%d/%m/%Y")} a {d1.strftime("%d/%m/%Y")}', _meia_noite(d0), _meia_noite(d1 + timedelta(days=1))
    p = (periodo or 'hoje').strip().lower().replace('ê', 'e')
    if p == 'hoje':
        d0, d1, rot = hoje, hoje, 'hoje'
    elif p == 'ontem':
        d0 = d1 = hoje - timedelta(days=1)
        rot = 'ontem'
    elif p == 'semana':
        d0 = hoje - timedelta(days=hoje.weekday())
        d1, rot = d0 + timedelta(days=6), 'esta semana (segunda a domingo)'
    elif p == 'mes':
        d0 = hoje.replace(day=1)
        d1 = (d0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        rot = 'este mês'
    else:
        raise ClockifyError(f'Período inválido: {periodo}. Válidos: {", ".join(PERIODOS)} ou inicio/fim em AAAA-MM-DD.')
    return rot, _meia_noite(d0), _meia_noite(d1 + timedelta(days=1))


def somar(entradas: list[dict], ini: datetime, fim: datetime, agora_: datetime) -> list[dict]:
    """Recorta cada entrada em [ini, fim) — a em andamento conta até agora — e descarta as de fora."""
    saida, vistos = [], set()
    for e in entradas:
        if e.get('id') in vistos:
            continue
        vistos.add(e.get('id'))
        ti = e.get('timeInterval') or {}
        s, f = parse_iso(ti.get('start')), parse_iso(ti.get('end'))
        if s is None:
            continue
        andamento = f is None
        f = f or agora_
        a, b = max(s, ini), min(f, fim, agora_ if andamento else fim)
        if b <= a:
            continue
        saida.append({'descricao': (e.get('description') or '').strip() or '(sem descrição)',
                      'inicio': a, 'fim': b, 'segundos': (b - a).total_seconds(), 'andamento': andamento,
                      'id': e.get('id')})
    saida.sort(key=lambda x: x['inicio'])
    return saida


def _entradas(ini: datetime, fim: datetime) -> list[dict]:
    u = me()
    base = f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries'
    # começa um dia antes para pegar entrada que atravessa a meia-noite; somar() recorta.
    params = {'start': iso(ini - timedelta(days=1)), 'end': iso(fim), 'page-size': PAGE_SIZE}
    todas = []
    for pagina in range(1, MAX_PAGES + 1):
        lote = api(base, {**params, 'page': pagina}) or []
        todas += lote
        if len(lote) < PAGE_SIZE:
            break
    todas += api(base, {'in-progress': 'true'}) or []
    return todas


def _todos_em_andamento() -> list[dict]:
    u = me()
    return api(f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries', {'in-progress': 'true'}) or []


def _em_andamento() -> dict | None:
    lote = _todos_em_andamento()
    return lote[0] if lote else None


def _resumo(e: dict, t: datetime) -> dict:
    s = parse_iso(e['timeInterval']['start'])
    return {'id': e.get('id'), 'descricao': e.get('description') or '(sem descrição)',
            'inicio': hora_local(s), 'decorrido': fmt_dur((t - s).total_seconds())}


def _escolher(lista: list[dict], seletor: str | None) -> tuple[dict | None, dict | None]:
    """(entrada, None) quando dá para agir; (None, resposta) quando é preciso perguntar ou não há alvo."""
    t = agora()
    seletor = (seletor or '').strip()
    if not seletor:
        if len(lista) == 1:
            return lista[0], None
        return None, {'nota': 'Há mais de um cronômetro em andamento; nada foi alterado. Informe qual '
                              '(id ou trecho da descrição).', 'em_andamento': [_resumo(e, t) for e in lista]}
    exato = [e for e in lista if e.get('id') == seletor]
    achados = exato or [e for e in lista if seletor.lower() in (e.get('description') or '').lower()]
    if len(achados) == 1:
        return achados[0], None
    if not achados:
        return None, {'nota': f'Nenhum cronômetro em andamento casa com "{seletor}"; nada foi alterado.',
                      'em_andamento': [_resumo(e, t) for e in lista]}
    return None, {'nota': f'Mais de um cronômetro casa com "{seletor}"; nada foi alterado. Use o id.',
                  'em_andamento': [_resumo(e, t) for e in achados]}


def _parar_por_id(e: dict, t: datetime) -> None:
    """Encerra esta entrada específica (PUT com start+end), sem tocar nas outras que estejam rodando."""
    ti = e['timeInterval']
    corpo = {'start': ti['start'], 'end': iso(t), 'description': e.get('description') or '',
             'projectId': e.get('projectId') or projeto_id(PROJETO_PADRAO),
             'billable': e.get('billable', False), 'tagIds': e.get('tagIds') or []}
    if e.get('taskId'):
        corpo['taskId'] = e['taskId']
    api(f'/workspaces/{me()["workspace"]}/time-entries/{e["id"]}', method='PUT', body=corpo)


# ── MCP ───────────────────────────────────────────────────────────────────────

mcp = FastMCP('clockify', instructions="""\
Clockify — cronômetro e horas trabalhadas de quem é dono da chave. Use estas ferramentas para
"começar/parar cronômetro", "o que estou cronometrando", "quantas horas trabalhei"; nunca monte
chamada HTTP na mão.

- Para iniciar o cronômetro a partir de um card do "Tarefas Leia": primeiro chame a ferramenta
  `card` do servidor leia-tarefas e passe o título COMPLETO do card como `descricao`. Este
  servidor não conhece o CRM.
- O workspace vem da credencial (`workspaceId`; no GRAV é o quadro "Tarefas Leia"). Projeto: "LeIA"
  por padrão; reunião → `reuniao=True` (projeto "Atividades GRAV"). Em caso de dúvida se é reunião,
  pergunte; não chute.
- Com cronômetros SIMULTÂNEOS ativos (`"concorrente": true`), `iniciar_cronometro` NÃO é acionável:
  ela fecharia o timer que está rodando, então responde ERRO e não inicia nem para nada. Nesse modo
  use `iniciar_cronometro_paralelo`, que começa ao lado dos que já rodam (cada timer é hora real de
  alguém; várias sessões do agente trabalham em paralelo) e devolve `continuam_rodando`. Para
  encerrar, `parar_cronometro` pelo id. Em servidor de cronômetro único (Clockify oficial, sem
  `concorrente`) `iniciar_cronometro` fecha o anterior, porque não existe como ter dois.
- Vários cronômetros ao mesmo tempo só existem em servidor declarado com `"concorrente": true` na
  credencial. Nele, `iniciar_cronometro_paralelo` começa outro SEM parar os que estão rodando;
  `cronometros_em_andamento` lista todos (id, descrição, projeto, início, decorrido). Fora desse modo
  o paralelo é recusado.
- Com mais de um rodando, `parar_cronometro` e `renomear_cronometro` não agem sem saber qual: devolvem
  a lista; repasse ao usuário e chame de novo com `cronometro` (id ou trecho da descrição).
- Título errado? `renomear_cronometro` corrige o em andamento (ou o último lançamento) sem perder o tempo.
- Horário errado num lançamento já encerrado (ex.: esqueceu ligado)? `editar_lancamento`, achando-o
  pelo início real visto em `horas_trabalhadas`.
- Horários em America/Sao_Paulo. Mostre os totais como vierem ("Xh YYmin").
- Se a chave não existir, a ferramenta explica onde colocá-la: repasse ao usuário.
""")


@mcp.tool()
def iniciar_cronometro(descricao: str, reuniao: bool = False) -> dict:
    """Inicia um cronômetro agora com a descrição dada (para um card do Tarefas Leia, use o título
    completo do card). Projeto "LeIA"; com `reuniao=True`, "Atividades GRAV".

    Esta ferramenta FECHA o cronômetro que estiver rodando. Por isso ela só existe em servidor de
    cronômetro ÚNICO: com a concorrência ativada (`"concorrente": true` na credencial) ela
    RESPONDE ERRO e não inicia nem para nada — ali o certo é `iniciar_cronometro_paralelo`, que
    começa ao lado dos que já rodam (cada timer é hora real de alguém, e várias sessões do agente
    trabalham em paralelo). Para encerrar um cronômetro, `parar_cronometro` pelo id."""
    if not (descricao or '').strip():
        raise ClockifyError('Descrição vazia: informe o que está sendo feito (ou o título do card).')
    if concorrente():
        # Concurrência ativada = vários cronômetros convivem. Fechar o alheio para abrir o meu é
        # perda de hora de outra sessão: a ferramenta destrutiva simplesmente não é acionável.
        raise ClockifyError(
            'Servidor com cronômetros SIMULTÂNEOS ativos: `iniciar_cronometro` fecharia o timer que '
            'está rodando, então ela não faz nada nesse modo. Use `iniciar_cronometro_paralelo` '
            '(começa ao lado dos que já rodam) e `parar_cronometro` com o id para encerrar. '
            'Nada foi iniciado nem parado.')
    u = me()
    projeto = PROJETO_REUNIAO if reuniao else PROJETO_PADRAO
    pid = projeto_id(projeto)
    t = agora()
    atuais = _todos_em_andamento()
    paradas = []
    for atual in atuais:
        if len(atuais) > 1:
            _parar_por_id(atual, t)
        else:
            _garantir_projeto(atual)
            api(f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries', method='PATCH', body={'end': iso(t)})
        s = parse_iso(atual['timeInterval']['start'])
        paradas.append({'id': atual.get('id'), 'descricao': atual.get('description') or '(sem descrição)',
                        'duracao': fmt_dur((t - s).total_seconds())})
    novo = api(f'/workspaces/{u["workspace"]}/time-entries', method='POST',
               body={'start': iso(t), 'description': descricao.strip(), 'projectId': pid}) or {}
    saida = {'iniciado': {'id': novo.get('id'), 'descricao': novo.get('description') or descricao.strip(),
                          'projeto': projeto, 'inicio': hora_local(t)}}
    if len(paradas) == 1:
        saida['parado_antes'] = paradas[0]
        saida['nota'] = f'O cronômetro "{paradas[0]["descricao"]}" estava rodando e foi parado ({paradas[0]["duracao"]}).'
    elif paradas:
        saida['parados_antes'] = paradas
        saida['nota'] = f'{len(paradas)} cronômetros estavam rodando e foram parados: ' + '; '.join(
            f'"{p["descricao"]}" ({p["duracao"]})' for p in paradas) + '.'
    return saida


@mcp.tool()
def iniciar_cronometro_paralelo(descricao: str, reuniao: bool = False) -> dict:
    """Inicia um cronômetro agora SEM parar os que já estão rodando (só em servidor declarado com
    "concorrente": true na credencial; senão recusa e não faz nada). Mesmos projeto e descrição de
    `iniciar_cronometro`.

    Equivale ao comportamento padrão do `iniciar_cronometro` em servidor concorrente (ele também
    não fecha mais os outros) — mantida para chamada explícita e para cliente antigo."""
    if not concorrente():
        raise ClockifyError('Este servidor não está declarado como aceitando cronômetros simultâneos: '
                            f'coloque "concorrente": true em {CRED_PATH} se ele aceita. Nada foi iniciado '
                            '(use iniciar_cronometro para trocar o cronômetro).')
    if not (descricao or '').strip():
        raise ClockifyError('Descrição vazia: informe o que está sendo feito (ou o título do card).')
    u = me()
    projeto = PROJETO_REUNIAO if reuniao else PROJETO_PADRAO
    pid = projeto_id(projeto)
    t = agora()
    novo = api(f'/workspaces/{u["workspace"]}/time-entries', method='POST',
               body={'start': iso(t), 'description': descricao.strip(), 'projectId': pid}) or {}
    return {'iniciado': {'id': novo.get('id'), 'descricao': novo.get('description') or descricao.strip(),
                         'projeto': projeto, 'inicio': hora_local(t)},
            'nota': 'Iniciado em paralelo; os outros cronômetros continuam rodando.'}


@mcp.tool()
def parar_cronometro(cronometro: str | None = None) -> dict:
    """Para o cronômetro em andamento e devolve descrição e duração. Se nada estiver rodando, avisa.
    Com vários rodando, `cronometro` (id ou trecho da descrição) diz qual parar; sem ele, nada é
    parado e a lista volta para o usuário escolher."""
    u = me()
    lista = _todos_em_andamento()
    if not lista:
        return {'nota': 'Nenhum cronômetro em andamento: nada a parar.'}
    atual, pergunta = _escolher(lista, cronometro)
    if pergunta:
        return pergunta
    t = agora()
    if concorrente() or len(lista) > 1 or (cronometro or '').strip():
        _parar_por_id(atual, t)
    else:
        _garantir_projeto(atual)
        api(f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries', method='PATCH', body={'end': iso(t)})
    s = parse_iso(atual['timeInterval']['start'])
    return {'parado': {'id': atual.get('id'), 'descricao': atual.get('description') or '(sem descrição)',
                       'inicio': hora_local(s), 'fim': hora_local(t), 'duracao': fmt_dur((t - s).total_seconds())}}


@mcp.tool()
def renomear_cronometro(descricao: str, cronometro: str | None = None) -> dict:
    """Troca a descrição do cronômetro em andamento; se nada estiver rodando, a do último lançamento
    (o mais recente). Mantém início, fim e projeto. Para um card do Tarefas Leia, use o título
    completo do card. Com vários rodando, `cronometro` (id ou trecho da descrição) diz qual; sem
    ele, nada é alterado e a lista volta para o usuário escolher."""
    if not (descricao or '').strip():
        raise ClockifyError('Descrição vazia: informe o novo título.')
    u = me()
    lista = _todos_em_andamento()
    if lista:
        alvo, pergunta = _escolher(lista, cronometro)
        if pergunta:
            return pergunta
    elif (cronometro or '').strip():
        return {'nota': f'Nenhum cronômetro em andamento casa com "{cronometro.strip()}"; nada foi alterado.'}
    else:
        alvo = None
    if not alvo:
        lote = api(f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries', {'page-size': 1}) or []
        if not lote:
            return {'nota': 'Nenhum lançamento encontrado: nada a renomear.'}
        alvo = lote[0]
    ti = alvo['timeInterval']
    corpo = {'start': ti['start'], 'description': descricao.strip(),
             'projectId': alvo.get('projectId') or projeto_id(PROJETO_PADRAO),
             'billable': alvo.get('billable', False), 'tagIds': alvo.get('tagIds') or []}
    if ti.get('end'):
        corpo['end'] = ti['end']
    if alvo.get('taskId'):
        corpo['taskId'] = alvo['taskId']
    api(f'/workspaces/{u["workspace"]}/time-entries/{alvo["id"]}', method='PUT', body=corpo)
    s, f = parse_iso(ti['start']), parse_iso(ti.get('end'))
    return {'renomeado': {'antes': alvo.get('description') or '(sem descrição)', 'agora': descricao.strip(),
                          'inicio': hora_local(s), 'fim': hora_local(f) if f else 'em andamento',
                          'duracao': fmt_dur(((f or agora()) - s).total_seconds())}}


def _momento(s: str, nome: str) -> datetime:
    """'AAAA-MM-DD HH:MM' no horário de São Paulo → UTC."""
    try:
        dt = datetime.strptime(s.strip(), '%Y-%m-%d %H:%M')
    except ValueError as exc:
        raise ClockifyError(f'"{nome}" inválido: use AAAA-MM-DD HH:MM (recebi "{s}").') from exc
    return dt.replace(tzinfo=TZ).astimezone(timezone.utc)


@mcp.tool()
def editar_lancamento(lancamento: str, novo_inicio: str | None = None, novo_fim: str | None = None,
                      descricao: str | None = None, dias: int = 7) -> dict:
    """Corrige um lançamento já encerrado (ex.: início atrasado, cronômetro esquecido ligado).
    `lancamento` identifica o lançamento: o id (devolvido por iniciar/parar/horas_trabalhadas) ou
    um trecho do título (ex.: o título do card). A busca olha os últimos `dias` (padrão 7). Se o
    trecho casar com mais de um lançamento, nada é alterado e a lista volta com id e horários para
    escolher pelo id. `novo_inicio`/`novo_fim` em "AAAA-MM-DD HH:MM" (São Paulo); `descricao` troca
    o título. O que não for passado fica como está. Não vale para o cronômetro em andamento."""
    if not (novo_inicio or novo_fim or (descricao or '').strip()):
        raise ClockifyError('Nada a alterar: informe novo_inicio, novo_fim ou descricao.')
    sel = (lancamento or '').strip()
    if not sel:
        raise ClockifyError('Informe `lancamento`: o id ou um trecho do título.')
    u = me()
    base = f'/workspaces/{u["workspace"]}/user/{u["id"]}/time-entries'
    t = agora()
    lote = api(base, {'start': iso(t - timedelta(days=max(1, int(dias)))), 'end': iso(t),
                      'page-size': PAGE_SIZE}) or []
    candidatos = [e for e in lote if e.get('id') == sel] or \
                 [e for e in lote if sel.lower() in (e.get('description') or '').lower()]
    if not candidatos:
        raise ClockifyError(f'Nenhum lançamento dos últimos {dias} dias casa com "{sel}".')
    encerrados = [c for c in candidatos if (c.get('timeInterval') or {}).get('end')]
    if not encerrados:
        raise ClockifyError('Esse lançamento ainda está em andamento: pare antes com parar_cronometro.')
    candidatos = encerrados  # o em andamento não é editável aqui, então não entra na escolha
    if len(candidatos) > 1:
        def _linha(c):
            ti = c.get('timeInterval') or {}
            s0, f0 = parse_iso(ti.get('start')), parse_iso(ti.get('end'))
            return {'id': c.get('id'), 'descricao': c.get('description') or '(sem descrição)',
                    'inicio': hora_local(s0) if s0 else '?', 'fim': hora_local(f0) if f0 else 'em andamento'}
        return {'nota': 'Mais de um lançamento casa com isso; nada foi alterado. Repita com o `id`.',
                'candidatos': [_linha(c) for c in candidatos]}
    alvo = candidatos[0]
    ti = alvo['timeInterval']
    if not ti.get('end'):
        raise ClockifyError('Esse lançamento ainda está em andamento: pare antes com parar_cronometro.')
    s = _momento(novo_inicio, 'novo_inicio') if novo_inicio else parse_iso(ti['start'])
    f = _momento(novo_fim, 'novo_fim') if novo_fim else parse_iso(ti['end'])
    if f <= s:
        raise ClockifyError(f'O fim ({hora_local(f)}) precisa ser depois do início ({hora_local(s)}).')
    if f > agora():
        raise ClockifyError('O fim não pode estar no futuro.')
    corpo = {'start': iso(s), 'end': iso(f),
             'description': (descricao or '').strip() or alvo.get('description') or '',
             'projectId': alvo.get('projectId') or projeto_id(PROJETO_PADRAO),
             'billable': alvo.get('billable', False), 'tagIds': alvo.get('tagIds') or []}
    if alvo.get('taskId'):
        corpo['taskId'] = alvo['taskId']
    api(f'/workspaces/{u["workspace"]}/time-entries/{alvo["id"]}', method='PUT', body=corpo)
    s0, f0 = parse_iso(ti['start']), parse_iso(ti['end'])
    return {'editado': {
        'id': alvo['id'],
        'descricao': corpo['description'] or '(sem descrição)',
        'antes': {'inicio': hora_local(s0), 'fim': hora_local(f0), 'duracao': fmt_dur((f0 - s0).total_seconds())},
        'agora': {'inicio': hora_local(s), 'fim': hora_local(f), 'duracao': fmt_dur((f - s).total_seconds())}}}


@mcp.tool()
def cronometro_atual() -> dict:
    """Mostra os cronômetros em andamento (descrição, início e tempo decorrido), se houver.
    Mantido por compatibilidade; equivale a `cronometros_em_andamento`."""
    return cronometros_em_andamento()


def _nome_projeto(pid: str | None) -> str:
    if not pid:
        return '(sem projeto)'
    try:
        projeto_id(PROJETO_PADRAO)  # garante o cache de projetos
    except ClockifyError:
        pass
    nome = next((n for n, i in (_projetos_cache or {}).items() if i == pid), pid)
    return next((c for c in (PROJETO_PADRAO, PROJETO_REUNIAO) if c.lower() == nome), nome)


@mcp.tool()
def cronometros_em_andamento() -> dict:
    """Lista TODOS os cronômetros em andamento (id, descrição, projeto, início e tempo decorrido).
    Funciona nos dois modos; no Clockify oficial haverá no máximo um."""
    lista = _todos_em_andamento()
    if not lista:
        return {'nota': 'Nenhum cronômetro em andamento.'}
    t = agora()
    itens = [{**_resumo(e, t), 'projeto': _nome_projeto(e.get('projectId'))} for e in lista]
    return {'em_andamento': itens if len(itens) > 1 else itens[0], 'quantidade': len(itens)}


def relatorio(entradas: list[dict], periodo: str, inicio: str | None, fim: str | None, t: datetime) -> dict:
    rotulo, ini, fim_ = intervalo(periodo, inicio, fim, t)
    itens = somar(entradas, ini, fim_, t)
    total = sum(i['segundos'] for i in itens)
    por_desc: dict[str, float] = {}
    for i in itens:
        por_desc[i['descricao']] = por_desc.get(i['descricao'], 0) + i['segundos']
    saida = {
        'periodo': rotulo,
        'total': fmt_dur(total),
        'entradas': len(itens),
        'por_descricao': [{'descricao': d, 'total': fmt_dur(s)}
                          for d, s in sorted(por_desc.items(), key=lambda kv: -kv[1])],
    }
    p = (periodo or 'hoje').strip().lower()
    manual = bool(inicio or fim)
    if not manual and p in ('hoje', 'ontem'):
        saida['lancamentos'] = [
            {'id': i['id'], 'descricao': i['descricao'], 'das': local(i['inicio']).strftime('%H:%M'),
             'ate': 'em andamento' if i['andamento'] else local(i['fim']).strftime('%H:%M'),
             'duracao': fmt_dur(i['segundos'])} for i in itens]
    if manual or p in ('mes', 'semana'):
        dias: dict[date, float] = {}
        for i in itens:
            # uma entrada que cruza a meia-noite local é repartida entre os dias
            cur = i['inicio']
            while cur < i['fim']:
                d = local(cur).date()
                lim = min(i['fim'], _meia_noite(d + timedelta(days=1)))
                dias[d] = dias.get(d, 0) + (lim - cur).total_seconds()
                cur = lim
        saida['por_dia'] = [{'dia': f'{d.strftime("%d/%m")} ({DIAS[d.weekday()]})', 'total': fmt_dur(s)}
                            for d, s in sorted(dias.items())]
    if any(i['andamento'] for i in itens):
        saida['nota'] = 'Há um cronômetro em andamento; ele conta até agora.'
    return saida


@mcp.tool()
def horas_trabalhadas(periodo: str = 'hoje', inicio: str | None = None, fim: str | None = None) -> dict:
    """Soma as horas cronometradas. `periodo` = hoje, ontem, semana ou mes; ou um intervalo
    explícito com `inicio` e `fim` em AAAA-MM-DD (fim inclusive). Limites no fuso America/Sao_Paulo.
    Devolve o total ("Xh YYmin"), nº de entradas e o total por descrição; hoje/ontem listam também
    os lançamentos com horários; mes/semana/intervalo trazem o total por dia."""
    t = agora()
    _, ini, fim_ = intervalo(periodo, inicio, fim, t)
    return relatorio(_entradas(ini, fim_), periodo, inicio, fim, t)


if __name__ == '__main__':
    mcp.run()
