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
  if (e.status === 503) return "Essa parte do app não está disponível agora" + (m ? `: ${m}` : ".");
  if (e.status >= 500) return "O Djorge encontrou um problema interno. Tente de novo em instantes.";
  return m || "Algo deu errado.";
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
  {id: "mensagens", label: "Mensagens", icon: "msg"}, {id: "automacoes", label: "Automações", icon: "tree"}];
const VIEW_ALIAS = {kanban: "quadro", lista: "tarefas", regras: "automacoes", "início": "inicio", home: "inicio", board: "quadro", list: "tarefas"};
const COLS = [{id: "entrada", label: "Entrada", c: "var(--ink3)"}, {id: "hoje", label: "Hoje", c: "var(--rose)"},
  {id: "fazendo", label: "Fazendo", c: "var(--info)"}, {id: "aguardando", label: "Aguardando", c: "var(--warn)"}, {id: "feito", label: "Feito", c: "var(--ok)"}];
const KINDS = {tarefa: "Tarefa", evento: "Evento", lembrete: "Lembrete", nota: "Nota", lista: "Lista", mensagem: "Mensagem"};
const KIND_ICO = {tarefa: "check", evento: "cal", lembrete: "bell", nota: "edit", lista: "list", mensagem: "msg"};
const PRI = ["Sem prioridade", "Baixa", "Média", "Alta"];
const CARD_COLORS = [["", "Nenhuma"], ["#b56576", "Rosa"], ["#c0714f", "Terracota"], ["#b7832f", "Mel"], ["#6f8f7a", "Sálvia"], ["#4f7d96", "Oceano"], ["#7d5a86", "Ameixa"]];
let ACCENTS = [["rosa", "#b56576"], ["vinho", "#6d2e46"], ["terracota", "#c06c4a"], ["mostarda", "#c9a227"], ["verde", "#5a7d5a"], ["azul", "#4a6c8c"], ["lavanda", "#8a7bb0"], ["grafite", "#4a4a4a"]];
const TOOL_PT = {calcular: ["Calcular", "calc"], datas_horas: ["Datas e horas", "clock"], proximas_tarefas: ["Próximas tarefas", "list"],
  cards: ["Card", "board"], listas: ["Lista", "list"], bilhete: ["Mensagem", "send"], buscar: ["Buscar", "search"],
  regra: ["Automação", "tree"], interface: ["Interface", "gear"], nenhuma: ["Fora do escopo", "spark"], triagem: ["Triagem", "shield"]};
const FIELD_CLS = {data: "c-data", hora: "c-hora", "período do dia": "c-periodo", pessoa: "c-pessoa", "endereço de e-mail": "c-email",
  lugar: "c-lugar", "empresa ou estabelecimento": "c-empresa", endereco: "c-endereco"};
const FIELD_EMO = {data: "📅", hora: "🕒", "período do dia": "🌤", pessoa: "👤", "evento ou compromisso": "📌", lugar: "📍", lista: "🗒",
  "endereço de e-mail": "✉", "empresa ou estabelecimento": "🏢", "frequência ou repetição": "🔁"};
const SRC_EMO = {google: "🟦", microsoft: "🟪", whatsapp: "🟩", gemini: "✦", local: "🏠"};
const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const WD = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
const PHRASES = ["lendo seu pedido", "separando as palavras", "achando a data", "procurando pessoas e lugares", "escolhendo a ferramenta",
  "conferindo a certeza", "montando a proposta", "arrumando as pétalas"];
const SUGGEST = {
  auto: ["Reunião com Ana amanhã às 15h", "O que tenho para hoje?", "Quanto é 18% de 2.350?", "#ana &chego em 10 minutos&", "Deixa o tema escuro"],
  pedido: ["Lembrar de pagar a luz dia 10", "Lista de compras: pão, café, leite", "Toda segunda às 9h revisar e-mails"],
  mensagem: ["Cole aqui uma mensagem recebida para triar"]};

/* ---------------------------------------------------------------- estado */
const S = {
  view: "inicio", cards: [], cardsLoaded: false, prefs: {}, model: {state: "ausente"}, voice: null, ints: null,
  filters: store.get("filters", {}), sort: store.get("sort", "manual"), q: "",
  thread: [], mode: "auto", agMode: store.get("agMode", "semana"), stripMode: store.get("stripMode", "dia"),
  anchor: new Date(), recents: store.get("recents", []), status: null, pendingSeen: new Set(), trees: null
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
    `<button type="button" role="${ir}" data-i="${i}" tabindex="-1" ${ir === "option" ? `aria-selected="${!!it.checked}"` : it.checked !== undefined ? `aria-checked="${!!it.checked}"` : ""}>${it.icon ? ico(it.icon, "sm") : ""}<span>${esc(it.label)}</span></button>`).join("");
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect(), w = Math.max(el.offsetWidth, opt.match ? r.width : 0);
  el.style.minWidth = w + "px";
  let top = r.bottom + 6; if (top + el.offsetHeight > innerHeight - 8) top = Math.max(8, r.top - el.offsetHeight - 6);
  el.style.top = top + "px"; el.style.left = Math.min(r.left, innerWidth - w - 8) + "px";
  anchor.setAttribute("aria-expanded", "true");
  openMenu = {el, anchor};
  const btns = $$("button", el); let ai = Math.max(0, btns.findIndex(b => b.getAttribute("aria-selected") === "true"));
  const act = i => { btns.forEach(b => b.classList.remove("act")); ai = (i + btns.length) % btns.length; btns[ai] && (btns[ai].classList.add("act"), btns[ai].focus()); };
  if (btns.length) act(ai);
  el.addEventListener("click", e => { const b = e.target.closest("button[data-i]"); if (!b) return; const it = items[+b.dataset.i];
    if (opt.multi) { it.checked = !it.checked; b.setAttribute("aria-checked", it.checked); onPick(it.value, it); return; }
    closeMenu(); onPick(it.value, it); });
  el.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); act(ai + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); act(ai - 1); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); closeMenu(); }
    else if (e.key === "Home") { e.preventDefault(); act(0); } else if (e.key === "End") { e.preventDefault(); act(btns.length - 1); }
  });
}
document.addEventListener("mousedown", e => { if (openMenu && !openMenu.el.contains(e.target) && !openMenu.anchor.contains(e.target)) closeMenu(); });
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
  $("#nav").innerHTML = vs.map(v => `<button class="navitem" data-v="${v.id}" ${S.view === v.id ? 'aria-current="page"' : ""} data-tip="${v.label}" data-tip-pos="right">${ico(v.icon)}<span>${v.label}</span>${v.id === "mensagens" && pend ? `<span class="badge" aria-label="${pend} pendências">${pend}</span>` : ""}</button>`).join("");
  $$("#nav .navitem").forEach(b => b.onclick = () => go(b.dataset.v));
}
function renderRecents() {
  const r = S.recents.slice(0, 12);
  $("#recents").innerHTML = r.length ? r.map((x, i) => `<button class="recent" data-i="${i}" title="${esc(x)}">${esc(x)}</button>`).join("") :
    `<p class="recent" style="cursor:default;opacity:.7">Seus pedidos aparecem aqui.</p>`;
  $$("#recents button").forEach(b => b.onclick = () => { setInput(r[+b.dataset.i]); });
}
function pushRecent(t) { S.recents = [t, ...S.recents.filter(x => x !== t)].slice(0, 20); store.set("recents", S.recents); renderRecents(); }
function go(v, push = true) {
  v = VIEW_ALIAS[v] || v; if (!VIEWS.find(x => x.id === v)) v = "inicio";
  S.view = v; if (push) history.replaceState(null, "", "#" + v);
  renderNav(); render(); $("#view").scrollTop = 0;
  if ($("#app").classList.contains("expanded")) $("#app").classList.remove("expanded");
}
function render() {
  const fn = {inicio: viewHome, quadro: viewBoard, agenda: viewAgenda, tarefas: viewTasks, mensagens: viewMessages, automacoes: viewAutos}[S.view];
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
async function createCard(data) {
  try { const r = await api("/api/cards", data); if (r && r.id) S.cards.push(r); toast("Card criado", "", {action: "Abrir", onAction: () => openCard(r.id)}); return r; }
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
  {k: "source", label: "Fonte", icon: "plug", opts: () => [["local", "Djorge (local)"], ["google", "Google"], ["microsoft", "Microsoft"], ["whatsapp", "WhatsApp"]]},
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
  return `<article class="kc ${c.done ? "done" : ""}" draggable="true" data-id="${esc(c.id)}" ${c.color ? `data-color style="--cc:${esc(c.color)}"` : ""} tabindex="0" aria-label="${esc(c.title)}" aria-roledescription="card arrastável">
    ${c.pinned ? `<span class="pin" aria-label="Fixado">${ico("pin", "sm")}</span>` : ""}
    <div class="kt" data-edit>${esc(c.title || "Sem título")}</div>
    ${c.body ? `<div class="kb">${esc(c.body)}</div>` : ""}
    <div class="km">
      <span class="tag">${ico(KIND_ICO[c.kind] || "check", "sm")} ${esc(KINDS[c.kind] || c.kind || "")}</span>
      ${cardDate(c) ? `<span class="when ${isLate(c) ? "late" : ""}">${ico("clock", "sm")}${esc(whenTxt(c))}</span>` : ""}
      ${c.priority ? `<span class="pri p${c.priority}"><i></i>${esc(PRI[c.priority])}</span>` : ""}
      ${ck.length ? `<span class="chk">☑ ${dn}/${ck.length}</span>` : ""}
      ${(c.tags || []).slice(0, 3).map(t => `<span class="tag">#${esc(t)}</span>`).join("")}
      ${src ? `<span class="src" title="${esc(src)}">${SRC_EMO[src] || "•"}</span>` : ""}
      ${(c.people || []).length ? `<span class="ppl" style="margin-left:auto">${c.people.slice(0, 3).map(p => `<i title="${esc(p.nome || p.email)}">${esc(initials(p.nome || p.email))}</i>`).join("")}</span>` : ""}
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
    el.addEventListener("click", e => { if (e.target.closest("[data-edit]") && el.classList.contains("kc") && e.detail === 2) return; if (e.target.closest(".cbx")) return; openCard(id); });
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
function viewHome() {
  const v = viewEl(), now = new Date();
  const today = S.cards.filter(c => !c.done && ((cardDate(c) && sameDay(cardDate(c), now)) || c.column === "hoje"));
  const late = S.cards.filter(isLate), next = S.cards.filter(c => { const d = cardDate(c); return d && d > now && d < addDays(now, 7) && !c.done; });
  const pend = S.status ? S.status.pending : 0;
  const noModel = !modelReady() && !store.get("inviteDismissed", false) && S.model.state !== "baixando";
  if (!S.thread.length) {
    v.innerHTML = `<div class="view-inner narrow">
      <div class="hero"><img src="/static/icon.svg" alt=""><h1>${greet()}, <em>o que vamos organizar?</em></h1>
      <p>${now.toLocaleDateString("pt-BR", {weekday: "long", day: "numeric", month: "long"})} · peça em português, do seu jeito.</p></div>
      ${noModel ? inviteHTML() : ""}
      <div class="tiles">
        <button class="tile" data-go="quadro" data-period="hoje"><div class="n">${today.length}</div><div class="l">para hoje</div></button>
        <button class="tile" data-go="tarefas" data-period="atrasados"><div class="n" style="${late.length ? "color:var(--bad)" : ""}">${late.length}</div><div class="l">atrasados</div></button>
        <button class="tile" data-go="agenda"><div class="n">${next.length}</div><div class="l">nos próximos 7 dias</div></button>
        <button class="tile" data-pend><div class="n">${pend}</div><div class="l">aguardando sua confirmação</div></button>
      </div>
      ${today.length ? `<div class="group"><h3>Hoje</h3><div class="tlist">${today.slice(0, 6).map(trowHTML).join("")}</div></div>` : ""}
    </div>`;
    $$("[data-go]", v).forEach(b => b.onclick = () => { if (b.dataset.period) { S.filters = {period: b.dataset.period}; store.set("filters", S.filters); } go(b.dataset.go); });
    const pb = $("[data-pend]", v); if (pb) pb.onclick = () => openSettings("avancado");
    wireInvite(v); wireCards(v);
  } else {
    v.innerHTML = `<div class="view-inner narrow"><div class="vh"><div><span class="kicker">Conversa</span><h1>Seus <em>pedidos</em></h1></div><span class="sp"></span><button class="btn sm ghost" id="clr">Limpar conversa</button></div>
      ${noModel ? inviteHTML() : ""}<div class="thread" id="thread"></div></div>`;
    $("#clr", v).onclick = () => { S.thread = []; render(); };
    wireInvite(v);
    const th = $("#thread", v); S.thread.forEach(it => th.appendChild(it.el));
    requestAnimationFrame(() => v.scrollTop = v.scrollHeight);
  }
}
function trowHTML(c) {
  return `<div class="trow ${c.done ? "done" : ""}" data-id="${esc(c.id)}" tabindex="0" role="button" aria-label="${esc(c.title)}">
    <button type="button" class="cbx" role="checkbox" aria-checked="${!!c.done}" data-id="${esc(c.id)}" aria-label="Concluir">${ico("check")}</button>
    <div class="tt"><b>${esc(c.title)}</b><small>${esc(KINDS[c.kind] || "")}${cardDate(c) ? " · " + esc(whenTxt(c)) : ""}${(c.tags || []).length ? " · " + c.tags.map(t => "#" + esc(t)).join(" ") : ""}</small></div>
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
  $$("[data-colmenu]", v).forEach(b => b.onclick = () => menu(b, [{value: "new", label: "Novo card aqui", icon: "plus"}, {value: "hide", label: "Esconder coluna", icon: "x"},
    {hr: 1}, {group: "Colunas visíveis"}, ...COLS.map(c => ({value: "t:" + c.id, label: c.label, checked: (S.prefs.columns || []).includes(c.id)}))], val => {
      if (val === "new") newCard(b.dataset.colmenu);
      else if (val === "hide") savePrefs({columns: (S.prefs.columns || []).filter(x => x !== b.dataset.colmenu)});
      else if (val.startsWith("t:")) { const id = val.slice(2), cur = new Set(S.prefs.columns || []); cur.has(id) ? cur.delete(id) : cur.add(id); savePrefs({columns: COLS.map(c => c.id).filter(x => cur.has(x))}); }
    }));
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
async function viewMessages() {
  const v = viewEl(), msgs = filtered(S.cards.filter(c => c.kind === "mensagem"), {done: S.filters.done}, "");
  v.innerHTML = `<div class="view-inner narrow" style="max-width:920px"><div class="vh"><div><span class="kicker">Mensagens</span><h1>Triagem e <em>envios</em></h1></div><span class="sp"></span>
    <button class="btn sm" id="tri">${ico("shield", "sm")} Triar mensagem recebida</button><button class="btn sm primary" id="wr">${ico("send", "sm")} Escrever</button></div>
    <div id="pend"></div>
    <section class="group"><h3>Mensagens em cards <span>${msgs.length}</span></h3>${msgs.length ? `<div class="tlist">${msgs.map(trowHTML).join("")}</div>` :
      empty("Sem mensagens ainda", "Mensagens importadas das integrações e as que você pedir para enviar aparecem aqui.")}</section></div>`;
  wireCards(v);
  $("#tri", v).onclick = () => { setMode("mensagem"); $("#in").focus(); };
  $("#wr", v).onclick = () => { setInput("#"); };
  try { const p = await api("/api/pending"); const list = Array.isArray(p) ? p : (p && p.pending) || [];
    if (list.length && S.view === "mensagens") { $("#pend", v).innerHTML = `<section class="group"><h3>Aguardando sua confirmação <span>${list.length}</span></h3><div class="tlist">${list.map(pendHTML).join("")}</div></section>`; wirePend($("#pend", v)); } }
  catch (e) {}
}
const pendHTML = p => `<div class="trow" style="cursor:default"><span class="pri p2"><i></i></span><div class="tt"><b>${esc((p.spec && (p.spec.action || p.spec.kind)) || "ação")} · ${esc(p.tree || "")}</b><small>${esc(JSON.stringify(p.spec || {}).slice(0, 140))}</small></div>
  <button class="btn sm" data-pok="${esc(p.id)}" data-a="0">Recusar</button><button class="btn sm primary" data-pok="${esc(p.id)}" data-a="1">Aprovar</button></div>`;
function wirePend(root, after) { $$("[data-pok]", root).forEach(b => b.onclick = async () => { const r = await tryApi("/api/pending/" + b.dataset.pok, {approve: b.dataset.a === "1"});
  if (r) toast(b.dataset.a === "1" ? "Aprovado" : "Recusado"); pollStatus(); after ? after() : render(); }); }

/* ---------------------------------------------------------------- AUTOMAÇÕES */
let treeEditorLoaded = null;
function loadScript(src) { return new Promise(res => { if ($(`script[src="${src}"]`)) return res(true); const s = document.createElement("script"); s.src = src; s.onload = () => res(true); s.onerror = () => { s.remove(); res(false); }; document.head.appendChild(s); }); }
async function viewAutos(openGraph) {
  const v = viewEl();
  v.innerHTML = `<div class="view-inner"><div class="vh"><div><span class="kicker">Automações</span><h1>Árvores de <em>decisão</em></h1><p>Cada resposta escolhe o próximo passo. Descreva uma regra no campo abaixo (“quando chegar boleto, avise”) ou desenhe aqui.</p></div><span class="sp"></span>
    <button class="btn sm" id="yaml">Ver YAML</button><button class="btn sm primary" id="ntree">${ico("plus", "sm")} Nova árvore</button></div>
    <div class="autos"><div class="tlistb" id="tl"><div class="sk"></div><div class="sk"></div></div><div class="editor" id="ted"><div class="sk" style="height:100%;min-height:500px;border-radius:0"></div></div></div></div>`;
  $("#yaml", v).onclick = () => yamlEditor($("#ted", v));
  if (treeEditorLoaded === null) treeEditorLoaded = await loadScript("/static/tree-editor.js") && !!window.TreeEditor;
  let graphs = null;
  try { const r = await api("/api/trees/graph"); graphs = (r && (r.trees || r.graphs)) || (Array.isArray(r) ? r : null); } catch (e) { graphs = null; }
  if (S.view !== "automacoes") return;
  const tl = $("#tl", v), ted = $("#ted", v);
  if (!treeEditorLoaded || !graphs) {
    let names = (S.status && S.status.trees) || [];
    tl.innerHTML = names.length ? names.map(n => `<button type="button"><b>${esc(n)}</b><small>árvore</small></button>`).join("") : empty("Nenhuma árvore", "Crie a primeira.");
    $("#ntree", v).onclick = () => { setInput("Quando chegar "); };
    yamlEditor(ted, !treeEditorLoaded ? "O editor visual ainda não está disponível; edite o texto da árvore." : "");
    if (openGraph) toast("Árvore proposta: ajuste no texto e salve.");
    return;
  }
  S.trees = graphs;
  let cur = openGraph ? -1 : 0;
  const list = () => { tl.innerHTML = (openGraph ? [{name: (openGraph.name || "nova árvore") + " (proposta)", _new: 1}] : []).concat(graphs).map((g, i) => `<button type="button" data-i="${i}" aria-current="${i === (openGraph ? cur + 1 : cur)}"><b>${esc(g.name || g.title || "árvore " + (i + 1))}</b><small>${(g.graph || g).nodes ? (g.graph || g).nodes.length + " nós" : ""}</small></button>`).join("") || empty("Nenhuma árvore", "Crie a primeira.");
    $$("button", tl).forEach(b => b.onclick = () => { cur = +b.dataset.i - (openGraph ? 1 : 0); list(); mount(cur < 0 ? openGraph : graphs[cur]); }); };
  let dirty = null;
  const mount = async g => { ted.innerHTML = ""; dirty = null;
    let graph = g.graph || g;
    if ((!graph.nodes || !graph.nodes.length) && g.tree && window.TreeEditor.fromTree) { try { graph = await window.TreeEditor.fromTree(g.tree); } catch (e) { toast(human(e), "bad"); } }
    const bar = document.createElement("div"); bar.style.cssText = "position:absolute;right:12px;bottom:12px;z-index:5;display:flex;gap:8px";
    bar.innerHTML = `<span class="chip" hidden data-dirty>alterações não salvas</span><button class="btn sm primary" data-save>${ico("check", "sm")} Salvar árvore</button>`;
    const host = document.createElement("div"); host.style.cssText = "position:absolute;inset:0"; ted.append(host, bar);
    try { const ed = window.TreeEditor.mount(host, {graph, onChange: gr => { dirty = gr; $("[data-dirty]", bar).hidden = false; }});
      $("[data-save]", bar).onclick = async e => { const b = e.currentTarget; b.setAttribute("aria-busy", "true");
        try { await api("/api/trees/graph", {graph: dirty || (ed.getGraph ? ed.getGraph() : graph)}); dirty = null; $("[data-dirty]", bar).hidden = true; toast("Árvore salva"); pollStatus(); }
        catch (er) { toast(er.data && er.data.errors ? "A árvore tem erros: " + er.data.errors.slice(0, 2).map(x => x.msg || x.message || x.text || JSON.stringify(x)).join("; ") : human(er), "bad"); }
        b.removeAttribute("aria-busy"); }; }
    catch (e) { console.warn(e); yamlEditor(ted, "Não consegui abrir o editor visual."); } };
  list();
  if (openGraph) mount(openGraph); else if (graphs.length) mount(graphs[0]); else ted.innerHTML = empty("Comece uma automação", "Clique em “Nova árvore” ou descreva a regra no campo abaixo.");
  $("#ntree", v).onclick = () => { openGraph = {name: "nova árvore", nodes: [], edges: []}; cur = -1; list(); mount(openGraph); };
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
  el.innerHTML = `<div class="bloom" aria-hidden="true">${petals}<b></b></div><div class="ph"><span>${PHRASES[0]}</span><span class="dots"></span></div>`;
  let k = 0; const t = setInterval(() => { k = (k + 1) % PHRASES.length; const s = el.querySelector(".ph span"); if (s) { s.textContent = PHRASES[k]; s.style.animation = "none"; void s.offsetWidth; s.style.animation = ""; } }, 1100);
  el._stop = () => clearInterval(t);
  return el;
}
function pushThread(el) {
  const it = {el}; S.thread.push(it);
  if (S.view !== "inicio") go("inicio"); else { const th = $("#thread"); if (th) th.appendChild(el); else render(); }
  requestAnimationFrame(() => { const v = viewEl(); v.scrollTop = v.scrollHeight; });
  return it;
}
async function submit() {
  const text = inp().value.trim(); if (!text) return;
  inp().value = ""; autoGrow(); pushRecent(text);
  const mode = S.mode, from = $("#in-from").value.trim(), subj = $("#in-subj").value.trim();
  const ub = document.createElement("div"); ub.className = "ubub"; ub.innerHTML = `<small>${mode === "mensagem" ? "mensagem recebida" + (from ? " · " + esc(from) : "") : "você"}</small>${esc(text)}`;
  pushThread(ub);
  const th = thinkingEl(); const thIt = pushThread(th);
  const t0 = performance.now();
  let res, err;
  try {
    if (mode === "mensagem") res = await api("/api/triage", {text, remetente: from, assunto: subj}, {quiet409: true});
    else res = await api("/api/command", {text, context: {view: S.view, mode, selected_card: drawerCard && drawerCard.id}}, {quiet409: true});
  } catch (e) { err = e; }
  const wait = Math.max(0, (REDUCED ? 0 : 650) - (performance.now() - t0)); if (wait) await sleep(wait);
  th._stop(); th.remove(); S.thread.splice(S.thread.indexOf(thIt), 1);
  if (mode === "mensagem") { $("#in-from").value = ""; $("#in-subj").value = ""; }
  if (err) { const el = document.createElement("div"); el.className = "rcard";
    if (err.status === 409 && err.data.error === "modelo_ausente") { el.className = ""; el.innerHTML = inviteHTML(true); wireInvite(el); }
    else el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti" style="background:var(--bad-soft);color:var(--bad)">${ico("x")}</span>Não deu certo</div></div><p class="reply">${esc(human(err))}</p><div class="ract"><button class="btn sm" data-retry>Tentar de novo</button></div>`;
    const rb = $("[data-retry]", el); if (rb) rb.onclick = () => setInput(text);
    pushThread(el); return; }
  const el = mode === "mensagem" ? triageCard(res, text) : resultCard(res, text);
  pushThread(el);
  const said = res && (res.reply || (res.resumo));
  if (said && S.prefs.speak) speak(said);
}

/* ----- cartão de resultado ----- */
function certHTML(p) { const v = Math.max(0, Math.min(1, +p || 0)); return `<span class="cert ${v < .6 ? "low" : ""}" title="certeza da escolha">certeza <span class="bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(v * 100)}" aria-label="Certeza"><i data-w="${Math.round(v * 100)}%"></i></span>${Math.round(v * 100)}%</span>`; }
function animateBars(el) { requestAnimationFrame(() => requestAnimationFrame(() => $$(".cert .bar i", el).forEach(i => i.style.width = i.dataset.w))); }
function fieldChip(f) {
  const cls = FIELD_CLS[f.campo] || (f.tipo && FIELD_CLS[f.tipo]) || "c-outro", isModel = /modelo|model|dofl/i.test(f.fonte || "");
  const val = f.valor && f.valor !== f.texto ? (typeof f.valor === "object" ? JSON.stringify(f.valor) : String(f.valor)) : "";
  return `<span class="fchip ${cls}" title="${esc(f.campo)} · ${isModel ? "achado pelo modelo" : "achado pelas regras"}${f.p != null ? " · " + Math.round(f.p * 100) + "%" : ""}">
    <span class="src" aria-label="${isModel ? "modelo" : "regra"}">${isModel ? "🧠" : "📐"}</span><span>${FIELD_EMO[f.campo] || ""}</span><b>${esc(f.texto || f.valor)}</b>${val ? `<small>→ ${esc(val)}</small>` : ""}<small>${esc(f.campo)}</small></span>`;
}
function resultCard(r, text) {
  r = r || {};
  const el = document.createElement("div"); el.className = "rcard"; el.setAttribute("aria-label", "Resultado do pedido");
  const tool = r.tool || "nenhuma", [tl, ti] = TOOL_PT[tool] || [tool, "spark"], p = r.proposal || {action: "none"};
  const alts = (r.tools || []).filter(t => t.tool !== tool).slice(0, 3);
  const az = r.authorization;
  el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti">${ico(ti)}</span>${esc(tl)}</div>${certHTML(r.tool_p)}</div>
    ${alts.length ? `<div class="alts" aria-label="Outras possibilidades">${alts.map(a => `<button type="button" data-alt="${esc(a.tool)}" title="Refazer como ${esc((TOOL_PT[a.tool] || [a.tool])[0])}">${esc((TOOL_PT[a.tool] || [a.tool])[0])} ${Math.round((a.p || 0) * 100)}%</button>`).join("")}</div>` : ""}
    ${r.reply ? `<p class="reply">${esc(r.reply)}</p>` : ""}
    ${r.warning || r.aviso ? `<p class="hint">⚠ ${esc(r.warning || r.aviso)}</p>` : ""}
    ${(r.fields || []).length ? `<div class="fields" aria-label="Campos encontrados">${r.fields.map(fieldChip).join("")}</div>` : ""}
    <div class="propbox"></div>
    ${az && az.decisao ? `<div class="authz ${/permit|ok|liber|autoriz/i.test(az.decisao) && !/confirm/i.test(az.decisao) ? "ok" : /neg|bloq|deny/i.test(az.decisao) ? "bad" : ""}">${ico("shield", "sm")}<span><b>${esc(az.decisao)}</b>${(az.motivos || []).length ? " · " + esc(az.motivos.join(", ")) : ""}</span></div>` : ""}
    <div class="ract"></div>
    <p class="meta">${r.timing_ms != null ? `${Math.round(r.timing_ms)} ms · ` : ""}${r.kind || "pedido"}${r.atalho ? " · atalho #&" : ""}</p>`;
  const pb = $(".propbox", el), ract = $(".ract", el);
  const state = {edits: {}};
  renderProposal(pb, p, r, state);
  const confirmable = p.action && !["none", "search", "calc", "time", "show_agenda"].includes(p.action);
  if (p.action === "ui" && p.patch) { state.prevServer = {...(S.prefs._server || {})};
    api("/api/ui").then(u => { S.prefs = normPrefs(u); applyPrefs(S.prefs); if (S.view !== "inicio") render(); }).catch(() => {}); }
  if (r.executed) {
    ract.innerHTML = `<div class="rdone">${ico("check", "sm")} ${p.action === "ui" ? "Interface ajustada" : p.action === "update_card" ? "Card atualizado" : "Feito"}</div>
      ${(r.results || [])[0] && (r.results[0] || {}).id ? `<button class="btn sm" data-open>Abrir card</button>` : ""}<button class="btn sm ghost" data-undo>Desfazer</button>`;
    $$("input,textarea,.sel button", pb).forEach(x => x.disabled = true);
    loadCards(true).then(() => { if (S.view !== "inicio") render(); });
    const op = $("[data-open]", ract); if (op) op.onclick = () => openCard(r.results[0].id);
    $("[data-undo]", ract).onclick = async () => {
      if (p.action === "ui") { const back = {}; Object.keys(p.patch || {}).forEach(k => { if (state.prevServer[k] !== undefined) back[k] = state.prevServer[k]; });
        try { const u = await api("/api/ui", back); S.prefs = normPrefs({prefs: u.prefs, choices: S.choices}); applyPrefs(S.prefs); render(); } catch (e) { return toast(human(e), "bad"); } }
      else { const o = await tryApi("/api/command/confirm", {id: r.id, approve: false, edits: {undo: true}}); if (!o) return; await loadCards(true); }
      ract.innerHTML = `<div class="rdone no">${ico("x", "sm")} Desfeito</div>`; toast("Desfeito"); };
  } else if (confirmable) {
    ract.innerHTML = `<button class="btn primary" data-ok>${ico("check", "sm")} ${p.action === "send_message" ? "Enviar" : p.action === "ui" ? "Manter" : "Confirmar"}</button>
      <button class="btn" data-ed>${ico("edit", "sm")} Editar</button><button class="btn ghost" data-no>Cancelar</button>
      ${r.needs_confirm ? `<span class="note">nada acontece sem sua confirmação</span>` : ""}`;
    const done = (ok, msg) => { ract.innerHTML = `<div class="rdone ${ok ? "" : "no"}">${ico(ok ? "check" : "x", "sm")} ${esc(msg)}</div>`; $$("input,textarea,.sel button,.seg button", pb).forEach(x => x.disabled = true); };
    $("[data-ok]", ract).onclick = async e => { const b = e.currentTarget; b.setAttribute("aria-busy", "true");
      const edits = collectEdits(pb, p);
      try { const out = await api("/api/command/confirm", {id: r.id, approve: true, edits}, {strict: false});
        if (out && out.error) throw new ApiError(out.error, 400, out);
        afterConfirm(p, out, el, edits); done(true, confirmMsg(p, out)); }
      catch (er) { b.removeAttribute("aria-busy");
        if (er.data && er.data.error === "fora_da_janela_24h") { toast(human(er), "warn", {action: "Enviar por modelo", onAction: async () => { const o2 = await tryApi("/api/command/confirm", {id: r.id, approve: true, edits: {...edits, template: true}}); if (o2 && !o2.error) done(true, "Enviado por modelo aprovado"); }, ms: 10000}); }
        else toast(human(er), "bad"); } };
    $("[data-ed]", ract).onclick = () => { if (p.action === "create_tree") { openTree(p); return; } const f = $("input,textarea", pb); f && f.focus(); pb.classList.add("editing"); };
    $("[data-no]", ract).onclick = async () => {
      tryApi("/api/command/confirm", {id: r.id, approve: false}, {silent: true}); done(false, "Cancelado"); };
  }
  $$("[data-alt]", el).forEach(b => b.onclick = () => setInput(text));
  animateBars(el);
  return el;
}
function confirmMsg(p, out) { return {create_card: "Card criado", update_card: "Card atualizado", send_message: out && out.status === "aguardando confirmação" ? "Na fila de confirmação" : "Mensagem enviada", create_tree: "Automação salva", ui: "Interface ajustada"}[p.action] || "Feito"; }
async function afterConfirm(p, out, el) {
  if (p.action === "create_card" || p.action === "update_card") { await loadCards(true); const c = (out && (out.card || (out.id && out))); if (c && c.id) toast("Pronto", "", {action: "Abrir card", onAction: () => openCard(c.id)}); }
  if (p.action === "ui") { try { const u = await api("/api/ui"); S.prefs = normPrefs(u); applyPrefs(S.prefs); } catch (e) {} }
  if (p.action === "create_tree") pollStatus();
}
function inputRow(label, key, val, type = "text") { return `<div class="field"><label>${esc(label)}</label>${type === "area" ? `<textarea class="ta" data-k="${key}" rows="3">${esc(val || "")}</textarea>` : `<input class="inp" data-k="${key}" type="${type}" value="${esc(val ?? "")}">`}</div>`; }
function renderProposal(pb, p, r, state) {
  const a = p.action;
  if (a === "create_card" || a === "update_card") {
    const c = p.card || p.data || p;
    const sd = parseD(c.start || c.due);
    pb.innerHTML = `<div class="prop"><h4>${ico("board", "sm")} ${a === "create_card" ? "Card a criar" : "Atualizar card"}</h4>
      ${inputRow("Título", "title", c.title)}
      <div class="grid3"><div class="field"><span class="lbl">Tipo</span>${selHTML("pk", Object.entries(KINDS), c.kind || "tarefa", "Tipo")}</div>
      <div class="field"><span class="lbl">Coluna</span>${selHTML("pc", COLS.map(x => [x.id, x.label]), c.column || "entrada", "Coluna")}</div>
      <div class="field"><span class="lbl">Prioridade</span>${selHTML("pp", PRI.map((l, i) => [String(i), l]), String(c.priority || 0), "Prioridade")}</div></div>
      <div class="field"><span class="lbl">Quando</span><div class="dt"><input type="date" data-k="date" value="${sd ? ymd(sd) : ""}" aria-label="Data"><input type="time" data-k="time" value="${sd && (sd.getHours() || sd.getMinutes()) ? hm(sd) : ""}" aria-label="Hora">
        ${(c.people || []).length ? `<span class="row" style="gap:4px">${c.people.map(x => `<span class="chip">${esc(x.nome || x.email)}</span>`).join("")}</span>` : ""}${c.recurrence ? `<span class="chip">🔁 ${esc(c.recurrence)}</span>` : ""}</div></div>
      ${(c.checklist || []).length ? `<div class="field"><span class="lbl">Itens</span><div class="row">${c.checklist.map(x => `<span class="chip">☐ ${esc(x.text)}</span>`).join("")}</div></div>` : ""}</div>`;
    ["pk", "pc", "pp"].forEach(k => wireSel(pb, k, k === "pk" ? Object.entries(KINDS) : k === "pc" ? COLS.map(x => [x.id, x.label]) : PRI.map((l, i) => [String(i), l])));
  } else if (a === "send_message") {
    const ch = p.channel || p.integration || "whatsapp";
    pb.innerHTML = `<div class="prop"><h4>${ico("send", "sm")} Mensagem a enviar</h4>
      <div class="grid2"><div class="field"><span class="lbl">Canal</span>${selHTML("pch", [["whatsapp", "WhatsApp"], ["google", "Gmail"], ["microsoft", "Outlook"]], ch, "Canal")}</div>${inputRow("Para", "to", p.to || p.destino || "")}</div>
      ${p.subject !== undefined || ch !== "whatsapp" ? inputRow("Assunto", "subject", p.subject || "") : ""}
      ${inputRow("Texto", "body", p.body || p.corpo || r.corpo || "", "area")}</div>`;
    wireSel(pb, "pch", [["whatsapp", "WhatsApp"], ["google", "Gmail"], ["microsoft", "Outlook"]]);
  } else if (a === "calc" || a === "time") {
    const v = p.result ?? p.value ?? (r.results && r.results[0] && (r.results[0].value ?? r.results[0].result ?? r.results[0].text));
    pb.innerHTML = v != null ? `<div class="prop"><h4>${ico(a === "calc" ? "calc" : "clock", "sm")} Resultado</h4><div class="bigres">${esc(typeof v === "object" ? JSON.stringify(v) : v)}</div>${p.expr ? `<p class="hint mono">${esc(p.expr)}</p>` : ""}</div>` : "";
  } else if (a === "search" || a === "show_agenda") {
    const items = r.results || p.results || [];
    pb.innerHTML = `<div class="prop"><h4>${ico(a === "search" ? "search" : "cal", "sm")} ${a === "search" ? "Encontrado" : "Na agenda"} · ${items.length}</h4>${items.length ? `<div class="rlist">${items.slice(0, 8).map(resultItemHTML).join("")}</div>` : `<p class="hint">Nada encontrado.</p>`}</div>`;
    wireResultItems(pb);
  } else if (a === "create_tree") {
    const t = p.tree || {}, s = p.summary || p.resumo || (t.name ? `Árvore “${t.name}”` : "Nova automação");
    pb.innerHTML = `<div class="prop"><h4>${ico("tree", "sm")} Automação proposta</h4><p>${esc(s)}</p>${(p.questions || []).length ? `<div class="grid2" style="margin-top:8px">${p.questions.map(q => inputRow(q.label || q.key, "q:" + (q.key || q.label), q.default || "")).join("")}</div>` : ""}
      <button class="btn sm" style="margin-top:10px" data-opentree>${ico("tree", "sm")} Abrir no editor</button></div>`;
    $("[data-opentree]", pb).onclick = () => openTree(p);
  } else if (a === "ui") {
    const patch = p.patch || {};
    pb.innerHTML = `<div class="prop"><h4>${ico("gear", "sm")} Ajuste da interface (já aplicado para você ver)</h4><div class="row">${Object.entries(patch).map(([k, v]) => `<span class="chip on">${esc(k)}: ${esc(Array.isArray(v) ? (v.map(x => x.name || x).join(", ")) : typeof v === "object" ? (v.name || JSON.stringify(v)) : v)}</span>`).join("")}</div></div>`;
  } else if ((r.results || []).length) {
    pb.innerHTML = `<div class="prop"><div class="rlist">${r.results.slice(0, 8).map(resultItemHTML).join("")}</div></div>`; wireResultItems(pb);
  }
}
function resultItemHTML(x) {
  const d = parseD(x.start || x.due || x.date || x.t);
  return `<button type="button" class="rli" ${x.id ? `data-cid="${esc(x.id)}"` : ""} ${x.url ? `data-url="${esc(x.url)}"` : ""}>${x.source ? `<span>${SRC_EMO[x.source.integration || x.source] || "•"}</span>` : ""}<span style="min-width:0"><b>${esc(x.title || x.titulo || x.text || x.name || "item")}</b>${x.snippet || x.trecho ? `<p>${esc(x.snippet || x.trecho)}</p>` : ""}</span><small>${d ? esc(relDay(d) + (d.getHours() ? " " + hm(d) : "")) : x.score != null ? (+x.score).toFixed(2) : ""}</small></button>`;
}
function wireResultItems(root) { $$("[data-cid]", root).forEach(b => b.onclick = () => { if (S.cards.find(c => c.id === b.dataset.cid)) openCard(b.dataset.cid); });
  $$("[data-url]:not([data-cid])", root).forEach(b => b.onclick = () => window.open(b.dataset.url, "_blank", "noopener")); }
function collectEdits(pb, p) {
  const e = {}; $$("[data-k]", pb).forEach(i => { if (i.dataset.k.startsWith("q:")) { (e.answers = e.answers || {})[i.dataset.k.slice(2)] = i.value; } else e[i.dataset.k] = i.value; });
  if (p.action === "create_card" || p.action === "update_card") {
    const card = {title: e.title, kind: selVal(pb, "pk"), column: selVal(pb, "pc"), priority: +selVal(pb, "pp") || 0};
    if (e.date) card[(p.card || p).due && !(p.card || p).start ? "due" : "start"] = `${e.date}T${e.time || "00:00"}:00`;
    return {card, ...card};
  }
  if (p.action === "send_message") { e.channel = selVal(pb, "pch"); }
  return e;
}
function openTree(p) { const g = p.graph || (p.tree && p.tree.nodes && Array.isArray(p.tree.nodes) ? p.tree : null) || {name: (p.tree && p.tree.name) || "nova árvore", tree: p.tree, nodes: [], edges: []};
  S.view = "automacoes"; history.replaceState(null, "", "#automacoes"); renderNav(); viewAutos(g); renderSuggest(); }

/* ----- triagem ----- */
function triageCard(r, text) {
  r = r || {};
  const el = document.createElement("div"); el.className = "rcard";
  const pr = r.prioridade ?? r.priority ?? "", cat = r.categoria || r.category || "", acts = r.acoes || r.actions || [], dest = r.destino || r.destination || "";
  const prN = typeof pr === "number" ? pr : {baixa: 1, media: 2, "média": 2, alta: 3, urgente: 3}[String(pr).toLowerCase()] || 0;
  el.innerHTML = `<div class="rhead"><div class="rtool"><span class="ti">${ico("shield")}</span>Triagem da mensagem</div>${r.p != null || r.confianca != null ? certHTML(r.p ?? r.confianca) : ""}</div>
    ${r.resumo || r.reply ? `<p class="reply">${esc(r.resumo || r.reply)}</p>` : ""}
    <div class="kv" style="margin-top:12px">
      <div><small>Prioridade</small><b><span class="pri p${prN}"><i></i>${esc(String(pr || "—"))}</span></b></div>
      <div><small>Categoria</small><b>${esc(cat || "—")}</b></div>
      <div><small>Destino</small><b>${esc(typeof dest === "object" ? (dest.coluna || dest.column || JSON.stringify(dest)) : dest || "—")}</b></div>
      ${r.seguranca || r.security ? `<div><small>Segurança</small><b>${esc((r.seguranca || r.security).label || r.seguranca || r.security)}</b></div>` : ""}</div>
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
  animateBars(el);
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
  const sub = {ausente: "baixar modelo", baixando: `baixando ${pct}%`, verificando: "verificando…", carregando: "carregando…", pronto: "CPU · int8", erro: "erro · ver"}[s];
  const ring = s === "baixando" ? ringSVG(m.progress || 0) : (s === "carregando" || s === "verificando") ? ringSVG(.28) : "";
  b.innerHTML = `<span class="ring">${ring}<span class="dot"></span></span><span class="mt"><span>${esc(m.name || "DOFL V2")}</span><small>${esc(sub)}</small></span>`;
  b.setAttribute("aria-label", `Modelo ${m.name || "DOFL"}: ${sub}`);
  const ms = $("#me-st"), dot = $("#me-dot"); if (ms) { ms.textContent = S.status ? (s === "pronto" ? "tudo local · pronto" : s === "ausente" ? "só regras · sem modelo" : sub) : "conectando…"; dot.className = S.status ? (s === "pronto" ? "ok" : "warn") : ""; }
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
      <p>${{ausente: "Ainda não está neste computador. Sem ele, o Djorge usa só as regras (datas, horas, atalhos) — funciona, mas entende menos.", baixando: "Baixando… você pode continuar usando o app.", verificando: "Conferindo os arquivos baixados…", carregando: "Instalado. Carregando na memória…", pronto: "Pronto e rodando só no seu computador (CPU, int8).", erro: "Algo deu errado: " + (m.error || "erro desconhecido")}[s]}</p></div></div>
      <div style="padding:0 24px 18px">
      ${s === "baixando" || s === "verificando" ? `<div class="pbar" role="progressbar" aria-valuenow="${Math.round(pct * 100)}" aria-valuemin="0" aria-valuemax="100" aria-label="Progresso do download"><i style="width:${Math.round(pct * 100)}%"></i></div><p class="hint mono">${fmtMB(m.bytes)} de ${m.total ? fmtMB(m.total) : (m.size_mb || 305) + " MB"} · ${Math.round(pct * 100)}%</p>` : ""}
      <div class="kv" style="margin-top:10px">
        <div><small>Tamanho</small><b>${esc(m.size_mb || 305)} MB</b></div>
        <div><small>Quantização</small><b>${esc(d.quantization || d.quantizacao || "int8 (CPU)")}</b></div>
        <div><small>Vocabulário</small><b>${esc(d.vocab_size || d.vocabulario || d.vocab || "—")}</b></div>
        <div><small>Versão</small><b>${esc(m.version || d.version || "—")}</b></div>
        ${m.path ? `<div style="grid-column:1/-1"><small>Pasta</small><b class="mono" style="font-size:.8em">${esc(m.path)}</b></div>` : ""}
        ${m.source ? `<div style="grid-column:1/-1"><small>Origem</small><b class="mono" style="font-size:.8em">${esc(m.source)}</b></div>` : ""}
        ${Object.entries(d.metrics || d.metricas || {}).slice(0, 6).map(([k, v]) => `<div><small>${esc(k)}</small><b>${esc(typeof v === "number" ? (v <= 1 ? (v * 100).toFixed(1) + "%" : v) : v)}</b></div>`).join("")}
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
    {t: "Tema claro", s: "aparência", i: "spark", run: () => savePrefs({theme: "light"})}, {t: "Recolher barra lateral", s: "layout", i: "side", run: toggleSide}];
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
    {id: "seguranca", label: "Segurança", icon: "shield"}, {id: "automacoes", label: "Automações", icon: "tree"}, {id: "avancado", label: "Avançado", icon: "edit"}],
    onTab: (id, el) => ({geral: setGeneral, voz: setVoice, modelo: setModel, seguranca: setSecurity, automacoes: setAutos, avancado: setAdvanced}[id])(el)});
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
    ${voices.length ? setRow("Voz", "", selHTML("v-voice", voices, store.get("voice", v.voice || voices[0][0]), "Voz")) : ""}
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
  el.innerHTML = `<p class="lead">O DOFL só decide (escolher, conferir, pontuar) — nunca inventa texto. Roda na CPU, quantizado em int8.</p>
    ${setRow("DOFL V2", {ausente: "Não instalado", baixando: `Baixando ${Math.round((m.progress || 0) * 100)}%`, pronto: "Instalado e carregado", carregando: "Carregando", verificando: "Verificando", erro: "Erro"}[s], `<button class="btn sm" id="mo-open">Detalhes</button>`)}
    <h3>Outros modelos</h3><div id="mo-list"><div class="sk"></div></div>
    <h3>Processamento</h3><div id="mo-dev"></div>`;
  $("#mo-open", el).onclick = openModel;
  try { const r = await api("/api/models"); const ms = (r && r.models) || [];
    const arr = Array.isArray(ms) ? ms : Object.entries(ms).map(([k, v]) => ({name: k, ...v}));
    $("#mo-list", el).innerHTML = arr.length ? arr.map(x => setRow(esc(x.name), esc(x.role || x.path || ""), `<span class="chip ${x.loaded ? "on" : ""}">${x.loaded ? "carregado" : x.enabled === false ? "desligado" : "parado"}</span>
      <button class="btn sm" data-ma="${x.loaded ? "unload" : "load"}" data-n="${esc(x.name)}">${x.loaded ? "Descarregar" : "Carregar"}</button>`)).join("") : `<p class="hint">Nenhum outro modelo.</p>`;
    $$("[data-ma]", el).forEach(b => b.onclick = async () => { await tryApi("/api/models/" + b.dataset.ma, {name: b.dataset.n}); toast(b.dataset.ma === "load" ? "Carregando…" : "Descarregado"); setTimeout(() => setModel(el), 600); });
    $("#mo-dev", el).innerHTML = setRow("Dispositivo", "Vale ao carregar de novo.", selHTML("dev", [["auto", "Automático"], ["cpu", "CPU"], ["cuda", "GPU (CUDA)"]], r.device || "auto", "Dispositivo")) + setRow("Threads", "", `<input class="inp" style="width:90px" type="number" min="1" max="64" id="thr" value="${esc(r.threads || "")}" aria-label="Threads">`);
    wireSel(el, "dev", [["auto", "Automático"], ["cpu", "CPU"], ["cuda", "GPU (CUDA)"]], v => tryApi("/api/models/settings", {device: v}).then(x => x && toast("Vale ao carregar de novo")));
    $("#thr", el).onchange = e => tryApi("/api/models/settings", {threads: e.target.value}).then(x => x && toast("Salvo"));
  } catch (e) { $("#mo-list", el).innerHTML = `<p class="hint">${esc(human(e))}</p>`; }
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
    <div class="seg" role="tablist" aria-label="Seção" style="margin-bottom:12px"><button role="tab" aria-selected="true" data-s="cfg">config.yaml</button><button role="tab" aria-selected="false" data-s="hist">Histórico</button><button role="tab" aria-selected="false" data-s="pend">Pendências</button><button role="tab" aria-selected="false" data-s="api">Token / API</button></div><div id="adv"></div>`;
  const box = $("#adv", el);
  const show = async s => { $$("[data-s]", el).forEach(b => b.setAttribute("aria-selected", b.dataset.s === s)); box.innerHTML = `<div class="sk"></div>`;
    if (s === "cfg") { const c = await loadCfg(); box.innerHTML = `<textarea class="ta code" id="cfgj" spellcheck="false" aria-label="Configuração">${esc(JSON.stringify(c || {}, null, 2))}</textarea><p class="hint">Formato JSON das mesmas chaves do config.yaml. Campos desconhecidos são recusados.</p><div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn primary" id="cfgs">Salvar configuração</button></div>`;
      $("#cfgs", box).onclick = () => { try { saveCfg(JSON.parse($("#cfgj", box).value)); } catch (e) { toast("JSON inválido: " + e.message, "bad"); } }; }
    if (s === "hist") { try { const h = await api("/api/history?n=60"); const list = Array.isArray(h) ? h : (h && h.items) || [];
      box.innerHTML = list.length ? `<div class="hist">${list.map(x => `<div class="card"><small>${esc(String(x.t || "").replace("T", " "))} · ${esc((x.event && x.event.source) || x.source || "")}</small><div>${esc(((x.event && (x.event.title || x.event.text)) || x.text || "").slice(0, 160))}</div>${(x.path || x.steps || []).length ? `<small>${esc((x.path || x.steps).map(p => p.node || p.label || p).join(" → ").slice(0, 200))}</small>` : ""}</div>`).join("")}</div>` : empty("Histórico vazio", "Os eventos que chegarem aparecem aqui."); }
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
  if (window.speechSynthesis) { const u = new SpeechSynthesisUtterance(text); u.lang = "pt-BR"; u.rate = store.get("rate", 1); speechSynthesis.cancel(); speechSynthesis.speak(u); }
}
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
  else if (e.key === "/" && !typing && !modalStack.length) { e.preventDefault(); inp().focus(); }
  else if (e.key === "Escape" && rec) stopRec(false);
  else if (e.key === "Escape" && modalStack.length && !openMenu && !modalStack[modalStack.length - 1].el.contains(t)) modalStack[modalStack.length - 1].close();
});
async function init() {
  if (store.get("collapsed", false)) $("#app").classList.add("collapsed");
  $("#collapse").onclick = toggleSide;
  $("#btn-new").onclick = () => { if (S.view === "inicio") { S.thread = []; render(); inp().focus(); } else newCard(); };
  $("#btn-search").onclick = () => openPalette();
  $("#btn-set").onclick = () => openSettings();
  $("#btn-int").onclick = () => openIntegrations();
  $("#mchip").onclick = openModel;
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
  await Promise.all([loadCards(true), pollModel(), pollStatus()]);
  render();
  if (!modelReady() && !store.get("welcomed", false) && S.model.state !== "baixando") { store.set("welcomed", true); setTimeout(openModel, 500); }
  setInterval(pollStatus, 5000);
  setInterval(() => { if (!document.hidden && !drawerCard && !dragId && !(document.activeElement && /INPUT|TEXTAREA/.test(document.activeElement.tagName))) refreshCards(); }, 30000);
  recorderLoaded = await loadScript("/static/app/recorder.js");
  await loadVoice(); syncMic();
}
document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
