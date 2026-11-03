/* Djorge Local v2 — interface (agente D). Sem dependências externas, tudo local. */
"use strict";
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ico = (n, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = {get(k, d) { try { const v = localStorage.getItem("dj." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("dj." + k, JSON.stringify(v)); } catch (e) {} }};
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
/* formatação: nomes em Maiúsculas Iniciais, valores e datas em pt-BR (o backend pode mandar já formatado) */
const LOWER_WORDS = new Set(["de", "da", "do", "das", "dos", "e", "em", "a", "o", "para", "com"]);
function titleCase(s) {
  s = String(s ?? "").trim(); if (!s || /@|https?:|^\+?\d/.test(s)) return s;
  return s.split(/(\s+)/).map((w, i) => /^\s+$/.test(w) ? w : (i && LOWER_WORDS.has(w.toLowerCase())) ? w.toLowerCase() :
    (w.length > 1 && w === w.toUpperCase() && /[A-Z]/.test(w) && w.length <= 4) || (/[a-zà-ú]/.test(w) && /.[A-ZÀ-Ú]/.test(w)) ? w : w.charAt(0).toLocaleUpperCase("pt-BR") + w.slice(1).toLocaleLowerCase("pt-BR")).join("");
}
const fmtMoney = v => { const n = typeof v === "number" ? v : parseFloat(String(v).replace(/[^\d,.-]/g, "").replace(/\.(?=\d{3}(\D|$))/g, "").replace(",", "."));
  return isNaN(n) ? String(v ?? "") : n.toLocaleString("pt-BR", {style: "currency", currency: "BRL"}); };
function fmtDT(v, opt = {}) {
  const d = v instanceof Date ? v : (typeof v === "string" && /^\d{4}-\d\d-\d\d/.test(v) ? new Date(v.length === 10 ? v + "T00:00:00" : v) : null);
  if (!d || isNaN(d)) return String(v ?? "");
  const day = d.toLocaleDateString("pt-BR", {weekday: "short", day: "numeric", month: "short"}).replace(/\.,/, ".,");
  const t = d.getHours() || d.getMinutes() ? ` às ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
  return opt.rel ? (relDayWord(d) || day) + t : day + t;
}
function relDayWord(d) { const t = new Date(); t.setHours(0, 0, 0, 0); const x = new Date(d); x.setHours(0, 0, 0, 0); const n = Math.round((x - t) / 864e5); return n === 0 ? "hoje" : n === 1 ? "amanhã" : n === -1 ? "ontem" : ""; }
function ago(v) { const d = v ? new Date(v) : null; if (!d || isNaN(d)) return ""; const s = (Date.now() - d) / 1000;
  if (s < 60) return "agora"; if (s < 3600) return `há ${Math.floor(s / 60)} min`; if (s < 86400) return `há ${Math.floor(s / 3600)} h`; return fmtDT(d); }
const asList = (r, ...keys) => Array.isArray(r) ? r : r ? (keys.map(k => r[k]).find(Array.isArray) || []) : [];

/* ---------------------------------------------------------------- API */
let TOKEN = (document.cookie.match(/(?:^|;\s*)djtoken=([^;]+)/) || [])[1] || new URLSearchParams(location.search).get("token") || "";
class ApiError extends Error { constructor(msg, status, data) { super(msg); this.status = status; this.data = data || {}; } }
const HUMAN = {
  modelo_ausente: "Isso precisa do modelo DOFL, que ainda não foi baixado.",
  token: "Sessão expirada. Feche e abra o Djorge de novo.", origem: "Pedido bloqueado por segurança (origem).",
  fora_da_janela_24h: "O WhatsApp só deixa enviar texto livre até 24 h depois da última mensagem da pessoa.",
  "não encontrado": "Essa função ainda não está disponível nesta versão."
};
function human(e) {
  if (!e) return "Algo deu errado.";
  if (e.status === 0) return "Não consegui falar com o Djorge. Ele está aberto?";
  const m = (e.data && (e.data.error || e.data.message)) || e.message || "";
  if (HUMAN[m]) return HUMAN[m];
  if (e.data && (e.data.dicas || []).length) return `${friendlyMsg(m) || "Não consegui fazer isso."} Tente: “${e.data.dicas[0]}”`;
  const fm = friendlyMsg(m); if (fm) return fm;
  if (e.status === 503) return "Essa parte do app não está disponível agora" + (m ? `: ${m}` : ".");
  if (e.status >= 500) return "O Djorge encontrou um problema interno. Tente de novo em instantes.";
  return m || "Algo deu errado.";
}
/* erros técnicos viram dicas em linguagem simples */
function friendlyMsg(m) {
  m = String(m || "");
  if (/data inv[aá]lida|invalid (iso|date)|isoformat|ValueError:.*(due|start|data)/i.test(m)) return "Não entendi a data. Tente “amanhã às 9h”, “03/11 14h” ou “sexta de manhã”.";
  if (/hora inv[aá]lida|invalid time/i.test(m)) return "Não entendi a hora. Tente “às 14h” ou “14:30”.";
  if (/KeyError|TypeError|AttributeError|Traceback/.test(m)) return "Faltou alguma informação no pedido. Tente dizer o quê, quando e para quem.";
  if (/destino|destinat[aá]rio|sem (para|to)\b/i.test(m)) return "Para quem é a mensagem? Tente: “#ana &chego em 10 minutos&”.";
  if (/n[aã]o conectad|not connected/i.test(m)) return "Essa integração não está conectada. Conecte em Integrações ou use “Abrir no WhatsApp”.";
  return "";
}
function errDicas(e) {
  const d = (e && e.data && e.data.dicas) || [];
  if (d.length) return d;
  const m = (e && ((e.data && e.data.error) || e.message)) || "";
  if (/data|hora|due|start/i.test(m)) return ["marca dentista dia 20 às 9h", "reunião com Ana amanhã às 15h"];
  return [];
}
async function api(path, body, opt = {}) {
  const init = {method: body !== undefined && body !== null ? "POST" : (opt.method || "GET"), headers: {}, credentials: "same-origin"};
  if (TOKEN) init.headers["X-Token"] = TOKEN;
  if (init.method === "POST") { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body || {}); }
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new ApiError("rede", 0); }
  let data = null; const txt = await r.text();
  try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = {raw: txt}; }
  if (!r.ok || (data && data.error && opt.strict)) {
    const err = new ApiError((data && data.error) || r.statusText, r.status, data);
    if (r.status === 409 && data && data.error === "modelo_ausente" && !opt.quiet409) modelInvite();
    throw err;
  }
  return data;
}
async function tryApi(path, body, opt) { try { return await api(path, body, opt); } catch (e) { if (!(opt && opt.silent)) toast(human(e), "bad"); return null; } }
async function ensureToken() { if (TOKEN) return; try { const t = await api("/api/token"); if (t && t.token) TOKEN = t.token; } catch (e) {} }

/* ---------------------------------------------------------------- constantes */
const VIEWS = [
  {id: "inicio", label: "Início", icon: "home"}, {id: "quadro", label: "Quadro", icon: "board"},
  {id: "agenda", label: "Agenda", icon: "cal"}, {id: "tarefas", label: "Tarefas", icon: "list"},
  {id: "mensagens", label: "Mensagens", icon: "msg"}, {id: "notificacoes", label: "Notificações", icon: "bell"}, {id: "automacoes", label: "Automações", icon: "tree"}];
const VIEW_ALIAS = {notificações: "notificacoes", notificacao: "notificacoes", kanban: "quadro", lista: "tarefas", regras: "automacoes", "início": "inicio", home: "inicio", board: "quadro", list: "tarefas"};
const COLS = [{id: "entrada", label: "Entrada", c: "var(--ink3)"}, {id: "hoje", label: "Hoje", c: "var(--rose)"},
  {id: "fazendo", label: "Fazendo", c: "var(--info)"}, {id: "aguardando", label: "Aguardando", c: "var(--warn)"}, {id: "feito", label: "Feito", c: "var(--ok)"}];
const KINDS = {tarefa: "Tarefa", evento: "Evento", lembrete: "Lembrete", nota: "Nota", lista: "Lista", mensagem: "Mensagem"};
const KIND_ICO = {tarefa: "check", evento: "cal", lembrete: "bell", nota: "edit", lista: "list", mensagem: "msg"};
const PRI = ["Sem prioridade", "Baixa", "Média", "Alta"];
const CARD_COLORS = [["", "Nenhuma"], ["#b56576", "Rosa"], ["#c0714f", "Terracota"], ["#b7832f", "Mel"], ["#6f8f7a", "Sálvia"], ["#4f7d96", "Oceano"], ["#7d5a86", "Ameixa"]];
let ACCENTS = [["rosa", "#b56576"], ["vinho", "#6d2e46"], ["terracota", "#c06c4a"], ["mostarda", "#c9a227"], ["verde", "#5a7d5a"], ["azul", "#4a6c8c"], ["lavanda", "#8a7bb0"], ["grafite", "#4a4a4a"]];
const TOOL_PT = {calcular: ["Calcular", "calc"], datas_horas: ["Datas e horas", "clock"], proximas_tarefas: ["Próximas tarefas", "list"],
  cards: ["Card", "board"], listas: ["Lista", "list"], bilhete: ["Mensagem", "send"], buscar: ["Buscar", "search"],
  regra: ["Automação", "tree"], web: ["Busca na web", "search"], abrir: ["Abrir app", "send"], perguntar: ["Só para confirmar", "spark"], interface: ["Interface", "gear"], nenhuma: ["Vamos ajustar o pedido", "spark"], triagem: ["Triagem", "shield"]};
const FIELD_CLS = {data: "c-data", hora: "c-hora", "período do dia": "c-periodo", pessoa: "c-pessoa", "endereço de e-mail": "c-email",
  lugar: "c-lugar", "empresa ou estabelecimento": "c-empresa", endereco: "c-endereco"};
const FIELD_EMO = {data: "📅", hora: "🕒", "período do dia": "🌤", pessoa: "👤", "evento ou compromisso": "📌", lugar: "📍", lista: "🗒",
  "endereço de e-mail": "✉", "empresa ou estabelecimento": "🏢", "frequência ou repetição": "🔁"};
const SRC_EMO = {google: "🟦", microsoft: "🟪", whatsapp: "🟩", gemini: "✦", local: "🏠"};
const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const WD = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
const PHRASES = ["lendo seu pedido", "separando as palavras", "achando a data", "procurando pessoas e lugares", "escolhendo a ferramenta",
  "montando a proposta", "arrumando as pétalas"];
const SUGGEST = {
  auto: ["Reunião com Ana amanhã às 15h", "O que tenho para hoje?", "Quanto é 18% de 2.350?", "#ana &chego em 10 minutos&", "Deixa o tema escuro"],
  pedido: ["Lembrar de pagar a luz dia 10", "Lista de compras: pão, café, leite", "Toda segunda às 9h revisar e-mails"],
  mensagem: ["Cole aqui uma mensagem recebida para triar"]};

/* ---------------------------------------------------------------- estado */
const S = {
  view: "inicio", cards: [], cardsLoaded: false, prefs: {}, model: {state: "ausente"}, voice: null,
  filters: store.get("filters", {}), sort: store.get("sort", "manual"), q: "",
  thread: [], mode: "auto", agMode: store.get("agMode", "semana"), stripMode: store.get("stripMode", "dia"),
  anchor: new Date(), recents: store.get("recents", []), status: null, pendingSeen: new Set(), trees: null, ints: null, notifUnread: 0
};

/* ---------------------------------------------------------------- datas */
const pad = n => String(n).padStart(2, "0");
const parseD = s => { if (!s) return null; const d = new Date(s); return isNaN(d) ? null : d; };
const sameDay = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const startDay = d => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const isoLocal = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const weekStart = d => { const x = startDay(d); const w = (x.getDay() + 6) % 7; return addDays(x, -w); };
const cardDate = c => parseD(c.start) || parseD(c.due);
function relDay(d) {
  const t = startDay(new Date()), x = startDay(d), diff = Math.round((x - t) / 864e5);
  if (diff === 0) return "hoje"; if (diff === 1) return "amanhã"; if (diff === -1) return "ontem";
  if (diff > 1 && diff < 7) return WD[x.getDay()];
  return `${x.getDate()} ${MONTHS[x.getMonth()].slice(0, 3)}`;
}
function whenTxt(c) {
  const d = cardDate(c); if (!d) return "";
  return relDay(d) + (c.all_day || (d.getHours() === 0 && d.getMinutes() === 0) ? "" : " " + hm(d));
}
const isLate = c => { const d = parseD(c.due) || (c.kind === "tarefa" ? parseD(c.start) : null); return d && !c.done && d < new Date() && !sameDay(d, new Date()); };

/* ---------------------------------------------------------------- toasts */
function toast(msg, kind = "", opt = {}) {
  const el = document.createElement("div");
  el.className = "toast " + kind; el.setAttribute("role", kind === "bad" ? "alert" : "status");
  const icon = kind === "bad" ? "shield" : kind === "warn" ? "bell" : "check";
  el.innerHTML = `<span class="tico">${ico(icon, "sm")}</span><div class="tb">${opt.title ? `<b>${esc(opt.title)}</b>` : ""}${esc(msg)}</div>` +
    (opt.action ? `<button type="button" data-a>${esc(opt.action)}</button>` : "") + `<button type="button" aria-label="Fechar aviso" data-x>${ico("x", "sm")}</button>`;
  $("#toasts").appendChild(el);
  const kill = () => { el.classList.add("out"); setTimeout(() => el.remove(), 300); };
  el.querySelector("[data-x]").onclick = kill;
  if (opt.action) el.querySelector("[data-a]").onclick = () => { kill(); opt.onAction && opt.onAction(); };
  setTimeout(kill, opt.ms || (kind === "bad" ? 7000 : 3800));
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
}

/* ---------------------------------------------------------------- menus / selects */
let openMenu = null;
function closeMenu() { if (openMenu) { openMenu.el.remove(); openMenu.anchor && openMenu.anchor.setAttribute("aria-expanded", "false"); const a = openMenu.anchor; openMenu = null; a && a.focus && a.focus({preventScroll: true}); } }
/* items: [{value,label,icon?,checked?,group?,hr?}] onPick(value,item) */
function menu(anchor, items, onPick, opt = {}) {
  if (openMenu && openMenu.anchor === anchor) { closeMenu(); return; }
  closeMenu();
  const el = document.createElement("div");
  el.className = "menu"; el.setAttribute("role", opt.role || "menu"); if (opt.label) el.setAttribute("aria-label", opt.label);
  const ir = opt.role === "listbox" ? "option" : (opt.multi ? "menuitemcheckbox" : "menuitem");
  el.innerHTML = items.map((it, i) => it.hr ? "<hr>" : it.group ? `<div class="mh">${esc(it.group)}</div>` :
    `<button type="button" role="${ir}" data-i="${i}" tabindex="-1" class="${it.danger ? "danger" : ""}" ${it.disabled ? "disabled" : ""} ${ir === "option" ? `aria-selected="${!!it.checked}"` : it.checked !== undefined ? `aria-checked="${!!it.checked}"` : ""}>${it.icon ? ico(it.icon, "sm") : it.dot ? `<i class="mdot" style="background:${it.dot}"></i>` : ""}<span>${esc(it.label)}</span>${it.kbd ? `<kbd>${esc(it.kbd)}</kbd>` : ""}</button>`).join("");
  if (opt.ctx) el.classList.add("ctx");
  document.body.appendChild(el);
  const r = opt.at ? {left: opt.at.x, right: opt.at.x, top: opt.at.y, bottom: opt.at.y, width: 0} : anchor.getBoundingClientRect(), w = Math.max(el.offsetWidth, opt.match ? r.width : 0);
  el.style.minWidth = w + "px";
  let top = r.bottom + (opt.at ? 2 : 6); if (top + el.offsetHeight > innerHeight - 8) top = Math.max(8, (opt.at ? r.top - el.offsetHeight : r.top - el.offsetHeight - 6));
  el.style.top = top + "px"; el.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  anchor.setAttribute && anchor.setAttribute("aria-expanded", "true");
  openMenu = {el, anchor};
  const btns = $$("button:not([disabled])", el); let ai = Math.max(0, btns.findIndex(b => b.getAttribute("aria-selected") === "true"));
  const act = i => { btns.forEach(b => b.classList.remove("act")); ai = (i + btns.length) % btns.length; btns[ai] && (btns[ai].classList.add("act"), btns[ai].focus()); };
  if (btns.length) act(ai);
  el.addEventListener("click", e => { const b = e.target.closest("button[data-i]"); if (!b || b.disabled) return; const it = items[+b.dataset.i];
    if (opt.multi) { it.checked = !it.checked; b.setAttribute("aria-checked", it.checked); onPick(it.value, it); return; }
    closeMenu(); onPick(it.value, it); });
  el.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); act(ai + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); act(ai - 1); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
    else if (e.key === "Home") { e.preventDefault(); act(0); } else if (e.key === "End") { e.preventDefault(); act(btns.length - 1); }
  });
}
document.addEventListener("mousedown", e => { if (openMenu && !openMenu.el.contains(e.target) && !(openMenu.anchor.contains && openMenu.anchor.contains(e.target))) closeMenu(); });
/* menu de botão direito: ctxMenu(evento, itens, aoEscolher) */
function ctxMenu(e, items, onPick) {
  e.preventDefault(); closeMenu(); hideTip();
  const anchor = e.target.closest("[tabindex],button,a") || e.target;
  menu(anchor, items, onPick, {at: {x: e.clientX, y: e.clientY}, ctx: true, label: "Ações"});
}
window.addEventListener("blur", () => closeMenu());
window.addEventListener("resize", () => closeMenu());

/* dicas (tooltips) num portal no <body>, sempre por cima de tudo e dentro da tela */
let tipEl = null, tipFor = null, tipT = null;
function hideTip() { clearTimeout(tipT); tipFor = null; if (tipEl) tipEl.classList.remove("on"); }
function showTip(t) {
  const txt = t.getAttribute("data-tip"); if (!txt) return;
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "djtip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
  if (tipEl.parentNode !== document.body || tipEl.nextSibling) document.body.appendChild(tipEl);
  tipFor = t; tipEl.textContent = txt; tipEl.classList.add("on");
  const r = t.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight, pos = t.getAttribute("data-tip-pos") || "bottom", g = 8;
  let x, y;
  if (pos === "right") { x = r.right + g; y = r.top + r.height / 2 - h / 2; if (x + w > innerWidth - 6) x = r.left - g - w; }
  else if (pos === "left") { x = r.left - g - w; y = r.top + r.height / 2 - h / 2; if (x < 6) x = r.right + g; }
  else if (pos === "top") { x = r.left + r.width / 2 - w / 2; y = r.top - g - h; if (y < 6) y = r.bottom + g; }
  else { x = r.left + r.width / 2 - w / 2; y = r.bottom + g; if (y + h > innerHeight - 6) y = r.top - g - h; }
  tipEl.style.left = Math.round(Math.max(6, Math.min(x, innerWidth - w - 6))) + "px"; tipEl.style.top = Math.round(Math.max(6, Math.min(y, innerHeight - h - 6))) + "px";
}
document.addEventListener("mouseover", e => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t === tipFor) return; clearTimeout(tipT);
  if (!t) return hideTip(); tipT = setTimeout(() => showTip(t), 260); });
document.addEventListener("focusin", e => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t && t.matches(":focus-visible")) showTip(t); else hideTip(); });
document.addEventListener("mousedown", hideTip, true); document.addEventListener("scroll", hideTip, true); document.addEventListener("keydown", e => { if (e.key === "Escape") hideTip(); }, true);
/* custom select: returns html; wire with wireSel */
function selHTML(id, opts, val, label) {
  const cur = opts.find(o => String(o[0]) === String(val)) || opts[0];
  return `<div class="sel" data-sel="${esc(id)}"><button type="button" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(label || "")}" data-v="${esc(cur ? cur[0] : "")}"><span>${esc(cur ? cur[1] : "")}</span>${ico("down", "sm")}</button></div>`;
}
function wireSel(root, id, opts, onChange) {
  const b = $(`[data-sel="${id}"] > button`, root); if (!b) return;
  b.onclick = () => menu(b, opts.map(o => ({value: o[0], label: o[1], checked: String(o[0]) === b.dataset.v})), v => {
    b.dataset.v = v; b.querySelector("span").textContent = (opts.find(o => String(o[0]) === String(v)) || [, v])[1]; onChange && onChange(v);
  }, {role: "listbox", match: true});
}
const selVal = (root, id) => { const b = $(`[data-sel="${id}"] > button`, root); return b ? b.dataset.v : undefined; };
function swHTML(id, on, label) { return `<label class="sw"><input type="checkbox" role="switch" id="${esc(id)}" ${on ? "checked" : ""}><span class="tr"></span>${label ? `<span>${esc(label)}</span>` : ""}</label>`; }
function rngHTML(id, val, min, max, step, fmt) { return `<div class="rng"><input type="range" id="${esc(id)}" min="${min}" max="${max}" step="${step}" value="${val}" data-fmt="${fmt || ""}" aria-label="${esc(id)}"><output></output></div>`; }
function wireRanges(root) {
  $$(".rng input", root).forEach(i => { const f = () => { const p = (i.value - i.min) / (i.max - i.min) * 100; i.parentNode.style.setProperty("--pct", p + "%");
    const o = i.nextElementSibling; o.textContent = i.dataset.fmt === "%" ? Math.round(i.value * 100) + "%" : i.value + (i.dataset.fmt || ""); }; i.addEventListener("input", f); f(); });
}
function segHTML(id, opts, val, label) {
  return `<div class="seg" role="radiogroup" aria-label="${esc(label || "")}" data-seg="${esc(id)}">${opts.map(o => `<button type="button" role="radio" aria-checked="${String(o[0]) === String(val)}" data-v="${esc(o[0])}">${esc(o[1])}</button>`).join("")}</div>`;
}
function wireSeg(root, id, onChange) {
  const g = $(`[data-seg="${id}"]`, root); if (!g) return;
  const bs = $$("button", g);
  const set = b => { bs.forEach(x => { x.setAttribute("aria-checked", x === b); x.tabIndex = x === b ? 0 : -1; }); onChange && onChange(b.dataset.v); };
  bs.forEach((b, i) => { b.tabIndex = b.getAttribute("aria-checked") === "true" ? 0 : -1; b.onclick = () => set(b);
    b.onkeydown = e => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const n = bs[(i + (e.key === "ArrowRight" ? 1 : -1) + bs.length) % bs.length]; n.focus(); set(n); } }; });
}
const segVal = (root, id) => { const b = $(`[data-seg="${id}"] [aria-checked="true"]`, root); return b ? b.dataset.v : undefined; };

/* ---------------------------------------------------------------- modal */
const modalStack = [];
function modal({title, cls = "", body = "", foot = "", onClose, label, raw}) {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  const id = "m" + Math.random().toString(36).slice(2, 8);
  scrim.innerHTML = raw ? `<div class="modal ${cls}" role="dialog" aria-modal="true" aria-label="${esc(label || title || "")}">${body}</div>` :
    `<div class="modal ${cls}" role="dialog" aria-modal="true" aria-labelledby="${id}"><header><h2 id="${id}">${title}</h2>
    <button class="iconbtn" data-close aria-label="Fechar">${ico("x")}</button></header>${body}${foot ? `<footer>${foot}</footer>` : ""}</div>`;
  const prev = document.activeElement;
  document.body.appendChild(scrim);
  const m = { el: scrim, box: scrim.firstElementChild, close() {
    if (!scrim.isConnected) return; scrim.remove(); modalStack.splice(modalStack.indexOf(m), 1); onClose && onClose(); prev && prev.focus && prev.focus({preventScroll: true}); } };
  modalStack.push(m);
  scrim.addEventListener("mousedown", e => { if (e.target === scrim) m.close(); });
  $$("[data-close]", scrim).forEach(b => b.onclick = () => m.close());
  scrim.addEventListener("keydown", e => {
    if (e.key === "Escape" && !openMenu) { e.stopPropagation(); m.close(); }
    if (e.key === "Tab") { const f = $$('button:not([disabled]),[href],input:not([type=hidden]),select,textarea,[tabindex]:not([tabindex="-1"])', m.box).filter(x => x.offsetParent !== null);
      if (!f.length) return; const a = f[0], z = f[f.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); } else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); } }
  });
  m.box.tabIndex = -1;
  { const af = $("[autofocus]", m.box); if (af) af.focus(); }
  setTimeout(() => { const f = $("[autofocus]", m.box) || $('input,textarea,button:not([data-close])', m.box) || m.box; f.focus(); }, 30);
  return m;
}
function tabsModal({title, tabs, start, cls, onTab}) {
  const body = `<div class="mbody"><div class="tabs" role="tablist" aria-orientation="vertical">${tabs.map(t => `<button role="tab" id="tb-${t.id}" aria-controls="tp" aria-selected="${t.id === start}" tabindex="${t.id === start ? 0 : -1}" data-t="${t.id}">${ico(t.icon, "sm")}<span>${esc(t.label)}</span></button>`).join("")}</div>
    <div class="tabpanel" id="tp" role="tabpanel" tabindex="0"></div></div>`;
  const m = modal({title, body, cls});
  const bs = $$(".tabs button", m.box);
  const go = id => { bs.forEach(b => { const on = b.dataset.t === id; b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1; });
    $("#tp", m.box).setAttribute("aria-labelledby", "tb-" + id); onTab(id, $("#tp", m.box), m); };
  bs.forEach((b, i) => { b.onclick = () => go(b.dataset.t);
    b.onkeydown = e => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = bs[(i + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length]; n.focus(); go(n.dataset.t); } }; });
  go(start || tabs[0].id);
  return m;
}

/* ---------------------------------------------------------------- preferências de interface */
/* O servidor (uiprefs.py) usa nomes em português; aqui a interface usa nomes curtos. */
const P_THEME = {claro: "light", escuro: "dark", auto: "auto"}, P_DENS = {compacta: "compact", normal: "comfortable", confortavel: "comfy"};
const P_FONT = {pequena: "sm", normal: "md", grande: "lg", "muito grande": "xl"};
const inv = o => Object.fromEntries(Object.entries(o).map(([k, v]) => [v, k]));
function filtFromServer(f) {
  f = f || {}; const o = {}; const sp = v => String(v).split(",").map(x => x.trim()).filter(Boolean);
  ["kind", "tag", "person", "source", "column"].forEach(k => { if (f[k]) o[k] = sp(f[k]); });
  if (f.priority) { const m = String(f.priority).match(/^(\d)\+$/); o.priority = m ? ["0", "1", "2", "3"].filter(x => +x >= +m[1]) : sp(f.priority); }
  if (f.done !== undefined && f.done !== "") o.done = /^(1|true|sim)$/i.test(f.done) ? "so" : "ocultar";
  if (f.from) o.from = f.from; if (f.to) o.to = f.to;
  return {filters: o, q: f.q || ""};
}
function filtToServer(F, q) {
  const o = {}; ["kind", "tag", "person", "source", "column"].forEach(k => { if (F[k] && F[k].length) o[k] = F[k].join(","); });
  if (F.priority && F.priority.length) o.priority = F.priority.join(",");
  if (F.done) o.done = F.done === "so" ? "true" : "false";
  let fr = F.from, to = F.to; const now = new Date(), t0 = startDay(now);
  if (F.period === "hoje") fr = to = ymd(t0); if (F.period === "semana") { fr = ymd(weekStart(now)); to = ymd(addDays(weekStart(now), 6)); }
  if (F.period === "mes") { fr = ymd(new Date(now.getFullYear(), now.getMonth(), 1)); to = ymd(new Date(now.getFullYear(), now.getMonth() + 1, 0)); }
  if (F.period === "proximos") { fr = ymd(t0); to = ymd(addDays(t0, 7)); }
  if (fr) o.from = fr; if (to) o.to = to; if (q) o.q = q;
  return o;
}
function normPrefs(resp) {
  const p = (resp && resp.prefs) || resp || {};
  if (resp && resp.choices) { S.choices = resp.choices; const ac = resp.choices.accent_colors; if (ac) ACCENTS = Object.entries(ac); }
  return {
    theme: P_THEME[p.theme] || p.theme || "auto", density: P_DENS[p.density] || p.density || "comfortable",
    accent: p.accent || "rosa", default_view: VIEW_ALIAS[p.default_view] || p.default_view || "inicio",
    columns: p.kanban_columns || p.columns || COLS.map(c => c.id),
    saved_filters: (p.saved_filters || []).map(f => f.filters && !f._c ? {name: f.name, ...filtFromServer(f.filters), _raw: f.filters} : f),
    voice: p.voice_enabled ?? p.voice ?? true, speak: p.speak_replies ?? p.speak ?? false,
    font_size: P_FONT[p.font_size] || p.font_size || "md", sidebar_order: p.sidebar_order || null, _server: p};
}
function toServer(patch) {
  const o = {};
  for (const [k, v] of Object.entries(patch)) {
    if (k === "theme") o.theme = inv(P_THEME)[v] || v;
    else if (k === "density") o.density = inv(P_DENS)[v] || v;
    else if (k === "font_size") o.font_size = inv(P_FONT)[v] || v;
    else if (k === "columns") o.kanban_columns = v;
    else if (k === "voice") o.voice_enabled = !!v;
    else if (k === "speak") o.speak_replies = !!v;
    else if (k === "default_view") o.default_view = {quadro: "kanban", tarefas: "lista"}[v] || (["inicio", "kanban", "agenda", "lista"].includes(v) ? v : "inicio");
    else if (k === "saved_filters") o.saved_filters = v.map(f => ({name: f.name, filters: f._raw && !f.filters ? f._raw : filtToServer(f.filters || {}, f.q)}));
    else o[k] = v;
  }
  return o;
}
function shade(hex, amt) { const n = parseInt(hex.slice(1), 16); let r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const f = x => Math.max(0, Math.min(255, Math.round(amt < 0 ? x * (1 + amt) : x + (255 - x) * amt))); return "#" + [f(r), f(g), f(b)].map(x => x.toString(16).padStart(2, "0")).join(""); }
function applyPrefs(p) {
  const r = document.documentElement;
  if (p.theme === "light" || p.theme === "dark") r.dataset.theme = p.theme; else delete r.dataset.theme;
  r.dataset.density = p.density === "compact" ? "compact" : p.density === "comfy" ? "comfy" : "comfortable";
  const hex = (ACCENTS.find(a => a[0] === p.accent) || [])[1];
  if (hex && p.accent !== "rosa") { const dark = r.dataset.theme === "dark" || (!r.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    const base = dark ? shade(hex, .25) : hex;
    r.style.setProperty("--rose", base); r.style.setProperty("--rose-deep", dark ? shade(hex, .45) : shade(hex, -.22));
    r.style.setProperty("--rose-soft", `color-mix(in srgb, ${base} ${dark ? 20 : 13}%, var(--surface))`); r.style.setProperty("--rose-glow", `color-mix(in srgb, ${base} 18%, transparent)`); }
  else ["--rose", "--rose-deep", "--rose-soft", "--rose-glow"].forEach(x => r.style.removeProperty(x));
  if (p.font_size && p.font_size !== "md") r.dataset.font = p.font_size; else delete r.dataset.font;
  renderNav();
}
async function savePrefs(patch, quiet) {
  S.prefs = {...S.prefs, ...patch}; applyPrefs(S.prefs);
  if (["quadro", "inicio", "tarefas"].includes(S.view)) render();
  try { const r = await api("/api/ui", toServer(patch)); if (r && r.prefs) { const keep = S.choices; S.prefs = normPrefs({prefs: r.prefs, choices: keep}); applyPrefs(S.prefs); }
    if (!quiet) toast("Preferência salva"); } catch (e) { toast(human(e), "bad"); }
}

/* ---------------------------------------------------------------- navegação */
function renderNav() {
  const order = (S.prefs.sidebar_order || []).map(x => VIEW_ALIAS[x] || x);
  const vs = [...VIEWS].sort((a, b) => { const ia = order.indexOf(a.id), ib = order.indexOf(b.id); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib); });
  const pend = S.status ? S.status.pending : 0;
  $("#nav").innerHTML = vs.map(v => `<button class="navitem" data-v="${v.id}" ${S.view === v.id ? 'aria-current="page"' : ""} data-tip="${v.label}" data-tip-pos="right">${ico(v.icon)}<span>${v.label}</span>${v.id === "mensagens" && pend ? `<span class="badge" aria-label="${pend} pendências">${pend}</span>` : ""}${v.id === "notificacoes" && S.notifUnread ? `<span class="badge soft" aria-label="${S.notifUnread} não lidas">${S.notifUnread > 99 ? "99+" : S.notifUnread}</span>` : ""}</button>`).join("");
  $$("#nav .navitem").forEach(b => b.onclick = () => go(b.dataset.v));
}
function renderRecents() {
  const r = S.recents.slice(0, 12);
  $("#recents").innerHTML = r.length ? r.map((x, i) => `<button class="recent" data-i="${i}" title="${esc(x)}">${esc(x)}</button>`).join("") :
    `<p class="recent" style="cursor:default;opacity:.7">Seus pedidos aparecem aqui.</p>`;
  $$("#recents button").forEach(b => { b.onclick = () => { setInput(r[+b.dataset.i]); };
    b.oncontextmenu = e => { const t = r[+b.dataset.i]; ctxMenu(e, [{value: "use", label: "Colocar no campo", icon: "edit"}, {value: "run", label: "Enviar de novo", icon: "up"},
      {value: "copy", label: "Copiar texto", icon: "list"}, {hr: 1}, {value: "del", label: "Remover dos recentes", icon: "trash", danger: true}, {value: "all", label: "Limpar todos os recentes", icon: "x", danger: true}], v => {
        if (v === "use") setInput(t); if (v === "run") { setInput(t); submit(); } if (v === "copy") copyText(t);
        if (v === "del") { S.recents = S.recents.filter(x => x !== t); store.set("recents", S.recents); renderRecents(); }
        if (v === "all") { S.recents = []; store.set("recents", []); renderRecents(); toast("Recentes limpos"); } }); }; });
}
async function copyText(t, msg = "Copiado") { try { await navigator.clipboard.writeText(t); toast(msg); } catch (e) {
  const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); toast(msg); } catch (x) { toast("Não consegui copiar.", "warn"); } ta.remove(); } }
function pushRecent(t) { S.recents = [t, ...S.recents.filter(x => x !== t)].slice(0, 20); store.set("recents", S.recents); renderRecents(); }
function go(v, push = true) {
  v = VIEW_ALIAS[v] || v; if (!VIEWS.find(x => x.id === v)) v = "inicio";
  if (S.view !== v) clearSel(true); closeFull();
  S.view = v; if (push) history.replaceState(null, "", "#" + v);
  renderNav(); render(); $("#view").scrollTop = 0;
  if ($("#app").classList.contains("expanded")) $("#app").classList.remove("expanded");
}
function render() {
  clearInterval(clockTimer);
  const fn = {inicio: viewHome, quadro: viewBoard, agenda: viewAgenda, tarefas: viewTasks, mensagens: viewMessages, notificacoes: viewNotifs, automacoes: viewAutos}[S.view];
  fn();
  renderSuggest();
}
const viewEl = () => $("#view");

/* ---------------------------------------------------------------- cards (dados) */
async function loadCards(quiet) {
  try { const r = await api("/api/cards"); S.cards = (r && r.cards) || []; S.cardsLoaded = true; }
  catch (e) { S.cardsLoaded = true; if (!quiet) toast(human(e), "bad"); }
}
async function refreshCards() { await loadCards(true); if (["quadro", "agenda", "tarefas", "inicio", "mensagens"].includes(S.view)) render(); }
async function saveCard(id, patch, quiet) {
  const c = S.cards.find(x => x.id === id); if (c) Object.assign(c, patch);
  try { const r = await api("/api/cards/" + encodeURIComponent(id), patch); if (r && r.id) { const i = S.cards.findIndex(x => x.id === id); if (i >= 0) S.cards[i] = r; }
    if (!quiet) toast("Card salvo"); return r; }
  catch (e) { toast(human(e), "bad"); refreshCards(); }
}
async function createCard(data, quiet) {
  try { const r = await api("/api/cards", data); if (r && r.id) S.cards.push(r); if (!quiet) toast("Card criado", "", {action: "Abrir", onAction: () => openCard(r.id)}); return r; }
  catch (e) { toast(human(e), "bad"); }
}
async function moveCard(id, column, order, start) {
  const c = S.cards.find(x => x.id === id); if (!c) return;
  const body = {id, column: column || c.column}; if (order !== undefined) body.order = order; if (start) body.start = start;
  Object.assign(c, {column: body.column}, order !== undefined ? {order} : {}, start ? {start} : {});
  if (body.column === "feito") c.done = true; else if (c.done && column) c.done = false;
  render();
  try { const r = await api("/api/cards/move", body); if (r && r.id) Object.assign(c, r); if (start) toast(`Marcado para ${relDay(parseD(start))} às ${hm(parseD(start))}`); }
  catch (e) { toast(human(e), "bad"); refreshCards(); }
}
async function deleteCard(id) {
  const c = S.cards.find(x => x.id === id);
  try { await api(`/api/cards/${encodeURIComponent(id)}/delete`, {}); S.cards = S.cards.filter(x => x.id !== id); render();
    toast("Card apagado", "", {action: "Desfazer", onAction: async () => { if (c) { const {id: _, ...rest} = c; await createCard(rest); render(); } }}); }
  catch (e) { toast(human(e), "bad"); }
}

/* ---------------------------------------------------------------- filtros */
function filtered(list, F = S.filters, q = S.q) {
  const now = new Date(), t0 = startDay(now);
  const qq = (q || "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  let out = list.filter(c => {
    if (F.kind && F.kind.length && !F.kind.includes(c.kind)) return false;
    if (F.priority && F.priority.length && !F.priority.map(Number).includes(+c.priority || 0)) return false;
    if (F.tag && F.tag.length && !(c.tags || []).some(t => F.tag.includes(t))) return false;
    if (F.person && F.person.length && !(c.people || []).some(p => F.person.includes(p.nome || p.email))) return false;
    if (F.source && F.source.length && !F.source.includes((c.source && c.source.integration) || "local")) return false;
    if (F.column && F.column.length && !F.column.includes(c.column || "entrada")) return false;
    if (F.from || F.to) { const d = cardDate(c); if (!d) return false; if (F.from && d < startDay(parseD(F.from + (F.from.length === 10 ? "T00:00:00" : "")))) return false;
      if (F.to && d >= addDays(startDay(parseD(F.to + (F.to.length === 10 ? "T00:00:00" : ""))), 1)) return false; }
    if (F.done === "ocultar" && c.done) return false; if (F.done === "so" && !c.done) return false;
    if (F.period) { const d = cardDate(c); if (!d) return false;
      if (F.period === "hoje" && !sameDay(d, now)) return false;
      if (F.period === "semana" && (d < weekStart(now) || d >= addDays(weekStart(now), 7))) return false;
      if (F.period === "mes" && (d.getMonth() !== now.getMonth() || d.getFullYear() !== now.getFullYear())) return false;
      if (F.period === "atrasados" && !(d < t0 && !c.done)) return false;
      if (F.period === "proximos" && (d < now || d > addDays(now, 7))) return false; }
    if (qq) { const h = [c.title, c.body, ...(c.tags || []), ...(c.people || []).map(p => p.nome)].join(" ").toLowerCase().normalize("NFD").replace(/\p{M}/gu, ""); if (!h.includes(qq)) return false; }
    return true;
  });
  const by = {manual: (a, b) => (b.pinned - a.pinned) || ((a.order ?? 0) - (b.order ?? 0)),
    data: (a, b) => ((cardDate(a) || 8e15) - (cardDate(b) || 8e15)), prioridade: (a, b) => (b.priority || 0) - (a.priority || 0),
    atualizado: (a, b) => String(b.updated || "").localeCompare(String(a.updated || "")), titulo: (a, b) => String(a.title).localeCompare(String(b.title), "pt")}[S.sort] || (() => 0);
  return out.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) || by(a, b));
}
const FILTER_DEFS = [
  {k: "kind", label: "Tipo", icon: "board", opts: () => Object.entries(KINDS)},
  {k: "priority", label: "Prioridade", icon: "bell", opts: () => PRI.map((l, i) => [String(i), l]).reverse()},
  {k: "tag", label: "Tag", icon: "hash", opts: () => [...new Set(S.cards.flatMap(c => c.tags || []))].sort().map(t => [t, "#" + t])},
  {k: "person", label: "Pessoa", icon: "msg", opts: () => [...new Set(S.cards.flatMap(c => (c.people || []).map(p => p.nome || p.email)))].filter(Boolean).sort().map(p => [p, p])},
  {k: "source", label: "Fonte", icon: "plug", opts: () => [["local", "Djorge (local)"], ...connected().map(i => [i.id, SRC_NAME[i.id] || i.name]), ...[...new Set(S.cards.map(c => c.source && c.source.integration).filter(Boolean))].filter(x => !connected().some(i => i.id === x)).map(x => [x, SRC_NAME[x] || titleCase(x)])]},
  {k: "period", label: "Período", icon: "cal", single: true, range: true, opts: () => [["", "Qualquer data"], ["hoje", "Hoje"], ["proximos", "Próximos 7 dias"], ["semana", "Esta semana"], ["mes", "Este mês"], ["atrasados", "Atrasados"]]},
  {k: "done", label: "Feitos", icon: "check", single: true, opts: () => [["", "Mostrar feitos"], ["ocultar", "Ocultar feitos"], ["so", "Só feitos"]]}];
const SORTS = [["manual", "Ordem manual"], ["data", "Data"], ["prioridade", "Prioridade"], ["atualizado", "Atualizados"], ["titulo", "Título (A–Z)"]];
function filterBarHTML() {
  const F = S.filters;
  const chip = d => { if (d.range && !F.period && (F.from || F.to)) { const f = x => x ? x.slice(8, 10) + "/" + x.slice(5, 7) : "…";
      return `<button type="button" class="chip on" data-f="period" aria-haspopup="menu" aria-expanded="false">${ico(d.icon, "sm")}${f(F.from)} – ${f(F.to)}<span class="x" data-clear="period" role="button" aria-label="Limpar período">✕</span></button>`; }
    const v = F[d.k]; const on = d.single ? !!v : v && v.length;
    const txt = on ? (d.single ? (d.opts().find(o => o[0] === v) || [, v])[1] : d.label + (v.length > 1 ? ` · ${v.length}` : `: ${(d.opts().find(o => String(o[0]) === String(v[0])) || [, v[0]])[1]}`)) : d.label;
    return `<button type="button" class="chip ${on ? "on" : ""}" data-f="${d.k}" aria-haspopup="menu" aria-expanded="false">${ico(d.icon, "sm")}${esc(txt)}${on ? `<span class="x" data-clear="${d.k}" role="button" aria-label="Limpar ${d.label}">✕</span>` : ""}</button>`; };
  const saved = S.prefs.saved_filters || [];
  return `<div class="fbar" role="toolbar" aria-label="Filtros">
    <label class="fsearch">${ico("search", "sm")}<input id="fq" type="search" placeholder="Filtrar cards…" value="${esc(S.q)}" aria-label="Filtrar cards"></label>
    ${FILTER_DEFS.map(chip).join("")}
    <span class="sp"></span>
    <button type="button" class="chip" id="fsort" aria-haspopup="listbox">${ico("sort", "sm")}${esc((SORTS.find(s => s[0] === S.sort) || SORTS[0])[1])}</button>
    ${Object.keys(F).some(k => F[k] && F[k].length) || S.q ? `<button type="button" class="btn sm ghost" id="fsave">Salvar filtro</button><button type="button" class="btn sm ghost" id="fclear">Limpar</button>` : ""}
  </div>${saved.length ? `<div class="saved"><span class="lbl">Salvos</span>${saved.map((s, i) => `<button type="button" class="chip" data-saved="${i}">${ico("filter", "sm")}${esc(s.name || s.nome || "Filtro")}</button>`).join("")}</div>` : ""}`;
}
function wireFilterBar(root, rerender) {
  const q = $("#fq", root);
  if (q) q.oninput = () => { S.q = q.value; rerender(true); };
  $$("[data-f]", root).forEach(b => b.onclick = e => {
    const cl = e.target.closest("[data-clear]"); const d = FILTER_DEFS.find(x => x.k === b.dataset.f);
    if (cl) { delete S.filters[d.k]; if (d.range) { delete S.filters.from; delete S.filters.to; } store.set("filters", S.filters); rerender(); return; }
    const opts = d.opts(); if (!opts.length) { toast(`Nenhum valor de ${d.label.toLowerCase()} nos cards ainda.`); return; }
    const cur = S.filters[d.k];
    if (d.single) menu(b, opts.map(o => ({value: o[0], label: o[1], checked: (cur || "") === o[0]})), v => { if (v) S.filters[d.k] = v; else delete S.filters[d.k]; if (d.range) { delete S.filters.from; delete S.filters.to; } store.set("filters", S.filters); rerender(); }, {role: "listbox", label: d.label});
    else menu(b, opts.map(o => ({value: String(o[0]), label: o[1], checked: (cur || []).map(String).includes(String(o[0]))})), (v, it) => {
      const s = new Set((S.filters[d.k] || []).map(String)); it.checked ? s.add(v) : s.delete(v); S.filters[d.k] = [...s]; if (!s.size) delete S.filters[d.k];
      store.set("filters", S.filters); rerender(false, true); }, {multi: true, label: d.label});
  });
  const so = $("#fsort", root); if (so) so.onclick = () => menu(so, SORTS.map(s => ({value: s[0], label: s[1], checked: s[0] === S.sort})), v => { S.sort = v; store.set("sort", v); rerender(); }, {role: "listbox"});
  const fc = $("#fclear", root); if (fc) fc.onclick = () => { S.filters = {}; S.q = ""; store.set("filters", {}); rerender(); };
  const fs = $("#fsave", root); if (fs) fs.onclick = () => saveFilterDialog();
  $$("[data-saved]", root).forEach(b => b.onclick = () => { const s = (S.prefs.saved_filters || [])[+b.dataset.saved]; if (!s) return;
    S.filters = {...(s.filters || s.filter || {})}; S.q = s.q || ""; if (s.sort) S.sort = s.sort; store.set("filters", S.filters); rerender(); toast(`Filtro “${s.name || s.nome}” aplicado`); });
}
function saveFilterDialog() {
  const m = modal({title: "Salvar filtro", cls: "sm", body: `<div style="padding:4px 24px 8px"><div class="field"><label for="sfn">Nome</label><input class="inp" id="sfn" autofocus placeholder="ex.: urgentes desta semana"></div></div>`,
    foot: `<button class="btn ghost" data-close>Cancelar</button><button class="btn primary" id="sfok">Salvar</button>`});
  const ok = () => { const n = $("#sfn", m.box).value.trim(); if (!n) return; const list = [...(S.prefs.saved_filters || []).filter(x => (x.name || x.nome) !== n), {name: n, filters: {...S.filters}, q: S.q, sort: S.sort}];
    m.close(); savePrefs({saved_filters: list}); };
  $("#sfok", m.box).onclick = ok; $("#sfn", m.box).onkeydown = e => { if (e.key === "Enter") ok(); };
}

/* ---------------------------------------------------------------- card UI */
function initials(n) { return String(n || "?").split(/\s+/).map(x => x[0]).slice(0, 2).join("").toUpperCase(); }
function kcardHTML(c) {
  const ck = c.checklist || [], dn = ck.filter(x => x.done).length;
  const src = c.source && c.source.integration;
  return `<article class="kc ${c.done ? "done" : ""} ${SEL.has(c.id) ? "selc" : ""}" draggable="true" data-id="${esc(c.id)}" ${c.color ? `data-color style="--cc:${esc(c.color)}"` : ""} tabindex="0" aria-label="${esc(c.title)}" aria-roledescription="card arrastável">
    ${c.pinned ? `<span class="pin" aria-label="Fixado">${ico("pin", "sm")}</span>` : ""}
    <div class="kt" data-edit>${esc(cardTitle(c))}</div>
    ${c.body ? `<div class="kb">${esc(c.body)}</div>` : ""}
    <div class="km">
      <span class="tag">${ico(KIND_ICO[c.kind] || "check", "sm")} ${esc(KINDS[c.kind] || c.kind || "")}</span>
      ${cardDate(c) ? `<span class="when ${isLate(c) ? "late" : ""}">${ico("clock", "sm")}${esc(whenTxt(c))}</span>` : ""}
      ${c.priority ? `<span class="pri p${c.priority}"><i></i>${esc(PRI[c.priority])}</span>` : ""}
      ${ck.length ? `<span class="chk">☑ ${dn}/${ck.length}</span>` : ""}
      ${(c.tags || []).slice(0, 3).map(t => `<span class="tag">#${esc(t)}</span>`).join("")}
      ${src ? `<span class="src" title="${esc(src)}">${SRC_EMO[src] || "•"}</span>` : ""}
      ${(c.people || []).length ? `<span class="ppl" style="margin-left:auto">${c.people.slice(0, 3).map(p => `<i data-tip="${esc(titleCase(p.nome || p.email))}">${esc(initials(p.nome || p.email))}</i>`).join("")}</span>` : ""}
    </div></article>`;
}
let dragId = null;
function wireCards(root) {
  $$(".kc, .mini[data-id], .trow[data-id]", root).forEach(el => {
    const id = el.dataset.id;
    if (el.draggable || el.classList.contains("mini")) {
      el.draggable = true;
      el.addEventListener("dragstart", e => { dragId = id; e.dataTransfer.setData("text/plain", id); e.dataTransfer.effectAllowed = "move"; requestAnimationFrame(() => el.classList.add("dragging")); });
      el.addEventListener("dragend", () => { el.classList.remove("dragging"); dragId = null; });
    }
    el.addEventListener("click", e => { if (e.target.closest("[data-edit]") && el.classList.contains("kc") && e.detail === 2) return; if (e.target.closest(".cbx")) return;
      if (e.ctrlKey || e.metaKey || e.shiftKey || SEL.size) { e.preventDefault(); selClick(id, e, root); return; } openCard(id); });
    el.addEventListener("contextmenu", e => cardCtx(e, id));
    el.addEventListener("keydown", e => {
      if (e.key === "Enter" && e.target === el) { e.preventDefault(); openCard(id); }
      if (el.classList.contains("kc") && e.target === el && (e.key === "ArrowLeft" || e.key === "ArrowRight") && e.altKey) {
        const c = S.cards.find(x => x.id === id), cols = visibleCols(), i = cols.findIndex(x => x.id === c.column);
        const n = cols[i + (e.key === "ArrowRight" ? 1 : -1)]; if (n) { moveCard(id, n.id); setTimeout(() => { const x = $(`.kc[data-id="${CSS.escape(id)}"]`); x && x.focus(); }, 50); } }
    });
    const t = el.querySelector(".kt[data-edit]");
    if (t) t.addEventListener("dblclick", e => { e.stopPropagation(); t.contentEditable = "true"; t.focus(); document.getSelection().selectAllChildren(t);
      const done = save => { t.contentEditable = "false"; const v = t.textContent.trim(); const c = S.cards.find(x => x.id === id);
        if (save && v && c && v !== c.title) saveCard(id, {title: v}, true); else if (c) t.textContent = c.title; };
      t.onkeydown = ev => { if (ev.key === "Enter") { ev.preventDefault(); done(true); } if (ev.key === "Escape") { ev.stopPropagation(); done(false); } };
      t.onblur = () => done(true); });
  });
  $$(".cbx[data-id]", root).forEach(b => b.onclick = e => { e.stopPropagation(); const c = S.cards.find(x => x.id === b.dataset.id); if (!c) return;
    const done = !c.done; b.setAttribute("aria-checked", done); setTimeout(() => saveCard(c.id, {done, column: done ? "feito" : (c.column === "feito" ? "hoje" : c.column)}, true).then(render), 250); });
}
/* ---------------------------------------------------------------- seleção múltipla + menus de cards */
const SEL = new Set(); let selLast = null;
const cardTitle = c => { const t = c.titulo || c.title || "Sem título"; return c.kind === "mensagem" || /^[a-zà-ú]/.test(t) ? t.charAt(0).toLocaleUpperCase("pt-BR") + t.slice(1) : t; };
function selClick(id, e, root) {
  if (e.shiftKey && selLast) { const ids = $$("[data-id].kc,[data-id].trow", root || viewEl()).map(x => x.dataset.id); const a = ids.indexOf(selLast), b = ids.indexOf(id);
    if (a >= 0 && b >= 0) ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => SEL.add(x)); }
  else if (SEL.has(id)) SEL.delete(id); else SEL.add(id);
  selLast = id; paintSel();
}
function paintSel() {
  $$("[data-id].kc,[data-id].trow").forEach(x => x.classList.toggle("selc", SEL.has(x.dataset.id)));
  let bar = $("#selbar");
  if (!SEL.size) { if (bar) bar.remove(); return; }
  if (!bar) { bar = document.createElement("div"); bar.id = "selbar"; bar.className = "selbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Ações nos selecionados"); $(".main").appendChild(bar); }
  bar.innerHTML = `<b>${SEL.size} selecionado${SEL.size > 1 ? "s" : ""}</b><button class="btn sm" data-sa="move">${ico("board", "sm")} Mover para…</button><button class="btn sm" data-sa="done">${ico("check", "sm")} Marcar feito</button>
    <button class="btn sm" data-sa="pri">${ico("bell", "sm")} Prioridade</button><button class="btn sm danger" data-sa="del">${ico("trash", "sm")} Apagar</button><span class="sp"></span><button class="btn sm ghost" data-sa="clear" data-tip="Esc">Limpar seleção</button>`;
  $$("[data-sa]", bar).forEach(b => b.onclick = () => { const a = b.dataset.sa;
    if (a === "clear") return clearSel(); if (a === "del") return bulkDelete([...SEL]); if (a === "done") return bulkPatch([...SEL], {done: true, column: "feito"}, "Marcados como feitos");
    if (a === "move") menu(b, COLS.map(c => ({value: c.id, label: c.label, dot: c.c})), v => bulkMove([...SEL], v));
    if (a === "pri") menu(b, PRI.map((l, i) => ({value: String(i), label: l})).reverse(), v => bulkPatch([...SEL], {priority: +v}, "Prioridade ajustada")); });
}
function clearSel(silent) { SEL.clear(); selLast = null; if (!silent) paintSel(); else { const b = $("#selbar"); if (b) b.remove(); } }
async function bulkPatch(ids, patch, msg) { await Promise.all(ids.map(id => saveCard(id, {...patch}, true))); clearSel(); render(); toast(msg || "Atualizado"); }
async function bulkMove(ids, col) { for (const id of ids) await moveCard(id, col); clearSel(); render(); toast(`Movido${ids.length > 1 ? "s" : ""} para ${(COLS.find(c => c.id === col) || {}).label}`); }
async function bulkDelete(ids) {
  const back = ids.map(id => S.cards.find(c => c.id === id)).filter(Boolean);
  await Promise.all(ids.map(id => api(`/api/cards/${encodeURIComponent(id)}/delete`, {}).catch(() => null)));
  S.cards = S.cards.filter(c => !ids.includes(c.id)); clearSel(); render();
  toast(`${ids.length} apagado${ids.length > 1 ? "s" : ""}`, "", {action: "Desfazer", onAction: async () => { for (const c of back) { const {id: _, ...rest} = c; await createCard(rest, true); } render(); }});
}
function cardCtx(e, id) {
  const c = S.cards.find(x => x.id === id); if (!c) return;
  const many = SEL.size > 1 && SEL.has(id), ids = many ? [...SEL] : [id];
  const items = many ? [{group: `${ids.length} selecionados`}] : [{value: "open", label: "Abrir", icon: "edit", kbd: "Enter"}, {value: "dup", label: "Duplicar", icon: "plus", kbd: "Ctrl D"}, {value: "sel", label: "Selecionar", icon: "check"}];
  items.push({hr: 1}, {group: "Mover para"}, ...COLS.filter(x => many || x.id !== c.column).map(x => ({value: "mv:" + x.id, label: x.label, dot: x.c})),
    {hr: 1}, {value: "done", label: !many && c.done ? "Marcar como não feito" : "Marcar feito", icon: "check"},
    {group: "Prioridade"}, ...PRI.map((l, i) => ({value: "p:" + i, label: l, checked: many ? undefined : (+c.priority || 0) === i})).reverse());
  if (!many) items.push({hr: 1}, {value: "pin", label: c.pinned ? "Desafixar" : "Fixar no topo", icon: "pin"}, {value: "today", label: "Para hoje", icon: "cal"}, {value: "tom", label: "Para amanhã", icon: "clock"}, {value: "copy", label: "Copiar título", icon: "list"});
  items.push({hr: 1}, {value: "del", label: many ? `Apagar ${ids.length}` : "Apagar", icon: "trash", danger: true, kbd: "Del"});
  ctxMenu(e, items, async v => {
    if (v === "open") openCard(id); else if (v === "sel") { SEL.add(id); paintSel(); }
    else if (v === "dup") dupCard(id);
    else if (v.startsWith("mv:")) many ? bulkMove(ids, v.slice(3)) : moveCard(id, v.slice(3));
    else if (v === "done") many ? bulkPatch(ids, {done: true, column: "feito"}, "Marcados como feitos") : saveCard(id, {done: !c.done, column: c.done ? "hoje" : "feito"}, true).then(render);
    else if (v.startsWith("p:")) many ? bulkPatch(ids, {priority: +v.slice(2)}, "Prioridade ajustada") : saveCard(id, {priority: +v.slice(2)}, true).then(render);
    else if (v === "pin") saveCard(id, {pinned: !c.pinned}, true).then(render);
    else if (v === "today" || v === "tom") { const d = addDays(startDay(new Date()), v === "tom" ? 1 : 0); const old = cardDate(c); d.setHours(old ? old.getHours() : 9, old ? old.getMinutes() : 0);
      saveCard(id, {[c.due && !c.start ? "due" : "start"]: isoLocal(d), column: c.column === "entrada" ? "hoje" : c.column}, true).then(() => { render(); toast(`Marcado para ${fmtDT(d, {rel: true})}`); }); }
    else if (v === "copy") copyText(cardTitle(c));
    else if (v === "del") many ? bulkDelete(ids) : deleteCard(id);
  });
}
async function dupCard(id) { const c = S.cards.find(x => x.id === id); if (!c) return; const {id: _, created, updated, source, ...rest} = c; const n = await createCard({...rest, title: cardTitle(c) + " (cópia)", order: (c.order || 0) + .5}); if (n) render(); }
function dropZone(el, onDrop) {
  el.addEventListener("dragover", e => { if (!dragId) return; e.preventDefault(); e.dataTransfer.dropEffect = "move"; el.classList.add("over"); });
  el.addEventListener("dragleave", e => { if (!el.contains(e.relatedTarget)) el.classList.remove("over"); });
  el.addEventListener("drop", e => { e.preventDefault(); el.classList.remove("over"); const id = dragId || e.dataTransfer.getData("text/plain"); if (id) onDrop(id, e); });
}
const visibleCols = () => COLS.filter(c => (S.prefs.columns || COLS.map(x => x.id)).includes(c.id));

/* ---------------------------------------------------------------- painel lateral do card */
let drawerCard = null;
function openCard(id, draft) {
  const c = draft || S.cards.find(x => x.id === id); if (!c) return;
  drawerCard = JSON.parse(JSON.stringify(c));
  const d = $("#drawer"), x = drawerCard;
  const sd = parseD(x.start), dd = parseD(x.due);
  d.innerHTML = `<header><span class="kicker">${draft ? "Novo card" : "Card"}</span><span class="sp"></span>
    <button class="iconbtn" id="dr-pin" aria-pressed="${!!x.pinned}" aria-label="Fixar" data-tip="Fixar">${ico("pin")}</button>
    ${draft ? "" : `<button class="iconbtn" id="dr-del" aria-label="Apagar" data-tip="Apagar">${ico("trash")}</button>`}
    <button class="iconbtn" id="dr-x" aria-label="Fechar painel">${ico("x")}</button></header>
  <div class="body">
    <label class="sr" for="dr-title">Título</label><input class="ttl" id="dr-title" value="${esc(x.title || "")}" placeholder="Título do card">
    <div class="field" style="margin-top:12px"><label for="dr-body">Descrição</label><textarea class="ta" id="dr-body" rows="3" placeholder="Detalhes, links, observações…">${esc(x.body || "")}</textarea></div>
    <div class="grid2"><div class="field"><span class="lbl">Tipo</span>${selHTML("kind", Object.entries(KINDS), x.kind || "tarefa", "Tipo")}</div>
      <div class="field"><span class="lbl">Coluna</span>${selHTML("col", COLS.map(c => [c.id, c.label]), x.column || "entrada", "Coluna")}</div></div>
    <div class="field"><span class="lbl">Início</span><div class="dt"><input type="date" id="dr-sd" value="${sd ? ymd(sd) : ""}" aria-label="Data de início"><input type="time" id="dr-st" value="${sd && !x.all_day ? hm(sd) : ""}" aria-label="Hora de início">${swHTML("dr-allday", x.all_day, "Dia todo")}</div>
      <div class="qdates" data-for="s"><button type="button" data-d="0">Hoje</button><button type="button" data-d="1">Amanhã</button><button type="button" data-d="w">Próx. segunda</button><button type="button" data-d="x">Limpar</button></div></div>
    <div class="field"><span class="lbl">Prazo</span><div class="dt"><input type="date" id="dr-dd" value="${dd ? ymd(dd) : ""}" aria-label="Data do prazo"><input type="time" id="dr-dt" value="${dd && (dd.getHours() || dd.getMinutes()) ? hm(dd) : ""}" aria-label="Hora do prazo"></div>
      <div class="qdates" data-for="d"><button type="button" data-d="0">Hoje</button><button type="button" data-d="1">Amanhã</button><button type="button" data-d="7">Em 1 semana</button><button type="button" data-d="x">Limpar</button></div></div>
    <div class="field"><span class="lbl">Prioridade</span>${segHTML("pri", PRI.map((l, i) => [i, i ? l : "Nenhuma"]), x.priority || 0, "Prioridade")}</div>
    <div class="field"><label for="dr-tagi">Tags</label><div class="tagin" id="dr-tags"></div></div>
    <div class="field"><label for="dr-ppli">Pessoas</label><div class="tagin" id="dr-ppl"></div></div>
    <div class="field"><span class="lbl">Checklist</span><div class="cklist" id="dr-ck"></div><button type="button" class="addc" id="dr-cka">${ico("plus", "sm")} Adicionar item</button></div>
    <div class="field"><label for="dr-rec">Repetição</label><input class="inp" id="dr-rec" value="${esc(x.recurrence || "")}" placeholder="ex.: toda segunda"></div>
    <div class="field"><span class="lbl">Cor</span><div class="colors" role="radiogroup" aria-label="Cor">${CARD_COLORS.map(([c, n]) => `<button type="button" role="radio" class="${c ? "" : "none"}" style="--c:${c || "transparent"}" aria-checked="${(x.color || "") === c}" aria-label="${n}" data-c="${c}" data-tip="${n}"></button>`).join("")}</div></div>
    ${x.source && x.source.integration ? `<p class="hint">Veio de ${esc(x.source.integration)}${x.source.url ? ` · <a href="${esc(x.source.url)}" target="_blank" rel="noopener">abrir original</a>` : ""}</p>` : ""}
    ${x.updated ? `<p class="meta">atualizado ${esc(String(x.updated).replace("T", " ").slice(0, 16))}</p>` : ""}
  </div>
  <footer><button class="btn ghost" id="dr-cancel">Cancelar</button><span style="flex:1"></span><button class="btn primary" id="dr-save">${draft ? "Criar card" : "Salvar"}</button></footer>`;
  d.classList.add("on"); d.setAttribute("aria-hidden", "false");
  wireSel(d, "kind", Object.entries(KINDS)); wireSel(d, "col", COLS.map(c => [c.id, c.label])); wireSeg(d, "pri");
  tagInput($("#dr-tags", d), x.tags || [], "dr-tagi", "nova tag", v => x.tags = v);
  tagInput($("#dr-ppl", d), (x.people || []).map(p => p.nome || p.email), "dr-ppli", "nome ou e-mail", v => x.people = v.map(n => (x.people || []).find(p => (p.nome || p.email) === n) || (n.includes("@") ? {nome: n, email: n, tipo: "email"} : {nome: n, tipo: "pessoa"})));
  const ckr = () => { $("#dr-ck", d).innerHTML = (x.checklist || []).map((it, i) => `<div class="ckrow ${it.done ? "done" : ""}"><button type="button" class="cbx" role="checkbox" aria-checked="${!!it.done}" data-i="${i}" aria-label="Concluir item">${ico("check")}</button><input class="inp" data-i="${i}" value="${esc(it.text)}" aria-label="Item ${i + 1}"><button type="button" class="iconbtn" data-rm="${i}" aria-label="Remover item">${ico("x", "sm")}</button></div>`).join("");
    $$("#dr-ck .cbx", d).forEach(b => b.onclick = () => { const it = x.checklist[+b.dataset.i]; it.done = !it.done; ckr(); });
    $$("#dr-ck input", d).forEach(i => { i.oninput = () => x.checklist[+i.dataset.i].text = i.value; i.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("#dr-cka", d).click(); } }; });
    $$("#dr-ck [data-rm]", d).forEach(b => b.onclick = () => { x.checklist.splice(+b.dataset.rm, 1); ckr(); }); };
  ckr();
  $("#dr-cka", d).onclick = () => { (x.checklist = x.checklist || []).push({text: "", done: false}); ckr(); const all = $$("#dr-ck input", d); all[all.length - 1].focus(); };
  $$(".colors button", d).forEach(b => b.onclick = () => { x.color = b.dataset.c; $$(".colors button", d).forEach(z => z.setAttribute("aria-checked", z === b)); });
  $("#dr-pin", d).onclick = e => { x.pinned = !x.pinned; e.currentTarget.setAttribute("aria-pressed", x.pinned); };
  $$(".qdates", d).forEach(g => g.onclick = e => { const b = e.target.closest("button"); if (!b) return; const di = g.dataset.for === "s" ? "#dr-sd" : "#dr-dd";
    let t = startDay(new Date()); if (b.dataset.d === "x") { $(di, d).value = ""; $(g.dataset.for === "s" ? "#dr-st" : "#dr-dt", d).value = ""; return; }
    if (b.dataset.d === "w") t = addDays(t, ((8 - t.getDay()) % 7) || 7); else t = addDays(t, +b.dataset.d); $(di, d).value = ymd(t); });
  const close = () => { d.classList.remove("on"); d.setAttribute("aria-hidden", "true"); drawerCard = null; };
  $("#dr-x", d).onclick = close; $("#dr-cancel", d).onclick = close;
  if ($("#dr-del", d)) $("#dr-del", d).onclick = () => { close(); deleteCard(x.id); };
  $("#dr-save", d).onclick = async () => {
    const sdv = $("#dr-sd", d).value, stv = $("#dr-st", d).value, ddv = $("#dr-dd", d).value, dtv = $("#dr-dt", d).value, allday = $("#dr-allday", d).checked;
    const patch = {title: $("#dr-title", d).value.trim() || "Sem título", body: $("#dr-body", d).value, kind: selVal(d, "kind"), column: selVal(d, "col"),
      priority: +segVal(d, "pri") || 0, tags: x.tags || [], people: x.people || [], checklist: (x.checklist || []).filter(i => i.text.trim()),
      recurrence: $("#dr-rec", d).value.trim(), color: x.color || "", pinned: !!x.pinned, all_day: allday,
      start: sdv ? `${sdv}T${allday || !stv ? "00:00" : stv}:00` : null, due: ddv ? `${ddv}T${dtv || "00:00"}:00` : null};
    patch.done = patch.column === "feito";
    close();
    if (draft) await createCard(patch); else await saveCard(x.id, patch);
    render();
  };
  d.onkeydown = e => { if (e.key === "Escape" && !openMenu) { e.stopPropagation(); close(); } if (e.key === "Enter" && e.ctrlKey) $("#dr-save", d).click(); };
  setTimeout(() => $("#dr-title", d).focus(), 60);
}
function tagInput(box, vals, id, ph, onChange) {
  const draw = () => { box.innerHTML = vals.map((v, i) => `<span class="chip on">${esc(v)}<button type="button" class="x" data-i="${i}" aria-label="Remover ${esc(v)}">✕</button></span>`).join("") + `<input id="${id}" placeholder="${esc(ph)}">`;
    const inp = $("input", box);
    inp.onkeydown = e => { if ((e.key === "Enter" || e.key === ",") && inp.value.trim()) { e.preventDefault(); vals.push(inp.value.trim().replace(/^#/, "")); onChange(vals); draw(); $("input", box).focus(); }
      else if (e.key === "Backspace" && !inp.value && vals.length) { vals.pop(); onChange(vals); draw(); $("input", box).focus(); } };
    $$("[data-i]", box).forEach(b => b.onclick = () => { vals.splice(+b.dataset.i, 1); onChange(vals); draw(); }); };
  vals = [...vals]; draw();
}
function newCard(column = "entrada", extra = {}) { openCard(null, {title: "", kind: "tarefa", column, priority: 0, tags: [], people: [], checklist: [], ...extra}); }

/* ---------------------------------------------------------------- estados vazios */
const ART = `<svg class="art" viewBox="0 0 120 90" aria-hidden="true"><g fill="none" stroke="var(--rose)" stroke-width="2" stroke-linecap="round">
  <path d="M60 78V44" stroke="var(--ok)"/><path d="M60 60c-8-2-14-8-14-15 7 0 13 5 14 15zM60 54c8-2 14-8 14-15-7 0-13 5-14 15z" stroke="var(--ok)" fill="var(--ok-soft)"/>
  <circle cx="60" cy="30" r="6" fill="var(--warn-soft)" stroke="var(--warn)"/>${[0, 72, 144, 216, 288].map(r => `<ellipse cx="60" cy="17" rx="6" ry="9" transform="rotate(${r} 60 30)" fill="var(--rose-soft)"/>`).join("")}
  <path d="M20 80h80" stroke="var(--line)"/></g></svg>`;
const empty = (t, p, btn) => `<div class="empty">${ART}<h3>${esc(t)}</h3><p>${esc(p)}</p>${btn || ""}</div>`;

/* ---------------------------------------------------------------- INÍCIO */
function greet() { const h = new Date().getHours(); return h < 5 ? "Boa madrugada" : h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite"; }
/* ----- relógios ----- */
const ZONES = [["America/Sao_Paulo", "São Paulo"], ["America/Manaus", "Manaus"], ["America/Fortaleza", "Fortaleza"], ["America/Rio_Branco", "Rio Branco"], ["America/Noronha", "Fernando de Noronha"],
  ["America/New_York", "Nova York"], ["America/Chicago", "Chicago"], ["America/Los_Angeles", "Los Angeles"], ["America/Mexico_City", "Cidade do México"], ["America/Argentina/Buenos_Aires", "Buenos Aires"],
  ["America/Santiago", "Santiago"], ["America/Bogota", "Bogotá"], ["Europe/Lisbon", "Lisboa"], ["Europe/London", "Londres"], ["Europe/Madrid", "Madri"], ["Europe/Paris", "Paris"], ["Europe/Berlin", "Berlim"],
  ["Europe/Rome", "Roma"], ["Africa/Luanda", "Luanda"], ["Africa/Maputo", "Maputo"], ["Asia/Dubai", "Dubai"], ["Asia/Kolkata", "Índia"], ["Asia/Shanghai", "Xangai"], ["Asia/Tokyo", "Tóquio"],
  ["Australia/Sydney", "Sydney"], ["UTC", "UTC"]];
const LOCAL_TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Sao_Paulo"; } catch (e) { return "America/Sao_Paulo"; } })();
let clockTimer = null;
function tzTime(tz, d = new Date()) { try { return d.toLocaleTimeString("pt-BR", {timeZone: tz, hour: "2-digit", minute: "2-digit"}); } catch (e) { return hm(d); } }
function tzOffsetMin(tz, d = new Date()) { try { const p = new Intl.DateTimeFormat("en-US", {timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).formatToParts(d).reduce((o, x) => (o[x.type] = x.value, o), {});
  return Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute) - d.getTime()) / 6e4); } catch (e) { return 0; } }
function tzDiff(tz) { const m = tzOffsetMin(tz) + new Date().getTimezoneOffset(); if (Math.abs(m) < 1) return "mesmo horário daqui";
  const h = Math.abs(m) / 60, t = Number.isInteger(h) ? `${h} h` : `${h.toFixed(1).replace(".", ",")} h`; return m > 0 ? `${t} à frente` : `${t} atrás`; }
const zoneName = tz => (ZONES.find(z => z[0] === tz) || [, tz.split("/").pop().replace(/_/g, " ")])[1];
function clockHTML() {
  const now = new Date(), tz2 = store.get("tz2", LOCAL_TZ === "Europe/Lisbon" ? "America/Sao_Paulo" : "Europe/Lisbon");
  return `<section class="dcard dclock" aria-label="Relógios"><header><span class="kicker">Agora</span><span class="sp"></span><span class="hint">${esc(zoneName(LOCAL_TZ))}</span></header>
    <div class="bigclock" id="clk1" aria-live="off">${esc(hm(now))}<small>${pad(now.getSeconds())}</small></div>
    <p class="dsub">${esc(titleCase(now.toLocaleDateString("pt-BR", {weekday: "long"})))}, ${now.getDate()} de ${MONTHS[now.getMonth()]}</p>
    <div class="clock2"><button type="button" class="zbtn" id="tz2" aria-haspopup="listbox" data-tip="Escolher outra região">${ico("clock", "sm")}<span>${esc(zoneName(tz2))}</span>${ico("down", "sm")}</button>
      <b id="clk2">${esc(tzTime(tz2))}</b><small id="clk2d">${esc(tzDiff(tz2))}</small></div></section>`;
}
function wireClock(v) {
  const z = $("#tz2", v); if (z) z.onclick = () => menu(z, ZONES.map(([id, n]) => ({value: id, label: `${n} · ${tzTime(id)}`, checked: id === store.get("tz2")})), id => { store.set("tz2", id); $("span", z).textContent = zoneName(id); tick(); }, {role: "listbox", label: "Região"});
  const tick = () => { const n = new Date(), a = $("#clk1"), b = $("#clk2"), c = $("#clk2d"); if (!a) return clearInterval(clockTimer);
    a.innerHTML = `${esc(hm(n))}<small>${pad(n.getSeconds())}</small>`; const tz2 = store.get("tz2", "Europe/Lisbon"); if (b) b.textContent = tzTime(tz2); if (c) c.textContent = tzDiff(tz2); };
  clearInterval(clockTimer); clockTimer = setInterval(tick, 1000);
}
/* ----- clima (Open-Meteo, sem chave; tudo com cache e falha silenciosa) ----- */
const WMO = {0: ["Céu limpo", "☀️"], 1: ["Quase limpo", "🌤️"], 2: ["Parcialmente nublado", "⛅"], 3: ["Nublado", "☁️"], 45: ["Neblina", "🌫️"], 48: ["Neblina", "🌫️"],
  51: ["Garoa fraca", "🌦️"], 53: ["Garoa", "🌦️"], 55: ["Garoa forte", "🌧️"], 56: ["Garoa gelada", "🌧️"], 57: ["Garoa gelada", "🌧️"], 61: ["Chuva fraca", "🌦️"], 63: ["Chuva", "🌧️"], 65: ["Chuva forte", "🌧️"],
  66: ["Chuva gelada", "🌧️"], 67: ["Chuva gelada", "🌧️"], 71: ["Neve fraca", "🌨️"], 73: ["Neve", "🌨️"], 75: ["Neve forte", "❄️"], 77: ["Granizo fino", "🌨️"], 80: ["Pancadas de chuva", "🌦️"],
  81: ["Pancadas de chuva", "🌧️"], 82: ["Temporal", "⛈️"], 85: ["Neve", "🌨️"], 86: ["Neve forte", "❄️"], 95: ["Trovoadas", "⛈️"], 96: ["Trovoadas com granizo", "⛈️"], 99: ["Trovoadas com granizo", "⛈️"]};
const TZ_PLACE = {"America/Sao_Paulo": [-23.55, -46.63, "São Paulo"], "America/Manaus": [-3.12, -60.02, "Manaus"], "America/Fortaleza": [-3.73, -38.52, "Fortaleza"], "America/Recife": [-8.05, -34.9, "Recife"],
  "America/Bahia": [-12.97, -38.5, "Salvador"], "America/Belem": [-1.46, -48.5, "Belém"], "America/Cuiaba": [-15.6, -56.1, "Cuiabá"], "America/Campo_Grande": [-20.47, -54.62, "Campo Grande"],
  "America/Porto_Velho": [-8.76, -63.9, "Porto Velho"], "America/Rio_Branco": [-9.97, -67.81, "Rio Branco"], "America/Maceio": [-9.65, -35.73, "Maceió"], "America/Araguaina": [-7.19, -48.2, "Araguaína"],
  "America/Boa_Vista": [2.82, -60.67, "Boa Vista"], "America/Noronha": [-3.85, -32.42, "Fernando de Noronha"], "Europe/Lisbon": [38.72, -9.14, "Lisboa"], "America/New_York": [40.71, -74.0, "Nova York"],
  "Europe/London": [51.5, -0.12, "Londres"], "Europe/Paris": [48.85, 2.35, "Paris"], "Europe/Madrid": [40.42, -3.7, "Madri"], "Africa/Luanda": [-8.84, 13.23, "Luanda"], "Africa/Maputo": [-25.97, 32.57, "Maputo"]};
async function fetchJSON(url, ms = 7000) { const c = new AbortController(); const t = setTimeout(() => c.abort(), ms); try { const r = await fetch(url, {signal: c.signal}); if (!r.ok) throw new Error(r.status); return await r.json(); } finally { clearTimeout(t); } }
function geoPos() { return new Promise(res => { if (!navigator.geolocation) return res(null); let done = false; const fin = v => { if (!done) { done = true; res(v); } };
  setTimeout(() => fin(null), 6500); try { navigator.geolocation.getCurrentPosition(p => fin({lat: p.coords.latitude, lon: p.coords.longitude}), () => fin(null), {timeout: 6000, maximumAge: 36e5}); } catch (e) { fin(null); } }); }
async function getPlace() {
  const c = store.get("place", null); if (c && Date.now() - c.t < 864e5) return c;
  let p = await geoPos(), city = "";
  if (p) { try { const g = await fetchJSON(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${p.lat}&longitude=${p.lon}&localityLanguage=pt`);
    city = g.city || g.locality || g.principalSubdivision || ""; } catch (e) {} }
  else { const f = TZ_PLACE[LOCAL_TZ]; if (!f) return null; p = {lat: f[0], lon: f[1]}; city = f[2]; }
  const out = {lat: +(+p.lat).toFixed(3), lon: +(+p.lon).toFixed(3), city, t: Date.now(), approx: !city}; store.set("place", out); return out;
}
async function getWeather(force) {
  const c = store.get("wx", null); if (!force && c && Date.now() - c.t < 30 * 6e4) return c;
  const pl = await getPlace(); if (!pl) return c || null;
  try { const d = await fetchJSON(`https://api.open-meteo.com/v1/forecast?latitude=${pl.lat}&longitude=${pl.lon}&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=5`);
    const out = {t: Date.now(), city: pl.city, d}; store.set("wx", out); return out; } catch (e) { return c || null; }
}
function weatherHTML(w) {
  if (!w || !w.d || !w.d.current) return `<section class="dcard dwx" aria-label="Clima"><header><span class="kicker">Clima</span></header><div class="wxnone">${w === undefined ? `<div class="sk" style="height:96px"></div>` : `<p class="hint">Clima indisponível agora. Tento de novo mais tarde.</p>`}</div></section>`;
  const cu = w.d.current, dy = w.d.daily || {}, [desc, em] = WMO[cu.weather_code] || ["—", "🌡️"];
  const days = (dy.time || []).slice(0, 5).map((t, i) => { const d = new Date(t + "T12:00:00"); const [dd, ee] = WMO[dy.weather_code[i]] || ["", "🌡️"];
    return `<div class="wday" data-tip="${esc(dd)}${dy.precipitation_probability_max ? ` · chuva ${dy.precipitation_probability_max[i] ?? 0}%` : ""}"><small>${i === 0 ? "hoje" : WD[d.getDay()]}</small><span class="we">${ee}</span><b>${Math.round(dy.temperature_2m_max[i])}°</b><small>${Math.round(dy.temperature_2m_min[i])}°</small></div>`; }).join("");
  return `<section class="dcard dwx" aria-label="Clima"><header><span class="kicker">Clima</span><span class="sp"></span><span class="hint">${esc(w.city || "sua região")}</span>
    <button class="iconbtn sm" id="wxr" aria-label="Atualizar clima" data-tip="Atualizar">${ico("sync", "sm")}</button></header>
    <div class="wxnow"><span class="we big" aria-hidden="true">${cu.is_day === 0 && cu.weather_code <= 1 ? "🌙" : em}</span><div><div class="temp">${Math.round(cu.temperature_2m)}°</div><p class="dsub">${esc(desc)}</p></div>
      <dl class="wxkv"><div><dt>Sensação</dt><dd>${Math.round(cu.apparent_temperature)}°</dd></div><div><dt>Umidade</dt><dd>${Math.round(cu.relative_humidity_2m)}%</dd></div><div><dt>Vento</dt><dd>${Math.round(cu.wind_speed_10m)} km/h</dd></div></dl></div>
    <div class="wdays">${days}</div></section>`;
}
/* ----- dados do Início ----- */
async function loadInicio() {
  try { S.inicio = await api("/api/inicio"); S.inicioOk = true; } catch (e) { S.inicioOk = false; }
  return S.inicio;
}
function localInicio() {
  const now = new Date(), act = S.cards.filter(c => !c.done);
  const score = c => (isLate(c) ? 10 : 0) + (c.priority || 0) * 2 + (cardDate(c) && sameDay(cardDate(c), now) ? 4 : 0) + (c.column === "hoje" ? 3 : 0) + (c.pinned ? 2 : 0);
  return {tarefas_relevantes: act.filter(c => c.kind !== "mensagem" && c.kind !== "evento").sort((a, b) => score(b) - score(a)).slice(0, 5),
    proximos_eventos: act.filter(c => { const d = cardDate(c); return d && d >= startDay(now) && (c.kind === "evento" || c.kind === "lembrete"); }).sort((a, b) => cardDate(a) - cardDate(b)).slice(0, 5),
    mensagens_importantes: act.filter(c => c.kind === "mensagem").sort((a, b) => (b.priority || 0) - (a.priority || 0)).slice(0, 4), notificacoes_recentes: []};
}
const byCardId = x => (x && x.id && S.cards.find(c => c.id === x.id)) || x;
function evRowHTML(c) { c = byCardId(c); const d = cardDate(c) || parseD(c.start || c.quando);
  return `<div class="erow" ${c.id ? `data-id="${esc(c.id)}" data-ctx="card"` : ""} tabindex="0" role="button"><span class="edate">${d ? `<b>${d.getDate()}</b><small>${MONTHS[d.getMonth()].slice(0, 3)}</small>` : "—"}</span>
    <span class="tt"><b>${esc(c.titulo || cardTitle(c))}</b><small>${esc(c.quando_fmt || (d ? fmtDT(d, {rel: true}) : ""))}${(c.people || []).length ? " · " + esc(c.people.map(p => titleCase(p.nome || p.email)).join(", ")) : ""}</small></span></div>`; }
function msgPerson(m) { return titleCase(m.remetente || m.de || (m.people && m.people[0] && (m.people[0].nome || m.people[0].email)) || (m.source && m.source.integration) || "Alguém"); }
function msgRowHTML(m) {
  m = byCardId(m); const who = msgPerson(m), d = parseD(m.t || m.data || m.updated || m.created), src = (m.source && m.source.integration) || m.canal || m.app || "";
  return `<div class="mrow ${m.done ? "done" : ""} ${m.lida === false || (!m.done && (m.priority || 0) >= 3) ? "unread" : ""}" ${m.id ? `data-id="${esc(m.id)}" data-ctx="msg"` : ""} tabindex="0" role="button" aria-label="${esc(who)}: ${esc(m.titulo || m.title || "")}">
    <span class="av" aria-hidden="true">${esc(initials(who))}</span>
    <span class="mbody"><span class="mtop"><b>${esc(who)}</b>${src ? `<span class="tag">${esc(SRC_NAME[src] || titleCase(src))}</span>` : ""}${m.priority >= 2 ? `<span class="pri p${m.priority}"><i></i>${esc(PRI[m.priority])}</span>` : ""}<time>${esc(d ? ago(d) : "")}</time></span>
      <span class="msubj">${esc(m.titulo || cardTitle(m))}</span>${m.body || m.texto || m.trecho ? `<span class="mtext">${esc(String(m.body || m.texto || m.trecho).slice(0, 180))}</span>` : ""}</span></div>`;
}
const SRC_NAME = {google: "Gmail", microsoft: "Outlook", whatsapp: "WhatsApp", gemini: "Gemini", local: "Djorge", notifications: "Notificação"};
function notifRowHTML(n, compact) {
  const app = titleCase(n.app || n.fonte || "Windows"), d = parseD(n.t || n.data || n.recebida || n.created), sug = asList(n.acoes_sugeridas || n.sugestoes), done = asList(n.acoes_tomadas || n.acoes);
  return `<div class="nrow ${n.lida ? "" : "unread"} ${NSEL.has(String(n.id)) ? "selc" : ""}" data-nid="${esc(n.id)}" data-ctx="notif" tabindex="0">
    ${compact ? "" : `<button type="button" class="cbx" role="checkbox" aria-checked="${NSEL.has(String(n.id))}" data-nsel="${esc(n.id)}" aria-label="Selecionar">${ico("check")}</button>`}
    <span class="av app" aria-hidden="true">${esc((app[0] || "?").toUpperCase())}</span>
    <span class="mbody"><span class="mtop"><b>${esc(app)}</b>${n.etiqueta ? `<span class="tag et" style="--tc:${tagColor(n.etiqueta)}">${esc(titleCase(n.etiqueta))}</span>` : ""}${(n.prioridade || 0) >= 2 ? `<span class="pri p${Math.min(3, n.prioridade)}"><i></i>${esc(PRI[Math.min(3, n.prioridade)])}</span>` : ""}<time>${esc(d ? ago(d) : "")}</time></span>
      <span class="msubj">${esc(n.titulo || n.title || "")}</span>${n.texto || n.text ? `<span class="mtext">${esc(String(n.texto || n.text).slice(0, compact ? 110 : 260))}</span>` : ""}
      ${compact ? "" : `${(n.regra || asList(n.regras).length) ? `<span class="nrule">${ico("tree", "sm")} Regra: ${esc([].concat(n.regra || n.regras).map(x => typeof x === "object" ? (x.nome || x.name || "") : x).join(", "))}</span>` : ""}
      ${done.length ? `<span class="nacts">${done.map(a => `<span class="chip ok">${ico("check", "sm")}${esc(actLabel(a))}</span>`).join("")}</span>` : ""}
      ${sug.length ? `<span class="nacts">${sug.map((a, i) => `<button type="button" class="chip sug" data-nact="${esc(n.id)}" data-ai="${i}" data-tip="Aprovar e fazer agora">${ico("spark", "sm")}${esc(actLabel(a))}</button>`).join("")}</span>` : ""}`}</span></div>`;
}
const ACT_PT = {criar_card: "Criar card", alerta: "Criar alerta", tarefa: "Criar tarefa", notificar: "Avisar", etiquetar: "Etiquetar", arquivar: "Arquivar", abrir_app: "Abrir app", responder_sugestao: "Sugerir resposta", ignorar: "Ignorar"};
const actLabel = a => typeof a === "string" ? (ACT_PT[a] || titleCase(a.replace(/_/g, " "))) : (a.rotulo || a.label || a.texto || (a.titulo && (ACT_PT[a.action || a.acao] || "Fazer") + ": " + a.titulo) || ACT_PT[a.acao || a.action] || titleCase(String(a.acao || a.action || "ação").replace(/_/g, " ")));
const actKey = a => typeof a === "string" ? a : (a.acao || a.action || a.id);
const TAG_COLORS = ["var(--rose)", "var(--info)", "var(--ok)", "var(--warn)", "#7d5a86", "#c0714f", "#4f7d96"];
const tagColor = t => { let h = 0; for (const ch of String(t)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return TAG_COLORS[h % TAG_COLORS.length]; };
function dlist(title, icon, items, rowFn, emptyTxt, goView, n) {
  return `<section class="dcard dl"><header>${ico(icon, "sm")}<h3>${esc(title)}</h3>${n ? `<span class="cnt">${n}</span>` : ""}<span class="sp"></span>${goView ? `<button class="btn sm ghost" data-go="${goView}">Ver tudo</button>` : ""}</header>
    ${items.length ? `<div class="dlist">${items.map(rowFn).join("")}</div>` : `<p class="dempty">${esc(emptyTxt)}</p>`}</section>`;
}
function viewHome() {
  const v = viewEl(), now = new Date();
  const noModel = !modelReady() && !store.get("inviteDismissed", false) && S.model.state !== "baixando";
  if (S.thread.length && !S.showPanel) {
    v.innerHTML = `<div class="view-inner narrow"><div class="vh"><div><span class="kicker">Conversa</span><h1>Seus <em>pedidos</em></h1></div><span class="sp"></span>
      <button class="btn sm" id="panel">${ico("home", "sm")} Ver painel</button><button class="btn sm ghost" id="clr">Limpar conversa</button></div>
      ${noModel ? inviteHTML() : ""}<div class="thread" id="thread"></div></div>`;
    $("#clr", v).onclick = () => { S.thread = []; render(); };
    $("#panel", v).onclick = () => { S.showPanel = true; render(); };
    wireInvite(v);
    const th = $("#thread", v); S.thread.forEach(it => th.appendChild(it.el));
    requestAnimationFrame(() => v.scrollTop = v.scrollHeight);
    return;
  }
  const I = S.inicioOk && S.inicio ? S.inicio : localInicio(), L = localInicio();
  const tasks = asList(I.tarefas_relevantes).length ? asList(I.tarefas_relevantes) : L.tarefas_relevantes;
  const evs = asList(I.proximos_eventos).length ? asList(I.proximos_eventos) : L.proximos_eventos;
  const msgs = asList(I.mensagens_importantes).concat(asList(I.mensagens_cards)), notes = asList(I.notificacoes_recentes);
  const late = S.cards.filter(isLate).length, today = S.cards.filter(c => !c.done && ((cardDate(c) && sameDay(cardDate(c), now)) || c.column === "hoje")).length;
  const frase = I.resumo_frase || (S.cardsLoaded ? `${today ? `${today} coisa${today > 1 ? "s" : ""} para hoje` : "Nada marcado para hoje"}${late ? ` · ${late} atrasada${late > 1 ? "s" : ""}` : ""}${evs.length ? ` · próximo: ${cardTitle(byCardId(evs[0]))}` : ""}.` : "");
  v.innerHTML = `<div class="view-inner dash">
    ${S.thread.length ? `<div class="backconv"><span>${ico("msg", "sm")} Você tem uma conversa em andamento.</span><button class="btn sm" id="bconv">Voltar à conversa</button></div>` : ""}
    <div class="dhero"><img src="/static/icon.svg" alt=""><div><h1>${greet()}, <em>o que vamos organizar?</em></h1><p>${esc(frase)}</p></div></div>
    ${noModel ? inviteHTML() : ""}
    <div class="dtop">${clockHTML()}${weatherHTML(S.wx)}</div>
    <div class="dgrid">
      ${dlist("Tarefas mais relevantes", "check", tasks.slice(0, 5), c => trowHTML(byCardId(c)), "Nada urgente. Peça “pagar a luz dia 10” no campo abaixo.", "tarefas", tasks.length)}
      ${dlist("Próximos eventos", "cal", evs.slice(0, 5), evRowHTML, "Agenda livre nos próximos dias.", "agenda")}
      ${dlist("Mensagens importantes", "msg", msgs.slice(0, 4), msgRowHTML, "Nenhuma mensagem importante agora.", "mensagens")}
      ${dlist("Notificações recentes", "bell", notes.slice(0, 5), n => notifRowHTML(n, true), S.inicioOk ? "Sem notificações novas." : "As notificações do Windows aparecem aqui quando o secretário estiver ligado.", "notificacoes")}
    </div>
    ${I.automacoes ? `<p class="hint dfoot">${ico("tree", "sm")} ${I.automacoes} automaç${I.automacoes > 1 ? "ões" : "ão"} cuidando das coisas para você.</p>` : ""}
  </div>`;
  const bc = $("#bconv", v); if (bc) bc.onclick = () => { S.showPanel = false; render(); };
  $$("[data-go]", v).forEach(b => b.onclick = () => go(b.dataset.go));
  $$(".nrow", v).forEach(r => r.onclick = () => go("notificacoes"));
  $$(".mrow[data-id],.erow[data-id]", v).forEach(r => { r.onclick = () => openCard(r.dataset.id); r.onkeydown = e => { if (e.key === "Enter") openCard(r.dataset.id); };
    r.oncontextmenu = e => cardCtx(e, r.dataset.id); });
  wireInvite(v); wireCards(v); wireClock(v);
  const wr = $("#wxr", v); if (wr) wr.onclick = async () => { wr.setAttribute("aria-busy", "true"); S.wx = await getWeather(true); if (S.view === "inicio") render(); };
  if (!S._inicioT || Date.now() - S._inicioT > 30000) { S._inicioT = Date.now(); loadInicio().then(() => { if (S.view === "inicio" && (!S.thread.length || S.showPanel)) render(); }); }
  if (S.wx === undefined && !S._wxLoading) { S._wxLoading = true; getWeather().then(w => { S.wx = w; S._wxLoading = false; if (S.view === "inicio" && (!S.thread.length || S.showPanel)) render(); }); }
}
function trowHTML(c) {
  return `<div class="trow ${c.done ? "done" : ""} ${SEL.has(c.id) ? "selc" : ""}" data-id="${esc(c.id)}" tabindex="0" role="button" aria-label="${esc(c.title)}">
    <button type="button" class="cbx" role="checkbox" aria-checked="${!!c.done}" data-id="${esc(c.id)}" aria-label="Concluir">${ico("check")}</button>
    <div class="tt"><b>${esc(cardTitle(c))}</b><small>${esc(KINDS[c.kind] || "")}${cardDate(c) ? " · " + esc(whenTxt(c)) : ""}${(c.tags || []).length ? " · " + c.tags.map(t => "#" + esc(t)).join(" ") : ""}</small></div>
    ${c.priority ? `<span class="pri p${c.priority}"><i></i>${esc(PRI[c.priority])}</span>` : ""}
    ${isLate(c) ? `<span class="tag" style="background:var(--bad-soft);color:var(--bad)">atrasado</span>` : ""}</div>`;
}

/* ---------------------------------------------------------------- QUADRO */
function viewBoard(keepFocus, keepMenu) {
  const v = viewEl(), cols = visibleCols(), list = filtered(S.cards);
  const fqFocus = keepFocus && document.activeElement && document.activeElement.id === "fq";
  if (keepMenu && openMenu) { const bar = $(".fbar", v); /* atualiza só contagens */ }
  v.innerHTML = `<div class="view-inner">
    <div class="vh"><div><span class="kicker">Quadro</span><h1>Agenda <em>&amp;</em> tarefas</h1></div><span class="sp"></span>
      <button class="btn sm primary" id="nb">${ico("plus", "sm")} Novo card <kbd style="background:transparent;color:inherit;border-color:currentColor;opacity:.75">Ctrl N</kbd></button></div>
    ${filterBarHTML()}
    <section class="agstrip" aria-label="Faixa de agenda">${stripHTML(list)}</section>
    ${!S.cardsLoaded ? `<div class="kanban">${cols.map(() => `<div class="col"><div class="sk"></div><div class="sk" style="margin-top:8px"></div></div>`).join("")}</div>` :
    `<div class="kanban" role="list">${cols.map(col => { const cs = list.filter(c => (c.column || "entrada") === col.id);
      return `<section class="col" role="listitem" aria-label="Coluna ${col.label}" data-col="${col.id}"><header><i class="cd" style="background:${col.c}"></i><b>${col.label}</b><span class="cnt">${cs.length}</span><span class="sp"></span>
        <button class="iconbtn" data-colmenu="${col.id}" aria-label="Opções da coluna ${col.label}" aria-haspopup="menu" style="width:28px;height:28px">⋯</button></header>
        <div class="cl" data-drop="${col.id}">${cs.map(kcardHTML).join("") || `<div class="empty"><p>${col.id === "feito" ? "Nada concluído ainda." : "Solte cards aqui."}</p></div>`}</div>
        <div class="quick"><input data-quick="${col.id}" placeholder="+ Adicionar rápido" aria-label="Adicionar card rápido em ${col.label}"></div></section>`; }).join("")}</div>`}
  </div>`;
  wireFilterBar(v, (kf, km) => viewBoard(kf, km));
  if (fqFocus) { const q = $("#fq", v); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  $("#nb", v).onclick = () => newCard();
  wireStrip(v); wireCards(v);
  $$("[data-drop]", v).forEach(z => dropZone(z.closest(".col"), (id, e) => {
    const col = z.dataset.drop, kids = $$(".kc", z).filter(k => k.dataset.id !== id);
    let order; const after = kids.find(k => e.clientY < k.getBoundingClientRect().top + k.offsetHeight / 2);
    const ord = k => (S.cards.find(c => c.id === k.dataset.id) || {}).order ?? 0;
    if (after) { const i = kids.indexOf(after); order = i === 0 ? ord(after) - 1 : (ord(kids[i - 1]) + ord(after)) / 2; } else order = kids.length ? ord(kids[kids.length - 1]) + 1 : 0;
    moveCard(id, col, order);
  }));
  $$("[data-quick]", v).forEach(i => i.onkeydown = async e => { if (e.key === "Enter" && i.value.trim()) { const t = i.value.trim(); i.value = "";
    const c = await createCard({title: t, column: i.dataset.quick, kind: "tarefa"}); if (c) { viewBoard(); const q = $(`[data-quick="${i.dataset.quick}"]`); q && q.focus(); } } if (e.key === "Escape") i.blur(); });
  $$("[data-colmenu]", v).forEach(b => b.onclick = () => menu(b, colItems(b.dataset.colmenu), val => colPick(b.dataset.colmenu, val)));
  $$(".col", v).forEach(col => col.addEventListener("contextmenu", e => { if (e.target.closest(".kc,input")) return; ctxMenu(e, colItems(col.dataset.col), val => colPick(col.dataset.col, val)); }));
}
function colItems(id) {
  return [{value: "new", label: "Novo card aqui", icon: "plus"}, {value: "selall", label: "Selecionar todos daqui", icon: "check"}, {value: "alldone", label: "Marcar todos como feitos", icon: "check", disabled: id === "feito"},
    {value: "sortd", label: "Ordenar por data", icon: "sort"}, {value: "sortp", label: "Ordenar por prioridade", icon: "sort"}, {value: "hide", label: "Esconder coluna", icon: "x"},
    ...(id === "feito" ? [{value: "clean", label: "Apagar os feitos", icon: "trash", danger: true}] : []),
    {hr: 1}, {group: "Colunas visíveis"}, ...COLS.map(c => ({value: "t:" + c.id, label: c.label, checked: (S.prefs.columns || []).includes(c.id)}))];
}
async function colPick(col, val) {
  const cs = filtered(S.cards).filter(c => (c.column || "entrada") === col);
  if (val === "new") newCard(col);
  else if (val === "selall") { cs.forEach(c => SEL.add(c.id)); paintSel(); }
  else if (val === "alldone") bulkPatch(cs.map(c => c.id), {done: true, column: "feito"}, "Coluna concluída");
  else if (val === "clean") bulkDelete(cs.map(c => c.id));
  else if (val === "sortd" || val === "sortp") { const k = val === "sortd" ? (a, b) => ((cardDate(a) || 8e15) - (cardDate(b) || 8e15)) : (a, b) => (b.priority || 0) - (a.priority || 0);
    const ids = [...cs].sort(k).map(c => c.id); ids.forEach((id, i) => { const c = S.cards.find(x => x.id === id); if (c) c.order = i; }); render();
    tryApi("/api/cards/reorder", {column: col, ids}, {silent: true}); }
  else if (val === "hide") savePrefs({columns: (S.prefs.columns || []).filter(x => x !== col)});
  else if (val.startsWith("t:")) { const id = val.slice(2), cur = new Set(S.prefs.columns || []); cur.has(id) ? cur.delete(id) : cur.add(id); savePrefs({columns: COLS.map(c => c.id).filter(x => cur.has(x))}); }
}
function stripHTML(list) {
  const now = new Date(), day = startDay(S.anchor);
  let slots = [];
  if (S.stripMode === "dia") for (let h = 6; h <= 22; h++) { const s = new Date(day); s.setHours(h); slots.push({s, h: `${pad(h)}h`, now: sameDay(day, now) && now.getHours() === h,
    cs: list.filter(c => { const d = parseD(c.start); return d && sameDay(d, day) && d.getHours() === h; })}); }
  else { const ws = weekStart(S.anchor); for (let i = 0; i < 7; i++) { const dd = addDays(ws, i); for (const [h, n] of [[9, "manhã"], [14, "tarde"], [19, "noite"]]) { const s = new Date(dd); s.setHours(h);
    slots.push({s, h: `${WD[dd.getDay()]} ${dd.getDate()} · ${n}`, now: sameDay(dd, now) && (now.getHours() < 12 ? h === 9 : now.getHours() < 18 ? h === 14 : h === 19),
      cs: list.filter(c => { const d = parseD(c.start); return d && sameDay(d, dd) && (h === 9 ? d.getHours() < 12 : h === 14 ? d.getHours() >= 12 && d.getHours() < 18 : d.getHours() >= 18); })}); } } }
  const label = S.stripMode === "dia" ? day.toLocaleDateString("pt-BR", {weekday: "long", day: "numeric", month: "long"}) : `Semana de ${weekStart(S.anchor).toLocaleDateString("pt-BR", {day: "numeric", month: "short"})}`;
  return `<header><b>${esc(label.charAt(0).toUpperCase() + label.slice(1))}</b><span class="hint">arraste um card para um horário</span><span class="sp"></span>
    <button class="iconbtn" data-nav="-1" aria-label="Anterior">‹</button><button class="btn sm ghost" data-nav="0">Hoje</button><button class="iconbtn" data-nav="1" aria-label="Próximo">›</button>
    ${segHTML("strip", [["dia", "Dia"], ["semana", "Semana"]], S.stripMode, "Escala da faixa")}</header>
    <div class="slots" style="${S.stripMode === "semana" ? "grid-auto-columns:minmax(96px,1fr)" : ""}">${slots.map(s => `<div class="slot ${s.now ? "now" : ""}" data-slot="${isoLocal(s.s)}" aria-label="${esc(s.h)}"><span class="h">${esc(s.h)}</span>${s.cs.map(c => `<button type="button" class="mini ${c.done ? "done" : ""}" data-id="${esc(c.id)}" style="${c.color ? `border-left-color:${esc(c.color)}` : ""}" title="${esc(c.title)}">${esc(c.title)}</button>`).join("")}</div>`).join("")}</div>`;
}
function wireStrip(v) {
  wireSeg(v, "strip", m => { S.stripMode = m; store.set("stripMode", m); render(); });
  $$("[data-nav]", $(".agstrip", v)).forEach(b => b.onclick = () => { const n = +b.dataset.nav; S.anchor = n === 0 ? new Date() : addDays(S.anchor, n * (S.stripMode === "dia" ? 1 : 7)); render(); });
  $$("[data-slot]", v).forEach(z => dropZone(z, id => { const c = S.cards.find(x => x.id === id); moveCard(id, c && c.column === "entrada" ? "hoje" : undefined, undefined, z.dataset.slot); }));
  const sc = $(".slots", v); const now = $(".slot.now", v); if (sc && now) sc.scrollLeft = now.offsetLeft - 80;
}

/* ---------------------------------------------------------------- AGENDA */
let agendaCache = null;
async function loadAgenda(from, to) {
  try { const r = await api(`/api/agenda?from=${encodeURIComponent(isoLocal(from))}&to=${encodeURIComponent(isoLocal(to))}`); return (r && (r.cards || r.items || r)) || []; }
  catch (e) { return S.cards.filter(c => { const d = cardDate(c); return d && d >= from && d < to; }); }
}
async function viewAgenda() {
  const v = viewEl(), a = S.anchor;
  const from = S.agMode === "semana" ? weekStart(a) : weekStart(new Date(a.getFullYear(), a.getMonth(), 1));
  const to = S.agMode === "semana" ? addDays(from, 7) : addDays(from, 42);
  const title = S.agMode === "semana" ? `${from.getDate()} – ${addDays(from, 6).toLocaleDateString("pt-BR", {day: "numeric", month: "long"})}` : `${MONTHS[a.getMonth()]} de ${a.getFullYear()}`;
  v.innerHTML = `<div class="view-inner"><div class="vh"><div><span class="kicker">Agenda</span><h1>${esc(title.charAt(0).toUpperCase() + title.slice(1))}</h1></div><span class="sp"></span>
    <button class="iconbtn" data-an="-1" aria-label="Anterior">‹</button><button class="btn sm" data-an="0">Hoje</button><button class="iconbtn" data-an="1" aria-label="Próximo">›</button>
    ${segHTML("ag", [["semana", "Semana"], ["mes", "Mês"]], S.agMode, "Visão da agenda")}
    <button class="btn sm primary" id="nb">${ico("plus", "sm")} Evento</button></div><div id="agbody"><div class="sk" style="height:420px"></div></div></div>`;
  wireSeg(v, "ag", m => { S.agMode = m; store.set("agMode", m); viewAgenda(); });
  $$("[data-an]", v).forEach(b => b.onclick = () => { const n = +b.dataset.an; S.anchor = n === 0 ? new Date() : (S.agMode === "semana" ? addDays(a, 7 * n) : new Date(a.getFullYear(), a.getMonth() + n, 1)); viewAgenda(); });
  $("#nb", v).onclick = () => newCard("hoje", {kind: "evento", start: isoLocal(new Date(new Date().setMinutes(0, 0, 0) + 36e5))});
  let items = await loadAgenda(from, to); if (!Array.isArray(items)) items = [];
  const byId = new Map(S.cards.map(c => [c.id, c])); items = items.map(c => byId.get(c.id) || c);
  items = filtered(items, {done: S.filters.done, kind: S.filters.kind, tag: S.filters.tag}, "");
  if (S.view !== "agenda") return;
  const body = $("#agbody", v), today = new Date();
  if (S.agMode === "semana") {
    let h = `<div class="week" role="grid" aria-label="Semana">`;
    h += `<div class="wh"></div>` + [0, 1, 2, 3, 4, 5, 6].map(i => { const d = addDays(from, i); return `<div class="wh ${sameDay(d, today) ? "today" : ""}" role="columnheader">${WD[d.getDay()]}<b>${d.getDate()}</b></div>`; }).join("");
    const allday = [0, 1, 2, 3, 4, 5, 6].map(i => items.filter(c => { const d = cardDate(c); return d && sameDay(d, addDays(from, i)) && (c.all_day || (!d.getHours() && !d.getMinutes())); }));
    if (allday.some(x => x.length)) h += `<div class="hl2" style="height:auto">dia</div>` + allday.map((cs, i) => `<div class="hcell" style="height:auto;min-height:30px" data-cell="${isoLocal(addDays(from, i))}">${cs.map(miniHTML).join("")}</div>`).join("");
    for (let hr = 6; hr <= 23; hr++) {
      h += `<div class="hl2">${pad(hr)}:00</div>`;
      for (let i = 0; i < 7; i++) { const d = addDays(from, i); d.setHours(hr);
        const cs = items.filter(c => { const x = cardDate(c); return x && sameDay(x, d) && x.getHours() === hr && !(c.all_day || (!x.getHours() && !x.getMinutes())); });
        h += `<div class="hcell" role="gridcell" data-cell="${isoLocal(d)}" ${sameDay(d, today) && today.getHours() === hr ? 'style="background:var(--rose-glow)"' : ""}>${cs.map(miniHTML).join("")}</div>`; }
    }
    body.innerHTML = h + `</div>`;
    const nowCell = $(`.hcell[style*="rose-glow"]`, body); if (nowCell) { const top = nowCell.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop; v.scrollTop = Math.max(0, top - 330); }
  } else {
    let h = `<div class="month" role="grid" aria-label="Mês">` + ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"].map(d => `<div class="mh" role="columnheader">${d}</div>`).join("");
    for (let i = 0; i < 42; i++) { const d = addDays(from, i); const cs = items.filter(c => sameDay(cardDate(c), d));
      h += `<div class="dcell ${d.getMonth() !== a.getMonth() ? "out" : ""} ${sameDay(d, today) ? "today" : ""}" role="gridcell" data-cell="${ymd(d)}T09:00:00" data-day="${ymd(d)}"><span class="dn">${d.getDate()}</span>${cs.slice(0, 3).map(miniHTML).join("")}${cs.length > 3 ? `<span class="more">+${cs.length - 3} mais</span>` : ""}</div>`; }
    body.innerHTML = h + `</div>`;
  }
  if (!items.length) body.insertAdjacentHTML("beforeend", empty("Agenda livre", "Nada marcado neste período. Peça “reunião sexta às 10h” ou arraste um card do quadro para um horário."));
  wireCards(body);
  $$("[data-cell]", body).forEach(z => { dropZone(z, id => { let s = z.dataset.cell; const c = S.cards.find(x => x.id === id), old = c && cardDate(c);
      if (S.agMode === "mes" && old) s = `${z.dataset.day}T${hm(old)}:00`; moveCard(id, undefined, undefined, s).then(() => viewAgenda()); });
    z.addEventListener("dblclick", e => { if (e.target === z) newCard("hoje", {kind: "evento", start: z.dataset.cell}); }); });
}
const miniHTML = c => `<button type="button" class="mini ${c.done ? "done" : ""}" data-id="${esc(c.id)}" style="${c.color ? `border-left-color:${esc(c.color)};` : ""}" title="${esc(c.title)}">${cardDate(c) && cardDate(c).getHours() ? hm(cardDate(c)) + " " : ""}${esc(c.title)}</button>`;

/* ---------------------------------------------------------------- TAREFAS */
function viewTasks(keepFocus) {
  const v = viewEl(), now = new Date(), t0 = startDay(now);
  const fqFocus = keepFocus && document.activeElement && document.activeElement.id === "fq";
  const list = filtered(S.cards.filter(c => c.kind !== "nota" && c.kind !== "mensagem"));
  const G = [["Atrasados", c => isLate(c)], ["Hoje", c => !c.done && ((cardDate(c) && sameDay(cardDate(c), now)) || (!cardDate(c) && c.column === "hoje"))],
    ["Amanhã", c => !c.done && cardDate(c) && sameDay(cardDate(c), addDays(t0, 1))], ["Esta semana", c => !c.done && cardDate(c) && cardDate(c) >= addDays(t0, 2) && cardDate(c) < addDays(t0, 7)],
    ["Depois", c => !c.done && cardDate(c) && cardDate(c) >= addDays(t0, 7)], ["Sem data", c => !c.done && !cardDate(c)], ["Feitos", c => c.done]];
  const used = new Set();
  const groups = G.map(([n, f]) => { const cs = list.filter(c => !used.has(c.id) && f(c)); cs.forEach(c => used.add(c.id)); return [n, cs]; }).filter(g => g[1].length);
  v.innerHTML = `<div class="view-inner narrow" style="max-width:920px"><div class="vh"><div><span class="kicker">Tarefas</span><h1>Tudo o que <em>falta fazer</em></h1></div><span class="sp"></span>
    <button class="btn sm primary" id="nb">${ico("plus", "sm")} Nova tarefa</button></div>${filterBarHTML()}
    ${!S.cardsLoaded ? `<div class="sk"></div>` : groups.length ? groups.map(([n, cs]) => `<section class="group"><h3 class="${n === "Atrasados" ? "late" : ""}">${n}<span>${cs.length}</span></h3><div class="tlist">${cs.map(trowHTML).join("")}</div></section>`).join("") :
      empty("Nenhuma tarefa por aqui", Object.keys(S.filters).length ? "Nada combina com os filtros atuais." : "Escreva no campo abaixo, por exemplo “pagar a luz dia 10”.", Object.keys(S.filters).length ? `<button class="btn" id="ec">Limpar filtros</button>` : "")}</div>`;
  wireFilterBar(v, kf => viewTasks(kf)); wireCards(v);
  if (fqFocus) { const q = $("#fq", v); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  $("#nb", v).onclick = () => newCard("hoje");
  const ec = $("#ec", v); if (ec) ec.onclick = () => { S.filters = {}; store.set("filters", {}); viewTasks(); };
}

/* ---------------------------------------------------------------- MENSAGENS */
const connected = () => (S.ints || []).filter(i => i.connected && i.id !== "gemini");
async function loadInts() { try { const r = await api("/api/integrations"); S.ints = asList(r, "integrations", "items"); } catch (e) { S.ints = S.ints || []; } return S.ints; }
async function viewMessages() {
  const v = viewEl(), mf = store.get("msgFilter", "todas");
  if (S.ints === null) await loadInts();
  if (S.view !== "mensagens") return;
  let msgs = S.cards.filter(c => c.kind === "mensagem");
  if (mf === "abertas") msgs = msgs.filter(c => !c.done); if (mf === "feitas") msgs = msgs.filter(c => c.done);
  msgs.sort((a, b) => (a.done - b.done) || ((b.priority || 0) - (a.priority || 0)) || String(b.updated || "").localeCompare(String(a.updated || "")));
  const imp = msgs.filter(m => !m.done && (m.priority || 0) >= 2), rest = msgs.filter(m => !imp.includes(m));
  const conn = connected();
  v.innerHTML = `<div class="view-inner narrow msgs"><div class="vh"><div><span class="kicker">Mensagens</span><h1>Triagem e <em>envios</em></h1></div></div>
    <div class="mbar">${segHTML("mf", [["todas", "Todas"], ["abertas", "Abertas"], ["feitas", "Respondidas"]], mf, "Filtrar mensagens")}
      ${conn.length ? `<span class="conn">${conn.map(i => `<span class="chip on" data-tip="${esc(i.account || "")}"><i class="okdot"></i>${esc(SRC_NAME[i.id] || i.name)}</span>`).join("")}</span>` : ""}
      <span class="sp"></span><button class="btn sm" id="tri">${ico("shield", "sm")} Triar mensagem recebida</button><button class="btn sm primary" id="wr">${ico("send", "sm")} Escrever</button></div>
    <div id="pend"></div>
    ${imp.length ? `<section class="group"><h3>Importantes <span>${imp.length}</span></h3><div class="mlist">${imp.map(msgRowHTML).join("")}</div></section>` : ""}
    <section class="group"><h3>${imp.length ? "Outras" : "Mensagens"} <span>${rest.length}</span></h3>${rest.length ? `<div class="mlist">${rest.map(msgRowHTML).join("")}</div>` :
      empty("Sem mensagens por aqui", conn.length ? "As mensagens importadas e as que você pedir para enviar aparecem aqui." : "Cole uma mensagem recebida para o Djorge triar, ou escreva “#ana &chego em 10 minutos&”.")}</section></div>`;
  wireSeg(v, "mf", x => { store.set("msgFilter", x); viewMessages(); });
  $$(".mrow[data-id]", v).forEach(r => { r.onclick = e => { if (e.ctrlKey || e.shiftKey) return; openCard(r.dataset.id); }; r.onkeydown = e => { if (e.key === "Enter") openCard(r.dataset.id); }; r.oncontextmenu = e => msgCtx(e, r.dataset.id); });
  $("#tri", v).onclick = () => { setMode("mensagem"); $("#in").focus(); };
  $("#wr", v).onclick = () => { setMode("auto"); setInput("#"); };
  try { const list = asList(await api("/api/pending"), "pending");
    if (list.length && S.view === "mensagens") { $("#pend", v).innerHTML = `<section class="group"><h3>Aguardando sua confirmação <span>${list.length}</span></h3><div class="tlist">${list.map(pendHTML).join("")}</div></section>`; wirePend($("#pend", v)); } }
  catch (e) {}
}
function msgCtx(e, id) {
  const c = S.cards.find(x => x.id === id); if (!c) return;
  const who = msgPerson(c);
  ctxMenu(e, [{value: "open", label: "Abrir", icon: "edit"}, {value: "reply", label: `Responder a ${who}`, icon: "send"}, {value: "wa", label: "Abrir no WhatsApp", icon: "msg"},
    {value: "task", label: "Virar tarefa", icon: "check"}, {value: "done", label: c.done ? "Marcar como aberta" : "Marcar como respondida", icon: "check"},
    {group: "Prioridade"}, ...PRI.map((l, i) => ({value: "p:" + i, label: l, checked: (+c.priority || 0) === i})).reverse(), {hr: 1}, {value: "copy", label: "Copiar texto", icon: "list"}, {value: "del", label: "Apagar", icon: "trash", danger: true}], v => {
    if (v === "open") openCard(id);
    if (v === "reply") { setMode("auto"); setInput(`#${who.split(" ")[0].toLowerCase()} &&`); const i = inp(); i.setSelectionRange(i.value.length - 1, i.value.length - 1); }
    if (v === "wa") openWhatsApp(phoneOf(c), "");
    if (v === "task") saveCard(id, {kind: "tarefa", column: "hoje"}, true).then(() => { render(); toast("Virou tarefa para hoje"); });
    if (v === "done") saveCard(id, {done: !c.done, column: c.done ? "entrada" : "feito"}, true).then(render);
    if (v.startsWith("p:")) saveCard(id, {priority: +v.slice(2)}, true).then(render);
    if (v === "copy") copyText((c.title || "") + (c.body ? "\n" + c.body : ""));
    if (v === "del") deleteCard(id);
  });
}
const phoneOf = c => { const p = (c.people || []).find(x => x.telefone || x.phone); return p ? (p.telefone || p.phone) : ""; };
function waUrl(phone, text) { const n = String(phone || "").replace(/\D/g, ""); return n ? `https://wa.me/${n}${text ? "?text=" + encodeURIComponent(text) : ""}` : `https://wa.me/${text ? "?text=" + encodeURIComponent(text) : ""}`; }
async function openWhatsApp(phone, text, url) { try { const r = await api("/api/abrir", {app: "whatsapp", para: phone || "", texto: text || "", url: url || ""}); if (!r || r.ok === false) throw 0; } catch (e) { window.open(url || waUrl(phone, text), "_blank", "noopener"); } toast("Abrindo o WhatsApp. O envio final é seu.", "", {ms: 4500}); }
const pendHTML = p => { const sp = p.spec || {}, a = sp.action || sp.kind || sp.acao || "ação";
  const desc = sp.titulo || sp.title || sp.message || sp.folder && `Mover para “${sp.folder}”` || sp.to && `Para ${titleCase(sp.to)}` || "";
  return `<div class="trow" style="cursor:default"><span class="pri p2"><i></i></span><div class="tt"><b>${esc(ACT_PT[a] || {email_move: "Mover e-mail", notify: "Avisar", webhook: "Chamar webhook", command: "Rodar comando", write_file: "Salvar em arquivo", send_message: "Enviar mensagem", log: "Registrar"}[a] || titleCase(String(a).replace(/_/g, " ")))}${p.tree ? ` · ${esc(titleCase(p.tree))}` : ""}</b><small>${esc(desc || "Precisa da sua aprovação")}</small></div>
  <button class="btn sm" data-pok="${esc(p.id)}" data-a="0">Recusar</button><button class="btn sm primary" data-pok="${esc(p.id)}" data-a="1">Aprovar</button></div>`; };
function wirePend(root, after) { $$("[data-pok]", root).forEach(b => b.onclick = async () => { const r = await tryApi("/api/pending/" + b.dataset.pok, {approve: b.dataset.a === "1"});
  if (r) toast(b.dataset.a === "1" ? "Aprovado" : "Recusado"); pollStatus(); after ? after() : render(); }); }

/* ---------------------------------------------------------------- NOTIFICAÇÕES */
const NSEL = new Set();
const NF = store.get("nf", {etiqueta: "", app: "", lida: "", q: ""});
let NOTIFS = [], NTAGS = [];
async function loadNotifs() {
  const qs = new URLSearchParams(Object.entries(NF).filter(([, v]) => v !== "" && v != null)).toString();
  try { const [r, t] = await Promise.all([api("/api/notificacoes" + (qs ? "?" + qs : "")), api("/api/notificacoes/etiquetas").catch(() => [])]);
    NOTIFS = asList(r, "notificacoes", "items"); NTAGS = asList(t, "etiquetas", "items").map(x => typeof x === "string" ? {etiqueta: x} : {etiqueta: x.etiqueta || x.nome || x.name, n: x.n ?? x.total ?? x.count});
    S.notifOk = true; return true; }
  catch (e) { S.notifOk = false; S.notifErr = e; return false; }
}
function dayLabel(d) { if (!d) return "Sem data"; const w = relDayWord(d); return w ? titleCase(w) : titleCase(d.toLocaleDateString("pt-BR", {weekday: "long", day: "numeric", month: "long"})); }
async function viewNotifs(keepFocus) {
  const v = viewEl(), qFocus = keepFocus && document.activeElement && document.activeElement.id === "nq";
  if (!keepFocus) v.innerHTML = `<div class="view-inner narrow notifs"><div class="vh"><div><span class="kicker">Notificações</span><h1>Tudo que <em>chegou</em></h1></div></div><div class="sk"></div><div class="sk" style="margin-top:8px"></div></div>`;
  await loadNotifs(); if (S.view !== "notificacoes") return;
  if (!S.notifOk) { v.innerHTML = `<div class="view-inner narrow notifs"><div class="vh"><div><span class="kicker">Notificações</span><h1>Tudo que <em>chegou</em></h1></div></div>
    ${empty("As notificações ainda não estão disponíveis", "Ligue o secretário para o Djorge acompanhar as notificações do Windows e organizar o que importa.", `<button class="btn primary" id="sw">${ico("spark", "sm")} Configurar o secretário</button>`)}</div>`;
    $("#sw", v).onclick = () => secretaryWizard(); return; }
  const apps = [...new Set(NOTIFS.map(n => n.app || n.fonte).filter(Boolean))].sort();
  const unread = NOTIFS.filter(n => !n.lida).length; S.notifUnread = unread; renderNav();
  const groups = []; NOTIFS.forEach(n => { const d = parseD(n.t || n.data || n.recebida || n.created), k = d ? ymd(d) : ""; let g = groups.find(x => x.k === k); if (!g) groups.push(g = {k, d, items: []}); g.items.push(n); });
  v.innerHTML = `<div class="view-inner narrow notifs"><div class="vh"><div><span class="kicker">Notificações</span><h1>Tudo que <em>chegou</em></h1><p>${NOTIFS.length} no total${unread ? ` · ${unread} não lida${unread > 1 ? "s" : ""}` : ""}. O secretário etiqueta e sugere o que fazer.</p></div></div>
    <div class="fbar" role="toolbar" aria-label="Filtros de notificações">
      <label class="fsearch">${ico("search", "sm")}<input id="nq" type="search" placeholder="Buscar nas notificações…" value="${esc(NF.q || "")}" aria-label="Buscar nas notificações"></label>
      <button type="button" class="chip ${NF.app ? "on" : ""}" id="napp" aria-haspopup="listbox">${ico("plug", "sm")}${esc(NF.app ? titleCase(NF.app) : "App")}</button>
      ${segHTML("nlida", [["", "Todas"], ["false", "Não lidas"], ["true", "Lidas"]], NF.lida, "Lidas")}
      <span class="sp"></span>
      ${unread ? `<button class="btn sm ghost" id="nall">${ico("check", "sm")} Marcar todas como lidas</button>` : ""}
      <button class="btn sm ghost" id="nclr" aria-haspopup="menu">${ico("trash", "sm")} Limpar…</button>
    </div>
    ${NTAGS.length ? `<div class="saved" role="group" aria-label="Etiquetas"><span class="lbl">Etiquetas</span><button type="button" class="chip ${!NF.etiqueta ? "on" : ""}" data-et="">Todas</button>${NTAGS.map(t => `<button type="button" class="chip ${NF.etiqueta === t.etiqueta ? "on" : ""}" data-et="${esc(t.etiqueta)}"><i class="mdot" style="background:${tagColor(t.etiqueta)}"></i>${esc(titleCase(t.etiqueta))}${t.n != null ? ` <small>${t.n}</small>` : ""}</button>`).join("")}</div>` : ""}
    ${NOTIFS.length ? groups.map(g => `<section class="group"><h3>${esc(dayLabel(g.d))}<span>${g.items.length}</span></h3><div class="mlist">${g.items.map(n => notifRowHTML(n)).join("")}</div></section>`).join("") :
      empty(Object.values(NF).some(Boolean) ? "Nada com esses filtros" : "Nenhuma notificação", Object.values(NF).some(Boolean) ? "Tente outra etiqueta ou limpe a busca." : "Quando chegarem notificações no computador, elas aparecem aqui, já etiquetadas.", Object.values(NF).some(Boolean) ? `<button class="btn" id="nfc">Limpar filtros</button>` : "")}
  </div>`;
  const setNF = (k, val) => { NF[k] = val; store.set("nf", NF); viewNotifs(k === "q"); };
  const q = $("#nq", v); let tq; q.oninput = () => { clearTimeout(tq); tq = setTimeout(() => setNF("q", q.value.trim()), 250); };
  if (qFocus) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  wireSeg(v, "nlida", x => setNF("lida", x));
  $("#napp", v).onclick = e => menu(e.currentTarget, [{value: "", label: "Todos os apps", checked: !NF.app}, ...apps.map(a => ({value: a, label: titleCase(a), checked: NF.app === a}))], x => setNF("app", x), {role: "listbox", label: "App"});
  $$("[data-et]", v).forEach(b => b.onclick = () => setNF("etiqueta", b.dataset.et));
  const fc = $("#nfc", v); if (fc) fc.onclick = () => { Object.assign(NF, {etiqueta: "", app: "", lida: "", q: ""}); store.set("nf", NF); viewNotifs(); };
  const na = $("#nall", v); if (na) na.onclick = () => markNotifs(NOTIFS.filter(n => !n.lida).map(n => n.id), true);
  $("#nclr", v).onclick = e => menu(e.currentTarget, [{value: "7", label: "Apagar as de mais de 7 dias"}, {value: "30", label: "Apagar as de mais de 30 dias"}, {value: "date", label: "Apagar antes de uma data…"}, {hr: 1}, {value: "all", label: "Apagar todas", danger: true, icon: "trash"}], clearNotifs);
  wireNotifRows(v);
  paintNSel();
}
function wireNotifRows(v) {
  $$(".nrow", v).forEach(r => { const id = r.dataset.nid;
    r.onclick = e => { if (e.target.closest("[data-nact],[data-nsel]")) return; if (e.ctrlKey || e.metaKey || NSEL.size) { toggleNSel(id); return; }
      r.classList.toggle("open"); const n = NOTIFS.find(x => String(x.id) === id); if (n && !n.lida) { n.lida = true; r.classList.remove("unread"); tryApi("/api/notificacoes/" + encodeURIComponent(id), {lida: true}, {silent: true}); S.notifUnread = Math.max(0, S.notifUnread - 1); renderNav(); } };
    r.onkeydown = e => { if (e.key === "Enter" && e.target === r) r.click(); };
    r.oncontextmenu = e => notifCtx(e, id); });
  $$("[data-nsel]", v).forEach(b => b.onclick = e => { e.stopPropagation(); toggleNSel(b.dataset.nsel); });
  $$("[data-nact]", v).forEach(b => b.onclick = async e => { e.stopPropagation(); const n = NOTIFS.find(x => String(x.id) === b.dataset.nact); if (!n) return;
    const a = asList(n.acoes_sugeridas || n.sugestoes)[+b.dataset.ai]; b.setAttribute("aria-busy", "true");
    const r = await tryApi(`/api/notificacoes/${encodeURIComponent(n.id)}/acao`, {acao: "aprovar", i: a && a.i != null ? a.i : +b.dataset.ai});
    if (r) { toast(r.mensagem || r.reply || `${actLabel(a)}: feito`, "", r.card && r.card.id ? {action: "Abrir card", onAction: () => openCard(r.card.id)} : {}); loadCards(true); viewNotifs(); } else b.removeAttribute("aria-busy"); });
}
function toggleNSel(id) { id = String(id); NSEL.has(id) ? NSEL.delete(id) : NSEL.add(id); paintNSel(); }
function paintNSel() {
  $$(".nrow").forEach(x => { const on = NSEL.has(x.dataset.nid); x.classList.toggle("selc", on); const c = $("[data-nsel]", x); if (c) c.setAttribute("aria-checked", on); });
  let bar = $("#selbar"); if (!NSEL.size) { if (bar && bar.dataset.k === "n") bar.remove(); return; }
  if (!bar) { bar = document.createElement("div"); bar.id = "selbar"; bar.className = "selbar"; $(".main").appendChild(bar); } bar.dataset.k = "n";
  bar.innerHTML = `<b>${NSEL.size} selecionada${NSEL.size > 1 ? "s" : ""}</b><button class="btn sm" data-na="read">${ico("check", "sm")} Marcar lidas</button><button class="btn sm" data-na="unread">Marcar não lidas</button><button class="btn sm" data-na="tag">${ico("hash", "sm")} Etiquetar…</button><span class="sp"></span><button class="btn sm ghost" data-na="clear">Limpar seleção</button>`;
  $$("[data-na]", bar).forEach(b => b.onclick = () => { const ids = [...NSEL], a = b.dataset.na;
    if (a === "clear") { NSEL.clear(); paintNSel(); } if (a === "read" || a === "unread") { markNotifs(ids, a === "read"); NSEL.clear(); }
    if (a === "tag") tagMenu(b, null, t => { Promise.all(ids.map(id => api("/api/notificacoes/" + encodeURIComponent(id), {etiqueta: t}).catch(() => null))).then(() => { NSEL.clear(); toast("Etiqueta aplicada"); viewNotifs(); }); }); });
}
async function markNotifs(ids, lida) { await Promise.all(ids.map(id => api("/api/notificacoes/" + encodeURIComponent(id), {lida}).catch(() => null))); toast(lida ? "Marcadas como lidas" : "Marcadas como não lidas"); viewNotifs(); }
function tagMenu(anchor, at, onPick) {
  const items = [...NTAGS.map(t => ({value: t.etiqueta, label: titleCase(t.etiqueta), dot: tagColor(t.etiqueta)})), {hr: 1}, {value: "__new", label: "Nova etiqueta…", icon: "plus"}];
  const pick = v => { if (v !== "__new") return onPick(v); askText("Nova etiqueta", "Nome da etiqueta", "ex.: financeiro", t => t && onPick(t.trim().toLowerCase())); };
  if (at) ctxMenu(at, items, pick); else menu(anchor, items, pick);
}
function askText(title, label, ph, cb, val = "", type = "text") {
  const m = modal({title: esc(title), cls: "sm", body: `<div style="padding:4px 24px 8px"><div class="field"><label for="askt">${esc(label)}</label><input class="inp" id="askt" type="${type}" autofocus placeholder="${esc(ph || "")}" value="${esc(val)}"></div></div>`,
    foot: `<button class="btn ghost" data-close>Cancelar</button><button class="btn primary" id="askok">OK</button>`});
  const ok = () => { const x = $("#askt", m.box).value; m.close(); cb(x); };
  $("#askok", m.box).onclick = ok; $("#askt", m.box).onkeydown = e => { if (e.key === "Enter") ok(); };
}
function notifCtx(e, id) {
  const n = NOTIFS.find(x => String(x.id) === String(id)); if (!n) return;
  const sug = asList(n.acoes_sugeridas || n.sugestoes);
  ctxMenu(e, [...(sug.length ? [{group: "Sugerido"}, ...sug.map((a, i) => ({value: "a:" + i, label: actLabel(a), icon: "spark"})), {hr: 1}] : []),
    {value: "read", label: n.lida ? "Marcar como não lida" : "Marcar como lida", icon: "check"}, {value: "tag", label: "Etiquetar…", icon: "hash"}, {value: "card", label: "Criar card a partir disso", icon: "board"},
    {value: "sel", label: NSEL.has(String(id)) ? "Tirar da seleção" : "Selecionar", icon: "check"}, {value: "mute", label: `Ignorar notificações de ${titleCase(n.app || "este app")}`, icon: "x"},
    {value: "rule", label: "Criar automação parecida…", icon: "tree"}, {hr: 1}, {value: "copy", label: "Copiar texto", icon: "list"}], async v => {
    if (v.startsWith("a:")) { const a = sug[+v.slice(2)]; const r = await tryApi(`/api/notificacoes/${encodeURIComponent(n.id)}/acao`, {acao: "aprovar", i: a && a.i != null ? a.i : +v.slice(2)}); if (r) { toast(`${actLabel(a)}: feito`); loadCards(true); viewNotifs(); } }
    if (v === "read") markNotifs([n.id], !n.lida);
    if (v === "tag") tagMenu(null, e, t => tryApi("/api/notificacoes/" + encodeURIComponent(n.id), {etiqueta: t}).then(r => { if (r) { toast(`Etiqueta “${titleCase(t)}”`); viewNotifs(); } }));
    if (v === "card") { const c = await createCard({kind: "tarefa", title: n.titulo || n.title || titleCase(n.app || "Notificação"), body: n.texto || n.text || "", column: "entrada", tags: n.etiqueta ? [n.etiqueta] : []}); if (c) openCard(c.id); }
    if (v === "sel") toggleNSel(id);
    if (v === "mute") { const cfg = await tryApi("/api/secretario", undefined, {silent: true}) || {}; const ig = [...new Set([...(cfg.apps_ignorados || []), n.app].filter(Boolean))];
      if (await tryApi("/api/secretario", {...cfg, apps_ignorados: ig})) toast(`O secretário vai ignorar ${titleCase(n.app)}`); }
    if (v === "rule") { go("automacoes"); setTimeout(() => autoBuilder({nome: `Notificações de ${titleCase(n.app || "")}`, ativa: true, quando: {fontes: ["notificacoes"], apps: n.app ? [n.app] : [], palavras: []}, entao: [{acao: "criar_card", params: {titulo: "{titulo}"}}]}), 60); }
    if (v === "copy") copyText([n.titulo, n.texto].filter(Boolean).join("\n"));
  });
}
function clearNotifs(v) {
  const run = async antes => { const r = await tryApi("/api/notificacoes/limpar", antes ? {antes_de: antes} : {}); if (r) { toast(r.apagadas != null ? `${r.apagadas} notificação(ões) apagada(s)` : "Notificações limpas"); viewNotifs(); } };
  if (v === "all") { confirmBox("Apagar todas as notificações?", "Isso tira todas da lista. Cards e automações continuam.", "Apagar todas", () => run(null)); return; }
  if (v === "date") { askText("Apagar antes de", "Apagar as notificações recebidas antes de", "", d => d && run(d + "T00:00:00"), ymd(addDays(new Date(), -7)), "date"); return; }
  run(isoLocal(addDays(startDay(new Date()), -(+v))));
}
function confirmBox(title, text, okLabel, onOk, danger = true) {
  const m = modal({title: esc(title), cls: "sm", body: `<div style="padding:0 24px 10px"><p>${esc(text)}</p></div>`, foot: `<button class="btn ghost" data-close>Cancelar</button><button class="btn ${danger ? "danger" : "primary"}" id="cbok">${esc(okLabel)}</button>`});
  $("#cbok", m.box).onclick = () => { m.close(); onOk(); };
}

/* ---------------------------------------------------------------- AUTOMAÇÕES */
let treeEditorLoaded = null;
function loadScript(src) { return new Promise(res => { if ($(`script[src="${src}"]`)) return res(true); const s = document.createElement("script"); s.src = src; s.onload = () => res(true); s.onerror = () => { s.remove(); res(false); }; document.head.appendChild(s); }); }
const FONTES = [["notificacoes", "Notificações do Windows"], ["email", "E-mail"], ["whatsapp", "WhatsApp"], ["mensagens", "Mensagens coladas"], ["cards", "Cards novos"]];
const ACOES = [["criar_card", "Criar um card"], ["alerta", "Criar um alerta"], ["tarefa", "Criar uma tarefa"], ["notificar", "Me avisar"], ["etiquetar", "Pôr uma etiqueta"], ["arquivar", "Arquivar"], ["abrir_app", "Abrir um app"], ["responder_sugestao", "Sugerir uma resposta"]];
const CAMPOS = [["valor", "Valor"], ["prazo", "Prazo"], ["remetente", "Quem mandou"], ["empresa", "Empresa"], ["data", "Data"], ["assunto", "Assunto"], ["codigo", "Código"], ["link", "Link"]];
const joinPt = a => a.length <= 1 ? (a[0] || "") : a.slice(0, -1).join(", ") + " e " + a[a.length - 1];
function autoPhrase(a) {
  const w = a.quando || {}, fs = (w.fontes || []).map(f => (FONTES.find(x => x[0] === f) || [, f])[1].toLowerCase());
  const se = [fs.length ? `chegar ${joinPt(fs)}` : "chegar algo", (w.apps || []).length ? `de ${joinPt(w.apps.map(titleCase))}` : "", w.remetente ? `de ${titleCase(w.remetente)}` : "",
    (w.palavras || []).length ? `com ${joinPt(w.palavras.map(x => `“${x}”`))}` : "", w.assunto ? `sobre ${w.assunto}` : "", w.categoria ? `da categoria ${w.categoria}` : ""].filter(Boolean).join(" ");
  const en = (a.entao || []).map(x => { const pa = x.params || {}, l = (ACOES.find(y => y[0] === x.acao) || [, actLabel(x.acao || "")])[1].toLowerCase();
    return l + (pa.titulo ? ` “${pa.titulo}”` : "") + ((pa.campos || []).length ? ` com ${joinPt(pa.campos.map(c => (CAMPOS.find(y => y[0] === c) || [, c])[1].toLowerCase()))}` : "") + (pa.etiqueta ? ` (${pa.etiqueta})` : ""); });
  return {se: "Se " + se, entao: "Então " + (en.length ? joinPt(en) : "nada ainda")};
}
let AUTOS = [], MODELOS = [], SEC = null;
async function viewAutos(openGraph) {
  const v = viewEl();
  v.innerHTML = `<div class="view-inner autosv"><div class="vh"><div><span class="kicker">Automações</span><h1>Se acontecer <em>isso</em>, faça <em>aquilo</em></h1><p>Diga em uma frase o que você quer, use uma receita pronta ou monte passo a passo.</p></div></div>
    <div id="secbox"><div class="sk" style="height:84px"></div></div>
    <section class="dcard fromtext"><label for="atx" class="kicker">Criar com uma frase</label>
      <div class="ftrow"><input class="inp" id="atx" placeholder="Se eu receber uma mensagem sobre orçamento, crie um card de alerta com valor, prazo e quem mandou">
      <button class="btn primary" id="atxb">${ico("spark", "sm")} Criar</button><button class="btn" id="anew">${ico("plus", "sm")} Montar passo a passo</button></div>
      <p class="hint">Você revisa tudo antes de salvar.</p></section>
    <section class="group"><h3>Receitas prontas</h3><div class="recipes" id="rec"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div></section>
    <section class="group"><h3>Minhas automações <span id="acnt"></span></h3><div id="alist"><div class="sk"></div></div></section>
    <section class="group"><h3>Árvores avançadas</h3><div class="tlistb" id="tl"><div class="sk"></div></div>
      <div class="row" style="margin-top:10px"><button class="btn sm" id="ntree">${ico("tree", "sm")} Nova árvore no editor visual</button><button class="btn sm ghost" id="yaml">Editar como texto (YAML)</button></div></section></div>`;
  const atx = $("#atx", v);
  const fromText = async () => { const t = atx.value.trim(); if (!t) { atx.focus(); return; } const b = $("#atxb", v); b.setAttribute("aria-busy", "true");
    try { const r = await api("/api/automacoes/de_texto", {texto: t}); const au = r && (r.automacao || r); b.removeAttribute("aria-busy");
      if (au && (au.quando || au.entao)) { autoBuilder(au, (r.notas || []).concat(r.dicas || [])); atx.value = ""; } else toast("Não entendi a frase. Tente: “Se chegar boleto, crie uma tarefa com valor e prazo”.", "warn"); }
    catch (e) { b.removeAttribute("aria-busy"); toast(human(e), e.status === 404 ? "warn" : "bad"); } };
  $("#atxb", v).onclick = fromText; atx.onkeydown = e => { if (e.key === "Enter") fromText(); };
  $("#anew", v).onclick = () => autoBuilder(null);
  $("#yaml", v).onclick = () => openFull({yaml: true});
  $("#ntree", v).onclick = () => openFull({graph: {name: "nova árvore", nodes: [], edges: []}, isNew: true});
  if (openGraph) openFull({graph: openGraph, isNew: true});
  drawSec(v); drawRecipes(v); drawAutos(v); drawTrees(v);
}
async function drawSec(v) {
  try { SEC = await api("/api/secretario"); } catch (e) { SEC = null; }
  const box = $("#secbox", v); if (!box) return;
  const on = SEC && SEC.ativo, modo = SEC && SEC.modo === "agir" ? "age e avisa" : "sugere e você aprova";
  box.innerHTML = `<section class="dcard secc ${on ? "on" : ""}"><div class="secic">${ico("spark")}</div><div class="sect"><h3>Secretário ${on ? `<span class="chip on"><i class="okdot"></i>ligado</span>` : `<span class="chip">desligado</span>`}</h3>
    <p>${SEC === null ? "O secretário ainda não está disponível nesta versão." : on ? `Lê as notificações e mensagens, ${modo}. ${(SEC.vips || []).length ? `VIPs: ${esc(joinPt(SEC.vips.slice(0, 3).map(titleCase)))}. ` : ""}${(SEC.apps_ignorados || []).length ? `Ignora ${esc(joinPt(SEC.apps_ignorados.slice(0, 3).map(titleCase)))}.` : ""}` : "Ligue para o Djorge organizar sozinho o que chega: etiquetar, criar cards e avisar só o que importa."}</p></div>
    ${SEC === null ? "" : `<button class="btn ${on ? "" : "primary"}" id="secw">${on ? "Ajustar" : "Configurar em 3 passos"}</button>`}</section>`;
  const w = $("#secw", box); if (w) w.onclick = () => secretaryWizard(() => drawSec(v));
}
async function drawRecipes(v) {
  const box = $("#rec", v); if (!box) return;
  try { MODELOS = asList(await api("/api/automacoes/modelos"), "modelos", "items"); } catch (e) { MODELOS = []; }
  if (!box.isConnected) return;
  const IC = {boleto: "🧾", boletos: "🧾", orcamento: "💼", orcamentos: "💼", entrega: "📦", entregas: "📦", reuniao: "📅", reunioes: "📅", codigo: "🔑", codigos: "🔑", chefe: "⭐", vip: "⭐"};
  box.innerHTML = MODELOS.length ? MODELOS.map((m, i) => { const au = m.automacao || m, ph = autoPhrase(au), k = String(m.id || m.nome || "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "").split(/[^a-z]/)[0];
    return `<article class="recipe" data-ri="${i}"><span class="rem" aria-hidden="true">${m.icone || IC[k] || "✨"}</span><div><b>${esc(m.nome || au.nome || "Receita")}</b><p>${esc(m.descricao || ph.se + ". " + ph.entao + ".")}</p></div>
      <div class="ract2"><button class="btn sm primary" data-use="${i}">Usar</button><button class="btn sm ghost" data-see="${i}" data-tip="Ver e ajustar antes">Ajustar</button></div></article>`; }).join("") :
    `<p class="hint">As receitas prontas aparecem aqui quando o secretário estiver disponível.</p>`;
  const pick = i => { const m = MODELOS[i], au = JSON.parse(JSON.stringify(m.automacao || m)); delete au.id; au.nome = au.nome || m.nome; if (au.ativa === undefined) au.ativa = true; return au; };
  $$("[data-use]", box).forEach(b => b.onclick = async () => { b.setAttribute("aria-busy", "true"); const m0 = MODELOS[+b.dataset.use]; const r = await tryApi("/api/automacoes", m0.id ? {modelo: m0.id} : pick(+b.dataset.use));
    b.removeAttribute("aria-busy"); if (r) { toast(`Receita “${MODELOS[+b.dataset.use].nome || "pronta"}” ligada`); drawAutos(v); } });
  $$("[data-see]", box).forEach(b => b.onclick = () => autoBuilder(pick(+b.dataset.see)));
}
async function drawAutos(v) {
  const box = $("#alist", v); if (!box) return;
  try { AUTOS = asList(await api("/api/automacoes"), "automacoes", "items"); S.autosOk = true; } catch (e) { AUTOS = []; S.autosOk = false; }
  if (!box.isConnected) return;
  $("#acnt", v).textContent = AUTOS.length || "";
  box.innerHTML = AUTOS.length ? `<div class="alist">${AUTOS.map((a, i) => { const ph = autoPhrase(a);
    return `<article class="acard ${a.ativa === false ? "off" : ""}" data-ai="${i}" data-ctx="auto" tabindex="0"><div class="at"><b>${esc(a.nome || "Automação")}</b>
      <p class="se"><span class="kw">Se</span>${esc(ph.se.slice(3))}</p><p class="en"><span class="kw">Então</span>${esc(ph.entao.slice(6))}</p>
      ${a.contagem ? `<small class="hint">rodou ${a.contagem} vez${a.contagem > 1 ? "es" : ""}</small>` : ""}</div>
      <div class="aa">${swHTML("aon" + i, a.ativa !== false)}<button class="iconbtn" data-am="${i}" aria-label="Mais opções" data-tip="Mais opções">⋯</button></div></article>`; }).join("")}</div>` :
    empty("Nenhuma automação ainda", S.autosOk ? "Use uma receita pronta ou escreva uma frase acima." : "As automações “Se… então…” chegam com o secretário. Enquanto isso, use as árvores avançadas abaixo.");
  $$(".acard", box).forEach(el => { const i = +el.dataset.ai, a = AUTOS[i];
    $(`#aon${i}`, el).onchange = async e => { const r = await tryApi(`/api/automacoes/${encodeURIComponent(a.id)}/${e.target.checked ? "ativar" : "desativar"}`, {}); if (r) { a.ativa = e.target.checked; el.classList.toggle("off", !a.ativa); toast(a.ativa ? "Automação ligada" : "Automação pausada"); } else e.target.checked = !e.target.checked; };
    $(".sw", el).onclick = e => e.stopPropagation();
    el.onclick = e => { if (e.target.closest(".aa")) return; autoBuilder(a); };
    el.onkeydown = e => { if (e.key === "Enter" && e.target === el) autoBuilder(a); };
    const items = [{value: "edit", label: "Editar", icon: "edit"}, {value: "test", label: "Testar com um texto", icon: "spark"}, {value: "dup", label: "Duplicar", icon: "plus"}, {value: "tog", label: a.ativa === false ? "Ligar" : "Pausar", icon: "check"}, {hr: 1}, {value: "del", label: "Apagar", icon: "trash", danger: true}];
    const pickA = async x => { if (x === "edit") autoBuilder(a); if (x === "test") autoBuilder(a, [], true);
      if (x === "dup") { const c = JSON.parse(JSON.stringify(a)); delete c.id; c.nome = (a.nome || "Automação") + " (cópia)"; if (await tryApi("/api/automacoes", c)) { toast("Duplicada"); drawAutos(v); } }
      if (x === "tog") { const r = await tryApi(`/api/automacoes/${encodeURIComponent(a.id)}/${a.ativa === false ? "ativar" : "desativar"}`, {}); if (r) drawAutos(v); }
      if (x === "del") confirmBox("Apagar automação?", `“${a.nome || "Automação"}” deixa de rodar.`, "Apagar", async () => { if (await tryApi(`/api/automacoes/${encodeURIComponent(a.id)}/delete`, {})) { toast("Automação apagada"); drawAutos(v); } }); };
    $("[data-am]", el).onclick = e => { e.stopPropagation(); menu(e.currentTarget, items, pickA); };
    el.oncontextmenu = e => ctxMenu(e, items, pickA); });
}
async function drawTrees(v) {
  const tl = $("#tl", v); if (!tl) return;
  let graphs = null;
  try { const r = await api("/api/trees/graph"); graphs = (r && (r.trees || r.graphs)) || (Array.isArray(r) ? r : null); } catch (e) { graphs = null; }
  if (!tl.isConnected) return;
  S.trees = graphs;
  const names = graphs ? graphs.map(g => g.name || g.title) : ((S.status && S.status.trees) || []);
  tl.innerHTML = names.length ? names.map((n, i) => `<button type="button" data-ti="${i}" data-ctx="tree">${ico("tree", "sm")}<b>${esc(titleCase(n || "árvore " + (i + 1)))}</b>${graphs && (graphs[i].graph || graphs[i]).nodes ? `<small>${(graphs[i].graph || graphs[i]).nodes.length} passos</small>` : ""}</button>`).join("") : `<p class="hint">Nenhuma árvore ainda.</p>`;
  $$("[data-ti]", tl).forEach(b => { const open = () => graphs ? openFull({graph: graphs[+b.dataset.ti]}) : openFull({yaml: true}); b.onclick = open;
    b.oncontextmenu = e => ctxMenu(e, [{value: "o", label: "Abrir no editor visual", icon: "tree"}, {value: "y", label: "Editar como texto", icon: "edit"}], x => x === "o" ? open() : openFull({yaml: true})); });
}
/* ----- construtor “Se… / Então…” ----- */
function autoBuilder(a0, notes = [], focusTest) {
  const a = JSON.parse(JSON.stringify(a0 || {nome: "", ativa: true, quando: {fontes: ["notificacoes"], apps: [], remetente: "", palavras: [], assunto: ""}, entao: [{acao: "criar_card", params: {titulo: "", campos: []}}]}));
  a.quando = a.quando || {}; a.entao = a.entao && a.entao.length ? a.entao : [{acao: "criar_card", params: {}}];
  const w = a.quando;
  const m = modal({title: a0 && a0.id ? "Editar automação" : "Nova automação", cls: "builder", body: `<div class="bbody">
    ${notes.length ? `<div class="leave">${ico("spark", "sm")}<div>${notes.map(esc).join("<br>")}</div></div>` : ""}
    <div class="field"><label for="b-nome">Nome</label><input class="inp" id="b-nome" value="${esc(a.nome || "")}" placeholder="ex.: Alerta de orçamento"></div>
    <section class="bsec"><h4><span class="kw">Se…</span> acontecer isto</h4>
      <div class="field"><span class="lbl">Quando chegar</span><div class="row" id="b-fontes" style="gap:6px">${FONTES.map(([k, l]) => `<button type="button" class="chip ${(w.fontes || []).includes(k) ? "on" : ""}" aria-pressed="${(w.fontes || []).includes(k)}" data-f="${k}">${esc(l)}</button>`).join("")}</div></div>
      <div class="grid2"><div class="field"><label for="b-appi">De qual app <small>(opcional)</small></label><div class="tagin" id="b-apps"></div></div>
        <div class="field"><label for="b-rem">De quem <small>(opcional)</small></label><input class="inp" id="b-rem" value="${esc(w.remetente || "")}" placeholder="ex.: Carlos, @empresa.com" list="b-cont"><datalist id="b-cont"></datalist></div></div>
      <div class="field"><label for="b-pali">Com alguma destas palavras <small>(opcional)</small></label><div class="tagin" id="b-pal"></div></div>
      <div class="field"><label for="b-ass">Sobre o assunto <small>(descreva do seu jeito; o Djorge julga)</small></label><input class="inp" id="b-ass" value="${esc(w.assunto || "")}" placeholder="ex.: mensagem sobre orçamento ou proposta de preço"></div>
    </section>
    <section class="bsec"><h4><span class="kw">Então…</span> faça isto</h4><div id="b-acts"></div><button type="button" class="addc" id="b-add">${ico("plus", "sm")} Adicionar outra ação</button></section>
    <section class="bsec test"><h4>Testar com um exemplo</h4><div class="ftrow"><textarea class="ta" id="b-tt" rows="2" placeholder="Cole uma mensagem de exemplo: “Oi, segue o orçamento de R$ 3.200, válido até sexta. — Carlos”"></textarea><button class="btn" id="b-test">${ico("spark", "sm")} Testar</button></div><div id="b-tr" aria-live="polite"></div></section>
    <p class="bphrase" id="b-ph"></p></div>`,
    foot: `${a0 && a0.id ? `<button class="btn ghost danger" id="b-del">${ico("trash", "sm")} Apagar</button>` : ""}<span style="flex:1"></span>${swHTML("b-on", a.ativa !== false, "Ligada")}<button class="btn ghost" data-close>Cancelar</button><button class="btn primary" id="b-save">${ico("check", "sm")} Salvar automação</button>`});
  const B = m.box;
  const phrase = () => { const ph = autoPhrase(a); $("#b-ph", B).innerHTML = `<span class="kw">Se</span> ${esc(ph.se.slice(3))} <span class="kw">Então</span> ${esc(ph.entao.slice(6))}`; };
  $$("#b-fontes [data-f]", B).forEach(b => b.onclick = () => { const s = new Set(w.fontes || []); s.has(b.dataset.f) ? s.delete(b.dataset.f) : s.add(b.dataset.f); w.fontes = [...s]; b.classList.toggle("on"); b.setAttribute("aria-pressed", s.has(b.dataset.f)); phrase(); });
  tagInput($("#b-apps", B), w.apps || [], "b-appi", "ex.: WhatsApp, Outlook", x => { w.apps = x; phrase(); });
  tagInput($("#b-pal", B), w.palavras || [], "b-pali", "palavra + Enter", x => { w.palavras = x; phrase(); });
  $("#b-rem", B).oninput = e => { w.remetente = e.target.value; phrase(); contactsInto($("#b-cont", B), e.target.value); };
  $("#b-ass", B).oninput = e => { w.assunto = e.target.value; phrase(); };
  $("#b-nome", B).oninput = e => a.nome = e.target.value;
  const acts = () => { const box = $("#b-acts", B);
    box.innerHTML = a.entao.map((x, i) => { const pa = x.params = x.params || {};
      return `<div class="bact" data-i="${i}"><div class="row"><span class="num">${i + 1}</span>${selHTML("ba" + i, ACOES, x.acao || "criar_card", "Ação")}<span class="sp" style="flex:1"></span>${a.entao.length > 1 ? `<button type="button" class="iconbtn" data-rm="${i}" aria-label="Remover ação" data-tip="Remover">${ico("x", "sm")}</button>` : ""}</div>
        ${["criar_card", "alerta", "tarefa", "notificar"].includes(x.acao || "criar_card") ? `<div class="field"><label>Título</label><input class="inp" data-pk="titulo" value="${esc(pa.titulo || "")}" placeholder="ex.: Alerta de orçamento — {remetente}">
          <div class="te-phs">${["{remetente}", "{assunto}", "{valor}", "{prazo}", "{app}"].map(t => `<button type="button" data-ins="${t}">${t}</button>`).join("")}</div></div>
          <div class="field"><span class="lbl">Incluir no card</span><div class="row" style="gap:6px">${CAMPOS.map(([k, l]) => `<button type="button" class="chip ${(pa.campos || []).includes(k) ? "on" : ""}" data-c="${k}" aria-pressed="${(pa.campos || []).includes(k)}">${esc(l)}</button>`).join("")}</div></div>
          ${x.acao === "notificar" ? "" : `<div class="grid2"><div class="field"><span class="lbl">Coluna</span>${selHTML("bc" + i, COLS.map(c => [c.id, c.label]), pa.coluna || "entrada", "Coluna")}</div><div class="field"><span class="lbl">Prioridade</span>${selHTML("bp" + i, PRI.map((l, k) => [String(k), l]), String(pa.prioridade ?? (x.acao === "alerta" ? 3 : 0)), "Prioridade")}</div></div>`}` : ""}
        ${x.acao === "etiquetar" ? `<div class="field"><label>Etiqueta</label><input class="inp" data-pk="etiqueta" value="${esc(pa.etiqueta || "")}" placeholder="ex.: financeiro"></div>` : ""}
        ${x.acao === "abrir_app" ? `<div class="field"><label>App ou endereço</label><input class="inp" data-pk="app" value="${esc(pa.app || pa.url || "")}" placeholder="ex.: whatsapp, https://…"></div>` : ""}</div>`; }).join("");
    a.entao.forEach((x, i) => { const el = $(`.bact[data-i="${i}"]`, box), pa = x.params;
      wireSel(el, "ba" + i, ACOES, val => { x.acao = val; acts(); phrase(); });
      wireSel(el, "bc" + i, COLS.map(c => [c.id, c.label]), val => pa.coluna = val); wireSel(el, "bp" + i, PRI.map((l, k) => [String(k), l]), val => pa.prioridade = +val);
      $$("[data-pk]", el).forEach(inp => inp.oninput = () => { pa[inp.dataset.pk] = inp.value; phrase(); });
      $$("[data-ins]", el).forEach(b => b.onclick = () => { const t = $("[data-pk=titulo]", el); const p0 = t.selectionStart ?? t.value.length; t.value = t.value.slice(0, p0) + b.dataset.ins + t.value.slice(t.selectionEnd ?? p0); pa.titulo = t.value; t.focus(); phrase(); });
      $$("[data-c]", el).forEach(b => b.onclick = () => { const s = new Set(pa.campos || []); s.has(b.dataset.c) ? s.delete(b.dataset.c) : s.add(b.dataset.c); pa.campos = CAMPOS.map(c => c[0]).filter(k => s.has(k)); b.classList.toggle("on"); b.setAttribute("aria-pressed", s.has(b.dataset.c)); phrase(); });
      const rm = $("[data-rm]", el); if (rm) rm.onclick = () => { a.entao.splice(i, 1); acts(); phrase(); }; }); };
  acts(); phrase();
  $("#b-add", B).onclick = () => { a.entao.push({acao: "notificar", params: {}}); acts(); phrase(); };
  $("#b-test", B).onclick = async e => { const t = $("#b-tt", B).value.trim(), out = $("#b-tr", B); if (!t) { $("#b-tt", B).focus(); return; }
    const btn = e.currentTarget; btn.setAttribute("aria-busy", "true");
    try { const r = await api(`/api/automacoes/${encodeURIComponent(a.id || "rascunho")}/testar`, {texto: t, automacao: a}); const hit = r.casou ?? r.casa ?? r.match ?? r.ok;
      const did = asList(r.acoes || r.resultado || r.faria, "acoes");
      out.innerHTML = `<div class="tres ${hit ? "ok" : "no"}">${ico(hit ? "check" : "x", "sm")}<div><b>${esc(r.frase || (hit ? "Isso dispararia a automação." : "Isso não dispararia a automação."))}</b>${(r.motivos || []).length ? `<small>${esc(r.motivos.join(" · "))}</small>` : ""}
        ${did.length ? `<ul>${did.map(x => `<li>${esc(typeof x === "string" ? x : (x.descricao || x.texto || (ACT_PT[x.action || x.acao] || actLabel(x)) + ((x.titulo || (x.params && x.params.titulo)) ? ` “${x.titulo || x.params.titulo}”` : "") + (x.etiqueta ? ` (${x.etiqueta})` : "")))}</li>`).join("")}</ul>` : ""}
        ${r.campos && Object.keys(r.campos).length ? `<div class="fields">${Object.entries(r.campos).map(([k, x]) => `<span class="fchip c-outro"><small>${esc((CAMPOS.find(c => c[0] === k) || [, k])[1])}</small><b>${esc(k === "valor" && !String(x).includes("R$") ? fmtMoney(x) : typeof x === "object" ? (x.texto || x.valor_fmt || JSON.stringify(x)) : x)}</b></span>`).join("")}</div>` : ""}</div></div>`; }
    catch (er) { out.innerHTML = `<p class="hint">${esc(er.status === 404 ? "O teste ainda não está disponível nesta versão." : human(er))}</p>`; }
    btn.removeAttribute("aria-busy"); };
  if (focusTest) setTimeout(() => $("#b-tt", B).focus(), 60);
  $("#b-save", B).onclick = async e => { a.nome = $("#b-nome", B).value.trim() || autoPhrase(a).se.slice(0, 60); a.ativa = $("#b-on", B).checked;
    if (!(w.fontes || []).length && !(w.palavras || []).length && !w.assunto && !w.remetente && !(w.apps || []).length) { toast("Diga pelo menos quando a automação deve rodar (o “Se…”).", "warn"); return; }
    e.currentTarget.setAttribute("aria-busy", "true"); const r = await tryApi("/api/automacoes", a); e.currentTarget.removeAttribute("aria-busy");
    if (r) { m.close(); toast("Automação salva", "", {title: a.nome}); if (S.view === "automacoes") drawAutos(viewEl()); } };
  const del = $("#b-del", B); if (del) del.onclick = () => confirmBox("Apagar automação?", `“${a.nome}” deixa de rodar.`, "Apagar", async () => { if (await tryApi(`/api/automacoes/${encodeURIComponent(a.id)}/delete`, {})) { m.close(); toast("Automação apagada"); drawAutos(viewEl()); } });
}
let contT = null;
function contactsInto(dl, q) { clearTimeout(contT); if (!q || q.length < 2) return; contT = setTimeout(async () => { try { const r = asList(await api("/api/contatos?q=" + encodeURIComponent(q)), "contatos", "items");
  dl.innerHTML = r.slice(0, 8).map(c => `<option value="${esc(titleCase(c.nome || c.email || c.telefone))}">${esc([c.telefone, c.email].filter(Boolean).join(" · "))}</option>`).join(""); } catch (e) {} }, 200); }
/* ----- secretário em 3 passos ----- */
async function secretaryWizard(after) {
  let cfg = {}; try { cfg = await api("/api/secretario") || {}; } catch (e) { if (e.status === 404 || e.status === 503) return toast("O secretário ainda não está disponível nesta versão.", "warn"); }
  const st = {ativo: cfg.ativo !== false, modo: cfg.modo || "sugerir", apps_ignorados: [...(cfg.apps_ignorados || [])], vips: [...(cfg.vips || [])], horario_silencio: typeof cfg.horario_silencio === "object" && cfg.horario_silencio ? (cfg.horario_silencio.ativo === false ? "" : `${cfg.horario_silencio.inicio || ""}-${cfg.horario_silencio.fim || ""}`) : (cfg.horario_silencio || ""), resumo_diario_hora: cfg.resumo_diario_hora || "08:00"};
  let step = 0; const apps = [...new Set([...(NOTIFS || []).map(n => n.app), "Teams", "Slack", "Spotify", "Steam", "Discord", "Windows Update"].filter(Boolean))];
  const m = modal({title: "Configurar o secretário", cls: "md wiz", body: `<div class="wsteps" aria-hidden="true"><i></i><i></i><i></i></div><div id="wb" class="wbody"></div>`, foot: `<button class="btn ghost" id="wback">Voltar</button><span style="flex:1"></span><span class="hint" id="wn"></span><button class="btn primary" id="wnext">Continuar</button>`});
  const B = m.box;
  const draw = () => { $$(".wsteps i", B).forEach((x, i) => x.classList.toggle("on", i <= step)); $("#wn", B).textContent = `Passo ${step + 1} de 3`; $("#wback", B).style.visibility = step ? "visible" : "hidden";
    $("#wnext", B).textContent = step === 2 ? "Concluir" : "Continuar"; const wb = $("#wb", B);
    if (step === 0) { wb.innerHTML = `<h3>Ligar o secretário</h3><p class="hint">Ele lê as notificações do Windows e as mensagens que chegam, separa por etiqueta e cuida do que importa. Tudo neste computador.</p>
      ${setRow("Secretário ligado", "Pode desligar quando quiser.", swHTML("w-on", st.ativo))}
      ${setRow("Resumo do dia", "Um aviso com o que importa, todo dia neste horário.", `<input class="inp" type="time" id="w-res" value="${esc(st.resumo_diario_hora)}" style="width:120px" aria-label="Hora do resumo">`)}
      ${setRow("Horário de silêncio", "Sem avisos nesse intervalo (ex.: 22:00-07:00).", `<input class="inp" id="w-sil" value="${esc(st.horario_silencio)}" placeholder="22:00-07:00" style="width:140px" aria-label="Horário de silêncio">`)}`;
      $("#w-on", wb).onchange = e => st.ativo = e.target.checked; $("#w-res", wb).onchange = e => st.resumo_diario_hora = e.target.value; $("#w-sil", wb).oninput = e => st.horario_silencio = e.target.value; }
    if (step === 1) { wb.innerHTML = `<h3>O que ignorar e quem é VIP</h3><p class="hint">Apps ignorados não viram nada. Pessoas VIP sempre chegam como importantes.</p>
      <div class="field"><span class="lbl">Ignorar estes apps</span><div class="row" style="gap:6px" id="w-apps">${apps.map(x => `<button type="button" class="chip ${st.apps_ignorados.includes(x) ? "on" : ""}" data-app="${esc(x)}" aria-pressed="${st.apps_ignorados.includes(x)}">${esc(titleCase(x))}</button>`).join("")}</div></div>
      <div class="field"><label for="w-ai">Outro app</label><div class="tagin" id="w-ax"></div></div>
      <div class="field"><label for="w-vi">Pessoas VIP</label><div class="tagin" id="w-vip"></div><small>Nomes, e-mails ou telefones. Ex.: chefe, Ana Souza, @empresa.com.br</small></div>`;
      $$("[data-app]", wb).forEach(b => b.onclick = () => { const s = new Set(st.apps_ignorados); s.has(b.dataset.app) ? s.delete(b.dataset.app) : s.add(b.dataset.app); st.apps_ignorados = [...s]; b.classList.toggle("on"); b.setAttribute("aria-pressed", s.has(b.dataset.app)); });
      tagInput($("#w-ax", wb), st.apps_ignorados.filter(x => !apps.includes(x)), "w-ai", "nome do app + Enter", x => { st.apps_ignorados = [...new Set([...st.apps_ignorados.filter(y => apps.includes(y)), ...x])]; });
      tagInput($("#w-vip", wb), st.vips, "w-vi", "nome + Enter", x => st.vips = x); }
    if (step === 2) { wb.innerHTML = `<h3>Sugerir ou agir?</h3><p class="hint">Você pode mudar depois.</p><div class="wopts" role="radiogroup" aria-label="Modo">
      <button type="button" role="radio" class="wopt" data-m="sugerir" aria-checked="${st.modo === "sugerir"}"><span class="rem">🤝</span><b>Sugerir</b><p>O secretário propõe (criar card, etiquetar, arquivar) e você aprova num clique. Recomendado para começar.</p></button>
      <button type="button" role="radio" class="wopt" data-m="agir" aria-checked="${st.modo === "agir"}"><span class="rem">⚡</span><b>Agir e avisar</b><p>Ele faz sozinho e te avisa. Tudo pode ser desfeito.</p></button></div>`;
      $$(".wopt", wb).forEach(b => b.onclick = () => { st.modo = b.dataset.m; $$(".wopt", wb).forEach(x => x.setAttribute("aria-checked", x === b)); }); }
  };
  $("#wback", B).onclick = () => { step = Math.max(0, step - 1); draw(); };
  $("#wnext", B).onclick = async e => { if (step < 2) { step++; draw(); return; } e.currentTarget.setAttribute("aria-busy", "true");
    const hs = /^(\d\d:\d\d)\s*-\s*(\d\d:\d\d)$/.exec(st.horario_silencio.trim());
    const r = await tryApi("/api/secretario", {...st, horario_silencio: hs ? {ativo: true, inicio: hs[1], fim: hs[2]} : {ativo: false}}); e.currentTarget.removeAttribute("aria-busy");
    if (r) { m.close(); toast(st.ativo ? `Secretário ligado · ${st.modo === "agir" ? "age e avisa" : "sugere e você aprova"}` : "Secretário desligado", "", {title: "Secretário"}); after && after(); } };
  draw();
}
/* ----- editor de árvore em tela cheia ----- */
let FULL = null;
function closeFull(force) { if (!FULL) return true; if (!force && FULL.dirty && !confirm("Sair sem salvar as mudanças da árvore?")) return false;
  try { FULL.ed && FULL.ed.destroy && FULL.ed.destroy(); } catch (e) {} FULL.el.remove(); FULL = null; document.body.classList.remove("fullopen"); return true; }
async function openFull({graph, yaml, isNew}) {
  closeFull(true);
  const el = document.createElement("div"); el.className = "fullview"; el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); el.setAttribute("aria-label", "Editor de automação");
  const g0 = graph || {}, name = g0.name || g0.title || (yaml ? "Árvores (texto)" : "nova árvore");
  el.innerHTML = `<header class="fvh"><button class="btn" id="fv-back">${ico("up", "sm back")} Voltar</button><div class="fvt"><span class="kicker">${yaml ? "Editar como texto" : "Editor visual"}</span><input class="fvname" id="fv-name" value="${esc(name)}" aria-label="Nome da árvore" ${yaml ? "disabled" : ""}></div>
    <span class="sp"></span><span class="hint fvhelp">${yaml ? "" : "Cada caixa é uma pergunta (“Se…”) ou uma ação (“Então…”). Arraste da bolinha de uma resposta até a próxima caixa para ligar."}</span>
    <span class="chip" hidden id="fv-dirty">alterações não salvas</span>${yaml ? "" : `<button class="btn primary" id="fv-save">${ico("check", "sm")} Salvar</button>`}</header><div class="fvbody" id="fv-body"><div class="sk" style="height:100%;border-radius:0"></div></div>`;
  document.body.appendChild(el); document.body.classList.add("fullopen");
  FULL = {el, dirty: false};
  $("#fv-back", el).onclick = () => closeFull();
  el.addEventListener("keydown", e => { if (e.key === "Escape" && !openMenu && !e.target.closest("input,textarea,select,.te-root")) { e.stopPropagation(); closeFull(); } });
  const body = $("#fv-body", el);
  if (yaml) { yamlEditor(body); setTimeout(() => $("#fv-back", el).focus(), 30); return; }
  if (treeEditorLoaded === null) treeEditorLoaded = await loadScript("/static/tree-editor.js") && !!window.TreeEditor;
  if (!FULL || FULL.el !== el) return;
  if (!treeEditorLoaded) { yamlEditor(body, "O editor visual ainda não está disponível; edite o texto da árvore."); return; }
  let gr = g0.graph || g0;
  if ((!gr.nodes || !gr.nodes.length) && g0.tree && window.TreeEditor.fromTree) { try { gr = await window.TreeEditor.fromTree(g0.tree); } catch (e) { toast(human(e), "bad"); } }
  body.innerHTML = ""; const host = document.createElement("div"); host.className = "fvhost"; body.appendChild(host);
  try { FULL.ed = window.TreeEditor.mount(host, {graph: gr, onChange: x => { FULL.g = x; if (!FULL.ready) return; FULL.dirty = true; FULL.g = x; $("#fv-dirty", el).hidden = false; }}); }
  catch (e) { console.warn(e); yamlEditor(body, "Não consegui abrir o editor visual."); return; }
  setTimeout(() => { if (FULL) FULL.ready = true; }, 400);
  if (isNew) { FULL.dirty = !!(gr.nodes && gr.nodes.length); $("#fv-dirty", el).hidden = !FULL.dirty; }
  $("#fv-save", el).onclick = async e => { const b = e.currentTarget; b.setAttribute("aria-busy", "true");
    const gg = FULL.g || (FULL.ed.getGraph ? FULL.ed.getGraph() : gr); gg.name = $("#fv-name", el).value.trim() || gg.name || "árvore";
    try { await api("/api/trees/graph", {graph: gg, name: gg.name}); FULL.dirty = false; $("#fv-dirty", el).hidden = true; toast("Árvore salva", "", {title: titleCase(gg.name)}); pollStatus(); }
    catch (er) { toast(er.data && er.data.errors ? "A árvore tem problemas: " + er.data.errors.slice(0, 2).map(x => x.msg || x.message || x.text || JSON.stringify(x)).join("; ") : human(er), "bad"); }
    b.removeAttribute("aria-busy"); };
  $("#fv-name", el).oninput = () => { FULL.dirty = true; $("#fv-dirty", el).hidden = false; };
}
async function yamlEditor(el, note) {
  el.innerHTML = `<div class="fallback">${note ? `<p class="hint" style="margin-bottom:8px">${esc(note)}</p>` : ""}<textarea class="ta code" id="ya" spellcheck="false" aria-label="Árvores em YAML">carregando…</textarea>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="yas">Salvar árvores</button><span class="hint" id="yam"></span></div></div>`;
  try { const r = await api("/api/trees"); $("#ya", el).value = (r && r.yaml) || ""; } catch (e) { $("#ya", el).value = ""; $("#yam", el).textContent = human(e); }
  $("#yas", el).onclick = async () => { try { const r = await api("/api/trees", {yaml: $("#ya", el).value}); $("#yam", el).textContent = `ok · ${(r && r.trees) || 0} árvore(s)`; toast("Árvores salvas"); pollStatus(); }
    catch (e) { $("#yam", el).textContent = human(e); toast("Erro na árvore", "bad"); } };
}

/* ---------------------------------------------------------------- COMPOSER */
const inp = () => $("#in");
function autoGrow() { const t = inp(); t.style.height = "auto"; t.style.height = Math.min(220, t.scrollHeight) + "px"; $("#send").disabled = !t.value.trim(); }
function setInput(t) { const i = inp(); i.value = t; autoGrow(); i.focus(); i.setSelectionRange(t.length, t.length); }
function setMode(m) {
  S.mode = m; const b = $("#btn-mode"); b.dataset.m = m; b.querySelector("span").textContent = {auto: "Automático", pedido: "Pedido", mensagem: "Mensagem recebida"}[m];
  $("#extra-in").classList.toggle("on", m === "mensagem");
  inp().placeholder = m === "mensagem" ? "Cole a mensagem que você recebeu para o Djorge triar…" : m === "pedido" ? "Diga o que quer que o Djorge faça…" : "Peça algo ao Djorge… (ex.: reunião com Ana amanhã às 15h)";
  renderSuggest();
}
function renderSuggest() {
  const show = S.view === "inicio" && !S.thread.length && !inp().value;
  $("#suggest").innerHTML = show ? (SUGGEST[S.mode] || SUGGEST.auto).map(s => `<button type="button">${esc(s)}</button>`).join("") : "";
  $$("#suggest button").forEach(b => b.onclick = () => setInput(S.mode === "mensagem" ? "" : b.textContent));
}
function thinkingEl() {
  const el = document.createElement("div"); el.className = "thinking"; el.setAttribute("role", "status");
  const petals = Array.from({length: 8}, (_, i) => { const a = i * 45, rad = a * Math.PI / 180; return `<i style="--r:${a}deg;--dx:${Math.round(Math.cos(rad) * 26)}px;--dy:${Math.round(Math.sin(rad) * 26)}px;animation-delay:${i * 0.07}s"></i>`; }).join("");
  el.innerHTML = `<div class="bloom" aria-hidden="true">${petals}<b></b></div><div class="thb"><div class="ph"><span>${PHRASES[0]}</span><span class="dots"></span></div><ol class="etapas" aria-label="Etapas"></ol></div>`;
  let k = 0, live = false;
  const t = setInterval(() => { if (live) return; k = (k + 1) % PHRASES.length; setPh(PHRASES[k]); }, 1100);
  const setPh = txt => { const s = el.querySelector(".ph span"); if (s && s.textContent !== txt) { s.textContent = txt; s.style.animation = "none"; void s.offsetWidth; s.style.animation = ""; } };
  el._stop = () => clearInterval(t);
  /* etapas reais vindas do backend: [{t, texto, estado}] */
  el._etapas = list => { if (!list || !list.length) return; live = true;
    const cur = list.find(x => x.estado === "agora") || list[list.length - 1];
    setPh(String(cur.texto || "").replace(/[.…]+$/, "").replace(/^./, c => c.toLowerCase()));
    const done = list.filter(x => x !== cur);
    $(".etapas", el).innerHTML = done.slice(-4).map(x => `<li class="${x.estado === "erro" ? "err" : "ok"}">${ico(x.estado === "erro" ? "x" : "check", "sm")}${esc(String(x.texto || "").replace(/[.…]+$/, ""))}</li>`).join(""); };
  return el;
}
function pushThread(el) {
  const it = {el}; S.thread.push(it);
  if (S.view !== "inicio") { S.showPanel = false; go("inicio"); } else { const th = $("#thread"); if (th && !S.showPanel) th.appendChild(el); else { S.showPanel = false; render(); } }
  requestAnimationFrame(() => { const v = viewEl(); v.scrollTop = v.scrollHeight; });
  return it;
}
/* comando com etapas: tenta stream; se o backend não suportar, usa a resposta direta */
const isResult = r => r && (r.tool !== undefined || r.proposal || r.reply !== undefined || r.results || r.dicas || r.perguntas);
async function runCommand(text, context, th) {
  let r = await api("/api/command", {text, context, stream: true}, {quiet409: true});
  if (isResult(r) || !r || !r.id) return r;
  const id = r.id, t0 = Date.now(); let misses = 0;
  while (Date.now() - t0 < 90000) {
    await sleep(320);
    let e; try { e = await api(`/api/command/${encodeURIComponent(id)}/etapas`, undefined, {quiet409: true}); misses = 0; }
    catch (er) { if (er.status === 404 && ++misses > 3) break; if (er.status === 409) throw er; continue; }
    if (th && th._etapas) th._etapas(asList(e, "etapas"));
    if (e && e.pronto) { if (e.resultado && e.resultado.error && !isResult(e.resultado)) throw new ApiError(e.resultado.error, 400, e.resultado); if (e.resultado) return e.resultado; if (e.erro || e.error) throw new ApiError(e.erro || e.error, 400, e); break; }
  }
  return await api("/api/command", {text, context}, {quiet409: true});
}
async function submit(over) {
  const text = over && over.text !== undefined ? over.text : inp().value.trim(); if (!text) return;
  if (!over) { inp().value = ""; autoGrow(); }
  pushRecent(text);
  const mode = over && over.mode || S.mode, from = $("#in-from").value.trim(), subj = $("#in-subj").value.trim();
  const ub = document.createElement("div"); ub.className = "ubub"; ub.innerHTML = `<small>${mode === "mensagem" ? "mensagem recebida" + (from ? " · " + esc(titleCase(from)) : "") : "você"}</small>${esc(text)}`;
  pushThread(ub);
  const th = thinkingEl(); const thIt = pushThread(th);
  const t0 = performance.now();
  let res, err;
  try {
    if (mode === "mensagem") res = await api("/api/triage", {text, remetente: from, assunto: subj}, {quiet409: true});
    else res = await runCommand(text, {view: S.view, mode, selected_card: drawerCard && drawerCard.id, ...(over && over.context || {})}, th);
  } catch (e) { err = e; }
  const wait = Math.max(0, (REDUCED ? 0 : 650) - (performance.now() - t0)); if (wait) await sleep(wait);
  th._stop(); th.remove(); S.thread.splice(S.thread.indexOf(thIt), 1);
  if (mode === "mensagem") { $("#in-from").value = ""; $("#in-subj").value = ""; }
  if (err) { const el = document.createElement("div"); el.className = "rcard";
    if (err.status === 409 && err.data.error === "modelo_ausente") { el.className = ""; el.innerHTML = inviteHTML(true); wireInvite(el); }
    else { const ds = errDicas(err);
      el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti warn">${ico("spark")}</span>Vamos tentar de outro jeito</div></div><p class="reply">${esc(human(err))}</p>
        ${dicasHTML(ds)}<div class="ract"><button class="btn sm" data-retry>${ico("edit", "sm")} Ajustar o pedido</button></div>`;
      wireDicas(el, text); }
    const rb = $("[data-retry]", el); if (rb) rb.onclick = () => setInput(text);
    pushThread(el); return; }
  const el = mode === "mensagem" ? triageCard(res, text) : resultCard(res, text);
  pushThread(el);
  const said = res && (res.reply || (res.resumo));
  if (said && S.prefs.speak) speak(said);
}
function dicasHTML(ds, qs) {
  ds = (ds || []).filter(Boolean); qs = (qs || []).filter(q => q && (q.opcoes || q.options || []).length);
  if (!ds.length && !qs.length) return "";
  return `<div class="dicas">${qs.map((q, qi) => `<div class="pq"><p>${esc(q.texto || q.text || "")}</p><div class="row">${(q.opcoes || q.options).map((o, oi) => `<button type="button" class="chip opt" data-q="${qi}" data-o="${oi}">${esc(o.rotulo || o.label || o.texto || "")}</button>`).join("")}</div></div>`).join("")}
    ${ds.length ? `<div class="dl2"><span class="lbl">${ico("spark", "sm")} Tente:</span>${ds.map((d, i) => `<button type="button" class="chip tip" data-dica="${i}" data-tip="Enviar este pedido">${esc(String(d).replace(/^tente:?\s*/i, "").replace(/^[“"']|[”"']$/g, ""))}</button>`).join("")}</div>` : ""}</div>`;
}
function wireDicas(el, text, ds, qs) {
  ds = ds || []; qs = qs || [];
  $$("[data-dica]", el).forEach(b => b.onclick = () => submit({text: b.textContent.trim()}));
  $$("[data-q]", el).forEach(b => b.onclick = () => { const o = (qs[+b.dataset.q].opcoes || qs[+b.dataset.q].options)[+b.dataset.o], pt = o.patch;
    $$("[data-q]", el).forEach(x => x.disabled = true); b.classList.add("on");
    const nt = typeof pt === "string" ? pt : (pt && (pt.text || pt.texto)) || (o.texto && o.texto !== o.rotulo ? o.texto : text);
    submit({text: nt, context: pt && typeof pt === "object" ? {patch: pt, original: text, ...(pt.tool ? {tool: pt.tool} : {})} : {original: text}}); });
}
/* ----- cartão de resultado ----- */
function fieldChip(f) {
  const cls = FIELD_CLS[f.campo] || (f.tipo && FIELD_CLS[f.tipo]) || "c-outro", isModel = /modelo|model|dofl/i.test(f.fonte || "");
  let val = f.valor_fmt || f.fmt || (f.valor && f.valor !== f.texto ? (typeof f.valor === "object" ? (f.valor.texto || f.valor.nome || "") : String(f.valor)) : "");
  if (!f.valor_fmt && val) { if (f.campo === "data" && /^\d{4}-\d\d-\d\d/.test(val)) val = fmtDT(val, {rel: true}); else if (f.campo === "pessoa") val = titleCase(val); else if (/valor|dinheiro|money/i.test(f.campo + (f.tipo || ""))) val = fmtMoney(val); }
  const txt = f.campo === "pessoa" ? titleCase(f.texto || f.valor) : (f.texto || f.valor);
  return `<span class="fchip ${cls}" data-tip="${esc(titleCase(f.campo))} · ${isModel ? "entendido pelo modelo" : "achado pelas regras"}"><span aria-hidden="true">${FIELD_EMO[f.campo] || ""}</span><b>${esc(txt)}</b>${val && val !== txt ? `<small>→ ${esc(val)}</small>` : ""}</span>`;
}
const PKINDS = [["mensagem", "Mensagem"], ["tarefa", "Tarefa"], ["lembrete", "Lembrete"], ["evento", "Evento"], ["nota", "Nota"], ["lista", "Lista"]];
function resultCard(r, text) {
  r = r || {};
  const el = document.createElement("div"); el.className = "rcard"; el.setAttribute("aria-label", "Resultado do pedido");
  const tool = r.tool || "nenhuma", [tl, ti] = TOOL_PT[tool] || [titleCase(String(tool).replace(/_/g, " ")), "spark"], p = r.proposal || {action: "none"};
  const ds = asList(r.dicas), qs = asList(r.perguntas);
  el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti">${ico(ti)}</span>${esc(tl)}</div>${tool === "web" ? `<span class="tag out">${ico("plug", "sm")} sai do computador</span>` : ""}</div>
    ${r.reply ? `<p class="reply">${esc(r.reply)}</p>` : ""}
    ${r.warning || r.aviso ? `<p class="hint">⚠ ${esc(r.warning || r.aviso)}</p>` : ""}
    ${(r.fields || []).length ? `<div class="fields" aria-label="O que eu entendi">${r.fields.map(fieldChip).join("")}</div>` : ""}
    <div class="propbox"></div>${dicasHTML(ds, qs)}
    <div class="ract"></div>
    ${r.timing_ms != null ? `<p class="meta">${Math.round(r.timing_ms)} ms</p>` : ""}`;
  wireDicas(el, text, ds, qs);
  const pb = $(".propbox", el), ract = $(".ract", el);
  const state = {W: null};
  renderProposal(pb, p, r, state);
  const editable = ["create_card", "update_card", "send_message", "abrir_app", "open_app"].includes(p.action);
  const confirmable = p.action && !["none", "search", "calc", "time", "show_agenda", "web"].includes(p.action);
  if (p.action === "ui" && p.patch) { state.prevServer = {...(S.prefs._server || {})};
    api("/api/ui").then(u => { S.prefs = normPrefs(u); applyPrefs(S.prefs); if (S.view !== "inicio") render(); }).catch(() => {}); }
  if (r.executed) {
    ract.innerHTML = `<div class="rdone">${ico("check", "sm")} ${p.action === "ui" ? "Interface ajustada" : p.action === "update_card" ? "Card atualizado" : "Feito"}</div>
      ${(r.results || [])[0] && (r.results[0] || {}).id ? `<button class="btn sm" data-open>Abrir card</button>` : ""}<button class="btn sm ghost" data-undo>Desfazer</button>`;
    $$("input,textarea,.sel button,.seg button", pb).forEach(x => x.disabled = true);
    loadCards(true).then(() => { if (S.view !== "inicio") render(); });
    const op = $("[data-open]", ract); if (op) op.onclick = () => openCard(r.results[0].id);
    $("[data-undo]", ract).onclick = async () => {
      if (p.action === "ui") { const back = {}; Object.keys(p.patch || {}).forEach(k => { if (state.prevServer[k] !== undefined) back[k] = state.prevServer[k]; });
        try { const u = await api("/api/ui", back); S.prefs = normPrefs({prefs: u.prefs, choices: S.choices}); applyPrefs(S.prefs); render(); } catch (e) { return toast(human(e), "bad"); } }
      else { const o = await tryApi("/api/command/confirm", {id: r.id, approve: false, edits: {undo: true}}); if (!o) return; await loadCards(true); }
      ract.innerHTML = `<div class="rdone no">${ico("x", "sm")} Desfeito</div>`; toast("Desfeito"); };
  } else if (confirmable) {
    const okLabel = () => { const W = state.W; if (!W) return p.action === "ui" ? "Manter" : p.action === "create_tree" ? "Salvar automação" : "Confirmar";
      if (W.kind === "mensagem") return W.channel === "app_whatsapp" ? "Abrir no WhatsApp" : "Enviar"; return p.action === "update_card" ? "Salvar" : `Criar ${(KINDS[W.kind] || "card").toLowerCase()}`; };
    ract.innerHTML = `<button class="btn primary" data-ok>${ico("check", "sm")} <span>${esc(okLabel())}</span></button>
      ${p.action === "create_tree" ? `<button class="btn" data-ed>${ico("tree", "sm")} Abrir no editor</button>` : ""}<button class="btn ghost" data-no>Cancelar</button>
      ${r.needs_confirm || editable ? `<span class="note">${editable ? "Você pode mudar o tipo e qualquer campo antes de confirmar." : "Nada acontece sem sua confirmação."}</span>` : ""}`;
    state.onChange = () => { const s2 = $("[data-ok] span", ract); if (s2) s2.textContent = okLabel(); };
    const done = (ok, msg) => { ract.innerHTML = `<div class="rdone ${ok ? "" : "no"}">${ico(ok ? "check" : "x", "sm")} ${esc(msg)}</div>`; $$("input,textarea,.sel button,.seg button,.pkind button", pb).forEach(x => x.disabled = true); };
    $("[data-ok]", ract).onclick = async e => { const b = e.currentTarget; b.setAttribute("aria-busy", "true");
      const edits = collectEdits(pb, p, state);
      if (edits.action === "abrir_app") { let o = null; try { o = await api("/api/command/confirm", {id: r.id, approve: true, edits}); } catch (x) {}
        if (o && o.abrir && o.url) { window.open(o.url, "_blank", "noopener"); toast("Abrindo o WhatsApp. O envio final é seu."); } else if (!(o && o.abrir === false)) openWhatsApp(edits.telefone, edits.body, edits.url);
        done(true, "WhatsApp aberto com a mensagem pronta"); return; }
      try { const out = await api("/api/command/confirm", {id: r.id, approve: true, edits}, {strict: false});
        if (out && out.error) throw new ApiError(out.error, 400, out);
        afterConfirm(edits.action ? {...p, action: edits.action} : p, out, el, edits); done(true, confirmMsg(edits.action ? {action: edits.action} : p, out, state.W));
        if (out && (asList(out.dicas).length)) el.insertAdjacentHTML("beforeend", dicasHTML(out.dicas)), wireDicas(el, text); }
      catch (er) { b.removeAttribute("aria-busy");
        if (er.data && er.data.error === "fora_da_janela_24h") { toast(human(er), "warn", {action: "Enviar por modelo", onAction: async () => { const o2 = await tryApi("/api/command/confirm", {id: r.id, approve: true, edits: {...edits, template: true}}); if (o2 && !o2.error) done(true, "Enviado por modelo aprovado"); }, ms: 10000}); }
        else { const ds2 = errDicas(er); let box = $(".errtip", el); if (!box) { box = document.createElement("div"); box.className = "errtip"; ract.before(box); }
          box.innerHTML = `<p class="hint warnc">${ico("spark", "sm")} ${esc(human(er))}</p>${dicasHTML(ds2)}`; wireDicas(box, text); } } };
    const ed = $("[data-ed]", ract); if (ed) ed.onclick = () => openTree(p);
    $("[data-no]", ract).onclick = async () => { tryApi("/api/command/confirm", {id: r.id, approve: false}, {silent: true}); done(false, "Cancelado"); };
  }
  return el;
}
function confirmMsg(p, out, W) { if (W && W.kind && W.kind !== "mensagem" && (p.action === "create_card")) return `${KINDS[W.kind] || "Card"} criad${W.kind === "lista" || W.kind === "nota" || W.kind === "tarefa" || W.kind === "mensagem" ? "a" : "o"}`;
  return {create_card: "Card criado", update_card: "Card atualizado", send_message: out && out.status === "aguardando confirmação" ? "Na fila de confirmação" : "Mensagem enviada", create_tree: "Automação salva", ui: "Interface ajustada"}[p.action] || "Feito"; }
async function afterConfirm(p, out, el) {
  if (p.action === "create_card" || p.action === "update_card") { await loadCards(true); const c = (out && (out.card || (out.id && out))); if (c && c.id) toast("Pronto", "", {action: "Abrir card", onAction: () => openCard(c.id)}); }
  if (p.action === "ui") { try { const u = await api("/api/ui"); S.prefs = normPrefs(u); applyPrefs(S.prefs); } catch (e) {} }
  if (p.action === "create_tree") pollStatus();
}
function inputRow(label, key, val, type = "text", ph = "") { return `<div class="field"><label>${esc(label)}</label>${type === "area" ? `<textarea class="ta" data-k="${key}" rows="3" placeholder="${esc(ph)}">${esc(val || "")}</textarea>` : `<input class="inp" data-k="${key}" type="${type}" value="${esc(val ?? "")}" placeholder="${esc(ph)}">`}</div>`; }
function chanOpts() { const c = connected().map(i => i.id); const o = []; if (c.includes("whatsapp")) o.push(["whatsapp", "WhatsApp (enviar daqui)"]);
  if (c.includes("google")) o.push(["google", "Gmail"]); if (c.includes("microsoft")) o.push(["microsoft", "Outlook"]); o.push(["app_whatsapp", "Abrir no WhatsApp (você envia)"]); return o; }
function initW(p, r) {
  const W = {kind: "tarefa", title: "", body: "", column: "entrada", priority: 0, date: "", time: "", dateKey: "start", people: [], tags: [], checklist: [], recurrence: "", to: "", channel: "", subject: "", msg: "", phone: "", url: ""};
  if (p.action === "create_card" || p.action === "update_card") { const c = p.card || p.data || p; const sd = parseD(c.start || c.due);
    Object.assign(W, {kind: c.kind || "tarefa", title: r.titulo || c.titulo || c.title || "", body: c.body || "", column: c.column || "entrada", priority: c.priority || 0, date: sd ? ymd(sd) : "", time: sd && (sd.getHours() || sd.getMinutes()) ? hm(sd) : "",
      dateKey: c.due && !c.start ? "due" : "start", people: (c.people || []).map(x => ({...x, nome: titleCase(x.nome || x.email)})), tags: c.tags || [], checklist: c.checklist || [], recurrence: c.recurrence || ""});
    W.to = W.people[0] ? W.people[0].nome : ""; W.phone = W.people[0] && (W.people[0].telefone || W.people[0].phone) || ""; W.msg = W.body; }
  else { const to = p.to || p.destino || p.contato && p.contato.nome || "";
    Object.assign(W, {kind: "mensagem", to: titleCase(to), msg: p.body || p.corpo || p.texto || r.corpo || "", subject: p.subject || p.assunto || "", phone: p.telefone || p.phone || (p.contato && p.contato.telefone) || "", url: p.app_url || p.url || ""});
    W.title = r.titulo || (to ? `Falar com ${titleCase(String(to).split(/[(<]/)[0].trim())}` : "Mensagem"); W.body = W.msg; if (to) W.people = [{nome: titleCase(String(to).split(/[(<]/)[0].trim()), tipo: "pessoa", telefone: W.phone}]; }
  const opts = chanOpts().map(o => o[0]); const want = p.action === "abrir_app" || p.action === "open_app" ? "app_whatsapp" : (p.channel || p.integration || p.canal || "whatsapp");
  W.channel = opts.includes(want) ? want : opts[0];
  return W;
}
function syncW(pb, W) {
  const g = k => { const i = $(`[data-k="${k}"]`, pb); return i ? i.value : undefined; };
  if (W.kind === "mensagem") { ["to", "subject"].forEach(k => { const v = g(k); if (v !== undefined) W[k] = v; }); const m = g("msg"); if (m !== undefined) { W.msg = m; W.body = m; } const ch = selVal(pb, "pch"); if (ch) W.channel = ch; }
  else { ["title", "body", "date", "time", "recurrence"].forEach(k => { const v = g(k); if (v !== undefined) W[k] = v; }); const c = selVal(pb, "pc"), pr = selVal(pb, "pp"); if (c) W.column = c; if (pr !== undefined) W.priority = +pr || 0;
    if (W.body && !W.msg) W.msg = W.body; }
}
function renderProposal(pb, p, r, state) {
  const a = p.action;
  if (["create_card", "update_card", "send_message", "abrir_app", "open_app"].includes(a)) {
    const W = state.W = state.W || initW(p, r);
    const draw = () => {
      const kinds = a === "update_card" ? PKINDS.filter(k => k[0] !== "mensagem") : PKINDS;
      const head = `<div class="pkind" role="radiogroup" aria-label="Tipo">${kinds.map(([k, l]) => `<button type="button" role="radio" aria-checked="${W.kind === k}" data-kind="${k}">${ico(KIND_ICO[k] || "check", "sm")}${esc(l)}</button>`).join("")}</div>`;
      if (W.kind === "mensagem") {
        const opts = chanOpts();
        pb.innerHTML = `<div class="prop">${head}
          <div class="grid2"><div class="field"><span class="lbl">Como enviar</span>${selHTML("pch", opts, W.channel, "Como enviar")}</div>
            <div class="field acwrap"><label>Para</label><input class="inp" data-k="to" value="${esc(W.to)}" placeholder="nome, telefone ou e-mail" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false"></div></div>
          ${W.channel === "google" || W.channel === "microsoft" ? inputRow("Assunto", "subject", W.subject) : ""}
          ${inputRow("Texto", "msg", W.msg, "area", "Escreva a mensagem")}
          ${W.channel === "app_whatsapp" ? `<p class="hint">${ico("send", "sm")} O Djorge abre o WhatsApp com a mensagem pronta. Quem aperta enviar é você.</p>` : ""}</div>`;
        wireSel(pb, "pch", opts, v => { syncW(pb, W); W.channel = v; draw(); });
        contactAuto($('[data-k="to"]', pb), c => { W.to = titleCase(c.nome || c.email || c.telefone); W.phone = c.telefone || c.phone || ""; W.email = c.email || ""; W.contato = c; });
      } else {
        const cols = COLS.map(x => [x.id, x.label]), pris = PRI.map((l, i) => [String(i), l]);
        pb.innerHTML = `<div class="prop">${head}
          ${inputRow("Título", "title", W.title)}
          ${W.kind === "nota" || W.kind === "mensagem" || W.body ? inputRow(W.kind === "nota" ? "Texto" : "Detalhes", "body", W.body, "area") : ""}
          <div class="grid3"><div class="field"><span class="lbl">Coluna</span>${selHTML("pc", cols, W.column, "Coluna")}</div>
          <div class="field"><span class="lbl">Prioridade</span>${selHTML("pp", pris, String(W.priority || 0), "Prioridade")}</div>
          ${W.kind === "nota" ? "" : `<div class="field"><span class="lbl">${W.kind === "tarefa" ? "Prazo" : "Quando"}</span><div class="dt"><input type="date" data-k="date" value="${esc(W.date)}" aria-label="Data"><input type="time" data-k="time" value="${esc(W.time)}" aria-label="Hora"></div></div>`}</div>
          ${W.date ? `<p class="hint whenf">${ico("cal", "sm")} ${esc(fmtDT(W.date + "T" + (W.time || "00:00") + ":00", {rel: true}))}</p>` : ""}
          ${W.people.length || W.recurrence ? `<div class="row" style="gap:6px">${W.people.map(x => `<span class="chip">${ico("msg", "sm")}${esc(titleCase(x.nome || x.email))}</span>`).join("")}${W.recurrence ? `<span class="chip">🔁 ${esc(typeof W.recurrence === "object" ? (W.recurrence.texto || JSON.stringify(W.recurrence)) : W.recurrence)}</span>` : ""}</div>` : ""}
          ${W.kind === "lista" || W.checklist.length ? `<div class="field" style="margin-top:8px"><span class="lbl">Itens</span><div class="row" style="gap:6px">${W.checklist.map(x => `<span class="chip">☐ ${esc(x.text)}</span>`).join("") || `<span class="hint">Adicione os itens depois, no card.</span>`}</div></div>` : ""}</div>`;
        wireSel(pb, "pc", cols); wireSel(pb, "pp", pris);
        $$('[data-k="date"],[data-k="time"]', pb).forEach(i => i.onchange = () => { syncW(pb, W); draw(); });
      }
      $$(".pkind [data-kind]", pb).forEach(b => b.onclick = () => { syncW(pb, W); const was = W.kind; W.kind = b.dataset.kind;
        if (W.kind === "mensagem" && was !== "mensagem") { if (!W.msg) W.msg = W.body || W.title; }
        if (was === "mensagem" && W.kind !== "mensagem") { if (!W.title || /^Falar com|^Mensagem$/.test(W.title)) W.title = W.msg ? W.msg.slice(0, 80) : W.title; if (W.kind === "lembrete" && W.to && !/\b(com|para)\b/.test(W.title)) W.title = `${W.title}`; }
        draw(); state.onChange && state.onChange(); const f = $(".prop input,.prop textarea", pb); f && f.focus(); });
      state.onChange && state.onChange();
    };
    draw();
  } else if (a === "calc" || a === "time") {
    const v = p.result_fmt || p.valor_fmt || (p.result ?? p.value ?? (r.results && r.results[0] && (r.results[0].value ?? r.results[0].result ?? r.results[0].text)));
    pb.innerHTML = v != null ? `<div class="prop"><h4>${ico(a === "calc" ? "calc" : "clock", "sm")} Resultado</h4><div class="bigres">${esc(typeof v === "object" ? (v.texto || JSON.stringify(v)) : v)}</div>${p.expr ? `<p class="hint mono">${esc(p.expr)}</p>` : ""}</div>` : "";
  } else if (a === "search" || a === "show_agenda" || a === "web") {
    const items = r.results || p.results || [];
    pb.innerHTML = `<div class="prop"><h4>${ico(a === "show_agenda" ? "cal" : "search", "sm")} ${a === "show_agenda" ? "Na agenda" : a === "web" ? "Na internet" : "Encontrado"} · ${items.length}</h4>${items.length ? `<div class="rlist">${items.slice(0, 8).map(resultItemHTML).join("")}</div>` : `<p class="hint">Nada encontrado.</p>`}</div>`;
    wireResultItems(pb);
  } else if (a === "create_tree") {
    const t = p.tree || {}, s2 = p.summary || p.resumo || (t.name ? `Árvore “${t.name}”` : "Nova automação");
    pb.innerHTML = `<div class="prop"><h4>${ico("tree", "sm")} Automação proposta</h4><p>${esc(s2)}</p>${(p.questions || []).length ? `<div class="grid2" style="margin-top:8px">${p.questions.map(q => inputRow(q.label || q.key, "q:" + (q.key || q.label), q.default || "")).join("")}</div>` : ""}</div>`;
  } else if (a === "ui") {
    const patch = p.patch || {};
    pb.innerHTML = `<div class="prop"><h4>${ico("gear", "sm")} Ajuste da interface (já aplicado para você ver)</h4><div class="row">${Object.entries(patch).map(([k, v]) => `<span class="chip on">${esc(k)}: ${esc(Array.isArray(v) ? (v.map(x => x.name || x).join(", ")) : typeof v === "object" ? (v.name || JSON.stringify(v)) : v)}</span>`).join("")}</div></div>`;
  } else if ((r.results || []).length) {
    pb.innerHTML = `<div class="prop"><div class="rlist">${r.results.slice(0, 8).map(resultItemHTML).join("")}</div></div>`; wireResultItems(pb);
  }
}
function resultItemHTML(x) {
  const d = parseD(x.start || x.due || x.date || x.t);
  const src = x.fonte || (x.source && (SRC_NAME[x.source.integration || x.source] || "")) || "";
  return `<button type="button" class="rli" ${x.id ? `data-cid="${esc(x.id)}"` : ""} ${x.url ? `data-url="${esc(x.url)}"` : ""}><span style="min-width:0"><b>${esc(x.titulo || x.title || x.text || x.name || "item")}</b>${x.snippet || x.trecho ? `<p>${esc(x.snippet || x.trecho)}</p>` : ""}${src ? `<em class="src2">${esc(src)}</em>` : ""}</span><small>${d ? esc(fmtDT(d, {rel: true})) : ""}</small></button>`;
}
function wireResultItems(root) { $$("[data-cid]", root).forEach(b => b.onclick = () => { if (S.cards.find(c => c.id === b.dataset.cid)) openCard(b.dataset.cid); });
  $$("[data-url]:not([data-cid])", root).forEach(b => b.onclick = () => window.open(b.dataset.url, "_blank", "noopener")); }
function collectEdits(pb, p, state) {
  const e = {}; $$("[data-k]", pb).forEach(i => { if (i.dataset.k.startsWith("q:")) (e.answers = e.answers || {})[i.dataset.k.slice(2)] = i.value; });
  const W = state && state.W; if (!W) { $$("[data-k]", pb).forEach(i => { if (!i.dataset.k.startsWith("q:")) e[i.dataset.k] = i.value; }); return e; }
  syncW(pb, W);
  if (W.kind === "mensagem") {
    const people = W.to ? [{nome: titleCase(W.to), tipo: "pessoa", ...(W.phone ? {telefone: W.phone} : {}), ...(W.email ? {email: W.email} : {})}] : [];
    if (W.channel === "app_whatsapp") return {action: "abrir_app", app: "whatsapp", to: W.to, telefone: W.phone, body: W.msg, url: W.url && !W.msg ? W.url : "", contato: W.contato};
    return {action: "send_message", channel: W.channel, to: W.to, subject: W.subject, body: W.msg, contato: W.contato || (people[0] || null)};
  }
  const card = {title: W.title.trim() || "Sem título", kind: W.kind, column: W.column, priority: W.priority || 0, body: W.body || "", people: W.people, tags: W.tags, checklist: W.checklist, recurrence: W.recurrence};
  if (W.date) card[W.kind === "tarefa" && W.dateKey === "due" ? "due" : (W.kind === "tarefa" ? W.dateKey : "start")] = `${W.date}T${W.time || "00:00"}:00`;
  if (W.date && !W.time) card.all_day = true;
  const action = p.action === "update_card" ? "update_card" : "create_card";
  return {action, card, ...card};
}
/* autocompletar contatos (GET /api/contatos?q=) sem roubar o foco do campo */
function contactAuto(input, onPick) {
  if (!input) return; let box = null, items = [], ai = -1, t = null, seq = 0;
  const close = () => { if (box) box.remove(); box = null; ai = -1; input.setAttribute("aria-expanded", "false"); };
  const draw = () => { if (!items.length) return close();
    if (!box) { box = document.createElement("div"); box.className = "acl"; box.setAttribute("role", "listbox"); document.body.appendChild(box); }
    const r = input.getBoundingClientRect(); box.style.left = r.left + "px"; box.style.top = (r.bottom + 4) + "px"; box.style.width = Math.max(240, r.width) + "px";
    box.innerHTML = items.map((c, i) => `<button type="button" role="option" class="${i === ai ? "act" : ""}" data-i="${i}"><span class="av">${esc(initials(c.nome || c.email))}</span><span><b>${esc(titleCase(c.nome || c.email || c.telefone))}</b><small>${esc([c.telefone, c.email].filter(Boolean).join(" · "))}</small></span></button>`).join("");
    input.setAttribute("aria-expanded", "true");
    $$("button", box).forEach(b => b.onmousedown = e => { e.preventDefault(); pick(+b.dataset.i); }); };
  const pick = i => { const c = items[i]; if (!c) return; input.value = titleCase(c.nome || c.email || c.telefone); onPick(c); close(); };
  input.addEventListener("input", () => { clearTimeout(t); const q = input.value.trim(); if (q.length < 1) return close(); const my = ++seq;
    t = setTimeout(async () => { try { const r = await api("/api/contatos?q=" + encodeURIComponent(q)); if (my !== seq) return; items = asList(r, "contatos", "items").slice(0, 7); ai = items.length ? 0 : -1; draw(); } catch (e) { close(); } }, 160); });
  input.addEventListener("keydown", e => { if (!box) return; if (e.key === "ArrowDown") { e.preventDefault(); ai = (ai + 1) % items.length; draw(); } else if (e.key === "ArrowUp") { e.preventDefault(); ai = (ai - 1 + items.length) % items.length; draw(); }
    else if (e.key === "Enter" && ai >= 0) { e.preventDefault(); pick(ai); } else if (e.key === "Escape") { e.stopPropagation(); close(); } });
  input.addEventListener("blur", () => setTimeout(close, 120));
}
function openTree(p) { const g = p.graph || (p.tree && p.tree.nodes && Array.isArray(p.tree.nodes) ? p.tree : null) || {name: (p.tree && p.tree.name) || "nova árvore", tree: p.tree, nodes: [], edges: []};
  openFull({graph: g, isNew: true}); }

/* ----- triagem ----- */
function triageCard(r, text) {
  r = r || {};
  const el = document.createElement("div"); el.className = "rcard";
  const pr = r.prioridade ?? r.priority ?? "", cat = r.categoria || r.category || "", acts = r.acoes || r.actions || [], dest = r.destino || r.destination || "";
  const prN = typeof pr === "number" ? pr : {baixa: 1, media: 2, "média": 2, alta: 3, urgente: 3}[String(pr).toLowerCase()] || 0;
  el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti">${ico("shield")}</span>Triagem da mensagem</div></div>
    ${r.resumo || r.reply ? `<p class="reply">${esc(r.resumo || r.reply)}</p>` : ""}
    <div class="kv" style="margin-top:12px">
      <div><small>Prioridade</small><b><span class="pri p${prN}"><i></i>${esc(titleCase(String(pr || "—")))}</span></b></div>
      <div><small>Categoria</small><b>${esc(titleCase(cat || "—"))}</b></div>
      <div><small>Destino</small><b>${esc(typeof dest === "object" ? (dest.coluna || dest.column || JSON.stringify(dest)) : dest || "—")}</b></div>
      ${(r.seguranca || r.security) && !/^seguro$/i.test((r.seguranca || r.security).label || r.seguranca || r.security) ? `<div><small>Atenção</small><b style="color:var(--bad)">${esc(titleCase((r.seguranca || r.security).label || r.seguranca || r.security))}</b></div>` : ""}</div>
    ${(r.motivos || []).length ? `<p class="hint" style="margin-top:8px">Por quê: ${esc(r.motivos.slice(0, 5).join(" · "))}</p>` : ""}
    ${r.prazo ? `<p class="hint">Prazo: <b>${esc(typeof r.prazo === "object" ? (r.prazo.texto || r.prazo.valor || JSON.stringify(r.prazo)) : r.prazo)}</b></p>` : ""}
    ${r.dados && Object.keys(r.dados).length ? `<div class="fields">${Object.entries(r.dados).map(([k, v]) => `<span class="fchip c-outro"><small>${esc(k)}</small><b>${esc((Array.isArray(v) ? v : [v]).map(x => typeof x === "object" ? (x.texto || x.valor || JSON.stringify(x)) : x).join(", ").slice(0, 80))}</b></span>`).join("")}</div>` : ""}
    ${(r.fields || r.campos || []).length ? `<div class="fields">${(r.fields || r.campos).map(fieldChip).join("")}</div>` : ""}
    ${acts.length ? `<div class="prop"><h4>Ações sugeridas</h4><div class="row">${acts.map((a, i) => `<button type="button" class="chip" data-act="${i}">${esc(typeof a === "string" ? a : a.label || a.acao || a.action)}</button>`).join("")}</div></div>` : ""}
    <div class="ract"><button class="btn primary" data-mk>${ico("plus", "sm")} Criar card</button><button class="btn ghost" data-ans>Responder</button></div>`;
  $("[data-mk]", el).onclick = async () => { const c = await createCard({kind: "mensagem", title: (r.assunto || text.split("\n")[0]).slice(0, 90), body: text, priority: Math.min(3, prN),
    column: (typeof dest === "string" && COLS.find(x => x.id === dest)) ? dest : prN >= 3 ? "hoje" : "entrada", tags: cat ? [String(cat)] : []}); if (c) $("[data-mk]", el).disabled = true; };
  $("[data-ans]", el).onclick = () => { setMode("auto"); setInput(`#${r.remetente || ""} &&`); const i = inp(); i.setSelectionRange(i.value.length - 1, i.value.length - 1); };
  $$("[data-act]", el).forEach(b => b.onclick = () => { const a = acts[+b.dataset.act]; setMode("auto"); setInput(typeof a === "string" ? a : a.command || a.label || ""); });
  el.insertAdjacentHTML("beforeend", dicasHTML(r.dicas, r.perguntas)); wireDicas(el, text, asList(r.dicas), asList(r.perguntas));
  return el;
}

/* ---------------------------------------------------------------- MODELO */
const modelReady = () => !!(S.model && (S.model.installed || S.model.state === "instalado"));
function modelUiState() {
  const m = S.model || {};
  if (m.state === "baixando") return "baixando"; if (m.state === "verificando") return "verificando"; if (m.state === "erro") return "erro";
  if (m.installed || m.state === "instalado") return m.loaded === false ? "carregando" : "pronto";
  return "ausente";
}
function ringSVG(p, r = 11, w = 26) { const C = 2 * Math.PI * r; return `<svg viewBox="0 0 ${w} ${w}" aria-hidden="true"><circle class="bgc" cx="${w / 2}" cy="${w / 2}" r="${r}"/><circle class="fgc" cx="${w / 2}" cy="${w / 2}" r="${r}" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - p)}"/></svg>`; }
function renderModelChip() {
  const s = modelUiState(), m = S.model || {}, b = $("#mchip"); b.dataset.s = s;
  const pct = Math.round((m.progress || 0) * 100);
  const sub = {ausente: "baixar modelo", baixando: `baixando ${pct}%`, verificando: "verificando…", carregando: "carregando…", pronto: "pronto", erro: "erro"}[s];
  const ring = s === "baixando" ? ringSVG(m.progress || 0) : (s === "carregando" || s === "verificando") ? ringSVG(.28) : "";
  b.innerHTML = `<span class="ring">${ring}<span class="dot"></span></span><span class="mt"><span>${esc(m.name || "DOFL V2")}</span><small>${esc(sub)}</small></span>`;
  b.setAttribute("aria-label", `Modelo ${m.name || "DOFL"}: ${sub}`);
  const ms = $("#me-st"), dot = $("#me-dot"); if (ms) { ms.textContent = S.rec && S.rec.pausado ? "pausado" : S.status ? (s === "pronto" ? "tudo local · pronto" : s === "ausente" ? "só regras · sem modelo" : sub) : "conectando…"; dot.className = S.status ? (s === "pronto" ? "ok" : "warn") : ""; }
}
let modelTimer = null, lastModelState = null;
async function pollModel() {
  clearTimeout(modelTimer);
  try { S.model = await api("/api/model/info") || {}; } catch (e) { if (e.status === 0) S.model = {...S.model}; }
  const s = modelUiState();
  if (lastModelState && lastModelState !== s) {
    if (s === "pronto") toast("O modelo DOFL está pronto. Agora o Djorge entende bem mais.", "", {title: "Modelo instalado"});
    if (s === "erro") toast(S.model.error || "Falha ao baixar o modelo.", "bad", {action: "Ver", onAction: openModel});
    if (S.view === "inicio") render();
  }
  lastModelState = s; renderModelChip(); if (modelPop) modelPop.update();
  modelTimer = setTimeout(pollModel, ["baixando", "verificando", "carregando"].includes(s) ? 900 : 15000);
}
let modelPop = null;
function fmtMB(b) { return b ? (b / 1048576).toFixed(b > 1e8 ? 0 : 1) + " MB" : "—"; }
function openModel() {
  if (modelPop) return;
  const m0 = modal({title: "Modelo local", cls: "md", body: `<div id="mpb"></div>`, foot: `<div id="mpf" class="row" style="width:100%"></div>`, onClose: () => modelPop = null});
  const update = () => {
    const m = S.model || {}, s = modelUiState(), pct = m.progress || 0, d = m.details || {};
    $("#mpb", m0.box).innerHTML = `<div class="mhero"><div class="big">${ringSVG(s === "pronto" ? 1 : pct, 32, 72)}<img src="/static/icon.svg" alt=""></div>
      <div><span class="kicker">Djorge Origo Flash Local</span><h3>${esc(m.name || "DOFL V2")} ${m.version ? `<small class="mono" style="color:var(--ink3)">v${esc(m.version)}</small>` : ""}</h3>
      <p>${{ausente: "Ainda não está neste computador. Sem ele, o Djorge usa só as regras (datas, horas, atalhos) — funciona, mas entende menos.", baixando: "Baixando… você pode continuar usando o app.", verificando: "Conferindo os arquivos baixados…", carregando: "Instalado. Carregando na memória…", pronto: "Pronto e rodando só no seu computador.", erro: "Algo deu errado: " + (m.error || "erro desconhecido")}[s]}</p></div></div>
      <div style="padding:0 24px 18px">
      ${s === "baixando" || s === "verificando" ? `<div class="pbar" role="progressbar" aria-valuenow="${Math.round(pct * 100)}" aria-valuemin="0" aria-valuemax="100" aria-label="Progresso do download"><i style="width:${Math.round(pct * 100)}%"></i></div><p class="hint mono">${fmtMB(m.bytes)} de ${m.total ? fmtMB(m.total) : (m.size_mb || 305) + " MB"} · ${Math.round(pct * 100)}%</p>` : ""}
      <div class="kv" style="margin-top:10px">
        <div><small>Tamanho</small><b>${esc(m.size_mb || 305)} MB</b></div>
        <div><small>Versão</small><b>${esc(m.version || d.version || "—")}</b></div>
        ${m.path ? `<div style="grid-column:1/-1"><small>Pasta</small><b class="mono" style="font-size:.8em">${esc(m.path)}</b></div>` : ""}
        ${m.source ? `<div style="grid-column:1/-1"><small>Origem</small><b class="mono" style="font-size:.8em">${esc(m.source)}</b></div>` : ""}
      </div></div>`;
    const f = $("#mpf", m0.box);
    const want = s === "ausente" || s === "erro" ? "dl" : s === "baixando" || s === "verificando" ? "cancel" : "rm";
    if (f.dataset.w !== want) { f.dataset.w = want;
      f.innerHTML = `<span class="hint" style="flex:1">${ico("shield", "sm")} O download vem só da fonte oficial configurada.</span>` +
        (want === "dl" ? `<button class="btn primary" data-a="download">${ico("dl", "sm")} Baixar modelo (${esc(m.size_mb || 305)} MB)</button>` :
         want === "cancel" ? `<button class="btn" data-a="cancel">Cancelar download</button>` : `<button class="btn danger" data-a="remove">${ico("trash", "sm")} Remover</button>`);
      $$("[data-a]", f).forEach(b => b.onclick = () => modelAction(b.dataset.a, b)); }
  };
  modelPop = {update}; update();
}
async function modelAction(a, btn) {
  if (a === "remove" && !confirm("Remover o modelo deste computador? Você pode baixar de novo depois.")) return;
  btn && btn.setAttribute("aria-busy", "true");
  try { const r = await api("/api/model/" + a, {}); if (r && r.state) S.model = r; toast({download: "Download iniciado — pode continuar usando o app.", cancel: "Download cancelado", remove: "Modelo removido"}[a]); }
  catch (e) { toast(human(e), "bad"); }
  btn && btn.removeAttribute("aria-busy");
  if (modelPop) { const f = $("#mpf"); if (f) f.dataset.w = ""; }
  pollModel();
}
function inviteHTML(fromErr) {
  const s = modelUiState();
  return `<div class="invite" role="region" aria-label="Convite para baixar o modelo"><div class="ic">${ico("cpu")}</div><div><h3>${fromErr ? "Esse pedido precisa do modelo" : "Deixe o Djorge mais esperto"}</h3>
    <p>${fromErr ? "As regras não deram conta sozinhas. Baixe o DOFL (305 MB, roda só no seu computador) e tente de novo." : "Baixe o modelo DOFL (305 MB). Ele roda só no seu computador — nada sai daqui. Enquanto isso, as regras já funcionam."}</p></div>
    <div class="row">${s === "baixando" ? `<button class="btn" data-inv="open">Ver progresso</button>` : `<button class="btn primary" data-inv="dl">${ico("dl", "sm")} Baixar</button>`}${fromErr ? "" : `<button class="btn ghost" data-inv="later">Agora não</button>`}</div></div>`;
}
function wireInvite(root) {
  $$("[data-inv]", root).forEach(b => b.onclick = () => { if (b.dataset.inv === "dl") { modelAction("download", b); openModel(); }
    else if (b.dataset.inv === "open") openModel(); else { store.set("inviteDismissed", true); b.closest(".invite").remove(); } });
}
let inviteShownAt = 0;
function modelInvite() { if (Date.now() - inviteShownAt < 4000 || modelPop) return; inviteShownAt = Date.now(); toast("Isso precisa do modelo DOFL.", "warn", {action: "Baixar", onAction: openModel, ms: 8000}); }

/* ---------------------------------------------------------------- BUSCA (Ctrl+K) */
function openPalette(initial = "") {
  if ($(".palette")) return;
  const cmds = [
    {t: "Novo card", s: "Ctrl+N", i: "plus", run: () => newCard()}, ...VIEWS.map(v => ({t: "Ir para " + v.label, s: "visão", i: v.icon, run: () => go(v.id)})),
    {t: "Configurações", s: "Ctrl+,", i: "gear", run: () => openSettings()}, {t: "Integrações", s: "Google, Microsoft, WhatsApp, Gemini", i: "plug", run: () => openIntegrations()},
    {t: "Modelo DOFL", s: "baixar, detalhes", i: "cpu", run: openModel}, {t: "Tema escuro", s: "aparência", i: "spark", run: () => savePrefs({theme: "dark"})},
    {t: "Tema claro", s: "aparência", i: "spark", run: () => savePrefs({theme: "light"})}, {t: "Recolher barra lateral", s: "layout", i: "side", run: toggleSide},
    {t: "Configurar o secretário", s: "3 passos", i: "spark", run: () => secretaryWizard()}, {t: "Nova automação", s: "Se… então…", i: "tree", run: () => { go("automacoes"); autoBuilder(null); }},
    {t: "Pausar Djorge", s: "leveza", i: "clock", run: () => appAction("pausar")}, {t: "Retomar Djorge", s: "leveza", i: "up", run: () => appAction("retomar")}, {t: "Liberar memória", s: "leveza", i: "sync", run: () => appAction("liberar_memoria")},
    {t: "API local", s: "configurações", i: "plug", run: () => openSettings("api")}];
  const m = modal({raw: true, cls: "palette", label: "Busca e comandos", body: `<div class="pin">${ico("search")}<input id="pq" placeholder="Busque cards, histórico, e-mails… ou digite um comando" aria-label="Buscar" role="combobox" aria-expanded="true" aria-controls="pl" aria-autocomplete="list" value="${esc(initial)}" autofocus><kbd>Esc</kbd></div>
    <div class="plist" id="pl" role="listbox"></div><div class="pfoot"><span><kbd>↑</kbd><kbd>↓</kbd> navegar</span><span><kbd>Enter</kbd> abrir</span><span><kbd>Ctrl</kbd>+<kbd>Enter</kbd> enviar como pedido</span></div>`});
  const q = $("#pq", m.box), pl = $("#pl", m.box);
  let items = [], ai = 0, seq = 0, tmr;
  const hi = (txt, qq) => { const s = esc(txt); if (!qq) return s; const i = s.toLowerCase().indexOf(esc(qq).toLowerCase()); return i < 0 ? s : s.slice(0, i) + "<mark>" + s.slice(i, i + esc(qq).length) + "</mark>" + s.slice(i + esc(qq).length); };
  const draw = (qq, remote) => {
    const ql = qq.toLowerCase();
    const cm = cmds.filter(c => !ql || c.t.toLowerCase().includes(ql)).slice(0, qq ? 4 : 9).map(c => ({...c, g: "Comandos"}));
    const local = qq ? S.cards.filter(c => (c.title + " " + (c.body || "")).toLowerCase().includes(ql)).slice(0, 6).map(c => ({t: c.title, s: (KINDS[c.kind] || "") + (cardDate(c) ? " · " + whenTxt(c) : ""), i: KIND_ICO[c.kind] || "board", g: "Cards", run: () => openCard(c.id)})) : [];
    const seen = new Set(local.map(x => x.t));
    const rem = (remote || []).filter(x => !seen.has(x.title || x.titulo)).slice(0, 8).map(x => ({t: x.title || x.titulo || x.text || "resultado", s: [x.snippet || x.trecho, x.fonte || (x.source && (x.source.integration || x.source)) || x.scope].filter(Boolean).join(" · "), i: x.scope === "history" ? "clock" : x.source ? "plug" : "search", g: "Resultados",
      run: () => { if (x.id && S.cards.find(c => c.id === x.id)) openCard(x.id); else if (x.url) window.open(x.url, "_blank", "noopener"); else setInput(x.title || ""); }}));
    const ask = qq ? [{t: `Pedir ao Djorge: “${qq}”`, s: "envia como comando", i: "spark", g: "Pedido", run: () => { setInput(qq); submit(); }}] : [];
    items = [...ask, ...local, ...rem, ...cm]; ai = 0;
    let g = ""; pl.innerHTML = items.map((it, i) => { const h = it.g !== g ? `<div class="pgroup">${esc(it.g)}</div>` : ""; g = it.g;
      return h + `<button type="button" class="pitem ${i === 0 ? "act" : ""}" role="option" id="po${i}" aria-selected="${i === 0}" data-i="${i}"><span class="pi">${ico(it.i, "sm")}</span><span class="pt"><b>${hi(it.t, qq)}</b>${it.s ? `<small>${esc(it.s)}</small>` : ""}</span></button>`; }).join("") ||
      `<div class="empty" style="padding:24px"><p>Nada encontrado.</p></div>`;
    q.setAttribute("aria-activedescendant", items.length ? "po0" : "");
    $$(".pitem", pl).forEach(b => { b.onclick = () => run(+b.dataset.i); b.onmousemove = () => act(+b.dataset.i); });
  };
  const act = i => { ai = (i + items.length) % items.length; $$(".pitem", pl).forEach((b, k) => { b.classList.toggle("act", k === ai); b.setAttribute("aria-selected", k === ai); }); const e = $(`#po${ai}`, pl); e && e.scrollIntoView({block: "nearest"}); q.setAttribute("aria-activedescendant", "po" + ai); };
  const run = i => { const it = items[i]; if (!it) return; m.close(); it.run(); };
  q.oninput = () => { const qq = q.value.trim(); draw(qq); clearTimeout(tmr); if (qq.length < 2) return; const my = ++seq;
    tmr = setTimeout(async () => { try { const r = await api(`/api/search?q=${encodeURIComponent(qq)}&scope=all`); if (my === seq) draw(qq, (r && (r.results || r.items)) || (Array.isArray(r) ? r : [])); } catch (e) {} }, 180); };
  q.onkeydown = e => { if (e.key === "ArrowDown") { e.preventDefault(); act(ai + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); act(ai - 1); }
    else if (e.key === "Enter") { e.preventDefault(); if (e.ctrlKey && q.value.trim()) { const t = q.value.trim(); m.close(); setInput(t); submit(); } else run(ai); } };
  draw(initial); if (initial) q.oninput();
}

/* ---------------------------------------------------------------- CONFIGURAÇÕES */
function setRow(title, sub, ctrl) { return `<div class="setrow"><div class="st"><b>${title}</b>${sub ? `<small>${sub}</small>` : ""}</div>${ctrl}</div>`; }
function openSettings(start = "geral") {
  if ($(".modal [data-settings]")) return;
  const m = tabsModal({title: `<span data-settings>Configurações</span>`, start, tabs: [
    {id: "geral", label: "Geral", icon: "gear"}, {id: "voz", label: "Voz", icon: "mic"}, {id: "modelo", label: "Modelo", icon: "cpu"},
    {id: "leve", label: "Desempenho", icon: "spark"}, {id: "api", label: "API local", icon: "plug"},
    {id: "seguranca", label: "Segurança", icon: "shield"}, {id: "automacoes", label: "Automações", icon: "tree"}, {id: "avancado", label: "Avançado", icon: "edit"}],
    onTab: (id, el) => ({geral: setGeneral, voz: setVoice, modelo: setModel, leve: setLeve, api: setLocalApi, seguranca: setSecurity, automacoes: setAutos, avancado: setAdvanced}[id])(el)});
  return m;
}
function setGeneral(el) {
  const p = S.prefs;
  const th = p.theme === "claro" ? "light" : p.theme === "escuro" ? "dark" : p.theme;
  el.innerHTML = `<p class="lead">Aparência e comportamento. As mudanças valem na hora.</p>
    ${setRow("Tema", "Automático segue o Windows.", segHTML("theme", [["light", "Claro"], ["dark", "Escuro"], ["auto", "Automático"]], th || "auto", "Tema"))}
    ${setRow("Densidade", "Compacta mostra mais cards por tela.", segHTML("density", [["compact", "Compacta"], ["comfortable", "Normal"], ["comfy", "Espaçosa"]], p.density, "Densidade"))}
    ${setRow("Cor de destaque", "Da paleta Djorge.", `<div class="swatches" role="radiogroup" aria-label="Cor de destaque">${ACCENTS.map(([n, c]) => `<button type="button" role="radio" style="--c:${c}" aria-checked="${(p.accent || "rosa") === n}" data-acc="${n}" aria-label="${n}" data-tip="${n}"></button>`).join("")}</div>`)}
    ${setRow("Tamanho do texto", "", segHTML("font", [["sm", "A−"], ["md", "A"], ["lg", "A+"], ["xl", "A++"]], typeof p.font_size === "string" ? p.font_size : "md", "Tamanho do texto"))}
    ${setRow("Visão inicial", "O que abre primeiro.", selHTML("dv", VIEWS.slice(0, 4).map(v => [v.id, v.label]), p.default_view, "Visão inicial"))}
    ${setRow("Colunas do quadro", "Quais colunas aparecem.", `<div class="row" style="gap:6px">${COLS.map(c => `<button type="button" class="chip ${(p.columns || []).includes(c.id) ? "on" : ""}" data-colt="${c.id}" aria-pressed="${(p.columns || []).includes(c.id)}">${c.label}</button>`).join("")}</div>`)}
    ${setRow("Filtros salvos", (p.saved_filters || []).length ? "" : "Nenhum ainda — salve pela barra de filtros.", `<div class="row" style="gap:6px">${(p.saved_filters || []).map((f, i) => `<span class="chip">${esc(f.name || f.nome)}<button type="button" class="x" data-rmf="${i}" aria-label="Apagar filtro">✕</button></span>`).join("")}</div>`)}`;
  wireSeg(el, "theme", v => savePrefs({theme: v}, true)); wireSeg(el, "density", v => savePrefs({density: v}, true)); wireSeg(el, "font", v => savePrefs({font_size: v}, true));
  wireSel(el, "dv", VIEWS.slice(0, 4).map(v => [v.id, v.label]), v => savePrefs({default_view: v}, true));
  $$("[data-acc]", el).forEach(b => b.onclick = () => { $$("[data-acc]", el).forEach(x => x.setAttribute("aria-checked", x === b)); savePrefs({accent: b.dataset.acc}, true); });
  $$("[data-colt]", el).forEach(b => b.onclick = () => { const cur = new Set(S.prefs.columns || []); cur.has(b.dataset.colt) ? cur.delete(b.dataset.colt) : cur.add(b.dataset.colt);
    if (!cur.size) return toast("Deixe pelo menos uma coluna."); b.classList.toggle("on"); b.setAttribute("aria-pressed", cur.has(b.dataset.colt)); savePrefs({columns: COLS.map(c => c.id).filter(x => cur.has(x))}, true); });
  $$("[data-rmf]", el).forEach(b => b.onclick = () => { const l = [...(S.prefs.saved_filters || [])]; l.splice(+b.dataset.rmf, 1); savePrefs({saved_filters: l}, true); setGeneral(el); });
}
async function setVoice(el) {
  el.innerHTML = `<p class="lead">Fale com o Djorge e, se quiser, ouça as respostas. O áudio é processado neste computador.</p><div class="sk"></div>`;
  await loadVoice();
  const v = S.voice || {}, p = S.prefs;
  const voices = (v.voices || []).map(x => typeof x === "string" ? [x, x] : [x.id || x.name, x.name || x.id]);
  const st = v.stt_setup || {};
  el.innerHTML = `<p class="lead">Fale com o Djorge e, se quiser, ouça as respostas. O áudio é processado neste computador.</p>
    ${setRow("Microfone", "Mostrar o botão de falar no campo de entrada.", swHTML("v-on", p.voice !== false))}
    ${setRow("Ler as respostas em voz alta", v.tts ? `Voz do Windows (${esc(v.tts_engine || "SAPI")}).` : "Usará a voz do navegador.", swHTML("v-speak", !!p.speak))}
    ${voices.length ? setRow("Voz", "", selHTML("v-voice", voices, store.get("voice", null) || v.voice || voices[0][0], "Voz")) : ""}
    ${setRow("Velocidade da fala", "", rngHTML("v-rate", store.get("rate", 1), .6, 1.6, .1, "×"))}
    ${setRow("Reconhecimento de fala local", v.stt ? `Ativo · ${esc(v.stt_engine || "")} ${esc(v.stt_model || "")}` : v.stt_available ? (v.stt_installed ? "Instalado, carrega ao usar." : "Disponível — precisa baixar o modelo de fala.") : "Indisponível; usaremos o reconhecimento do navegador se existir.",
      v.stt_available && !v.stt_installed ? `<button class="btn sm primary" id="v-setup">${ico("dl", "sm")} Ativar voz</button>` : `<span class="chip ${v.stt ? "on" : ""}">${v.stt ? "ativo" : v.stt_available ? "pronto" : "navegador"}</span>`)}
    ${st.state && st.state !== "idle" && st.state !== "pronto" ? `<div class="pbar"><i style="width:${Math.round((st.progress || 0) * 100)}%"></i></div><p class="hint">${esc(st.message || st.state)}</p>` : ""}
    <div class="row" style="margin-top:14px"><button class="btn sm" id="v-test">${ico("vol", "sm")} Testar fala</button><button class="btn sm ghost" id="v-stop">Parar</button></div>`;
  wireRanges(el);
  $("#v-on", el).onchange = e => { savePrefs({voice: e.target.checked}, true); syncMic(); };
  $("#v-speak", el).onchange = e => savePrefs({speak: e.target.checked}, true);
  if (voices.length) wireSel(el, "v-voice", voices, x => store.set("voice", x));
  $("#v-rate", el).onchange = e => store.set("rate", +e.target.value);
  $("#v-test", el).onclick = () => speak("Olá! Eu sou o Djorge, e estou falando daqui do seu computador.");
  $("#v-stop", el).onclick = stopSpeak;
  const su = $("#v-setup", el); if (su) su.onclick = async () => { su.setAttribute("aria-busy", "true"); await tryApi("/api/voice/setup", {}); const t = setInterval(async () => { await loadVoice(); const s = (S.voice || {}).stt_setup || {};
    if (!el.isConnected || !s.state || /pronto|done|ok|erro|error|idle/.test(s.state)) { clearInterval(t); if (el.isConnected) setVoice(el); syncMic(); } else { const b = $(".pbar i", el); if (b) b.style.width = Math.round((s.progress || 0) * 100) + "%"; else setVoice(el); } }, 1000); };
}
async function setModel(el) {
  const m = S.model || {}, s = modelUiState();
  el.innerHTML = `<p class="lead">O DOFL só decide (escolher, conferir, pontuar) — nunca inventa texto. Roda só neste computador.</p>
    ${setRow("DOFL V2", {ausente: "Não instalado", baixando: `Baixando ${Math.round((m.progress || 0) * 100)}%`, pronto: "Instalado e carregado", carregando: "Carregando", verificando: "Verificando", erro: "Erro"}[s], `<button class="btn sm" id="mo-open">Detalhes</button>`)}
    <h3>Outros modelos</h3><div id="mo-list"><div class="sk"></div></div>`;
  $("#mo-open", el).onclick = openModel;
  try { const r = await api("/api/models"); const ms = (r && r.models) || [];
    const arr = Array.isArray(ms) ? ms : Object.entries(ms).map(([k, v]) => ({name: k, ...v}));
    $("#mo-list", el).innerHTML = arr.length ? arr.map(x => setRow(esc(x.name), esc(x.role || x.path || ""), `<span class="chip ${x.loaded ? "on" : ""}">${x.loaded ? "carregado" : x.enabled === false ? "desligado" : "parado"}</span>
      <button class="btn sm" data-ma="${x.loaded ? "unload" : "load"}" data-n="${esc(x.name)}">${x.loaded ? "Descarregar" : "Carregar"}</button>`)).join("") : `<p class="hint">Nenhum outro modelo.</p>`;
    $$("[data-ma]", el).forEach(b => b.onclick = async () => { await tryApi("/api/models/" + b.dataset.ma, {name: b.dataset.n}); toast(b.dataset.ma === "load" ? "Carregando…" : "Descarregado"); setTimeout(() => setModel(el), 600); });
  } catch (e) { $("#mo-list", el).innerHTML = `<p class="hint">${esc(human(e))}</p>`; }
}
/* ----- leveza: pausar, liberar memória, iniciar com o Windows ----- */
async function loadRec() { try { S.rec = await api("/api/app/recursos"); } catch (e) { S.rec = null; } return S.rec; }
const mb = v => v == null ? "—" : (+v >= 1024 ? (v / 1024).toFixed(1).replace(".", ",") + " GB" : Math.round(v) + " MB");
async function appAction(a, quiet) {
  const r = await tryApi("/api/app/" + a, {}); if (!r) return null;
  if (!quiet) toast({pausar: "Djorge pausado: nada roda em segundo plano até você retomar.", retomar: "Djorge retomado", liberar_memoria: r.liberado_mb != null ? `Memória liberada: ${mb(r.liberado_mb)}` : "Memória liberada"}[a] || "Feito");
  await loadRec(); renderModelChip(); return r;
}
async function setLeve(el) {
  el.innerHTML = `<p class="lead">Deixe o Djorge leve: pause quando não precisar e libere memória quando quiser.</p><div class="sk"></div>`;
  const r = await loadRec(); if (!el.isConnected) return;
  if (!r) { el.innerHTML = `<p class="lead">Deixe o Djorge leve: pause quando não precisar e libere memória quando quiser.</p><p class="hint">Esses controles ainda não estão disponíveis nesta versão.</p>`; return; }
  const run = asList(r.rodando || r.processos || r.partes, "items");
  el.innerHTML = `<p class="lead">Deixe o Djorge leve: pause quando não precisar e libere memória quando quiser.</p>
    <div class="kv" style="margin-bottom:10px"><div><small>Memória em uso</small><b>${esc(mb(r.memoria_mb ?? r.memoria))}</b></div><div><small>Estado</small><b>${r.pausado ? "Pausado" : "Rodando"}</b></div>${r.modelo_carregado != null ? `<div><small>Modelo na memória</small><b>${r.modelo_carregado ? "Sim" : "Não"}</b></div>` : ""}</div>
    ${setRow(r.pausado ? "Djorge pausado" : "Pausar Djorge", r.pausado ? "Nada roda em segundo plano. As suas coisas continuam salvas." : "Para sincronizações, secretário e modelo até você retomar.", r.pausado ? `<button class="btn primary" data-aa="retomar">Retomar</button>` : `<button class="btn" data-aa="pausar">Pausar</button>`)}
    ${setRow("Liberar memória", "Descarrega o modelo e limpa caches. Ele volta sozinho quando precisar.", `<button class="btn" data-aa="liberar_memoria">Liberar memória</button>`)}
    ${setRow("Iniciar com o Windows", "Abre minimizado na bandeja, para os avisos chegarem mesmo com a janela fechada.", swHTML("lw-start", r.iniciar_com_windows ?? true))}
    ${run.length ? `<h3>O que está rodando</h3><div class="tlist runl">${run.map(x => `<div class="trow" style="cursor:default"><i class="okdot ${x.ativo === false || /parad|paus|ocios/i.test(x.estado || "") ? "off" : ""}"></i><div class="tt"><b>${esc(x.nome || x.name)}</b><small>${esc(titleCase(x.estado || (x.ativo === false ? "parado" : "rodando")))}</small></div><span class="mono hint">${esc(mb(x.memoria_mb ?? x.memoria))}</span></div>`).join("")}</div>` : ""}`;
  $$("[data-aa]", el).forEach(b => b.onclick = async () => { b.setAttribute("aria-busy", "true"); await appAction(b.dataset.aa); if (el.isConnected) setLeve(el); });
  $("#lw-start", el).onchange = async e => { const on = e.target.checked; const x = await tryApi("/api/app/iniciar_com_windows", {ativo: on}); if (x) toast(on ? "Vai iniciar com o Windows (minimizado)" : "Não vai mais iniciar com o Windows"); else e.target.checked = !on; };
}
/* ----- API local de cada modelo ----- */
async function setLocalApi(el) {
  el.innerHTML = `<p class="lead">Outros programas deste computador podem usar os modelos do Djorge por uma API local. Só responde em 127.0.0.1.</p><div class="sk"></div>`;
  let list; try { list = asList(await api("/api/local_api"), "apis", "items", "modelos"); } catch (e) { list = null; }
  let tok = ""; try { tok = (await api("/api/token")).token || ""; } catch (e) {}
  if (!el.isConnected) return;
  const head = `<p class="lead">Outros programas deste computador podem usar os modelos do Djorge por uma API local. Só responde em 127.0.0.1.</p>`;
  const evt = `curl -X POST http://127.0.0.1:8765/api/event -H "X-Token: ${tok || "SEU_TOKEN"}" -H "Content-Type: application/json" -d "{\\"text\\":\\"boleto vence amanhã\\"}"`;
  el.innerHTML = head + (list === null ? `<p class="hint">O painel de API por modelo ainda não está disponível nesta versão.</p>` : list.length ? list.map((x, i) => { const url = x.url || `http://127.0.0.1:${x.porta}`;
    const ex = x.exemplo || x.curl || `curl -X POST ${url}/v1/decide -H "X-Token: ${tok || "SEU_TOKEN"}" -H "Content-Type: application/json" -d "{\\"text\\":\\"reunião amanhã às 15h\\"}"`;
    return `<article class="apic" data-i="${i}"><header><div><b>${esc(titleCase(x.modelo || x.name))}</b><small class="mono">${esc(url)}</small></div><span class="sp"></span>${swHTML("la-on" + i, !!x.ativa, x.ativa ? "Ligada" : "Desligada")}</header>
      <div class="row"><label class="hint" for="la-p${i}">Porta</label><input class="inp" id="la-p${i}" type="number" min="1024" max="65535" value="${esc(x.porta || "")}" style="width:110px">
        <button class="btn sm" data-cp="url">Copiar URL</button><button class="btn sm" data-cp="tok">Copiar token</button><button class="btn sm" data-cp="curl">Copiar exemplo curl</button></div>
      ${x.token_dica ? `<p class="hint">${esc(x.token_dica)}</p>` : ""}<pre class="code">${esc(ex)}</pre></article>`; }).join("") : `<p class="hint">Nenhum modelo instalado ainda.</p>`) +
    `<h3>Enviar eventos</h3><p class="hint">Qualquer programa pode mandar textos para o Djorge triar e rodar as automações.</p><pre class="code">${esc(evt)}</pre><div class="row" style="margin-top:8px"><button class="btn sm" id="la-evt">Copiar exemplo</button><button class="btn sm" id="la-tok">Copiar token</button></div>`;
  $("#la-evt", el).onclick = () => copyText(evt, "Exemplo copiado"); $("#la-tok", el).onclick = () => tok ? copyText(tok, "Token copiado") : toast("O token está no arquivo token.txt da pasta do Djorge.");
  (list || []).forEach((x, i) => { const card = $(`.apic[data-i="${i}"]`, el), url = x.url || `http://127.0.0.1:${x.porta}`;
    const save = async patch => { const r = await tryApi("/api/local_api", {modelo: x.modelo || x.name, ativa: !!x.ativa, porta: +x.porta, ...patch}); if (r) { Object.assign(x, patch); toast(patch.ativa === undefined ? "Porta salva" : patch.ativa ? "API ligada" : "API desligada"); setLocalApi(el); } return r; };
    $(`#la-on${i}`, card).onchange = async e => { if (!(await save({ativa: e.target.checked}))) e.target.checked = !e.target.checked; };
    $(`#la-p${i}`, card).onchange = e => { const v = +e.target.value; if (v < 1024 || v > 65535) return toast("Use uma porta entre 1024 e 65535.", "warn"); save({porta: v}); };
    $$("[data-cp]", card).forEach(b => b.onclick = () => { const k = b.dataset.cp; if (k === "url") copyText(url, "URL copiada"); if (k === "tok") tok ? copyText(tok, "Token copiado") : toast("O token está no arquivo token.txt da pasta do Djorge.");
      if (k === "curl") copyText($("pre", card).textContent, "Exemplo copiado"); }); });
}
let CFG = null;
async function loadCfg() { try { CFG = await api("/api/config"); } catch (e) { CFG = null; toast(human(e), "bad"); } return CFG; }
async function saveCfg(patch) { try { const r = await api("/api/config", patch); toast(r && r.restart && r.restart.length ? "Salvo · alguns itens valem após reiniciar" : "Configuração salva"); } catch (e) { toast(human(e), "bad"); } }
async function setSecurity(el) {
  el.innerHTML = `<p class="lead">Cada mensagem que chega é verificada: seguro, spam, golpe, phishing ou malware.</p><div class="sk"></div>`;
  const c = await loadCfg(); const sg = (c && c.seguranca) || {};
  const kinds = ["spam", "golpe", "phishing", "malware"], on = sg.alert_on || [];
  el.innerHTML = `<p class="lead">Cada mensagem que chega é verificada: seguro, spam, golpe, phishing ou malware.</p>
    ${setRow("Verificação automática", "Analisa tudo que chega.", swHTML("sg-en", sg.enabled !== false))}
    ${setRow("Avisar quando for", "", `<div class="row" style="gap:6px">${kinds.map(k => `<button type="button" class="chip ${on.includes(k) ? "on" : ""}" aria-pressed="${on.includes(k)}" data-k="${k}">${k}</button>`).join("")}</div>`)}
    ${setRow("Certeza mínima para avisar", "Mais alto = menos alarmes falsos.", rngHTML("sg-p", sg.min_probability ?? .7, .3, .99, .01, "%"))}
    ${setRow("Tamanho mínimo do texto", "Textos muito curtos são ignorados.", rngHTML("sg-c", sg.min_chars ?? 12, 0, 80, 1, " car."))}
    ${setRow("Mostrar avisos", "Toasts discretos no canto da tela.", swHTML("sg-al", sg.alert !== false))}
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn primary" id="sg-save">Salvar</button></div>`;
  wireRanges(el);
  $$("[data-k]", el).forEach(b => b.onclick = () => { b.classList.toggle("on"); b.setAttribute("aria-pressed", b.classList.contains("on")); });
  $("#sg-save", el).onclick = () => saveCfg({seguranca: {enabled: $("#sg-en", el).checked, alert: $("#sg-al", el).checked, min_probability: +$("#sg-p", el).value, min_chars: +$("#sg-c", el).value, alert_on: $$("[data-k].on", el).map(b => b.dataset.k)}});
}
async function setAutos(el) {
  const c = await loadCfg(); const ac = (c && c.actions) || {};
  el.innerHTML = `<p class="lead">Como as árvores agem quando algo chega.</p>
    ${Object.entries(ac).filter(([, v]) => typeof v === "boolean" || typeof v === "number").map(([k, v]) => setRow(esc(k.replace(/_/g, " ")), "", typeof v === "boolean" ? swHTML("ac-" + k, v) : `<input class="inp" style="width:110px" type="number" id="ac-${esc(k)}" value="${esc(v)}">`)).join("") || `<p class="hint">Sem opções de ações nesta versão.</p>`}
    ${setRow("Árvores", ((S.status && S.status.trees) || []).length + " carregada(s)", `<button class="btn sm" id="au-go">Abrir editor</button>`)}
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn primary" id="au-save">Salvar</button></div>`;
  $("#au-go", el).onclick = () => { modalStack.forEach(m => m.close()); go("automacoes"); };
  $("#au-save", el).onclick = () => { const o = {}; Object.entries(ac).forEach(([k, v]) => { const i = $("#ac-" + CSS.escape(k), el); if (!i) return; o[k] = typeof v === "boolean" ? i.checked : +i.value; }); saveCfg({actions: o}); };
}
async function setAdvanced(el) {
  el.innerHTML = `<p class="lead">Arquivo de configuração, histórico de eventos e confirmações pendentes.</p>
    <div class="seg" role="tablist" aria-label="Seção" style="margin-bottom:12px"><button role="tab" aria-selected="true" data-s="cfg">config.yaml</button><button role="tab" aria-selected="false" data-s="hist">Histórico</button><button role="tab" aria-selected="false" data-s="pend">Pendências</button></div><div id="adv"></div>`;
  const box = $("#adv", el);
  const show = async s => { $$("[data-s]", el).forEach(b => b.setAttribute("aria-selected", b.dataset.s === s)); box.innerHTML = `<div class="sk"></div>`;
    if (s === "cfg") { const c = await loadCfg(); box.innerHTML = `<textarea class="ta code" id="cfgj" spellcheck="false" aria-label="Configuração">${esc(JSON.stringify(c || {}, null, 2))}</textarea><p class="hint">Formato JSON das mesmas chaves do config.yaml. Campos desconhecidos são recusados.</p><div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn primary" id="cfgs">Salvar configuração</button></div>`;
      $("#cfgs", box).onclick = () => { try { saveCfg(JSON.parse($("#cfgj", box).value)); } catch (e) { toast("JSON inválido: " + e.message, "bad"); } }; }
    if (s === "hist") { try { const h = await api("/api/history?n=100"); const list = asList(h, "items", "history");
      box.innerHTML = `<div class="row hbar"><button class="btn sm" id="hb-before">${ico("clock", "sm")} Apagar antes de…</button><button class="btn sm danger" id="hb-all">${ico("trash", "sm")} Apagar tudo</button><span class="hint">Clique com o botão direito num item para mais opções.</span></div>` +
        (list.length ? `<div class="hist">${list.map((x, i) => `<div class="card hitem" data-hi="${i}" data-ctx="hist"><div class="hrow"><small>${esc(fmtDT(x.t || x.time || ""))} · ${esc(titleCase((x.event && x.event.source) || x.source || ""))}</small>${x.id != null ? `<button class="iconbtn sm" data-hdel="${i}" aria-label="Apagar item" data-tip="Apagar">${ico("trash", "sm")}</button>` : ""}</div><div>${esc(((x.event && (x.event.title || x.event.text)) || x.text || "").slice(0, 160))}</div>${(x.path || x.steps || []).length ? `<small>${esc((x.path || x.steps).map(p => p.node || p.label || p).join(" → ").slice(0, 200))}</small>` : ""}</div>`).join("")}</div>` : empty("Histórico vazio", "Os eventos que chegarem aparecem aqui."));
      const delOne = async i => { const x = list[i]; if (x.id == null) return toast("Este item não pode ser apagado sozinho.", "warn"); if (await tryApi(`/api/history/${encodeURIComponent(x.id)}/delete`, {})) { toast("Item apagado"); show("hist"); } };
      $$("[data-hdel]", box).forEach(b => b.onclick = () => delOne(+b.dataset.hdel));
      $$("[data-hi]", box).forEach(r => r.oncontextmenu = e => { const i = +r.dataset.hi, x = list[i]; ctxMenu(e, [{value: "copy", label: "Copiar texto", icon: "list"}, {value: "before", label: "Apagar este e os mais antigos", icon: "clock", danger: true}, {value: "del", label: "Apagar", icon: "trash", danger: true, disabled: x.id == null}], async v => {
        if (v === "copy") copyText(((x.event && (x.event.title || x.event.text)) || x.text || ""));
        if (v === "del") delOne(i);
        if (v === "before" && (x.t || x.time)) { const d = new Date(new Date(x.t || x.time).getTime() + 1000); if (await tryApi("/api/history/clear", {antes_de: isoLocal(d)})) { toast("Itens apagados"); show("hist"); } } }); });
      $("#hb-all", box).onclick = () => confirmBox("Apagar todo o histórico?", "Os eventos registrados somem. Cards e automações continuam.", "Apagar tudo", async () => { if (await tryApi("/api/history/clear", {tudo: true})) { toast("Histórico apagado"); show("hist"); } });
      $("#hb-before", box).onclick = () => askText("Apagar antes de", "Apagar o histórico anterior a", "", async d => { if (d && await tryApi("/api/history/clear", {antes_de: d + "T00:00:00"})) { toast("Histórico antigo apagado"); show("hist"); } }, ymd(addDays(new Date(), -30)), "date"); }
      catch (e) { box.innerHTML = `<p class="hint">${esc(human(e))}</p>`; } }
    if (s === "pend") { try { const p = await api("/api/pending"); const list = Array.isArray(p) ? p : [];
      box.innerHTML = list.length ? `<div class="tlist">${list.map(pendHTML).join("")}</div>` : empty("Nada pendente", "Ações que precisam da sua aprovação aparecem aqui."); wirePend(box, () => show("pend")); }
      catch (e) { box.innerHTML = `<p class="hint">${esc(human(e))}</p>`; } }
    if (s === "api") { box.innerHTML = `<p>Outros programas podem enviar eventos para <code>POST http://127.0.0.1:8765/api/event</code> com o cabeçalho <code>X-Token</code>.</p><div class="row" style="margin-top:12px"><button class="btn" id="tok">Copiar token</button></div>`;
      $("#tok", box).onclick = async () => { try { const t = (await api("/api/token")).token; await navigator.clipboard.writeText(t); toast("Token copiado"); } catch (e) { toast("O token está no arquivo token.txt da pasta do Djorge."); } }; }
  };
  $$("[data-s]", el).forEach(b => b.onclick = () => show(b.dataset.s));
  show("cfg");
}

/* ---------------------------------------------------------------- INTEGRAÇÕES */
const INT_META = {
  google: {c: "linear-gradient(135deg,#4285f4,#34a853)", l: "G", steps: ["No Google Cloud Console, crie um projeto e uma credencial OAuth do tipo “App para computador” (Desktop app).", "Habilite as APIs Gmail, Calendar e Tasks.", "Cole aqui o client ID e o client secret e clique em Conectar."]},
  microsoft: {c: "linear-gradient(135deg,#0078d4,#7b3fe4)", l: "M", steps: ["No portal Microsoft Entra, registre um app para contas pessoais e organizacionais.", "Adicione a plataforma “Mobile and desktop” com o redirect http://127.0.0.1:8765/oauth/callback e ligue “fluxos de cliente público”.", "Cole aqui o client ID (application ID) e clique em Conectar."]},
  whatsapp: {c: "linear-gradient(135deg,#25d366,#128c7e)", l: "W", steps: ["No Meta for Developers, crie um app Business com o produto WhatsApp.", "Gere um token permanente de usuário do sistema.", "Cole o token e o phone number ID. O Djorge só envia; não lê mensagens recebidas."]},
  gemini: {c: "linear-gradient(135deg,#8e75f0,#d96570)", l: "✦", steps: ["Crie uma chave no Google AI Studio.", "Cole a chave aqui. Só é usada quando você pedir um rascunho de texto."]}};
async function openIntegrations(focusId) {
  const m = modal({title: "Integrações", body: `<div class="tabpanel" style="padding:4px 24px 24px"><div class="leave">${ico("shield")}<div><b>O que sai do seu computador?</b> Ao conectar, o Djorge fala direto com o serviço (Google, Microsoft, Meta) usando suas credenciais, guardadas no cofre do Windows. Nada passa por servidores do Djorge. O Gemini é o único que envia texto para uma IA de fora — e só quando você pedir.</div></div><div class="icards" id="ic"><div class="sk" style="height:180px"></div><div class="sk" style="height:180px"></div></div></div>`});
  const box = $("#ic", m.box);
  const draw = async () => {
    let list;
    try { const r = await api("/api/integrations"); list = Array.isArray(r) ? r : (r && (r.integrations || r.items)) || []; S.ints = list; }
    catch (e) { box.innerHTML = empty("Integrações indisponíveis", human(e)); return; }
    if (!m.el.isConnected) return;
    box.innerHTML = list.map(it => { const meta = INT_META[it.id] || {c: "var(--rose)", l: (it.name || "?")[0], steps: []};
      const st = it.connected ? `<span class="st on">● conectado${it.account ? " · " + esc(it.account) : ""}</span>` : it.error ? `<span class="st err">● ${esc(it.error)}</span>` : `<span class="st">${it.configured ? "configurado · não conectado" : "não configurado"}</span>`;
      return `<article class="icard" data-int="${esc(it.id)}"><header><span class="logo" style="background:${meta.c}">${esc(meta.l)}</span><div style="flex:1;min-width:0"><h4>${esc(it.name || it.id)} ${it.leaves_computer || it.id === "gemini" ? `<span class="tag" style="background:var(--bad-soft);color:var(--bad)">Sai do computador</span>` : ""}</h4>${st}</div></header>
        ${(it.features || []).length ? `<div class="feats">${it.features.map(f => `<span class="tag">${esc(typeof f === "string" ? f : f.label || f.id)}</span>`).join("")}</div>` : ""}
        ${it.last_sync ? `<p class="hint">última sincronização: ${esc(String(it.last_sync).replace("T", " ").slice(0, 16))}</p>` : ""}
        ${it.leaves_computer || it.id === "gemini" ? `<div class="leave out">${ico("shield", "sm")}<span>O texto do rascunho vai para o Google. Só quando você pedir.</span></div>` : ""}
        <details ${!it.configured && !it.connected && focusId === it.id ? "open" : ""}><summary>${it.configured ? "Credenciais" : "Configurar"}</summary>
          <ol class="help" style="padding-left:18px;margin:6px 0 10px">${meta.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>
          ${(it.needs || []).map(n => `<div class="field"><label for="f-${esc(it.id)}-${esc(n.key)}">${esc(n.label || n.key)}${n.optional ? " <small>(opcional)</small>" : ""}</label>
            <input class="inp" id="f-${esc(it.id)}-${esc(n.key)}" data-key="${esc(n.key)}" type="${n.secret ? "password" : "text"}" autocomplete="off" placeholder="${n.filled ? (n.secret ? "••• salvo" : "salvo — deixe vazio para manter") : ""}"></div>`).join("")}
          ${(it.needs || []).length ? `<button class="btn sm" data-ia="configure">Salvar credenciais</button>` : ""}</details>
        <div class="row" style="margin-top:12px">${it.connected ? `<button class="btn sm" data-ia="sync">${ico("sync", "sm")} Sincronizar</button><button class="btn sm ghost" data-ia="disconnect">Desconectar</button>` :
          it.id === "gemini" && it.configured ? `<span class="chip on">chave salva</span><button class="btn sm ghost" data-ia="disconnect">Remover</button>` : `<button class="btn sm primary" data-ia="connect">Conectar</button>`}</div></article>`; }).join("") || empty("Nenhuma integração", "Esta versão não trouxe integrações.");
    $$("[data-ia]", box).forEach(b => b.onclick = () => intAction(b.closest("[data-int]").dataset.int, b.dataset.ia, b, draw));
  };
  draw();
}
async function intAction(id, a, btn, redraw) {
  const card = btn.closest("[data-int]");
  if (a === "disconnect") {
    const m = modal({title: "Desconectar", cls: "sm", body: `<div style="padding:0 24px 10px"><p>Como você quer desconectar?</p><label class="sw" style="margin-top:12px">${""}<input type="checkbox" id="forget"><span class="tr"></span><span>Também esquecer as credenciais salvas</span></label></div>`,
      foot: `<button class="btn ghost" data-close>Cancelar</button><button class="btn danger" id="dok">Desconectar</button>`});
    $("#dok", m.box).onclick = async () => { const forget = $("#forget", m.box).checked; m.close(); if (await tryApi(`/api/integrations/${id}/disconnect`, forget ? {forget: true} : {})) { toast("Desconectado"); redraw(); } };
    return;
  }
  btn.setAttribute("aria-busy", "true");
  try {
    if (a === "configure" || a === "connect") {
      const values = {}; $$("input[data-key]", card).forEach(i => { if (i.value.trim()) values[i.dataset.key] = i.value.trim(); });
      if (Object.keys(values).length) await api(`/api/integrations/${id}/configure`, values);
      if (a === "configure") { toast("Credenciais salvas"); redraw(); return; }
      const r = await api(`/api/integrations/${id}/connect`, {});
      if (r && r.auth_url) { window.open(r.auth_url, "_blank", "noopener"); toast("Conclua o login na janela que abriu. Eu aviso quando terminar.", "", {ms: 7000});
        let n = 0; const t = setInterval(async () => { n++; try { const l = await api("/api/integrations"); const it = (Array.isArray(l) ? l : l.integrations || []).find(x => x.id === id);
          if (it && it.connected) { clearInterval(t); toast(`${it.name || id} conectado`, "", {title: "Integração"}); if (card.isConnected) redraw(); refreshCards(); } } catch (e) {} if (n > 90) clearInterval(t); }, 2000); }
      else { toast("Conectado"); redraw(); }
    }
    if (a === "sync") { const r = await api(`/api/integrations/${id}/sync`, {}); toast(`Sincronizado · ${(r && r.imported) || 0} novos, ${(r && r.updated) || 0} atualizados`); refreshCards(); redraw(); }
  } catch (e) { toast(human(e), "bad"); if (!card.querySelector("details").open && /config|credencia|client|token|chave/i.test(human(e))) card.querySelector("details").open = true; }
  btn.removeAttribute("aria-busy");
}

/* ---------------------------------------------------------------- VOZ */
let recorderLoaded = null;
async function loadVoice() { try { S.voice = await api("/api/voice/status"); } catch (e) { S.voice = S.voice || {stt: false, tts: false, stt_available: false}; } return S.voice; }
function micMode() {
  const v = S.voice || {};
  if (v.stt_available && (v.stt_installed || v.stt) && window.DjorgeRecorder) return "local";
  const BR = window.DjorgeRecorder && window.DjorgeRecorder.browserRecognizer ? window.DjorgeRecorder.browserRecognizer() : null;
  if (BR || window.webkitSpeechRecognition || window.SpeechRecognition) return "browser";
  if (v.stt_available && !v.stt_installed) return "setup";
  return null;
}
function syncMic() { const b = $("#btn-mic"); const mm = micMode(); b.hidden = S.prefs.voice === false || (!mm); }
let rec = null, recTimer = null, recStart = 0;
async function toggleMic() {
  if (rec) return stopRec(true);
  const mm = micMode();
  if (mm === "setup") { openSettings("voz"); toast("Ative a voz local para falar com o Djorge."); return; }
  const b = $("#btn-mic"), bar = $("#recbar"), wave = $("#wave");
  wave.innerHTML = "<i></i>".repeat(18);
  const level = v => { b.style.setProperty("--lv", 1 + Math.min(.6, v * 1.6)); const is = wave.children; for (let i = is.length - 1; i > 0; i--) is[i].style.height = is[i - 1].style.height; is[0].style.height = Math.max(3, Math.min(22, v * 70)) + "px"; };
  try {
    if (mm === "local") rec = {kind: "local", h: await window.DjorgeRecorder.start({onLevel: level, onAutoStop: r => handleTranscript(r)})};
    else {
      const R = (window.DjorgeRecorder && window.DjorgeRecorder.browserRecognizer && window.DjorgeRecorder.browserRecognizer()) || null;
      if (R && R.start) { rec = {kind: "br", h: R}; R.onresult = t => handleTranscript({text: t}); await R.start({lang: "pt-BR", onLevel: level}); }
      else { const SR = window.SpeechRecognition || window.webkitSpeechRecognition; const r = new SR(); r.lang = "pt-BR"; r.interimResults = true;
        let finalT = ""; r.onresult = e => { let t = ""; for (const x of e.results) t += x[0].transcript; finalT = t; inp().value = t; autoGrow(); level(.3 + Math.random() * .4); };
        r.onend = () => { if (rec && rec.h === r) { rec = null; endRecUi(); } }; r.onerror = e => { toast("Microfone: " + (e.error === "not-allowed" ? "permissão negada." : e.error), "bad"); };
        rec = {kind: "web", h: r, get text() { return finalT; }}; r.start(); }
    }
  } catch (e) { rec = null; toast(e && e.name === "NotAllowedError" ? "Permita o uso do microfone para falar com o Djorge." : "Não consegui abrir o microfone.", "bad"); return; }
  b.classList.add("rec"); b.setAttribute("aria-pressed", "true"); b.setAttribute("aria-label", "Parar gravação"); bar.classList.add("on");
  recStart = Date.now(); recTimer = setInterval(() => { const s = Math.floor((Date.now() - recStart) / 1000); $("#rect").textContent = `gravando ${Math.floor(s / 60)}:${pad(s % 60)}`; }, 250);
}
function endRecUi() { clearInterval(recTimer); const b = $("#btn-mic"); b.classList.remove("rec"); b.setAttribute("aria-pressed", "false"); b.setAttribute("aria-label", "Falar"); b.style.removeProperty("--lv"); $("#recbar").classList.remove("on"); }
async function stopRec(use) {
  const r = rec; rec = null; endRecUi(); if (!r) return;
  try {
    if (!use) { r.h.cancel ? r.h.cancel() : r.h.abort ? r.h.abort() : r.h.stop(); return; }
    if (r.kind === "local") { $("#rect").textContent = "transcrevendo…"; const out = await r.h.stop(); handleTranscript(out); }
    else if (r.kind === "web") r.h.stop();
    else { const out = await r.h.stop(); if (out) handleTranscript(out); }
  } catch (e) { toast(human(e), "bad"); }
}
function handleTranscript(o) { if (!o) return; if (o.error) { toast(human({message: o.error}), "bad"); return; } const t = (o.text || "").trim(); if (!t) { toast("Não ouvi nada. Tente de novo mais perto do microfone.", "warn"); return; }
  const i = inp(); i.value = (i.value ? i.value + " " : "") + t; autoGrow(); i.focus(); }
async function speak(text) {
  const v = S.voice || {};
  if (v.tts) { try { await api("/api/voice/speak", {text, voice: store.get("voice", undefined), rate: store.get("rate", 1)}); return; } catch (e) {} }
  if (window.speechSynthesis) { const u = new SpeechSynthesisUtterance(text); u.lang = "pt-BR"; u.rate = store.get("rate", 1); const bv = maleVoice(); if (bv) u.voice = bv; speechSynthesis.cancel(); speechSynthesis.speak(u); }
}
function maleVoice() { try { const vs = speechSynthesis.getVoices().filter(v => /^pt(-|_)?BR/i.test(v.lang) || /portugu/i.test(v.name)); if (!vs.length) return null;
  const M = /daniel|antonio|ant[oô]nio|donato|fabio|f[aá]bio|humberto|julio|j[uú]lio|nicolau|valerio|val[eé]rio|\bmale\b|masculin/i, F = /maria|francisca|thalita|female|feminin|luciana|vit[oó]ria/i;
  const br = vs.filter(v => /BR/i.test(v.lang)); const pool = br.length ? br : vs; return pool.find(v => M.test(v.name) && !F.test(v.name)) || pool.find(v => !F.test(v.name)) || pool[0]; } catch (e) { return null; } }
function stopSpeak() { tryApi("/api/voice/stop", {}, {silent: true}); if (window.speechSynthesis) speechSynthesis.cancel(); }

/* ---------------------------------------------------------------- ajuda de atalhos */
function plusMenu() {
  const b = $("#btn-plus");
  menu(b, [{group: "Atalhos"}, {value: "msg", label: "Enviar mensagem  #destino &texto&", icon: "send"}, {value: "card", label: "Novo card", icon: "board"}, {value: "list", label: "Nova lista", icon: "list"},
    {value: "rule", label: "Nova automação", icon: "tree"}, {value: "calc", label: "Calcular", icon: "calc"}, {hr: 1}, {value: "file", label: "Anexar texto de arquivo…", icon: "plus"}, {value: "help", label: "Como escrever pedidos", icon: "spark"}], v => {
    if (v === "msg") { setInput("#ana &&"); const i = inp(); i.setSelectionRange(6, 6); }
    if (v === "card") newCard();
    if (v === "list") setInput("Lista de compras: ");
    if (v === "rule") setInput("Quando chegar ");
    if (v === "calc") setInput("Quanto é ");
    if (v === "help") helpPop(b);
    if (v === "file") { const f = document.createElement("input"); f.type = "file"; f.accept = ".txt,.md,.eml,.csv"; f.onchange = async () => { const x = f.files[0]; if (!x) return; if (x.size > 200000) return toast("Arquivo grande demais para colar (máx. 200 KB).", "warn");
      const t = await x.text(); setMode("mensagem"); setInput(t.slice(0, 20000)); $("#in-subj").value = x.name; }; f.click(); }
  });
}
function helpPop(anchor) {
  const m = modal({title: "Como escrever pedidos", cls: "sm", body: `<div style="padding:0 24px 20px" class="help">
    <p><b>Mensagem rápida:</b> <code>#destino &amp;texto&amp;</code><br><span class="hint">ex.: <code>#ana &amp;chego em 10 min&amp;</code> — o Djorge acha a Ana e propõe o envio, sempre pedindo sua confirmação.</span></p>
    <p><b>Datas e horas</b> em português: “amanhã às 15h”, “sexta de manhã”, “dia 10”, “toda segunda”.</p>
    <p><b>Listas:</b> “lista de compras: pão, café, leite”.</p>
    <p><b>Automações:</b> “quando chegar boleto, me avise”.</p>
    <p><b>Interface:</b> “deixa escuro”, “esconde a coluna feito”, “salva filtro urgentes desta semana”.</p>
    <p><b>Modos:</b> Automático decide sozinho; Pedido força um comando; Mensagem recebida faz a triagem.</p>
    <p class="hint"><kbd>Ctrl K</kbd> busca · <kbd>Ctrl N</kbd> novo card · <kbd>Ctrl ,</kbd> configurações · <kbd>/</kbd> foca aqui</p></div>`});
  return m;
}

/* ---------------------------------------------------------------- estado / notificações */
let lastSec = null;
async function pollStatus() {
  try { const s = await api("/api/status"); const prevPend = S.status ? S.status.pending : 0; S.status = s;
    if (s.pending > prevPend && prevPend !== undefined && lastSec !== null) toast(`${s.pending} ação(ões) aguardando sua confirmação.`, "warn", {action: "Ver", onAction: () => openSettings("avancado")});
    const sec = s.security || {}; const alerts = sec.alerts || sec.last_alerts || [];
    if (Array.isArray(alerts)) alerts.forEach(a => { const k = a.id || a.t || JSON.stringify(a); if (lastSec && !lastSec.has(k)) toast((a.text || a.title || "").slice(0, 120), "bad", {title: `Alerta de segurança: ${a.label || a.kind || "suspeito"}`}); (lastSec = lastSec || new Set()).add(k); });
    if (!lastSec) lastSec = new Set();
    const nu = s.notificacoes_nao_lidas ?? s.notif_unread ?? (s.notificacoes && s.notificacoes.nao_lidas); if (typeof nu === "number") S.notifUnread = nu;
    renderNav(); renderModelChip();
  } catch (e) { S.status = null; renderModelChip(); }
}

/* ---------------------------------------------------------------- teclado & init */
function toggleSide() {
  const a = $("#app");
  if (innerWidth <= 1040) { a.classList.toggle("expanded"); return; }
  a.classList.toggle("collapsed"); store.set("collapsed", a.classList.contains("collapsed"));
  $("#collapse").setAttribute("aria-label", a.classList.contains("collapsed") ? "Expandir barra lateral" : "Recolher barra lateral");
}
document.addEventListener("keydown", e => {
  const t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") { e.preventDefault(); newCard(S.view === "quadro" ? "entrada" : "hoje"); }
  else if ((e.ctrlKey || e.metaKey) && e.key === ",") { e.preventDefault(); openSettings(); }
  else if (e.key === "/" && !typing && !modalStack.length && !FULL) { e.preventDefault(); inp().focus(); }
  else if (!typing && !modalStack.length && !FULL && SEL.size && (e.key === "Delete" || e.key === "Backspace")) { e.preventDefault(); bulkDelete([...SEL]); }
  else if (!typing && !modalStack.length && !FULL && e.key === "Escape" && (SEL.size || NSEL.size)) { SEL.clear(); NSEL.clear(); paintSel(); paintNSel(); }
  else if (!typing && !modalStack.length && !FULL && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && ["quadro", "tarefas"].includes(S.view)) { e.preventDefault(); $$("[data-id].kc,[data-id].trow", viewEl()).forEach(x => SEL.add(x.dataset.id)); paintSel(); }
  else if (!typing && !modalStack.length && !FULL && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d" && SEL.size === 1) { e.preventDefault(); dupCard([...SEL][0]); }
  else if (!typing && !modalStack.length && !FULL && e.key === "ContextMenu") { const f = document.activeElement; if (f && f.dataset && f.dataset.id && f.closest("#view")) { const r = f.getBoundingClientRect(); e.preventDefault(); cardCtx({preventDefault() {}, target: f, clientX: r.left + 20, clientY: r.top + 20}, f.dataset.id); } }
  else if (e.key === "Escape" && rec) stopRec(false);
  else if (e.key === "Escape" && modalStack.length && !openMenu && !modalStack[modalStack.length - 1].el.contains(t)) modalStack[modalStack.length - 1].close();
});
async function init() {
  if (store.get("collapsed", false)) $("#app").classList.add("collapsed");
  $("#collapse").onclick = toggleSide;
  $("#btn-new").onclick = () => { if (S.view === "inicio") { S.thread = []; S.showPanel = false; render(); inp().focus(); } else newCard(); };
  $("#btn-search").onclick = () => openPalette();
  $("#btn-set").onclick = () => openSettings();
  $("#btn-int").onclick = () => openIntegrations();
  $("#mchip").onclick = openModel;
  const me = $("#me"); me.tabIndex = 0; me.setAttribute("role", "button"); me.setAttribute("aria-haspopup", "menu"); me.setAttribute("aria-label", "Perfil e desempenho");
  const meMenu = async () => { const r = S.rec || await loadRec();
    menu(me, [{group: r ? `Djorge · ${r.pausado ? "pausado" : "rodando"}${r.memoria_mb != null ? " · " + mb(r.memoria_mb) : ""}` : "Djorge"},
      r && r.pausado ? {value: "retomar", label: "Retomar Djorge", icon: "up"} : {value: "pausar", label: "Pausar Djorge", icon: "clock"},
      {value: "liberar_memoria", label: "Liberar memória", icon: "sync"}, {value: "leve", label: "Desempenho e Windows…", icon: "spark"}, {hr: 1},
      {value: "sec", label: "Secretário…", icon: "spark"}, {value: "int", label: "Integrações", icon: "plug"}, {value: "set", label: "Configurações", icon: "gear", kbd: "Ctrl ,"}], async v => {
      if (v === "pausar" || v === "retomar" || v === "liberar_memoria") appAction(v);
      if (v === "leve") openSettings("leve"); if (v === "sec") secretaryWizard(); if (v === "int") openIntegrations(); if (v === "set") openSettings(); }); };
  me.onclick = meMenu; me.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); meMenu(); } };
  $("#btn-plus").onclick = plusMenu;
  $("#btn-mic").onclick = toggleMic;
  $("#rec-stop").onclick = () => stopRec(true); $("#rec-cancel").onclick = () => stopRec(false);
  $("#btn-mode").onclick = e => menu(e.currentTarget, [["auto", "Automático", "spark"], ["pedido", "Pedido", "check"], ["mensagem", "Mensagem recebida", "msg"]].map(([v, l, i]) => ({value: v, label: l, icon: i, checked: S.mode === v})), setMode, {role: "listbox", label: "Modo"});
  const i = inp();
  i.addEventListener("input", () => { autoGrow(); renderSuggest(); });
  i.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  $("#composer").addEventListener("submit", e => { e.preventDefault(); submit(); });
  window.addEventListener("hashchange", () => go(location.hash.slice(1), false));
  renderRecents(); renderModelChip();
  await ensureToken();
  let prefs = {};
  try { prefs = await api("/api/ui"); } catch (e) {}
  S.prefs = normPrefs(prefs); applyPrefs(S.prefs);
  const h = location.hash.slice(1);
  S.view = VIEW_ALIAS[h] || (VIEWS.find(v => v.id === h) ? h : S.prefs.default_view);
  if (!VIEWS.find(v => v.id === S.view)) S.view = "inicio";
  render();
  await Promise.all([loadCards(true), pollModel(), pollStatus(), loadInts(), loadRec()]);
  render();
  if (!modelReady() && !store.get("welcomed", false) && S.model.state !== "baixando") { store.set("welcomed", true); setTimeout(openModel, 500); }
  setInterval(pollStatus, 5000);
  setInterval(() => { if (!document.hidden && !drawerCard && !dragId && !(document.activeElement && /INPUT|TEXTAREA/.test(document.activeElement.tagName))) refreshCards(); }, 30000);
  recorderLoaded = await loadScript("/static/app/recorder.js");
  await loadVoice(); syncMic();
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
