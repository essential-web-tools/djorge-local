# Djorge v2.1 — o secretário (contrato da rodada 2)

Complementa `docs/V2_SPEC.md` (continua valendo). Objetivo: o Djorge vira um **secretário e otimizador de tempo**:
entende o que chega (notificações, mensagens), organiza sozinho com o modelo + pistas + regras, e ajuda a pessoa
no dia a dia. **A estética atual fica** (marca, tipografia, layout estilo Claude): só melhorar o que for pedido.

Princípios
- O DOFL só decide; textos ao usuário são montados por modelos de frase, sempre formatados (nomes em Maiúsculas
  Iniciais, datas "ter., 3 de nov. às 14:00", valores "R$ 1.250,00").
- **Nada forçado**: ferramentas, triagem e segurança só rodam quando há pistas para isso. Pedido do próprio usuário
  nunca passa por segurança. Na dúvida, o app pergunta com opções (chips), não chuta.
- Não mostrar o que não ajuda: alternativas com probabilidade, segurança do texto do próprio usuário, CPU/GPU,
  recursos de integrações não conectadas.
- Erros e "não consegui" viram **dicas** acionáveis: "Tente: 'marca dentista dia 20 às 9h'".

## Pistas (R: `djorge_local/pistas.py`)
`pistas(texto, regras) -> {"ferramentas": {tool: score 0..1}, "sinais": [...], "faltando": [...], "perguntar": [...]}`:
léxico + padrões por ferramenta (verbos, objetos, formas), campos reconhecidos (data/hora/e-mail/valor) e
contexto. `decidir(model_probs, pistas) -> {tool, confianca, motivo, alternativas_uteis}`: guardrails — uma
ferramenta só é escolhida se o modelo e as pistas concordarem o suficiente; ferramenta incompatível com os campos
(ex.: bilhete sem destino) vira pergunta; regras explícitas ganham ("#destino &texto&" = bilhete).
Ferramentas: calcular · datas_horas · proximas_tarefas · cards · listas · bilhete · buscar · regra · **web** (nova:
busca na internet) · **abrir** (novo: abrir app/site local, ex. WhatsApp com mensagem pronta) · nenhuma.

## Comando (R) — mudanças
- `POST /api/command` aceita `{"text", "context", "stream": true}`; com stream o núcleo devolve `{"id"}` e o
  progresso fica em `GET /api/command/<id>/etapas` → `{"etapas":[{"t","texto","estado":"ok|agora|erro"}],
  "pronto": bool, "resultado"?: ...}` (frases: "Lendo o pedido…", "Achando data e hora…", "Usando a ferramenta
  Cards…", "Buscando na web…"). Sem stream continua igual.
- Resposta ganha `"dicas": [str]`, `"perguntas": [{"texto","opcoes":[{"rotulo","patch"}]}]`, `"titulo"` formatado,
  e campos já formatados (`valor_fmt`). Sem `tools` alternativos na resposta (o front não mostra).
- `POST /api/command/confirm {"id","approve","edits"}`: `edits` pode trocar o tipo da proposta
  (`{"action":"create_card","card":{...}}` ↔ `{"action":"send_message",...}`), título, campos, data.
- Datas: `cards.py` aceita ISO, "2026-11-03 14:00", "03/11/2026 14h", "amanhã 9h" (usa os reconhecedores);
  nunca mais `ValueError: due: data inválida` — valor ruim vira dica.

## Regras mais inteligentes (R: `djorge/runtime/recognizers.py`)
"todo dia 20", "todo dia 5 às 9h", "a cada 2 semanas", "toda primeira segunda do mês", "dias úteis",
"fim do mês", "daqui a 3 horas/20 min", "às 3" (tarde por contexto), "meio da tarde", "próximo dia útil",
"semana que vem na terça", "em 15/11", "15 de nov", "amanhã cedo", intervalos "das 14h às 16h", durações,
valores "1,5 mil", "R$ 2k", telefones/contatos, recorrência normalizada `{"freq":"mensal","dia":20,"hora":"09:00"}`.
Próxima ocorrência calculada (`proxima(recorrencia, agora)`).

## Busca na web (R: `djorge_local/web.py`)
`buscar_web(q, n=6) -> [{"titulo","url","trecho","fonte"}]` com a API aberta do DuckDuckGo
(`https://api.duckduckgo.com/?q=&format=json&no_html=1&skip_disambig=1`) + fallback no HTML leve
(`https://html.duckduckgo.com/html/?q=`), timeout 8 s, sem chave, marcado como "sai do computador".
Rota (T): `GET /api/web?q=`.

## Secretário, automações e notificações (T)
- **Automações "Se acontecer X, faça Y"** (`djorge_local/automacoes.py`, compiladas para árvores de `tree.py`):
  `{id, nome, ativa, quando:{fontes[], apps[], remetente, palavras[], assunto (descrição livre que o modelo
  julga: "mensagem sobre orçamento"), categoria, prioridade_min}, entao:[{acao: "criar_card"|"alerta"|"tarefa"|
  "notificar"|"etiquetar"|"arquivar"|"abrir_app"|"responder_sugestao", params:{titulo:"Alerta de orçamento —
  {remetente}", campos:["valor","prazo","remetente","empresa"], coluna, prioridade, etiqueta}}], criada, contagem}`.
  `de_texto(frase) -> automação` ("Se eu receber uma mensagem sobre orçamentos, crie um card de alerta de
  orçamento com valor, prazo e quem mandou"). Rotas: `GET /api/automacoes`, `POST /api/automacoes` (cria/atualiza),
  `POST /api/automacoes/<id>/delete`, `POST /api/automacoes/de_texto {texto}`, `POST /api/automacoes/<id>/testar {texto}`,
  `GET /api/automacoes/modelos` (receitas prontas: boletos, orçamentos, entregas, reuniões, códigos, chefe/VIP).
- **Notificações** (`djorge_local/notificacoes.py`, SQLite): todas as notificações do Windows capturadas
  (`connectors.Notifications`), com etiqueta (categoria da triagem), prioridade, regra que casou, ações tomadas,
  card criado. `GET /api/notificacoes?etiqueta=&q=&app=&lida=`, `POST /api/notificacoes/<id> {lida, etiqueta}`,
  `POST /api/notificacoes/<id>/acao {acao}`, `POST /api/notificacoes/limpar {antes_de?}`, `GET /api/notificacoes/etiquetas`.
- **Agente secretário** (`djorge_local/secretario.py`): para cada notificação/mensagem: triagem (modelo +
  pistas) → automações que casam → ações (criar card/tarefa/alerta, etiquetar, arquivar) conforme o modo
  `sugerir` (padrão: propõe, pessoa aprova num clique) ou `agir` (faz e avisa, desfazível). Configuração simples
  `GET/POST /api/secretario {ativo, modo, apps_ignorados[], vips[], horario_silencio, resumo_diario_hora}`.
- **Notificações do app com a janela fechada**: o processo segue na bandeja; toasts nativos do Windows
  (winotify ou pywin32) para alertas e resumo diário. Iniciar com o Windows ligado por padrão (minimizado).
- **Apps locais**: ação `abrir_app` — WhatsApp (`whatsapp://send?phone=&text=` ou `https://wa.me/<num>?text=`),
  navegador (URL), e-mail (`mailto:`). Contatos: `GET /api/contatos?q=` junta pessoas dos cards e das integrações
  (nome formatado, telefone, e-mail). O envio final é sempre da pessoa (o app só prepara e abre).
- **Histórico**: `POST /api/history/clear {tudo|antes_de|ids[]}`; `POST /api/history/<id>/delete`.
- **API local**: `GET /api/local_api` → `[{modelo, porta, ativa, url, token_dica}]`, `POST /api/local_api {modelo,
  ativa, porta}` liga/desliga a API do modelo sem reiniciar; botão copiar exemplo curl.
- **Início**: `GET /api/inicio` → `{tarefas_relevantes[], mensagens_importantes[], notificacoes_recentes[],
  proximos_eventos[], automacoes: n, resumo_frase}`.

## Modelo leve e int8 (F)
O DOFL deve rodar em **ONNX int8 na CPU**. Pacotes sem `onnx/` (artefato PyTorch) são convertidos uma vez em
segundo plano ("Otimizando o modelo para este computador…") ou o card pede o pacote certo. Caminho ONNX sem
importar torch (instalador menor). Modo leve: baixa prioridade do processo, descarregar modelo ocioso, iniciar
minimizado na bandeja.

## Interface (D)
Início (painel com relógio local e de outra região, clima e previsão via Open-Meteo com localização do navegador
e cidade por geocodificação reversa, tarefas relevantes, mensagens importantes, notificações recentes); Mensagens
alinhada e bonita; nova visão **Notificações**; secretário fácil de montar (assistente em 3 passos + receitas);
automações "Se… então…" com construtor simples e editor de árvore em tela cheia com botão Voltar; etapas
dinâmicas do comando; dicas no lugar de erros; trocar o tipo da proposta no resultado; menus de botão direito;
apagar histórico; dicas (tooltips) sempre por cima de tudo; painel de API local; esconder o que não ajuda.
