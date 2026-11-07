/* Djorge Local — editor visual de árvores de decisão. Sem dependências.
 *
 *   const ed = TreeEditor.mount(el, {graph, onChange, api, readOnly})
 *   ed.getGraph() · ed.setGraph(g) · ed.validate() · ed.autoLayout() · ed.fit() · ed.undo() · ed.redo()
 *   ed.test(text) · ed.highlight(trail) · ed.select(id) · ed.destroy()
 *   TreeEditor.fromTree(tree, api?) -> Promise<graph> · TreeEditor.toTree(graph, api?) -> Promise<{tree, errors}>
 *
 * O grafo é o de djorge_local/treegraph.py: {name, enabled, when, start, extra, _has,
 *   nodes:[{id, kind, label, props, ports, list_ports, x, y}], edges:[{from, to, label, key?}]}
 */
(function () {
  'use strict';
  const W = 240, H = 64, PH = 26, GX = 90, GY = 40;
  const MODELS = [['', 'automático'], ['origo', 'Origo'], ['flash', 'Flash']];
  const KINDS = {
    choice: {name: 'Pergunta com opções', icon: 'diamond', hint: 'Se o texto for sobre A, B ou C… cada opção segue um caminho',
      make: () => ({choice: ['opção 1', 'opção 2'], ask: 'Do que se trata?'})},
    check: {name: 'Pergunta sim ou não', icon: 'check', hint: 'Se o texto disser tal coisa… então sim, senão não',
      make: () => ({check: 'o texto pede uma resposta'})},
    score: {name: 'Nota de 1 a 3', icon: 'bars', hint: 'Dá uma nota (baixa, média, alta) e segue pelo nível',
      make: () => ({score: ['baixa', 'média', 'alta'], ask: 'Qual a urgência?'})},
    multi: {name: 'Vários assuntos', icon: 'list', hint: 'Marca todos os assuntos que aparecem no texto',
      make: () => ({multi: ['rótulo 1', 'rótulo 2'], ask: 'Quais assuntos aparecem?'})},
    if: {name: 'Regra exata', icon: 'branch', hint: 'Se tiver estas palavras, deste remetente ou deste app…',
      make: () => ({if: {contains_any: ['urgente']}})},
    select: {name: 'Medir um valor', icon: 'hash', hint: 'Soma ou mede algo do texto (ex.: valores em R$)',
      make: () => ({select: 'TOTAL TYPE money'})},
    query: {name: 'Consulta avançada', icon: 'term', hint: 'Para quem conhece a linguagem DJORGE', make: () => ({query: 'NOUL "…" FROM text'})},
    action: {name: 'Ação', icon: 'play', hint: '', make: () => ({action: 'notify'})}
  };
  const ACTIONS = {
    notify: {name: 'Me avisar', icon: 'bell', make: () => ({action: 'notify', title: '{title}', message: '{sender}: {text:140}'})},
    log: {name: 'Anotar no histórico', icon: 'edit', make: () => ({action: 'log', message: '{source} · {sender} · {title}'})},
    write_file: {name: 'Salvar em arquivo', icon: 'file', make: () => ({action: 'write_file', file: 'registro.txt', content: '{time} | {title}'})},
    webhook: {name: 'Chamar um site (webhook)', icon: 'swap', make: () => ({action: 'webhook', url: 'https://', body: {texto: '{text}'}})},
    command: {name: 'Rodar um programa', icon: 'term', make: () => ({action: 'command', argv: []})},
    email_move: {name: 'Mover e-mail', icon: 'mail', make: () => ({action: 'email_move', folder: '', uid: '{uid}', mailbox: '{mailbox}'})},
    stop: {name: 'Parar aqui', icon: 'stop', make: () => ({action: 'stop'})},
    // ações das automações e do secretário (tree.NOVAS_ACOES)
    criar_card: {name: 'Criar um card', icon: 'board', fields: 'card', make: () => ({action: 'criar_card', titulo: '{title}', campos: []})},
    alerta: {name: 'Criar um alerta', icon: 'warn', fields: 'card', make: () => ({action: 'alerta', titulo: 'Alerta: {title}', campos: []})},
    tarefa: {name: 'Criar uma tarefa', icon: 'check', fields: 'card', make: () => ({action: 'tarefa', titulo: '{title}', campos: []})},
    notificar: {name: 'Avisar no Windows', icon: 'bell', fields: 'card', make: () => ({action: 'notificar', titulo: '{title}'})},
    etiquetar: {name: 'Pôr uma etiqueta', icon: 'tag', make: () => ({action: 'etiquetar', etiqueta: ''})},
    arquivar: {name: 'Arquivar', icon: 'archive', make: () => ({action: 'arquivar'})},
    abrir_app: {name: 'Abrir um app', icon: 'open', make: () => ({action: 'abrir_app', app: 'whatsapp'})},
    responder_sugestao: {name: 'Sugerir uma resposta', icon: 'reply', make: () => ({action: 'responder_sugestao'})},
    destacar: {name: 'Pôr em destaque', icon: 'star', make: () => ({action: 'destacar', cor: 'rosa', icone: 'estrela'})},
    // v2.5: tratamentos (antes das ações) e ações novas
    tratar: {name: 'Tratar a mensagem', icon: 'filter', make: () => ({action: 'tratar', tipo: 'extrair', campos: ['valor', 'prazo']})},
    lembrete: {name: 'Criar um lembrete', icon: 'clock', fields: 'card', make: () => ({action: 'lembrete', titulo: '{title}', campos: []})},
    nota: {name: 'Criar uma nota', icon: 'note', make: () => ({action: 'nota', titulo: '{title}'})},
    atualizar_card: {name: 'Atualizar o card', icon: 'edit', make: () => ({action: 'atualizar_card'})},
    mover_card: {name: 'Mover o card', icon: 'arrow', make: () => ({action: 'mover_card', coluna: 'hoje'})},
    concluir_card: {name: 'Concluir o card', icon: 'boxok', make: () => ({action: 'concluir_card'})},
    prioridade: {name: 'Mudar a prioridade', icon: 'flame', make: () => ({action: 'prioridade', prioridade: 'alta'})},
    abrir_site: {name: 'Abrir um site', icon: 'globe', make: () => ({action: 'abrir_site', url: 'https://'})},
    ignorar: {name: 'Ignorar', icon: 'eye', make: () => ({action: 'ignorar'})},
    encadear: {name: 'Rodar outra automação', icon: 'branch', make: () => ({action: 'encadear', automacao: ''})},
    adicionar_lista: {name: 'Adicionar a uma lista', icon: 'list', make: () => ({action: 'adicionar_lista', lista: 'Compras', texto: '{title}'})},
    registrar: {name: 'Registrar no histórico', icon: 'file', make: () => ({action: 'registrar', mensagem: '{title}'})}
  };
  const NEW_ACTIONS = ['criar_card', 'alerta', 'tarefa', 'notificar', 'etiquetar', 'arquivar', 'abrir_app', 'responder_sugestao',
    'destacar', 'tratar', 'lembrete', 'nota', 'atualizar_card', 'mover_card', 'concluir_card', 'prioridade', 'abrir_site', 'ignorar',
    'encadear', 'adicionar_lista', 'registrar'];
  const TRATS = [['extrair', 'Extrair campos'], ['classificar', 'Classificar entre opções'], ['resumir', 'Resumir (frase central)'],
    ['formatar', 'Formatar um campo'], ['agrupar', 'Juntar várias em um resumo']];
  // ícone do conjunto do app (<symbol id="i-…"> em ui.html); fora do app cai num ponto
  const ico = (name) => { const sp = document.createElement('span'); sp.className = 'te-svg-i'; sp.setAttribute('aria-hidden', 'true');
    sp.innerHTML = document.getElementById('i-' + name) ? `<svg class="i sm" aria-hidden="true"><use href="#i-${name}"/></svg>` : '•'; return sp; };
  const PALETTE = ['choice', 'check', 'score', 'multi', 'if', 'select', 'action:tratar', 'action:criar_card', 'action:alerta', 'action:notificar',
    'action:lembrete', 'action:nota', 'action:mover_card', 'action:concluir_card', 'action:adicionar_lista', 'action:responder_sugestao',
    'action:encadear', 'action:etiquetar', 'action:arquivar', 'action:notify', 'action:log', 'action:write_file', 'action:webhook', 'action:command', 'action:stop'];
  const PORT_NAMES = {'true': 'sim', 'false': 'não', abstain: 'não tenho certeza', 'default': 'qualquer outro caso', none: 'nenhum', then: 'e depois'};
  const COND = [['contains_any', 'Contém alguma destas palavras', 'chips'], ['contains_all', 'Contém todas estas palavras', 'chips'],
    ['sender_has', 'Remetente contém', 'chips'], ['app_in', 'Aplicativo', 'chips'], ['source_in', 'De onde veio (email, notifications, folder, clipboard, http)', 'chips'],
    ['seguranca_in', 'Parece (seguro, spam, golpe, phishing, malware)', 'chips'], ['categoria_in', 'Categoria da triagem', 'chips'],
    ['prioridade_min', 'Prioridade mínima (baixa, normal, alta, urgente)', 'text'], ['vip', 'Só de pessoas VIP', 'bool'], ['regex', 'Padrão avançado (expressão regular)', 'text'],
    ['not_contains', 'Não contém nenhuma destas palavras', 'chips'], ['has_attachment', 'Tem anexo', 'bool'], ['has_link', 'Tem link', 'bool'],
    ['has_code', 'Tem código', 'bool'], ['valor_gt', 'Valor maior que (R$)', 'num'], ['valor_lt', 'Valor menor que (R$)', 'num'],
    ['tag_in', 'Etiqueta do card', 'chips'], ['column_in', 'Coluna do card', 'chips']];
  const PLACEHOLDERS = ['{text}', '{title}', '{sender}', '{source}', '{app}', '{time}', '{text:100}', '{valor}', '{prazo}', '{classe}', '{resumo}'];

  const CSS = `
.te-root{--te-bg:var(--bg,#f6f3ee);--te-bg2:var(--bg2,#efe9e0);--te-surface:var(--surface,#fdfcf9);--te-ink:var(--ink,#26221e);
 --te-ink2:var(--ink2,#5f584f);--te-ink3:var(--ink3,#968d81);--te-rose:var(--rose,#b56576);--te-rose-deep:var(--rose-deep,#93485b);
 --te-rose-soft:var(--rose-soft,#f5e5e9);--te-glow:var(--rose-glow,rgba(181,101,118,.16));--te-line:var(--line,rgba(38,34,30,.10));
 --te-line2:var(--line2,rgba(38,34,30,.06));--te-sh1:var(--sh1,0 10px 30px rgba(38,34,30,.07));--te-sh2:var(--sh2,0 26px 60px rgba(38,34,30,.12));
 --te-serif:var(--serif,'Fraunces',Georgia,serif);--te-sans:var(--sans,'Manrope',system-ui,sans-serif);--te-mono:var(--mono,'Geist Mono',ui-monospace,monospace);
 --te-err:#c0563f;--te-warn:#b8892e;
 position:relative;display:grid;grid-template-columns:200px 1fr 300px;grid-template-rows:auto 1fr auto;height:100%;min-height:480px;
 font-family:var(--te-sans);color:var(--te-ink);background:var(--te-bg);border:1px solid var(--te-line);border-radius:var(--r-lg,28px);overflow:hidden;outline:none}
@media (prefers-color-scheme:dark){.te-root{--te-bg:var(--bg,#1b1916);--te-bg2:var(--bg2,#24211d);--te-surface:var(--surface,#2a2622);--te-ink:var(--ink,#f1ebe3);
 --te-ink2:var(--ink2,#c4bab0);--te-ink3:var(--ink3,#8e857b);--te-rose-soft:var(--rose-soft,#3d2a2f);--te-line:var(--line,rgba(255,255,255,.10));--te-line2:var(--line2,rgba(255,255,255,.06))}}
.te-root.te-ro{grid-template-columns:0 1fr 300px}.te-root.te-ro .te-pal{display:none}
.te-root *{box-sizing:border-box}
.te-bar{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--te-line);background:var(--te-surface)}
.te-bar .te-name{font:600 17px var(--te-serif);margin-right:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
.te-btn{font:600 12px var(--te-sans);color:var(--te-ink2);background:transparent;border:1px solid var(--te-line);padding:7px 12px;border-radius:999px;cursor:pointer;transition:background .25s,color .25s,border-color .25s}
.te-btn:hover,.te-btn:focus-visible{background:var(--te-rose-soft);color:var(--te-rose-deep);border-color:transparent;outline:none}
.te-btn:disabled{opacity:.45;cursor:default}
.te-btn.te-pri{background:linear-gradient(135deg,var(--te-rose),var(--te-rose-deep));color:#fff;border-color:transparent;box-shadow:0 8px 20px var(--te-glow)}
.te-sp{flex:1}
.te-test{display:flex;gap:6px;align-items:center;flex:1 1 260px;max-width:440px}
.te-in-text{flex:1;font:13px var(--te-sans);color:var(--te-ink);background:var(--te-bg);border:1px solid var(--te-line);border-radius:999px;padding:7px 14px;outline:none;min-width:0}
.te-in-text:focus{border-color:var(--te-rose);box-shadow:0 0 0 3px var(--te-glow)}
.te-pal{border-right:1px solid var(--te-line);padding:12px;overflow:auto;background:var(--te-surface)}
.te-help{font:12px/1.4 var(--te-sans);color:var(--te-ink3);margin:0 2px 10px}
.te-pal h4,.te-props h4{font:600 10px var(--te-mono);letter-spacing:.22em;text-transform:uppercase;color:var(--te-ink3);margin:6px 2px 8px}
.te-pal button{display:flex;gap:8px;align-items:center;width:100%;text-align:left;font:600 12.5px var(--te-sans);color:var(--te-ink);background:var(--te-bg);
 border:1px solid var(--te-line2);border-radius:14px;padding:8px 10px;margin-bottom:6px;cursor:grab;transition:transform .2s,box-shadow .2s,border-color .2s}
.te-pal button:hover,.te-pal button:focus-visible{border-color:var(--te-rose);box-shadow:var(--te-sh1);transform:translateY(-1px);outline:none}
.te-pal .te-ic{width:24px;height:24px;flex:none;display:grid;place-items:center;border-radius:8px;background:var(--te-rose-soft);color:var(--te-rose-deep);font-size:12px}
.te-stage{position:relative;overflow:hidden;cursor:grab;touch-action:none;background-color:var(--te-bg);
 background-image:radial-gradient(var(--te-line) 1px,transparent 1px);background-size:22px 22px}
.te-stage.te-panning{cursor:grabbing}
.te-world{position:absolute;left:0;top:0;transform-origin:0 0}
.te-svg{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
.te-edge{fill:none;stroke:var(--te-ink3);stroke-width:2;opacity:.75;transition:stroke .2s}
.te-hitp{fill:none;stroke:transparent;stroke-width:14;pointer-events:stroke;cursor:pointer}
.te-edge.te-sel{stroke:var(--te-rose);stroke-width:3;opacity:1}
.te-edge.te-flow{stroke:var(--te-rose);stroke-width:3;opacity:1;stroke-dasharray:8 6;animation:te-dash .6s linear infinite}
.te-tmp{fill:none;stroke:var(--te-rose);stroke-width:2.5;stroke-dasharray:6 5}
@keyframes te-dash{to{stroke-dashoffset:-28}}
.te-node{position:absolute;width:${W}px;background:var(--te-surface);border:1px solid var(--te-line);border-radius:18px;box-shadow:var(--te-sh1);
 cursor:move;user-select:none;transition:box-shadow .25s,border-color .25s;outline:none}
.te-node:hover{box-shadow:var(--te-sh2)}
.te-node.te-sel,.te-node:focus-visible{border-color:var(--te-rose);box-shadow:0 0 0 3px var(--te-glow),var(--te-sh2)}
.te-node.te-err{border-color:var(--te-err);border-style:dashed}
.te-node.te-warn{border-color:var(--te-warn);border-style:dashed}
.te-node.te-hit{border-color:var(--te-rose);animation:te-pulse 1.1s var(--ease,ease) 2}
@keyframes te-pulse{0%{box-shadow:0 0 0 0 var(--te-rose)}70%{box-shadow:0 0 0 14px rgba(181,101,118,0)}100%{box-shadow:0 0 0 3px var(--te-glow)}}
.te-node.te-hit-done{border-color:var(--te-rose);box-shadow:0 0 0 3px var(--te-glow),var(--te-sh1)}
.te-head{height:${H}px;padding:10px 14px 0 18px;border-radius:18px 18px 0 0;position:relative}
.te-head::before{content:"";position:absolute;left:0;top:14px;bottom:14px;width:4px;border-radius:0 4px 4px 0;background:var(--te-kc,var(--te-rose))}
.te-kind{display:flex;justify-content:space-between;gap:6px;font:600 9.5px var(--te-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--te-ink3)}
.te-kind b{color:var(--te-kc,var(--te-rose-deep));font-weight:600}
.te-title{font:600 13.5px var(--te-sans);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.te-badge{position:absolute;top:-10px;left:14px;font:600 9px var(--te-mono);letter-spacing:.14em;text-transform:uppercase;background:linear-gradient(135deg,var(--te-rose),var(--te-rose-deep));color:#fff;padding:3px 8px;border-radius:999px}
.te-ebadge{position:absolute;top:-9px;right:12px;width:20px;height:20px;border-radius:50%;background:var(--te-err);color:#fff;font:700 12px var(--te-sans);display:grid;place-items:center}
.te-node.te-warn .te-ebadge{background:var(--te-warn)}
.te-port{height:${PH}px;display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:0 0 0 14px;border-top:1px solid var(--te-line2);font:500 12px var(--te-sans);color:var(--te-ink2);position:relative}
.te-port span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:190px}
.te-port.te-on span{color:var(--te-ink)}
.te-dot{width:14px;height:14px;border-radius:50%;background:var(--te-surface);border:2px solid var(--te-ink3);margin-right:-7px;cursor:crosshair;flex:none;transition:transform .15s,background .15s,border-color .15s}
.te-port.te-on .te-dot{background:var(--te-rose);border-color:var(--te-rose)}
.te-dot:hover{transform:scale(1.3);border-color:var(--te-rose)}
.te-in{position:absolute;left:-7px;top:${H / 2 - 7}px;width:14px;height:14px;border-radius:50%;background:var(--te-surface);border:2px solid var(--te-ink3)}
.te-node.te-drop{border-color:var(--te-rose);box-shadow:0 0 0 4px var(--te-glow)}
.te-props{border-left:1px solid var(--te-line);padding:14px;overflow:auto;background:var(--te-surface);font-size:13px}
.te-props h3{font:600 18px var(--te-serif);margin:0 0 4px}
.te-props .te-sub{color:var(--te-ink3);font:500 11px var(--te-mono);margin-bottom:12px}
.te-f{margin:0 0 12px}.te-f>label,.te-f>.te-l{display:block;font:600 11.5px var(--te-sans);color:var(--te-ink2);margin-bottom:5px}
.te-f input[type=text],.te-f textarea,.te-f select{width:100%;font:13px var(--te-sans);color:var(--te-ink);background:var(--te-bg);border:1px solid var(--te-line);border-radius:12px;padding:8px 10px;outline:none}
.te-f textarea{font:12px var(--te-mono);min-height:70px;resize:vertical}
.te-f input:focus,.te-f textarea:focus,.te-f select:focus{border-color:var(--te-rose);box-shadow:0 0 0 3px var(--te-glow)}
.te-mono{font-family:var(--te-mono)!important}
.te-chips{display:flex;flex-wrap:wrap;gap:5px;padding:6px;background:var(--te-bg);border:1px solid var(--te-line);border-radius:12px}
.te-chip{display:inline-flex;align-items:center;gap:4px;background:var(--te-rose-soft);color:var(--te-rose-deep);font:600 12px var(--te-sans);padding:3px 4px 3px 10px;border-radius:999px}
.te-chip button{border:0;background:transparent;color:inherit;cursor:pointer;font-size:14px;line-height:1;padding:0 4px;border-radius:50%}
.te-chip button:focus-visible{outline:2px solid var(--te-rose)}
.te-chips input{flex:1;min-width:80px;border:0!important;background:transparent!important;box-shadow:none!important;padding:3px 4px!important;font:12.5px var(--te-sans);color:var(--te-ink);outline:none}
.te-range{display:flex;align-items:center;gap:10px}
.te-range input[type=range]{flex:1;-webkit-appearance:none;appearance:none;height:6px;border-radius:999px;background:linear-gradient(90deg,var(--te-rose) var(--p,50%),var(--te-line) var(--p,50%));outline:none}
.te-range input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:var(--te-surface);border:3px solid var(--te-rose);box-shadow:0 2px 8px var(--te-glow);cursor:pointer}
.te-range input[type=range]:focus-visible::-webkit-slider-thumb{box-shadow:0 0 0 4px var(--te-glow)}
.te-range output{font:600 12px var(--te-mono);color:var(--te-rose-deep);min-width:38px;text-align:right}
.te-row{display:flex;gap:8px;align-items:center}.te-row label{font-weight:500}
.te-link{display:grid;grid-template-columns:1fr 1.2fr;gap:6px;align-items:center;margin-bottom:6px}
.te-link span{font:600 12px var(--te-sans);color:var(--te-ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.te-ph{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}.te-ph button{font:500 10.5px var(--te-mono);border:1px dashed var(--te-line);background:transparent;color:var(--te-ink2);border-radius:8px;padding:2px 6px;cursor:pointer}
.te-ph button:hover{border-color:var(--te-rose);color:var(--te-rose-deep)}
.te-msg{color:var(--te-err);font-size:12px;margin:4px 0}
.te-foot{grid-column:1/-1;border-top:1px solid var(--te-line);background:var(--te-surface);padding:8px 14px;font-size:12px;color:var(--te-ink2);display:flex;gap:10px;flex-wrap:wrap;align-items:center;max-height:150px;overflow:auto}
.te-foot .te-ok{color:var(--te-rose-deep);font-weight:600}
.te-foot button.te-e{font:500 12px var(--te-sans);border:0;background:transparent;color:var(--te-err);cursor:pointer;text-align:left;padding:2px 4px;border-radius:6px}
.te-foot button.te-e.te-w{color:var(--te-warn)}
.te-foot button.te-e:hover{background:var(--te-rose-soft)}
.te-trail{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.te-step{background:var(--te-rose-soft);color:var(--te-rose-deep);border-radius:999px;padding:3px 10px;font:600 11.5px var(--te-sans)}
.te-mini{position:absolute;right:12px;bottom:12px;width:180px;height:120px;background:var(--te-surface);border:1px solid var(--te-line);border-radius:14px;box-shadow:var(--te-sh1);opacity:.92;cursor:pointer}
.te-mini rect.n{fill:var(--te-ink3);opacity:.5}.te-mini rect.n.s{fill:var(--te-rose);opacity:1}.te-mini rect.v{fill:var(--te-glow);stroke:var(--te-rose);stroke-width:1.5}
.te-zoom{position:absolute;left:12px;bottom:12px;display:flex;gap:4px;background:var(--te-surface);border:1px solid var(--te-line);border-radius:999px;padding:4px;box-shadow:var(--te-sh1)}
.te-zoom button{width:30px;height:30px;border-radius:50%;border:0;background:transparent;color:var(--te-ink2);font:600 14px var(--te-sans);cursor:pointer}
.te-zoom button:hover,.te-zoom button:focus-visible{background:var(--te-rose-soft);color:var(--te-rose-deep);outline:none}
.te-svg-i{display:inline-grid;place-items:center;line-height:0}.te-svg-i svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.te-btn .te-svg-i,.te-e .te-svg-i,.te-ok .te-svg-i{vertical-align:-2px}
.te-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
@media (max-width:860px){.te-root{grid-template-columns:0 1fr;grid-template-rows:auto 1fr auto auto}.te-pal{display:none}.te-props{grid-column:1/-1;border-left:0;border-top:1px solid var(--te-line);max-height:40vh}}
`;

  // ---------------------------------------------------------------- utilidades
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const h = (tag, attrs, ...kids) => {
    const e = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
    return e;
  };
  const SVGNS = 'http://www.w3.org/2000/svg';
  const s = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const slug = (t) => String(t).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 30) || 'no';
  const portName = (p) => PORT_NAMES[p] || p;
  const kindOf = (n) => (n.kind === 'action' ? ((ACTIONS[n.props.action] || {}).name || n.props.action) : (KINDS[n.kind] || {}).name || n.kind);

  function labelOf(n) {
    const p = n.props || {};
    if (n.kind === 'action' && p.action === 'tratar') { const t = (TRATS.find((x) => x[0] === p.tipo) || [, 'Tratar'])[1]; return p.tipo === 'classificar' && (p.opcoes || []).length ? `${t}: ${p.opcoes.join(' / ')}` : p.tipo === 'agrupar' ? `${t} a cada ${p.minutos || 30} min` : t; }
    if (n.kind === 'if' && p.if && p.if.count_min) return `Mais de ${p.if.count_min.n} em ${p.if.count_min.minutos} min`;
    if (n.kind === 'action') { const d = [p.title, p.titulo, p.etiqueta, p.file, p.url, p.folder, p.message, (p.argv || []).join(' ')].find((v) => typeof v === 'string' && v.trim()); return d || (ACTIONS[p.action] || {}).name || String(p.action); }
    if (p.ask) return String(p.ask);
    if (['check', 'select', 'query'].includes(n.kind) && typeof p[n.kind] === 'string' && p[n.kind].trim()) return p[n.kind];
    return (KINDS[n.kind] || {}).name || n.kind;
  }

  function phraseOf(n) {
    const p = n.props || {}, l = labelOf(n);
    if (n.kind === 'action') return (ACTIONS[p.action] || {}).name === l ? l : ((ACTIONS[p.action] || {}).name || 'Fazer') + ': ' + l;
    if (n.kind === 'if') { const c = p.if || {}; const w = (c.contains_any || c.contains_all || []).slice(0, 3);
      return w.length ? 'Se tiver “' + w.join('”, “') + '”' : c.sender_has ? 'Se for de ' + [].concat(c.sender_has).join(', ') : c.app_in ? 'Se vier de ' + [].concat(c.app_in).join(', ') : 'Se a regra valer'; }
    if (n.kind === 'check') return 'Se ' + String(l).replace(/^se\s+/i, '') + '?';
    return l;
  }
  function defaultPorts(n) {
    const p = n.props || {};
    switch (n.kind) {
      case 'choice': return [...(p.choice || []).map(String), 'abstain', 'default'];
      case 'score': return [...(p.score || []).map(String), 'abstain', 'default'];
      case 'multi': return [...(p.multi || []).map(String), 'none', 'abstain'];
      case 'check': return ['true', 'false', 'abstain'];
      case 'if': return ['true', 'false'];
      case 'select': case 'query': return ['default', 'abstain'];
      case 'action': return p.action === 'stop' ? [] : ['then'];
    }
    return [];
  }

  function portsOf(g, n) {
    const out = [];
    const add = (p) => { if (p != null && !out.includes(String(p))) out.push(String(p)); };
    (n.ports || []).forEach(add);
    defaultPorts(n).forEach(add);
    g.edges.forEach((e) => { if (e.from === n.id) add(e.label); });
    if (n.kind === 'action' && n.props.action === 'stop') return out.filter((p) => g.edges.some((e) => e.from === n.id && e.label === p));
    // ordem: padrão primeiro na ordem das opções, extras (">= 2") antes de abstain/default
    const tail = ['none', 'abstain', 'default'];
    return out.filter((p) => !tail.includes(p)).concat(tail.filter((p) => out.includes(p)));
  }

  const nodeH = (g, n) => H + PH * Math.max(1, portsOf(g, n).length);

  function layoutGraph(g) {
    const by = {}; g.nodes.forEach((n) => (by[n.id] = n));
    const out = {}; g.nodes.forEach((n) => (out[n.id] = []));
    g.edges.forEach((e) => { if (out[e.from] && by[e.to] && !out[e.from].includes(e.to)) out[e.from].push(e.to); });
    const depth = {};
    const roots = (by[g.start] ? [g.start] : []).concat(g.nodes.map((n) => n.id).filter((i) => i !== g.start));
    for (const r of roots) {
      if (r in depth) continue;
      const vals = Object.values(depth);
      depth[r] = vals.length ? Math.max(...vals) + 1 : 0;
      const q = [r];
      while (q.length) { const c = q.shift(); for (const x of out[c]) if (!(x in depth)) { depth[x] = depth[c] + 1; q.push(x); } }
    }
    const cols = {};
    g.nodes.forEach((n) => (cols[depth[n.id]] = cols[depth[n.id]] || []).push(n.id));
    const order = {}, hs = {};
    Object.keys(cols).map(Number).sort((a, b) => a - b).forEach((d) => {
      if (d) {
        const key = (id) => { const ps = Object.keys(out).filter((p) => out[p].includes(id) && p in order).map((p) => order[p]); return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : 1e9; };
        cols[d].sort((a, b) => key(a) - key(b));
      }
      let y = 0;
      cols[d].forEach((id, i) => { order[id] = i; by[id].x = d * (W + GX); by[id].y = y; y += nodeH(g, by[id]) + GY; });
      hs[d] = y - GY;
    });
    const top = Math.max(0, ...Object.values(hs));
    for (const d in cols) cols[d].forEach((id) => (by[id].y = Math.round(by[id].y + (top - hs[d]) / 2)));
    return g;
  }

  // validação local (o servidor confirma com tree.validate quando api.validate existe)
  function validateGraph(g) {
    const errs = [];
    const add = (node, message, level = 'erro') => errs.push({node, message, level});
    if (!String(g.name || '').trim()) add(null, 'a árvore precisa de um nome');
    if (!g.nodes.length) add(null, 'a árvore não tem nenhum nó');
    const ids = new Set();
    for (const n of g.nodes) {
      const p = n.props || {};
      if (ids.has(n.id)) add(n.id, `identificador repetido “${n.id}”`);
      ids.add(n.id);
      if (['choice', 'score', 'multi'].includes(n.kind)) {
        if (!Array.isArray(p[n.kind]) || p[n.kind].filter((x) => String(x).trim()).length < 2) add(n.id, `${KINDS[n.kind].name.toLowerCase()} precisa de pelo menos duas opções`);
      } else if (['check', 'select', 'query'].includes(n.kind)) {
        if (!String(p[n.kind] || '').trim()) add(n.id, {check: 'escreva a afirmação a verificar', select: 'escreva o seletor (ex.: TOTAL TYPE money)', query: 'escreva a consulta'}[n.kind]);
      } else if (n.kind === 'if') {
        const c = p.if || {};
        if (!COND.some(([k]) => (Array.isArray(c[k]) ? c[k].length : c[k] || c[k] === 0)) && !['all', 'any', 'not', 'count_min', 'hora_entre', 'dias_semana'].some((k) => c[k])) add(n.id, 'a condição está vazia: diga o que procurar no texto, remetente, app ou origem');
        else if (c.regex) { try { new RegExp(c.regex); } catch (e) { add(n.id, 'expressão regular inválida'); } }
      } else if (n.kind === 'action') {
        const a = p.action;
        if (!ACTIONS[a]) add(n.id, `ação desconhecida “${a}”`);
        else if (a === 'webhook' && !String(p.url || '').trim()) add(n.id, 'o webhook precisa de uma URL');
        else if (a === 'command' && !(p.argv || []).length) add(n.id, 'o comando precisa do programa e dos argumentos');
        else if (a === 'write_file' && !String(p.file || '').trim()) add(n.id, 'diga em qual arquivo salvar');
        else if (a === 'email_move' && !String(p.folder || '').trim()) add(n.id, 'diga para qual pasta mover o e-mail');
        else if (a === 'etiquetar' && !String(p.etiqueta || '').trim()) add(n.id, 'diga qual etiqueta pôr');
      } else add(n.id, `tipo de nó desconhecido “${n.kind}”`);
      if (p.min_probability != null && !(typeof p.min_probability === 'number' && p.min_probability >= 0 && p.min_probability <= 1)) add(n.id, 'a confiança mínima deve estar entre 0 e 1');
    }
    for (const e of g.edges) {
      if (!ids.has(e.to)) add(e.from, `a resposta “${portName(e.label)}” aponta para um nó que não existe (“${e.to}”)`);
      const f = g.nodes.find((n) => n.id === e.from);
      if (f && f.kind === 'action' && f.props.action === 'stop') add(f.id, '“parar” encerra a árvore: não pode ter próximo passo');
    }
    if (g.nodes.length && !ids.has(g.start)) add(null, 'escolha o nó de início');
    else if (g.nodes.length) {
      const reach = new Set([g.start]), q = [g.start];
      while (q.length) { const c = q.pop(); g.edges.forEach((e) => { if (e.from === c && ids.has(e.to) && !reach.has(e.to)) { reach.add(e.to); q.push(e.to); } }); }
      g.nodes.forEach((n) => { if (!reach.has(n.id)) add(n.id, 'este nó nunca é alcançado a partir do início', 'aviso'); });
    }
    return errs;
  }

  function normalize(g) {
    g = clone(g || {});
    g.nodes = g.nodes || []; g.edges = g.edges || []; g.when = g.when || {}; g.extra = g.extra || {};
    if (g.enabled == null) g.enabled = true;
    if (!g.name) g.name = 'nova-arvore';
    g.nodes.forEach((n) => { n.props = n.props || {}; n.ports = n.ports || []; n.list_ports = n.list_ports || []; n.x = +n.x || 0; n.y = +n.y || 0; });
    if (!g.start && g.nodes.length) g.start = g.nodes[0].id;
    return g;
  }

  const defaultApi = {
    test: (text, graph) => post('/api/event', {sync: true, text, source: 'teste', title: '', tree: graph.name, graph}),
    validate: (graph) => post('/api/trees/graph/validate', {graph}),
    fromTree: (tree) => post('/api/trees/graph/from_tree', {tree}).then((r) => r.graph || r),
    toTree: (graph) => post('/api/trees/graph/to_tree', {graph})
  };
  function post(url, body) {
    return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
      .then((r) => r.json().then((j) => { if (!r.ok && !j.errors) throw new Error(j.error || ('HTTP ' + r.status)); return j; }));
  }

  let cssDone = false;
  function injectCss() {
    if (cssDone || document.getElementById('te-css')) return;
    document.head.append(h('style', {id: 'te-css', text: CSS})); cssDone = true;
  }

  // ---------------------------------------------------------------- editor
  class Editor {
    constructor(el, opts) {
      injectCss();
      this.el = el; this.opts = opts || {};
      this.api = Object.assign({}, defaultApi, this.opts.api || {});
      this.ro = !!this.opts.readOnly;
      this.g = normalize(this.opts.graph);
      this.view = {x: 40, y: 40, k: 1};
      this.sel = null; this.undoS = []; this.redoS = []; this.errors = []; this.serverErrors = null;
      this.ptrs = new Map(); this.lastField = null;
      this.build();
      if (!this.g.nodes.some((n) => n.x || n.y) && this.g.nodes.length > 1) layoutGraph(this.g);
      this.render(); this.renderProps(); this.revalidate();
      requestAnimationFrame(() => this.fit());
    }

    // ---- DOM
    build() {
      this.el.innerHTML = '';
      const r = this.root = h('div', {class: 'te-root' + (this.ro ? ' te-ro' : ''), tabindex: '0', role: 'application', 'aria-label': 'Editor de árvore de decisão'});
      this.nameEl = h('div', {class: 'te-name'});
      this.btn = {};
      const B = (key, text, title, fn, cls) => (this.btn[key] = h('button', {class: 'te-btn ' + (cls || ''), type: 'button', title, 'aria-label': title, onclick: fn}, text));
      this.testIn = h('input', {class: 'te-in-text', type: 'text', placeholder: 'Cole um exemplo para ver o caminho…', 'aria-label': 'Texto para testar a árvore',
        onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); this.test(this.testIn.value); } }});
      const bar = h('div', {class: 'te-bar', role: 'toolbar', 'aria-label': 'Ferramentas'}, this.nameEl,
        this.ro ? null : [B('undo', [ico('undo'), ' Desfazer'], 'Desfazer (Ctrl+Z)', () => this.undo()), B('redo', [ico('redo'), ' Refazer'], 'Refazer (Ctrl+Y)', () => this.redo()),
          B('dup', 'Duplicar', 'Duplicar nó (Ctrl+D)', () => this.duplicate()), B('del', 'Apagar', 'Apagar seleção (Del)', () => this.deleteSel()),
          B('layout', 'Organizar', 'Organizar os nós automaticamente', () => this.autoLayout())],
        B('fit', 'Ver tudo', 'Mostrar a árvore inteira', () => this.fit()), h('span', {class: 'te-sp'}),
        h('div', {class: 'te-test'}, this.testIn, B('test', 'Testar', 'Testar com um texto', () => this.test(this.testIn.value), 'te-pri')));
      // paleta
      const pal = h('div', {class: 'te-pal', role: 'list', 'aria-label': 'Tipos de nó'}, h('h4', {text: 'Se… (perguntas)'}));
      pal.append(h('p', {class: 'te-help', text: 'Clique ou arraste para o quadro. Depois puxe a bolinha de cada resposta até a próxima caixa.'}));
      PALETTE.forEach((key, i) => {
        if (i === 6) pal.append(h('h4', {text: 'Então… (ações)'}));
        const [kind, action] = key.split(':');
        const info = action ? ACTIONS[action] : KINDS[kind];
        const b = h('button', {type: 'button', role: 'listitem', draggable: 'true', title: (action ? 'Ação: ' : '') + (info.hint || info.name) + ' — clique ou arraste',
          'data-key': key, onclick: () => this.addNode(kind, action),
          ondragstart: (e) => { e.dataTransfer.setData('text/x-te-node', key); e.dataTransfer.effectAllowed = 'copy'; }},
        h('span', {class: 'te-ic', 'aria-hidden': 'true'}, ico(info.icon)), info.name);
        pal.append(b);
      });
      // palco
      this.stage = h('div', {class: 'te-stage', 'aria-label': 'Área da árvore. Use Tab para percorrer os nós e as setas para movê-los.'});
      this.world = h('div', {class: 'te-world'});
      this.svg = s('svg', {class: 'te-svg', width: '1', height: '1'});
      this.edgeG = s('g', {}); this.tmp = s('path', {class: 'te-tmp', d: ''});
      this.svg.append(this.edgeG, this.tmp);
      this.nodesEl = h('div', {});
      this.world.append(this.svg, this.nodesEl);
      this.mini = s('svg', {class: 'te-mini', viewBox: '0 0 180 120', role: 'img', 'aria-label': 'Minimapa'});
      const zoom = h('div', {class: 'te-zoom'},
        h('button', {type: 'button', title: 'Afastar', 'aria-label': 'Afastar', onclick: () => this.zoomBy(1 / 1.2)}, '−'),
        h('button', {type: 'button', title: 'Tamanho real', 'aria-label': 'Zoom 100%', onclick: () => this.zoomTo(1)}, '1:1'),
        h('button', {type: 'button', title: 'Aproximar', 'aria-label': 'Aproximar', onclick: () => this.zoomBy(1.2)}, '+'));
      this.stage.append(this.world, this.mini, zoom);
      this.props = h('div', {class: 'te-props', 'aria-label': 'Propriedades'});
      this.foot = h('div', {class: 'te-foot', 'aria-live': 'polite'});
      r.append(bar, pal, this.stage, this.props, this.foot);
      this.el.append(r);
      this.bind();
    }

    bind() {
      const st = this.stage;
      this._kd = (e) => this.onKey(e);
      this.root.addEventListener('keydown', this._kd);
      st.addEventListener('wheel', (e) => {
        e.preventDefault();
        const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
        const r = st.getBoundingClientRect();
        this.zoomAt(f, e.clientX - r.left, e.clientY - r.top);
      }, {passive: false});
      st.addEventListener('pointerdown', (e) => this.onDown(e));
      st.addEventListener('pointermove', (e) => this.onMove(e));
      st.addEventListener('pointerup', (e) => this.onUp(e));
      st.addEventListener('pointercancel', (e) => this.onUp(e));
      st.addEventListener('dragover', (e) => { if (!this.ro) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
      st.addEventListener('drop', (e) => {
        const key = e.dataTransfer.getData('text/x-te-node'); if (!key || this.ro) return;
        e.preventDefault();
        const [kind, action] = key.split(':'); const p = this.toWorld(e.clientX, e.clientY);
        this.addNode(kind, action, p.x - W / 2, p.y - H / 2);
      });
      this.mini.addEventListener('pointerdown', (e) => { e.stopPropagation(); this.miniGo(e); this.miniDrag = true; this.mini.setPointerCapture(e.pointerId); });
      this.mini.addEventListener('pointermove', (e) => { if (this.miniDrag) this.miniGo(e); });
      this.mini.addEventListener('pointerup', () => (this.miniDrag = false));
      this.props.addEventListener('focusin', (e) => {
        if (e.target.matches('input,textarea,select')) { this.pending = JSON.stringify(this.g); if (e.target.matches('input[type=text],textarea')) this.lastField = e.target; }
      });
      this.props.addEventListener('change', () => this.flushEdit());
    }

    toWorld(cx, cy) { const r = this.stage.getBoundingClientRect(); return {x: (cx - r.left - this.view.x) / this.view.k, y: (cy - r.top - this.view.y) / this.view.k}; }
    applyView() { this.world.style.transform = `translate(${this.view.x}px,${this.view.y}px) scale(${this.view.k})`; this.renderMini(); }
    zoomAt(f, sx, sy) {
      const k = Math.min(2.5, Math.max(0.2, this.view.k * f)); f = k / this.view.k;
      this.view.x = sx - (sx - this.view.x) * f; this.view.y = sy - (sy - this.view.y) * f; this.view.k = k; this.applyView();
    }
    zoomBy(f) { const r = this.stage.getBoundingClientRect(); this.zoomAt(f, r.width / 2, r.height / 2); }
    zoomTo(k) { this.zoomBy(k / this.view.k); }
    bbox() {
      if (!this.g.nodes.length) return {x: 0, y: 0, w: W, h: H};
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      this.g.nodes.forEach((n) => { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x + W); y1 = Math.max(y1, n.y + nodeH(this.g, n)); });
      return {x: x0, y: y0, w: x1 - x0, h: y1 - y0};
    }
    fit() {
      const r = this.stage.getBoundingClientRect(); if (!r.width) return;
      const b = this.bbox(), pad = 60;
      const k = Math.min(1.1, Math.max(0.2, Math.min((r.width - pad * 2) / b.w, (r.height - pad * 2) / b.h)));
      this.view = {k, x: (r.width - b.w * k) / 2 - b.x * k, y: (r.height - b.h * k) / 2 - b.y * k}; this.applyView();
    }

    // ---- desenho
    render() {
      this.nameEl.textContent = this.g.name || 'Árvore';
      const errBy = {};
      this.allErrors().forEach((e) => { if (e.node) (errBy[e.node] = errBy[e.node] || []).push(e); });
      this.nodesEl.innerHTML = '';
      this.nodeEls = {};
      this.g.nodes.forEach((n) => {
        const ports = portsOf(this.g, n), ne = errBy[n.id] || [];
        const lvl = ne.some((e) => e.level === 'erro') ? 'te-err' : ne.length ? 'te-warn' : '';
        const sel = this.sel && this.sel.type === 'node' && this.sel.id === n.id;
        const el = h('div', {class: `te-node ${lvl} ${sel ? 'te-sel' : ''}`, 'data-id': n.id, tabindex: '0', role: 'group',
          'aria-label': `${kindOf(n)}: ${labelOf(n)}${n.id === this.g.start ? ', início' : ''}${ne.length ? ', ' + ne.map((e) => e.message).join('; ') : ''}`,
          style: `left:${n.x}px;top:${n.y}px;height:${nodeH(this.g, n)}px;--te-kc:${this.kindColor(n)}`,
          onfocus: () => { if (!this.sel || this.sel.id !== n.id) this.select(n.id, true); }},
        n.id === this.g.start ? h('span', {class: 'te-badge', text: 'início'}) : null,
        ne.length ? h('span', {class: 'te-ebadge', title: ne.map((e) => e.message).join('\n'), text: '!'}) : null,
        h('i', {class: 'te-in', 'aria-hidden': 'true'}),
        h('div', {class: 'te-head'}, h('div', {class: 'te-kind'}, h('b', {text: (n.kind === 'action' ? 'Então · ' : 'Se · ') + kindOf(n)})), h('div', {class: 'te-title', title: phraseOf(n), text: phraseOf(n)})),
        ports.map((p) => h('div', {class: 'te-port' + (this.g.edges.some((e) => e.from === n.id && e.label === p) ? ' te-on' : ''), 'data-port': p},
          h('span', {title: p, text: portName(p)}), h('i', {class: 'te-dot', 'data-port': p, title: this.ro ? '' : `Arraste para ligar “${portName(p)}” a outro nó`}))));
        this.nodeEls[n.id] = el; this.nodesEl.append(el);
      });
      this.renderEdges(); this.applyView(); this.updateButtons();
    }
    kindColor(n) {
      return {choice: '#b56576', check: '#6d8a6a', score: '#c08a3e', multi: '#8a6fa8', if: '#5f7f99', select: '#a0745a', query: '#7a7268'}[n.kind] ||
        (n.props.action === 'stop' ? '#968d81' : '#93485b');
    }
    portPos(n, label) {
      const i = portsOf(this.g, n).indexOf(String(label));
      return {x: n.x + W, y: n.y + H + PH * Math.max(0, i) + PH / 2};
    }
    curve(a, b) { const dx = Math.max(40, Math.abs(b.x - a.x) / 2); return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`; }
    renderEdges() {
      this.edgeG.innerHTML = ''; this.edgeEls = [];
      const by = {}; this.g.nodes.forEach((n) => (by[n.id] = n));
      this.g.edges.forEach((e, i) => {
        const a = by[e.from], b = by[e.to]; if (!a || !b) { this.edgeEls.push(null); return; }
        const d = this.curve(this.portPos(a, e.label), {x: b.x, y: b.y + H / 2});
        const sel = this.sel && this.sel.type === 'edge' && this.sel.i === i;
        const p = s('path', {class: 'te-edge' + (sel ? ' te-sel' : ''), d});
        const hit = s('path', {class: 'te-hitp', d, 'data-edge': i});
        const t = s('title', {}); t.textContent = `${e.from} —${portName(e.label)}→ ${e.to}`; hit.append(t);
        this.edgeG.append(p, hit); this.edgeEls.push(p);
      });
      // tamanho do svg para conter tudo (pointer-events nos caminhos)
      const b = this.bbox(); this.svg.setAttribute('width', Math.max(1, b.x + b.w + 400)); this.svg.setAttribute('height', Math.max(1, b.y + b.h + 400));
      this.renderMini();
    }
    renderMini() {
      if (!this.mini) return;
      const b = this.bbox(), r = this.stage.getBoundingClientRect();
      const vx = -this.view.x / this.view.k, vy = -this.view.y / this.view.k, vw = r.width / this.view.k, vh = r.height / this.view.k;
      const x0 = Math.min(b.x, vx), y0 = Math.min(b.y, vy), x1 = Math.max(b.x + b.w, vx + vw), y1 = Math.max(b.y + b.h, vy + vh);
      const k = Math.min(170 / (x1 - x0 || 1), 110 / (y1 - y0 || 1));
      this.miniT = {x0, y0, k};
      this.mini.innerHTML = '';
      this.g.nodes.forEach((n) => this.mini.append(s('rect', {class: 'n' + (this.sel && this.sel.id === n.id ? ' s' : ''), x: 5 + (n.x - x0) * k, y: 5 + (n.y - y0) * k, width: W * k, height: nodeH(this.g, n) * k, rx: 2})));
      this.mini.append(s('rect', {class: 'v', x: 5 + (vx - x0) * k, y: 5 + (vy - y0) * k, width: vw * k, height: vh * k, rx: 3}));
    }
    miniGo(e) {
      const r = this.mini.getBoundingClientRect(), t = this.miniT, sr = this.stage.getBoundingClientRect();
      const wx = t.x0 + (e.clientX - r.left - 5) / t.k, wy = t.y0 + (e.clientY - r.top - 5) / t.k;
      this.view.x = sr.width / 2 - wx * this.view.k; this.view.y = sr.height / 2 - wy * this.view.k; this.applyView();
    }

    // ---- ponteiro: arrastar nó, ligar porta, pan, pinça
    onDown(e) {
      if (e.target.closest('.te-mini,.te-zoom')) return;
      this.ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
      this.stage.setPointerCapture(e.pointerId);
      if (this.ptrs.size === 2) { const [a, b] = [...this.ptrs.values()]; this.pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), k: this.view.k}; this.drag = null; return; }
      const dot = e.target.closest('.te-dot'), nodeEl = e.target.closest('.te-node'), hit = e.target.closest('.te-hitp');
      if (dot && !this.ro) {
        const id = nodeEl.dataset.id; this.drag = {type: 'link', from: id, label: dot.dataset.port}; e.preventDefault(); return;
      }
      if (hit) { this.selectEdge(+hit.dataset.edge); this.drag = null; return; }
      if (nodeEl) {
        const n = this.node(nodeEl.dataset.id); this.select(n.id);
        if (!this.ro) this.drag = {type: 'node', id: n.id, sx: e.clientX, sy: e.clientY, x: n.x, y: n.y, before: JSON.stringify(this.g), moved: false};
        return;
      }
      this.select(null);
      this.drag = {type: 'pan', sx: e.clientX, sy: e.clientY, x: this.view.x, y: this.view.y};
      this.stage.classList.add('te-panning');
    }
    onMove(e) {
      if (!this.ptrs.has(e.pointerId)) return;
      this.ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
      if (this.pinch && this.ptrs.size === 2) {
        const [a, b] = [...this.ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y), r = this.stage.getBoundingClientRect();
        this.zoomAt(this.pinch.k * d / this.pinch.d / this.view.k, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top); return;
      }
      const d = this.drag; if (!d) return;
      if (d.type === 'pan') { this.view.x = d.x + e.clientX - d.sx; this.view.y = d.y + e.clientY - d.sy; this.applyView(); }
      else if (d.type === 'node') {
        const n = this.node(d.id); n.x = Math.round(d.x + (e.clientX - d.sx) / this.view.k); n.y = Math.round(d.y + (e.clientY - d.sy) / this.view.k);
        d.moved = d.moved || Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) > 3;
        const el = this.nodeEls[n.id]; el.style.left = n.x + 'px'; el.style.top = n.y + 'px'; this.renderEdges();
      } else if (d.type === 'link') {
        const p = this.toWorld(e.clientX, e.clientY);
        this.tmp.setAttribute('d', this.curve(this.portPos(this.node(d.from), d.label), p));
        const over = this.nodeAt(e.clientX, e.clientY);
        Object.values(this.nodeEls).forEach((el) => el.classList.toggle('te-drop', !!over && el.dataset.id === over && over !== d.from));
      }
    }
    onUp(e) {
      this.ptrs.delete(e.pointerId);
      if (this.ptrs.size < 2) this.pinch = null;
      const d = this.drag; this.drag = null; this.stage.classList.remove('te-panning');
      if (!d) return;
      if (d.type === 'node' && d.moved) { this.undoS.push(d.before); this.redoS = []; this.emit(); this.renderMini(); }
      if (d.type === 'link') {
        this.tmp.setAttribute('d', ''); Object.values(this.nodeEls).forEach((el) => el.classList.remove('te-drop'));
        const to = this.nodeAt(e.clientX, e.clientY);
        if (to && to !== d.from) this.connect(d.from, d.label, to);
      }
    }
    nodeAt(cx, cy) {
      const el = document.elementsFromPoint(cx, cy).map((x) => x.closest && x.closest('.te-node')).find((x) => x && this.root.contains(x));
      return el ? el.dataset.id : null;
    }

    // ---- teclado
    onKey(e) {
      const inField = e.target.matches('input,textarea,select,[contenteditable]');
      const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
      if (mod && k === 'z' && !e.shiftKey && !inField) { e.preventDefault(); this.undo(); return; }
      if (mod && (k === 'y' || (k === 'z' && e.shiftKey)) && !inField) { e.preventDefault(); this.redo(); return; }
      if (inField) { if (e.key === 'Escape') e.target.blur(); return; }
      if (mod && k === 'd') { e.preventDefault(); this.duplicate(); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { if (this.sel) { e.preventDefault(); this.deleteSel(); } return; }
      if (e.key === 'Escape') { this.select(null); this.root.focus(); return; }
      if (e.key === '+' || e.key === '=') { this.zoomBy(1.2); return; }
      if (e.key === '-') { this.zoomBy(1 / 1.2); return; }
      if (e.key === 'Enter' && this.sel && this.sel.type === 'node') { e.preventDefault(); const f = this.props.querySelector('input,textarea,select'); if (f) f.focus(); return; }
      const arrows = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
      if (arrows[e.key] && this.sel && this.sel.type === 'node' && !this.ro) {
        e.preventDefault(); const n = this.node(this.sel.id), st = e.shiftKey ? 50 : 10;
        this.commit(() => { n.x += arrows[e.key][0] * st; n.y += arrows[e.key][1] * st; });
        this.nodeEls[n.id] && this.nodeEls[n.id].focus();
      }
    }

    // ---- modelo
    node(id) { return this.g.nodes.find((n) => n.id === id); }
    uid(base) { let id = slug(base), i = 2; const b = id; while (this.node(id)) id = `${b}_${i++}`; return id; }
    snapshot() { this.undoS.push(JSON.stringify(this.g)); if (this.undoS.length > 150) this.undoS.shift(); this.redoS = []; }
    commit(fn, keepProps) {
      if (this.ro) return;
      this.snapshot(); fn(); this.refresh(keepProps);
    }
    refresh(keepProps) {
      this.g.nodes.forEach((n) => (n.label = labelOf(n)));
      this.revalidate(); this.render(); if (!keepProps) this.renderProps(); this.emit();
    }
    flushEdit() {
      if (this.pending && this.pending !== JSON.stringify(this.g)) { this.undoS.push(this.pending); this.redoS = []; }
      this.pending = JSON.stringify(this.g); this.updateButtons();
    }
    liveEdit() {                         // durante a digitação: sem refazer o painel
      this.g.nodes.forEach((n) => (n.label = labelOf(n)));
      this.revalidate(); this.render(); this.emit();
    }
    emit() { if (this.opts.onChange) { try { this.opts.onChange(this.getGraph()); } catch (err) { console.error(err); } } }
    undo() { if (!this.undoS.length) return; this.redoS.push(JSON.stringify(this.g)); this.g = JSON.parse(this.undoS.pop()); this.fixSel(); this.refresh(); }
    redo() { if (!this.redoS.length) return; this.undoS.push(JSON.stringify(this.g)); this.g = JSON.parse(this.redoS.pop()); this.fixSel(); this.refresh(); }
    fixSel() { if (this.sel && (this.sel.type === 'node' ? !this.node(this.sel.id) : !this.g.edges[this.sel.i])) this.sel = null; }
    updateButtons() {
      const b = this.btn; if (!b.undo) return;
      b.undo.disabled = !this.undoS.length; b.redo.disabled = !this.redoS.length;
      b.dup.disabled = !(this.sel && this.sel.type === 'node'); b.del.disabled = !this.sel;
    }

    addNode(kind, action, x, y) {
      if (this.ro) return null;
      const props = action ? ACTIONS[action].make() : KINDS[kind].make();
      const base = action ? {notify: 'aviso', log: 'registro', write_file: 'arquivo', webhook: 'webhook', command: 'comando', stop: 'fim', email_move: 'mover'}[action]
        : {choice: 'pergunta', check: 'verifica', score: 'escala', multi: 'multipla', if: 'se', select: 'quantidade', query: 'consulta'}[kind];
      const id = this.uid(base);
      if (x == null) {
        const r = this.stage.getBoundingClientRect(), c = this.toWorld(r.left + r.width / 2, r.top + r.height / 2);
        x = c.x - W / 2; y = c.y - H; let tries = 0;
        while (tries++ < 30 && this.g.nodes.some((n) => Math.abs(n.x - x) < W && Math.abs(n.y - y) < 60)) y += 70;
      }
      const n = {id, kind, label: '', props, ports: [], list_ports: [], x: Math.round(x), y: Math.round(y)};
      this.commit(() => { this.g.nodes.push(n); if (!this.node(this.g.start)) this.g.start = id; this.sel = {type: 'node', id}; });
      this.announce(`${kindOf(n)} adicionado`);
      const el = this.nodeEls[id]; if (el) el.focus({preventScroll: true});
      return id;
    }
    connect(from, label, to) {
      if (this.ro) return;
      const f = this.node(from); if (!f || !this.node(to)) return;
      if (f.kind === 'action' && f.props.action === 'stop') { this.announce('“Parar” encerra a árvore: não pode ter próximo passo.'); return; }
      if (this.g.edges.some((e) => e.from === from && e.to === to && e.label === label)) return;
      this.commit(() => {
        if (!f.ports.includes(label)) f.ports.push(label);
        this.g.edges.push({from, to, label});
        if (f.kind === 'check' || f.kind === 'if') { const last = this.g.edges[this.g.edges.length - 1]; if (label === 'true' || label === 'false') last.key = label === 'true'; }
      }, this.sel && this.sel.id === from ? false : true);
      this.announce(`Ligado: ${from} —${portName(label)}→ ${to}`);
    }
    disconnect(i) { this.commit(() => { this.g.edges.splice(i, 1); this.sel = null; }); }
    deleteSel() {
      if (!this.sel || this.ro) return;
      if (this.sel.type === 'edge') return this.disconnect(this.sel.i);
      const id = this.sel.id;
      this.commit(() => {
        this.g.nodes = this.g.nodes.filter((n) => n.id !== id);
        this.g.edges = this.g.edges.filter((e) => e.from !== id && e.to !== id);
        if (this.g.start === id) this.g.start = this.g.nodes[0] ? this.g.nodes[0].id : null;
        this.sel = null;
      });
      this.announce(`Nó ${id} apagado`); this.root.focus();
    }
    duplicate() {
      if (!this.sel || this.sel.type !== 'node' || this.ro) return;
      const n = this.node(this.sel.id), id = this.uid(n.id + '_copia');
      this.commit(() => {
        const c = clone(n); c.id = id; c.x += 40; c.y += 40; this.g.nodes.push(c);
        this.g.edges.filter((e) => e.from === n.id).forEach((e) => this.g.edges.push(Object.assign(clone(e), {from: id})));
        this.sel = {type: 'node', id};
      });
      this.nodeEls[id] && this.nodeEls[id].focus({preventScroll: true});
    }
    renameNode(old, nu) {
      nu = slug(nu);
      if (!nu || nu === old) return false;
      if (this.node(nu)) { this.announce(`Já existe um nó “${nu}”.`); return false; }
      this.commit(() => {
        this.node(old).id = nu;
        this.g.edges.forEach((e) => { if (e.from === old) e.from = nu; if (e.to === old) e.to = nu; });
        if (this.g.start === old) this.g.start = nu;
        this.sel = {type: 'node', id: nu};
      });
      return true;
    }
    autoLayout() { this.commit(() => layoutGraph(this.g), true); this.fit(); }
    select(id, fromFocus) {
      this.sel = id ? {type: 'node', id} : null;
      Object.values(this.nodeEls || {}).forEach((el) => el.classList.toggle('te-sel', el.dataset.id === id));
      this.renderEdges(); this.renderProps(); this.updateButtons();
      if (id && !fromFocus && this.nodeEls[id] && document.activeElement !== this.nodeEls[id]) this.nodeEls[id].focus({preventScroll: true});
    }
    selectEdge(i) { this.sel = {type: 'edge', i}; Object.values(this.nodeEls).forEach((el) => el.classList.remove('te-sel')); this.renderEdges(); this.renderProps(); this.updateButtons(); }

    // ---- validação
    allErrors() { return this.errors.concat(this.serverErrors || []); }
    revalidate() {
      this.errors = validateGraph(this.g); this.serverErrors = null;
      clearTimeout(this._vt);
      if (this.api.validate && !this.errors.some((e) => e.level === 'erro')) {
        const snap = JSON.stringify(this.g);
        this._vt = setTimeout(() => {
          Promise.resolve(this.api.validate(this.getGraph())).then((r) => {
            if (!r || snap !== JSON.stringify(this.g)) return;
            const extra = (r.errors || []).filter((e) => !this.errors.some((x) => x.node === e.node && x.message === e.message));
            if (extra.length) { this.serverErrors = extra.map((e) => Object.assign({level: 'erro'}, e)); this.render(); this.renderFoot(); }
          }).catch(() => {});
        }, 400);
      }
      this.renderFoot();
      return this.errors;
    }
    validate() { return this.revalidate().concat([]); }
    renderFoot(trailEl) {
      const f = this.foot; f.innerHTML = '';
      if (trailEl) f.append(trailEl);
      const errs = this.allErrors();
      if (!errs.length) f.append(h('span', {class: 'te-ok'}, ico('check'), ' Árvore válida'), h('span', {text: `${this.g.nodes.length} nós · ${this.g.edges.length} ligações`}));
      else errs.forEach((e) => f.append(h('button', {class: 'te-e' + (e.level === 'erro' ? '' : ' te-w'), type: 'button',
        onclick: () => e.node && this.node(e.node) && (this.select(e.node), this.centerOn(e.node))},
        ico(e.level === 'erro' ? 'x' : 'warn'), ` ${e.node ? e.node + ': ' : ''}${e.message}`)));
    }
    centerOn(id) {
      const n = this.node(id), r = this.stage.getBoundingClientRect(); if (!n) return;
      this.view.x = r.width / 2 - (n.x + W / 2) * this.view.k; this.view.y = r.height / 2 - (n.y + nodeH(this.g, n) / 2) * this.view.k; this.applyView();
    }
    announce(t) { if (!this.sr) { this.sr = h('div', {class: 'te-sr', 'aria-live': 'assertive'}); this.root.append(this.sr); } this.sr.textContent = t; }

    // ---- teste com texto
    test(text) {
      text = String(text || '').trim();
      if (!text) { this.testIn.focus(); this.announce('Escreva um texto para testar.'); return Promise.resolve(null); }
      if (!this.api.test) return Promise.resolve(null);
      this.btn.test.disabled = true; this.btn.test.textContent = 'Testando…';
      return Promise.resolve(this.api.test(text, this.getGraph())).then((res) => {
        const run = res && res.runs ? (res.runs.find((r) => r.tree === this.g.name) || res.runs[0]) : res;
        if (!run) { this.renderFoot(h('span', {text: 'A árvore não rodou para este texto (o “quando” não casou?).'})); return res; }
        if (run.error) { this.renderFoot(h('span', {class: 'te-msg', text: run.error})); return res; }
        this.highlight(run.trail || []); return res;
      }).catch((err) => { this.renderFoot(h('span', {class: 'te-msg', text: 'Não consegui testar: ' + (err.message || err)})); })
        .finally(() => { this.btn.test.disabled = false; this.btn.test.textContent = 'Testar'; });
    }
    highlight(trail) {
      clearTimeout(this._ht);
      Object.values(this.nodeEls).forEach((el) => el.classList.remove('te-hit', 'te-hit-done'));
      (this.edgeEls || []).forEach((p) => p && p.classList.remove('te-flow'));
      const steps = h('div', {class: 'te-trail'}, h('b', {text: 'Caminho:'}));
      trail.forEach((t, i) => {
        const ans = t.error ? 'erro' : t.abstain ? 'incerto' : t.selected ? t.selected.join(', ') : t.status != null ? String(t.status) : t.label != null ? t.label : t.answer != null ? String(t.answer) : t.value != null ? String(t.value) : '';
        const pr = typeof t.probability === 'number' ? ` · ${Math.round(t.probability * 100)}%` : '';
        steps.append(i ? ' → ' : '', h('span', {class: 'te-step', title: t.error || '', text: `${t.node}${ans ? ': ' + ans : ''}${pr}`}));
      });
      this.renderFoot(steps);
      let i = 0;
      const step = () => {
        if (i >= trail.length) return;
        const t = trail[i++], el = this.nodeEls[t.node];
        if (el) { el.classList.add('te-hit', 'te-hit-done'); }
        (t.next || []).forEach((to) => this.g.edges.forEach((e, k) => { if (e.from === t.node && e.to === to && this.edgeEls[k]) this.edgeEls[k].classList.add('te-flow'); }));
        this._ht = setTimeout(step, 420);
      };
      step();
      this.lastTrail = trail;
    }

    // ---- painel de propriedades
    renderProps() {
      const P = this.props; P.innerHTML = '';
      if (this.sel && this.sel.type === 'edge') {
        const e = this.g.edges[this.sel.i]; if (!e) return;
        P.append(h('h3', {text: 'Ligação'}), h('div', {class: 'te-sub', text: `${e.from} —${portName(e.label)}→ ${e.to}`}));
        if (!this.ro) P.append(h('button', {class: 'te-btn', type: 'button', onclick: () => this.disconnect(this.sel.i), text: 'Remover ligação (Del)'}));
        return;
      }
      const n = this.sel && this.node(this.sel.id);
      if (!n) return this.renderTreeProps();
      P.append(h('h4', {text: kindOf(n)}), h('h3', {text: labelOf(n)}));
      const dis = this.ro;
      // id e início
      const idIn = h('input', {type: 'text', class: 'te-mono', value: n.id, disabled: dis, 'aria-label': 'Identificador do nó'});
      idIn.addEventListener('change', (ev) => { ev.stopPropagation(); if (!this.renameNode(n.id, idIn.value)) idIn.value = n.id; });
      P.append(this.field('Nome interno (não aparece para ninguém)', idIn));
      if (n.id !== this.g.start && !dis) P.append(h('div', {class: 'te-f'}, h('button', {class: 'te-btn', type: 'button', onclick: () => this.commit(() => (this.g.start = n.id)), text: 'Começar a árvore por aqui'})));
      const p = n.props;
      const set = (k, v) => { if (v === '' || v == null || (Array.isArray(v) && !v.length && k !== n.kind)) delete p[k]; else p[k] = v; };
      if (['choice', 'score', 'multi'].includes(n.kind)) {
        P.append(this.field('Pergunta', this.text(p.ask || '', (v) => { set('ask', v); this.liveEdit(); }, 'Ex.: Do que se trata?')));
        P.append(this.field(n.kind === 'score' ? 'Escala (da menor para a maior)' : 'Opções', this.chips(p[n.kind] || [], (arr) => this.commit(() => (p[n.kind] = arr)), 'nova opção + Enter')));
      }
      if (n.kind === 'check') P.append(this.field('Afirmação a verificar', this.text(p.check || '', (v) => { p.check = v; this.liveEdit(); }, 'Ex.: o prazo já venceu', true)));
      if (n.kind === 'select') P.append(this.field('Seletor', this.text(p.select || '', (v) => { p.select = v; this.liveEdit(); }, 'TOTAL TYPE money', false, true)));
      if (n.kind === 'query') P.append(this.field('Consulta DJORGE', this.text(p.query || '', (v) => { p.query = v; this.liveEdit(); }, '', true, true)));
      if (n.kind === 'if') P.append(...this.condFields(p.if = p.if || {}));
      if (n.kind !== 'action' && n.kind !== 'if') {
        const sel = h('select', {disabled: dis, 'aria-label': 'Modelo'}, MODELS.map(([v, t]) => h('option', {value: v, selected: (p.model || '') === v, text: t})));
        sel.addEventListener('input', () => { set('model', sel.value); this.liveEdit(); });
        P.append(this.field('Modelo', sel));
        P.append(this.range(p));
      }
      if (n.kind === 'action') P.append(...this.actionFields(n));
      // portas: ramos personalizados e ligação pelo teclado
      if (!(n.kind === 'action' && p.action === 'stop')) P.append(this.linksField(n));
      // avançado
      const ta = h('textarea', {class: 'te-mono', disabled: dis, 'aria-label': 'Propriedades em JSON'}); ta.value = JSON.stringify(p, null, 2);
      const msg = h('div', {class: 'te-msg'});
      ta.addEventListener('change', (ev) => {
        ev.stopPropagation();
        try { const v = JSON.parse(ta.value); if (typeof v !== 'object' || Array.isArray(v) || !v) throw new Error(); this.commit(() => (n.props = v)); }
        catch (err) { msg.textContent = 'JSON inválido: confira vírgulas e aspas.'; }
      });
      P.append(h('details', {class: 'te-f'}, h('summary', {class: 'te-l', text: 'Avançado (JSON)'}), ta, msg));
      if (!dis) P.append(h('div', {class: 'te-row'}, h('button', {class: 'te-btn', type: 'button', onclick: () => this.duplicate(), text: 'Duplicar'}),
        h('button', {class: 'te-btn', type: 'button', onclick: () => this.deleteSel(), text: 'Apagar nó'})));
    }
    renderTreeProps() {
      const P = this.props, g = this.g, dis = this.ro;
      P.append(h('h4', {text: 'Árvore'}), h('h3', {text: g.name || 'Árvore'}));
      P.append(this.field('Nome', this.text(g.name || '', (v) => { g.name = v; this.liveEdit(); }, 'nome-da-arvore')));
      const en = h('input', {type: 'checkbox', checked: g.enabled !== false, disabled: dis, id: 'te-en-' + this.uidc()});
      en.addEventListener('change', () => { g.enabled = en.checked; this.emit(); });
      P.append(h('div', {class: 'te-f te-row'}, en, h('label', {for: en.id, text: 'Ligada'})));
      const st = h('select', {disabled: dis, 'aria-label': 'Nó de início'}, g.nodes.map((n) => h('option', {value: n.id, selected: n.id === g.start, text: `${n.id} — ${labelOf(n)}`})));
      st.addEventListener('change', (ev) => { ev.stopPropagation(); this.commit(() => (g.start = st.value), true); });
      P.append(this.field('Começa por', st));
      P.append(h('h4', {text: 'Quando roda'}), ...this.condFields(g.when = g.when || {}));
      P.append(h('p', {class: 'te-sub', text: 'Dica: arraste da bolinha de uma resposta até outro nó para ligar. Del apaga, Ctrl+D duplica, Ctrl+Z desfaz.'}));
    }
    uidc() { return (this._c = (this._c || 0) + 1); }
    field(label, input) { const id = 'te-f' + this.uidc(); const t = input.matches && input.matches('input,select,textarea') ? input : input.querySelector('input'); if (t) t.id = id; return h('div', {class: 'te-f'}, h('label', {for: id, text: label}), input); }
    text(v, on, ph, multi, mono) {
      const e = h(multi ? 'textarea' : 'input', {type: multi ? null : 'text', placeholder: ph || '', disabled: this.ro, class: mono ? 'te-mono' : null});
      e.value = v; if (multi) e.style.minHeight = '54px';
      e.addEventListener('input', () => on(e.value));
      return e;
    }
    chips(arr, on, ph) {
      arr = arr.slice();
      const box = h('div', {class: 'te-chips'});
      const inp = h('input', {type: 'text', placeholder: ph || 'adicionar + Enter', disabled: this.ro, 'aria-label': ph || 'adicionar'});
      arr.forEach((v, i) => box.append(h('span', {class: 'te-chip'}, String(v), this.ro ? null : h('button', {type: 'button', 'aria-label': `Remover ${v}`,
        onclick: () => { arr.splice(i, 1); on(arr); this.refocus('.te-chips input'); }}, '×'))));
      inp.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ',') && inp.value.trim()) { e.preventDefault(); e.stopPropagation(); const v = inp.value.trim(); if (!arr.includes(v)) { arr.push(v); on(arr); this.refocus('.te-chips input'); } inp.value = ''; }
        else if (e.key === 'Backspace' && !inp.value && arr.length) { arr.pop(); on(arr); this.refocus('.te-chips input'); }
      });
      inp.addEventListener('change', (e) => e.stopPropagation());
      box.append(inp); return box;
    }
    refocus(sel) { const f = this.props.querySelector(sel); if (f) f.focus(); }
    range(p) {
      const on = p.min_probability != null;
      const cb = h('input', {type: 'checkbox', checked: on, disabled: this.ro, id: 'te-mp' + this.uidc()});
      const r = h('input', {type: 'range', min: '0.3', max: '0.99', step: '0.01', value: on ? p.min_probability : 0.6, disabled: this.ro || !on, 'aria-label': 'Confiança mínima'});
      const out = h('output', {text: Math.round((on ? p.min_probability : 0.6) * 100) + '%'});
      const paint = () => r.style.setProperty('--p', ((r.value - 0.3) / 0.69 * 100) + '%');
      paint();
      cb.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { if (cb.checked) p.min_probability = +r.value; else delete p.min_probability; }); });
      r.addEventListener('input', () => { p.min_probability = +r.value; out.textContent = Math.round(r.value * 100) + '%'; paint(); this.liveEdit(); });
      return h('div', {class: 'te-f'}, h('div', {class: 'te-row'}, cb, h('label', {for: cb.id, class: 'te-l', text: 'Confiança mínima (abaixo dela: “incerto”)'})),
        h('div', {class: 'te-range'}, r, out));
    }
    condFields(c) {
      return COND.map(([k, label, type]) => type === 'bool'
        ? (() => { const cb = h('input', {type: 'checkbox', checked: !!c[k], disabled: this.ro, id: 'te-c' + this.uidc()});
            cb.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { if (cb.checked) c[k] = true; else delete c[k]; }); });
            return h('div', {class: 'te-f te-row'}, cb, h('label', {for: cb.id, text: label})); })()
        : type === 'num'
        ? this.field(label, this.text(c[k] == null ? '' : String(c[k]), (v) => { const x = parseFloat(String(v).replace(/\./g, '').replace(',', '.')); if (v && !isNaN(x)) c[k] = x; else delete c[k]; this.liveEdit(); }, 'ex.: 500', false, true))
        : type === 'chips'
        ? this.field(label, this.chips(c[k] || [], (arr) => this.commit(() => { if (arr.length) c[k] = arr; else delete c[k]; }), 'adicionar + Enter'))
        : this.field(label, this.text(c[k] || '', (v) => { if (v) c[k] = v; else delete c[k]; this.liveEdit(); }, '', false, true)));
    }
    actionFields(n) {
      const p = n.props, out = [];
      const sel = h('select', {disabled: this.ro, 'aria-label': 'Tipo de ação'}, Object.keys(ACTIONS).map((a) => h('option', {value: a, selected: p.action === a, text: ACTIONS[a].name})));
      sel.addEventListener('change', (e) => {
        e.stopPropagation(); const a = sel.value;
        this.commit(() => { const keep = {}; ['auto'].forEach((k) => k in p && (keep[k] = p[k])); n.props = Object.assign(ACTIONS[a].make(), keep);
          if (a === 'stop') this.g.edges = this.g.edges.filter((x) => x.from !== n.id); });
      });
      out.push(this.field('Ação', sel));
      const txt = (k, label, ph, multi, mono) => out.push(this.withPh(this.field(label, this.text(p[k] == null ? '' : String(p[k]), (v) => { p[k] = v; this.liveEdit(); }, ph, multi === 'noph' ? false : multi, mono)), multi !== 'noph'));
      if (p.action === 'notify') { txt('title', 'Título', '{title}'); txt('message', 'Mensagem', '{sender}: {text:140}', true); }
      if (p.action === 'log') txt('message', 'Mensagem', '{source} · {title}', true);
      if (p.action === 'write_file') { txt('file', 'Arquivo (dentro da pasta de saída)', 'registro.txt', false, true); txt('content', 'Conteúdo', '{time} | {title}', true); }
      if (p.action === 'email_move') { txt('folder', 'Pasta de destino', 'Trabalho'); }
      if ((ACTIONS[p.action] || {}).fields === 'card') {
        txt('titulo', 'Título', '{title}');
        out.push(this.field('Incluir (valor, prazo, remetente, empresa, data, hora, codigo, link)', this.chips(p.campos || [], (arr) => this.commit(() => (p.campos = arr)), 'campo + Enter')));
        if (p.action !== 'notificar') { txt('coluna', 'Coluna (entrada, hoje, fazendo, aguardando, feito)', 'entrada', 'noph');
          const pr = h('select', {disabled: this.ro, 'aria-label': 'Prioridade'}, [['', 'padrão'], ['0', 'sem prioridade'], ['1', 'baixa'], ['2', 'média'], ['3', 'alta']].map(([v, t]) => h('option', {value: v, selected: String(p.prioridade ?? '') === v, text: t})));
          pr.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { if (pr.value === '') delete p.prioridade; else p.prioridade = +pr.value; }); });
          out.push(this.field('Prioridade', pr)); }
      }
      if (p.action === 'etiquetar') txt('etiqueta', 'Etiqueta', 'financeiro', 'noph');
      if (p.action === 'tratar') {
        const ts = h('select', {disabled: this.ro, 'aria-label': 'Tratamento'}, TRATS.map(([v, t]) => h('option', {value: v, selected: p.tipo === v, text: t})));
        ts.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { n.props = {action: 'tratar', tipo: ts.value};
          if (ts.value === 'classificar') n.props.opcoes = ['', '']; if (ts.value === 'agrupar') n.props.minutos = 30; if (ts.value === 'extrair') n.props.campos = ['valor', 'prazo']; }); });
        out.push(this.field('Tratamento', ts));
        if (p.tipo === 'extrair') out.push(this.field('Campos (valor, prazo, pessoa, empresa, link, codigo)', this.chips(p.campos || [], (arr) => this.commit(() => (p.campos = arr)), 'campo + Enter')));
        if (p.tipo === 'classificar') { out.push(this.field('Opções (o resultado vai para {classe})', this.chips((p.opcoes || []).filter(Boolean), (arr) => this.commit(() => (p.opcoes = arr)), 'opção + Enter'))); }
        if (p.tipo === 'formatar') { txt('campo', 'Campo', 'title', 'noph'); txt('como', 'Como (maiusculas, minusculas, titulo, data, moeda)', 'maiusculas', 'noph'); }
        if (p.tipo === 'agrupar') out.push(this.field('Juntar a cada (minutos)', this.text(String(p.minutos ?? 30), (v) => { if (+v > 0) p.minutos = +v; this.liveEdit(); }, '30')));
      }
      if (p.action === 'nota') { txt('titulo', 'Título', '{title}'); txt('texto', 'Texto', '{resumo}', true); }
      if (['mover_card', 'atualizar_card'].includes(p.action)) txt('coluna', 'Coluna (entrada, hoje, fazendo, aguardando, feito)', 'hoje', 'noph');
      if (['prioridade', 'atualizar_card'].includes(p.action)) txt('prioridade', 'Prioridade (baixa, normal, alta, urgente)', 'alta', 'noph');
      if (p.action === 'atualizar_card') txt('etiqueta', 'Etiqueta a pôr', '', 'noph');
      if (p.action === 'abrir_site') txt('url', 'Endereço', 'https://…', 'noph', true);
      if (p.action === 'encadear') txt('automacao', 'Nome da automação', 'Boletos e contas', 'noph');
      if (p.action === 'adicionar_lista') { txt('lista', 'Lista', 'Compras', 'noph'); txt('texto', 'Item', '{title}'); }
      if (p.action === 'registrar') txt('mensagem', 'O que anotar', '{title}', true);
      if (p.action === 'notificar') txt('tom', 'Tom (info, aviso, urgente, sucesso)', 'info', 'noph');
      if (p.action === 'responder_sugestao') { txt('texto', 'Rascunho', 'Olá! Recebi sua mensagem.', true);
        const ab = h('input', {type: 'checkbox', checked: !!p.abrir, disabled: this.ro, id: 'te-ab' + this.uidc()});
        ab.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { if (ab.checked) p.abrir = true; else delete p.abrir; }); });
        out.push(h('div', {class: 'te-f te-row'}, ab, h('label', {for: ab.id, text: 'Abrir o app com o rascunho (você é quem envia)'}))); }
      if (p.action === 'abrir_app') { txt('app', 'App', 'whatsapp', 'noph'); txt('texto', 'Texto pronto', '{text}', true); }
      if (p.action === 'webhook') {
        txt('url', 'URL', 'https://…', false, true);
        const ta = h('textarea', {class: 'te-mono', disabled: this.ro}); ta.value = JSON.stringify(p.body || {}, null, 2);
        const msg = h('div', {class: 'te-msg'});
        ta.addEventListener('change', (e) => { e.stopPropagation(); try { const v = JSON.parse(ta.value); this.commit(() => (p.body = v), true); msg.textContent = ''; } catch (err) { msg.textContent = 'JSON inválido.'; } });
        out.push(h('div', {}, this.field('Corpo (JSON)', ta), msg));
      }
      if (p.action === 'command') {
        out.push(this.field('Programa e argumentos', this.chips(p.argv || [], (arr) => this.commit(() => (p.argv = arr)), 'programa, depois cada argumento')));
        out.push(this.field('Tempo limite (s)', this.text(p.timeout == null ? '' : String(p.timeout), (v) => { if (v && !isNaN(+v)) p.timeout = +v; else delete p.timeout; this.liveEdit(); }, '30')));
      }
      if (p.action !== 'stop' && !NEW_ACTIONS.includes(p.action)) {
        const cb = h('input', {type: 'checkbox', checked: !!p.auto, disabled: this.ro, id: 'te-auto' + this.uidc()});
        cb.addEventListener('change', (e) => { e.stopPropagation(); this.commit(() => { if (cb.checked) p.auto = true; else delete p.auto; }); });
        out.push(h('div', {class: 'te-f te-row'}, cb, h('label', {for: cb.id, text: 'Executar sem pedir confirmação'})));
      }
      return out;
    }
    withPh(fieldEl, on) {
      if (!on || this.ro) return fieldEl;
      const inp = fieldEl.querySelector('input,textarea');
      const prev = this.g.nodes.filter((x) => x.kind !== 'action').slice(0, 4).map((x) => `{${x.id}.answer}`);
      fieldEl.append(h('div', {class: 'te-ph', 'aria-label': 'Inserir campo'}, PLACEHOLDERS.concat(prev).map((t) => h('button', {type: 'button', title: 'Inserir ' + t, text: t,
        onmousedown: (e) => e.preventDefault(),
        onclick: () => { const a = inp.selectionStart ?? inp.value.length, b = inp.selectionEnd ?? a; this.pending = JSON.stringify(this.g);
          inp.value = inp.value.slice(0, a) + t + inp.value.slice(b); inp.dispatchEvent(new Event('input')); this.flushEdit(); inp.focus(); inp.setSelectionRange(a + t.length, a + t.length); }}))));
      return fieldEl;
    }
    linksField(n) {
      const box = h('div', {class: 'te-f'}, h('div', {class: 'te-l', text: 'Respostas e próximos passos'}));
      const others = this.g.nodes.filter((x) => x.id !== n.id);
      portsOf(this.g, n).forEach((port) => {
        const cur = this.g.edges.map((e, i) => [e, i]).filter(([e]) => e.from === n.id && e.label === port);
        cur.forEach(([e, i]) => box.append(h('div', {class: 'te-link'}, h('span', {title: port, text: portName(port) + ' →'}),
          h('span', {class: 'te-chip'}, e.to, this.ro ? null : h('button', {type: 'button', 'aria-label': `Remover ligação ${portName(port)} para ${e.to}`, onclick: () => this.disconnect(i)}, '×')))));
        if (!this.ro) {
          const s2 = h('select', {'aria-label': `Ligar a resposta ${portName(port)} a um nó`}, h('option', {value: '', text: cur.length ? '+ outro nó…' : 'ligar a…'}),
            others.filter((x) => !cur.some(([e]) => e.to === x.id)).map((x) => h('option', {value: x.id, text: `${x.id} — ${labelOf(x)}`})));
          s2.addEventListener('change', (e) => { e.stopPropagation(); if (s2.value) this.connect(n.id, port, s2.value); });
          box.append(h('div', {class: 'te-link'}, h('span', {title: port, text: cur.length ? '' : portName(port)}), s2));
        }
      });
      if (!this.ro && n.kind !== 'action') {
        const inp = h('input', {type: 'text', placeholder: n.kind === 'score' || n.kind === 'select' ? 'nova resposta (ex.: >= 2) + Enter' : 'nova resposta + Enter', 'aria-label': 'Nova resposta'});
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && inp.value.trim()) { e.preventDefault(); e.stopPropagation(); const v = inp.value.trim(); this.commit(() => { if (!n.ports.includes(v)) n.ports.push(v); }); } });
        inp.addEventListener('change', (e) => e.stopPropagation());
        box.append(inp);
      }
      return box;
    }

    // ---- API pública
    getGraph() {
      const g = clone(this.g);
      g.nodes.forEach((n) => { n.label = labelOf(n); n.ports = portsOf(this.g, this.node(n.id)); });
      return g;
    }
    setGraph(graph, keepHistory) {
      if (!keepHistory) { this.undoS = []; this.redoS = []; }
      this.g = normalize(graph); this.sel = null;
      if (!this.g.nodes.some((n) => n.x || n.y) && this.g.nodes.length > 1) layoutGraph(this.g);
      this.revalidate(); this.render(); this.renderProps(); this.fit();
    }
    destroy() { clearTimeout(this._ht); clearTimeout(this._vt); this.root.removeEventListener('keydown', this._kd); this.el.innerHTML = ''; }
  }

  window.TreeEditor = {
    mount(el, opts) {
      if (typeof el === 'string') el = document.querySelector(el);
      const ed = new Editor(el, opts);
      return {
        getGraph: () => ed.getGraph(), setGraph: (g, k) => ed.setGraph(g, k), validate: () => ed.validate(),
        autoLayout: () => ed.autoLayout(), fit: () => ed.fit(), undo: () => ed.undo(), redo: () => ed.redo(),
        test: (t) => ed.test(t), highlight: (tr) => ed.highlight(tr), select: (id) => ed.select(id),
        addNode: (kind, action, x, y) => ed.addNode(kind, action, x, y), connect: (a, p, b) => ed.connect(a, p, b),
        destroy: () => ed.destroy(), _editor: ed
      };
    },
    fromTree(tree, api) { return Promise.resolve(((api && api.fromTree) || defaultApi.fromTree)(tree)); },
    toTree(graph, api) { return Promise.resolve(((api && api.toTree) || defaultApi.toTree)(graph)); },
    layout: (g) => layoutGraph(normalize(g)),
    validate: (g) => validateGraph(normalize(g)),
    KINDS: Object.fromEntries(Object.entries(KINDS).map(([k, v]) => [k, v.name])),
    ACTIONS: Object.fromEntries(Object.entries(ACTIONS).map(([k, v]) => [k, v.name]))
  };
})();
