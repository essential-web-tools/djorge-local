# Djorge Local v2 (Windows) — contrato entre as partes

Branch `v2-windows`. Só Windows 10/11 (WebView2 via pywebview, Python 3.11+). Tudo local: servidor em
`http://127.0.0.1:8765`, token `X-Token` (cookie `djtoken` no painel). Interface em português do Brasil.
Referência visual: `docs/referencia_claude.png` (layout do app Claude: barra lateral à esquerda, conversa ao
centro, campo de entrada no rodapé, sem cabeçalho). Marca atual (manter): tokens em `djorge_local/ui.html`
(`--bg #f6f3ee`, `--rose #b56576`, Fraunces/Manrope/Geist Mono em `static/fonts`, ícone `static/icon.svg`).

O modelo é o **DOFL** (Djorge Origo Flash Local): só decide (CHOICE/NOUL/SCORE/resolve), nunca gera texto.
Runtime: `djorge/runtime/` — `recognizers.py` (reconhecer/combinar/atalho_envio/corpo_mensagem/nome_do_email),
`triagem.py` (triar), `autorizacao.py` (autorizar), `clean.py`, `semantic.py` (SemanticEngine, SemReq),
`hag.py` (busca BM25 extrativa), `tools.py` (math/time/pii). Ferramentas do app (rótulos do treino, use
EXATAMENTE estes): `djorge.training.flash_data.TOOLS` / `TOOL_PT` / `CAN` / `ASK_TOOL`:
calcular · datas_horas · proximas_tarefas · cards · listas · bilhete · buscar · regra · nenhuma.
Campos do app (alvos `resolve` ENTITY): data, hora, período do dia, pessoa, evento ou compromisso, lugar, lista,
endereço de e-mail, empresa ou estabelecimento, frequência ou repetição.

## Donos dos arquivos (cada agente só edita os seus; os demais são lidos)
| Parte | Arquivos |
|---|---|
| A. Núcleo | `djorge_local/app.py` (rotas), `cards.py`, `command.py`, `search.py`, `uiprefs.py`, `tests/test_v2_core.py` |
| B. Integrações | `djorge_local/integrations/` (pacote), `tests/test_v2_integrations.py` |
| C. Voz | `djorge_local/voice.py`, `tests/test_v2_voice.py` |
| D. Interface | `djorge_local/ui.html` (reescrita completa), `djorge_local/static/app/*` se quiser separar CSS/JS |
| E. Árvores visuais | `djorge_local/static/tree-editor.js`, `djorge_local/treegraph.py`, `tests/test_v2_tree.py` |
| F. Windows + modelo depois | `djorge_local/model_download.py`, `djorge_local/launcher.py`, `build_windows.bat`, `installer.iss`, `djorge-local.spec`, `build_exe.py`, `requirements.txt`, `README.md`, remove `build_unix.sh`/`iniciar.sh`; `tests/test_v2_model_download.py` |

A (núcleo) registra TODAS as rotas em `app.py`, chamando os módulos de B, C, E, F pelas interfaces abaixo,
com import protegido (se o módulo faltar ou falhar, a rota responde `{"error": ...}` 503, o app não cai).
Servir estáticos: `/static/app/<arquivo>.{js,css}` e `/static/tree-editor.js` (A adiciona).

## Modelo baixado depois (F)
O instalador NÃO traz o modelo. Primeiro abre a interface; o modelo é baixado quando a pessoa pede.
`ModelDownloader(cfg, models)` em `model_download.py`:
- `info() -> {"installed": bool, "name": "DOFL V2", "version": str, "path": str|None, "size_mb": 305,
  "state": "ausente"|"baixando"|"verificando"|"instalado"|"erro", "progress": 0..1, "bytes": int, "total": int,
  "error": str|None, "source": str, "details": {...metadata/djorge.json resumido...}, "loaded": bool}`
- `start() -> info` (thread: baixa de `modelo-fonte.txt`/config `model_source`, extrai em `%LOCALAPPDATA%/Djorge/modelos/flash`,
  verifica `metadata/djorge.json`, configura `cfg.models.flash.path`, pede `models` para carregar), `cancel()`, `remove()`.
Rotas (A): `GET /api/model/info`, `POST /api/model/download`, `POST /api/model/cancel`, `POST /api/model/remove`.
Tudo que precisa do modelo, sem modelo, responde 409 `{"error":"modelo_ausente"}`; a interface mostra o card do
modelo e oferece baixar. Regras (recognizers) funcionam sem modelo.

## Cards (A) — SQLite em `HOME/cards.db`
Card: `{id, kind: "tarefa"|"evento"|"lembrete"|"nota"|"lista"|"mensagem", title, body, column:
"entrada"|"hoje"|"fazendo"|"aguardando"|"feito", start, end, due (ISO 8601 ou null), all_day, priority 0..3,
tags[], people[{nome,tipo,email}], checklist[{text,done}], recurrence (texto), color, pinned, done,
source {integration, external_id, url}, order (float), created, updated}`.
- `GET /api/cards?q=&kind=&column=&tag=&from=&to=&source=&priority=&done=&person=` → `{"cards":[...]}`
- `POST /api/cards` (cria; campos parciais) → card · `POST /api/cards/<id>` (atualiza parcial) → card
- `POST /api/cards/<id>/delete` · `POST /api/cards/move {id, column, order, start?}` (arrastar no kanban/agenda)
- `GET /api/agenda?from=&to=` → cards com data no intervalo (eventos, lembretes, tarefas com prazo)

## Comando (A) — o card de entrada estilo IA
`POST /api/command {"text", "context"?: {"view":..., "selected_card":...}}` →
`{"id", "kind": "pedido"|"mensagem", "tool", "tool_p", "tools":[{tool,p}], "can_do_p", "fields":[{campo,texto,
valor,p,fonte,tipo,ini,fim}], "corpo", "atalho", "reply": "texto curto montado por modelos de frase (não gerado)",
"proposal": {"action": "create_card"|"update_card"|"send_message"|"search"|"show_agenda"|"calc"|"time"|
"create_tree"|"ui"|"none", ... dados ...}, "authorization": {"decisao","motivos","p"}, "needs_confirm": bool,
"results": [...] (busca/agenda/calculo), "timing_ms"}`
`POST /api/command/confirm {"id", "approve": bool, "edits"?: {...}}` executa a proposta (cria card, envia
mensagem por B, cria árvore por E, aplica preferência de interface).
`POST /api/triage {"text","remetente","assunto"}` → saída de `triar` (+ engine se houver modelo).
Ferramentas: calcular → `djorge.runtime.tools` math; datas_horas → tools time; proximas_tarefas → agenda;
cards/listas → proposta de card (título sem os campos, data/hora normalizadas das regras); bilhete → proposta
de mensagem (destino, corpo, canal), sempre `needs_confirm` via `autorizar`; buscar → search; regra → árvore
(E); nenhuma → reply educado de fora do escopo. Sem modelo: só regras + palavras-chave, com aviso.

## Interface editada pelo agente (A: `uiprefs.py`)
Preferências seguras (lista fechada, nada de código): tema claro/escuro/auto, densidade, cor de destaque
(paleta da marca), visão padrão (início/kanban/agenda/lista), colunas visíveis do kanban, filtros salvos,
ordem da barra lateral, voz ligada, fala das respostas, tamanho da fonte. `GET /api/ui`, `POST /api/ui`.
Comandos como "deixa escuro", "esconde a coluna feito", "salva filtro urgentes desta semana" viram
`proposal.action="ui"` com o patch; a interface aplica ao vivo.

## Busca (A: `search.py`)
`GET /api/search?q=&scope=all|cards|history|integrations` → resultados com trecho, fonte, pontuação. Usa BM25
de `hag.py` + entidades/datas das regras + (com modelo) reordenação por NOUL "este item responde a busca?".
Integrações (B) expõem `search(q)` quando conectadas (Gmail/Outlook).

## Integrações (B) — `from djorge_local.integrations import Integrations`
`Integrations(cfg, cards, notify)`, só biblioteca padrão (urllib) + `keyring` para segredos:
- `list() -> [{"id","name","group","icon","connected","account","last_sync","error","needs":[{key,label,secret}],"features":[...]}]`
- `configure(id, values)` (client id/secret, tokens, phone id...) · `connect(id) -> {"auth_url"}|{"ok"}` (OAuth 2
  com PKCE e redirect `http://127.0.0.1:8765/oauth/callback`) · `oauth_callback(query) -> html`
  · `disconnect(id)` · `sync(id) -> {"imported":n,"updated":n}` (importa para cards) · `send(id, to, body,
  subject=None) -> {"ok","id"}` · `search(q) -> [...]` · `start_background()` (sincroniza a cada N min).
- ids: `google` (Gmail, Calendar, Tasks), `microsoft` (Outlook, Calendar, To Do — Graph), `whatsapp` (Cloud API
  oficial: token + phone number id; envio; recebimento por webhook não, só envio), `gemini` (chave de API;
  opcional, só para rascunho de texto de mensagem quando a pessoa pedir; marcado como "sai do computador").
Rotas (A): `GET /api/integrations`, `POST /api/integrations/<id>/configure|connect|disconnect|sync`,
`GET /oauth/callback` (sem token; valida `state`).

## Voz (C) — `from djorge_local.voice import Voice`
`Voice(cfg)`: `status() -> {"tts":bool,"stt":bool,"tts_engine","stt_engine","voices":[...],"stt_model"}`,
`speak(text, voice=None, rate=None)`, `stop()`, `transcribe(wav_bytes, lang="pt") -> {"text","ms"}`,
`ensure_stt()` (baixa o modelo de fala quando pedido). TTS: SAPI do Windows (comtypes/pywin32, ou pyttsx3),
STT: faster-whisper local (opcional). A interface grava WAV 16 kHz mono (AudioWorklet) e manda em base64;
se o STT local não existir, usa `webkitSpeechRecognition` do WebView2 quando disponível.
Rotas (A): `GET /api/voice/status`, `POST /api/voice/speak {text}`, `POST /api/voice/stop`,
`POST /api/voice/transcribe {audio_b64}`, `POST /api/voice/setup`.

## Árvores visuais (E)
`treegraph.py`: `tree_to_graph(tree) -> {"nodes":[{id,kind,label,props,x,y}],"edges":[{from,to,label}]}`,
`graph_to_tree(graph) -> tree` (formato de `tree.py`, valida com `tree.validate`), `layout(graph)` automático.
`static/tree-editor.js`: componente sem dependências `window.TreeEditor.mount(el, {graph, onChange, api})` —
nós arrastáveis, ligar saídas a entradas, paleta de nós (choice/check/score/multi/if/select/action), painel de
propriedades, zoom/pan, desfazer, validar, testar com texto (`POST /api/event {sync:true, tree}`), e a mesma
árvore criada por comando aparece pronta para ajustar.
Rotas (A): `GET /api/trees/graph` (todas as árvores como grafos), `POST /api/trees/graph {graph}` (salva),
`POST /api/rules/preview` já existe (texto → árvore).
