# Djorge v2.2 — o modelo no centro (contrato da rodada 3)

Complementa V2_SPEC.md e V2_SPEC_2.md. Estética continua a mesma.

## Quem decide
- Com o modelo carregado, **o DOFL decide** (ferramenta, entendimento, intenção, entidades via `resolve`,
  categorias, sim/não, ordinais). As regras viram **pistas** que o modelo confirma, não sobrepõem.
- Regras vêm antes do modelo só quando: (a) não há modelo; (b) a pessoa escolheu "Respostas rápidas (regras
  primeiro)"; (c) a regra tem certeza explícita maior que o modelo (ex.: `#destino &texto&`, conta "15% de 230",
  comando de interface reconhecido exatamente).
- Sempre por regras (rápido e exato): datas/horas/recorrência, mudanças do chat/interface (tema, colunas...).
- Preferência `prioridade`: `"modelo"` (padrão) | `"equilibrado"` | `"regras"` em `uiprefs` (GET/POST /api/ui).
  A resposta do comando traz `decidido_por: "modelo"|"regras"` (não mostrado como número; só um rótulo discreto).

## Djorge como pessoa
- `uiprefs`: `nome_usuario` (padrão: vazio → "você") e `nome_agente` (padrão "Djorge").
- Etapas narradas na 3ª pessoa com o nome do agente e dados reais: "Lendo o seu pedido…", "Djorge está
  entendendo o que você quer…", "Reconheceu a data ter., 3 de nov. às 14:00", "Entendeu: criar um evento",
  "Djorge está usando a ferramenta Cards…", "Encontrou 'Silvio Santos'". Cada etapa é uma linha nova; o front
  mostra a atual sem piscar e acumula as concluídas.
- Respostas usam o que o modelo extraiu: "Encontre quem foi Silvio Santos" → o modelo resolve a entidade
  ("Silvio Santos") e a resposta é "Aqui estão resultados sobre “Silvio Santos”".

## Busca na web
- Antes de sair do computador, **pergunta**: resposta com `tool:"web"`, `needs_confirm:true`,
  `proposal:{action:"web_search", q:"Silvio Santos"}` e reply "Quer que eu busque “Silvio Santos” na internet?".
  Ao confirmar, `POST /api/command/confirm` faz a busca e devolve `results`. Corrigir o bug atual da busca web
  (resultados vazios/erro): usar a Instant Answer + fallback html.duckduckgo.com com User-Agent genérico, parse
  robusto, e testes com HTML real de exemplo.

## Persistência e edição
- Alterações feitas na interface (configurações, cards, automações, árvores editadas) ficam salvas: tudo vai ao
  backend e é relido do backend ao reabrir. Editar uma regra/árvore e Salvar funciona (e mostra erros
  de validação em linguagem simples).
- Emojis da interface viram ícones SVG (mesmo conjunto de ícones do app).
