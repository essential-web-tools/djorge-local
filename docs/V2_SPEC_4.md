# Djorge v2.3 — secretário completo, alertas, histórico de ações e bloco de notas (rodada 4)

Complementa as specs anteriores. Regra de ouro do dono: "o app base era bom, tinha que evoluir só" —
mudanças cirúrgicas, mesma estética.

## Uso máximo do DOFL (todos os agentes)
O DOFL é um decisor: CHOICE (escolher entre opções descritas), NOUL (sim/não calibrado), SCORE (ordinal),
`resolve` (extrair/localizar trechos: ENTITY, campos), com as perguntas exatas do treino
(`djorge.training.flash_data`: TOOLS/TOOL_PT/CAN/ASK_TOOL e nomes de slot). Nunca gera texto; textos saem de
trechos extraídos + modelos de frase. Rode várias perguntas num só `engine.run([...])` (lote). Com o modelo
carregado ele decide; regras são pistas (V2_SPEC_3).

## Notificações do Windows (por padrão)
- O conector de notificações do Windows (UserNotificationListener / WinRT, com fallback) fica **ligado por
  padrão** e lista todas as notificações do sistema e dos apps na visão Notificações (mesmo com o secretário
  desligado: só lista e etiqueta por regras; sem ações).
- Com o secretário ativo: triagem completa pelo modelo — categoria, prioridade, é pedido?, ações.

## Pedidos (mensagens que pedem algo)
Para mensagens/notificações que são pedidos, o modelo extrai: **texto central** (o pedido em si, via resolve),
**título** curto (trecho extraído, limpo e formatado), **nome da tarefa**, quem pediu, prazo/data, valor.
Saída `pedido: {titulo, tarefa, texto_central, quem, prazo, valor}` em `triar`/secretário, usada no card.

## Alertas do Djorge
Card/lembrete ganha `alertas`: `{quando: ISO | "antes": minutos, repetir: recorrência normalizada
(diaria/semanal/dias_uteis/mensal/a cada N horas/minutos) , ate: ISO (todos os dias até a data X),
janela: {inicio:"08:00", fim:"20:00"} (períodos específicos), canal: ["toast","app"]}`. Comandos: "me lembra
todo dia às 9h até sexta de tomar o remédio", "me avisa de 2 em 2 horas entre 8h e 18h", "avisa 30 min antes".
Agendador no processo (bandeja) dispara toasts do Windows e aviso no app; adiar (5/15/60 min), concluir.
`GET /api/alertas`, `POST /api/alertas/<id>/adiar {min}`, `/concluir`, `/desativar`.

## Histórico = ações, não conversa
Não salvar a lista de conversas/mensagens. Salvar **cada solicitação e o que foi feito**: `{id, t, pedido
(texto curto do pedido), ferramenta, acao, alvo (card/automação/etc. com id), resultado, alteracoes:[{t,
campo, de, para}], desfeito}` — com histórico de alterações dos itens (cards etc.). A conversa da sessão
fica só na tela (memória), não no disco. Rotas existentes de histórico continuam (lista, apagar).

## Bloco de notas
Visão **Notas** (barra lateral): notas em Markdown leve com pastas/etiquetas, fixar, buscar, autosave,
histórico de versões. Participação do modelo (sem gerar texto): extrair tarefas/datas da nota e transformar
em cards ("Achei 3 tarefas"), sugerir título (trecho mais central), etiquetas (CHOICE entre etiquetas
existentes), resumo extrativo (frases centrais via HAG/BM25 + NOUL), ligar a cards/pessoas mencionadas,
perguntar à nota (busca extrativa). `notas.py` (SQLite em HOME/notas.db): `GET/POST /api/notas`,
`GET/POST /api/notas/<id>`, `/delete`, `/versoes`, `POST /api/notas/<id>/analisar` → `{titulo_sugerido,
etiquetas[], tarefas:[proposta de card], datas[], pessoas[], resumo[]}`, `POST /api/notas/<id>/perguntar {q}`.
