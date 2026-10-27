// Djorge Sentinel — Guarda · service worker
// Só faz algo nos sites que o usuário ligou. Os textos vão SÓ para o servidor Sentinel configurado.
// Leve: cache de veredictos, no máximo 3 pedidos ao mesmo tempo, textos cortados e dados pessoais mascarados.

const DEFAULTS = {
  server: "", key: "", profile: "equilibrado", mask: true, failOpen: true,
  sites: {},            // host -> {messages, links, notifications, page, onWarn, onBlock}
  stats: {checked: 0, warned: 0, blocked: 0}
};
export const PRESETS = {
  "web.whatsapp.com": "WhatsApp Web", "mail.google.com": "Gmail", "outlook.live.com": "Outlook",
  "outlook.office.com": "Outlook (trabalho)", "web.telegram.org": "Telegram Web",
  "www.instagram.com": "Instagram", "www.messenger.com": "Messenger", "www.facebook.com": "Facebook",
  "www.linkedin.com": "LinkedIn", "x.com": "X / Twitter", "discord.com": "Discord"
};
const MAX_TEXT = 2000, CACHE_MAX = 3000, CONCURRENCY = 3, TIMEOUT_MS = 6000;

let S = null;
async function settings() {
  if (S) return S;
  const got = await chrome.storage.local.get(DEFAULTS);
  S = {...DEFAULTS, ...got};
  return S;
}
chrome.storage.onChanged.addListener((ch, area) => {
  if (area !== "local") return;
  S = null;
  if (ch.sites || ch.server) registerAll();
});

// ---------- dados pessoais mascarados antes de sair do navegador ----------
const PII = [
  [/\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b/g, "[CPF]"],
  [/\b(?:\d[ -]?){13,19}\b/g, "[CARTÃO]"],
  [/[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{2,63})+/g, "[E-MAIL]"],
  [/(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b/g, "[TELEFONE]"]
];
const maskPII = t => PII.reduce((s, [re, r]) => s.replace(re, r), t);

// ---------- cache (memória + sessão) ----------
const mem = new Map();
async function hash(s) {
  const b = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, "0")).join("");
}
async function cacheGet(k) {
  if (mem.has(k)) return mem.get(k);
  const o = await chrome.storage.session.get(k);
  if (o[k]) mem.set(k, o[k]);
  return o[k];
}
function cacheSet(k, v) {
  mem.set(k, v);
  if (mem.size > CACHE_MAX) mem.delete(mem.keys().next().value);
  chrome.storage.session.set({[k]: v}).catch(() => {});
}

// ---------- fila com concorrência limitada ----------
let running = 0;
const queue = [];
function schedule(fn) {
  return new Promise((res, rej) => { queue.push({fn, res, rej}); pump(); });
}
function pump() {
  while (running < CONCURRENCY && queue.length) {
    const {fn, res, rej} = queue.shift();
    running++;
    fn().then(res, rej).finally(() => { running--; pump(); });
  }
}

async function callSentinel(kind, text, cfg) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(cfg.server.replace(/\/+$/, "") + "/v1/sentinel", {
      method: "POST", signal: ctl.signal,
      headers: {"Content-Type": "application/json", ...(cfg.key ? {"Authorization": "Bearer " + cfg.key} : {})},
      body: JSON.stringify({text, kind, profile: cfg.profile})
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const j = await r.json();
    return {action: j.action, answer: j.answer ?? j.proposed, p: j.probability, danger: j.danger?.level};
  } finally { clearTimeout(t); }
}

async function check(item) {
  const cfg = await settings();
  if (!cfg.server) return {action: "permitir", error: "servidor não configurado"};
  let text = String(item.text || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
  if (text.length < 4) return {action: "permitir"};
  if (cfg.mask && item.kind !== "dominio") text = maskPII(text);
  const k = "v:" + (await hash(item.kind + "|" + cfg.profile + "|" + text));
  const hit = await cacheGet(k);
  if (hit) return hit;
  try {
    const v = await schedule(() => callSentinel(item.kind, text, cfg));
    cacheSet(k, v);
    count(v.action);
    return v;
  } catch (e) {
    return {action: "permitir", error: String(e.message || e)};
  }
}

let statsDirty = false;
function count(action) {
  if (!S) return;
  S.stats.checked++;
  if (action === "avisar" || action === "revisar") S.stats.warned++;
  if (action === "bloquear") S.stats.blocked++;
  if (!statsDirty) {
    statsDirty = true;
    setTimeout(() => { statsDirty = false; chrome.storage.local.set({stats: S.stats}); }, 3000);
  }
}

// ---------- scripts só nos sites ligados ----------
async function registerAll() {
  const cfg = await settings();
  const old = await chrome.scripting.getRegisteredContentScripts();
  if (old.length) await chrome.scripting.unregisterContentScripts({ids: old.map(s => s.id)});
  const regs = [];
  for (const [host, site] of Object.entries(cfg.sites)) {
    if (!site.enabled) continue;
    const local = host === "localhost" || host === "127.0.0.1";
    const origins = local ? [`http://${host}/*`, `https://${host}/*`] : [`https://${host}/*`];
    const granted = [];
    for (const o of origins) if (await chrome.permissions.contains({origins: [o]})) granted.push(o);
    if (!granted.length) continue;
    origins.length = 0; origins.push(...granted);
    const id = host.replace(/[^a-z0-9]/gi, "_");
    regs.push({id: "c_" + id, matches: origins, js: ["content.js"], runAt: "document_idle"});
    if (site.notifications) regs.push({id: "n_" + id, matches: origins, js: ["hook.js"], runAt: "document_start", world: "MAIN"});
  }
  if (regs.length) await chrome.scripting.registerContentScripts(regs);
}
chrome.runtime.onInstalled.addListener(async () => {
  registerAll();
  chrome.contextMenus.create({id: "dj-check", title: "Verificar com o Sentinel", contexts: ["selection", "link"]});
  if (!(await settings()).server) chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(registerAll);

// ---------- mensagens dos scripts ----------
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "cfg") {
      const cfg = await settings();
      const host = new URL(sender.url).hostname;
      reply({site: cfg.sites[host] || null, failOpen: cfg.failOpen});
    } else if (msg.type === "check") {
      const out = await Promise.all(msg.items.map(check));
      const bad = out.filter(v => v.action === "bloquear" || v.action === "avisar").length;
      if (bad && sender.tab) bump(sender.tab.id, bad);
      reply(out);
    } else if (msg.type === "register") {
      await registerAll(); reply(true);
    } else if (msg.type === "test") {
      try {
        const v = await callSentinel("texto", "Olá, tudo bem? Amanhã nos vemos.", await settings());
        reply({ok: true, v});
      } catch (e) { reply({ok: false, error: String(e.message || e)}); }
    }
  })();
  return true;
});

const tabCount = {};
function bump(tabId, n) {
  tabCount[tabId] = (tabCount[tabId] || 0) + n;
  chrome.action.setBadgeBackgroundColor({tabId, color: "#93485b"});
  chrome.action.setBadgeText({tabId, text: String(Math.min(99, tabCount[tabId]))});
}
chrome.tabs.onRemoved.addListener(id => delete tabCount[id]);

// ---------- menu de contexto: verificar seleção ou link em qualquer página ----------
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const item = info.linkUrl ? {kind: "dominio", text: info.linkUrl} : {kind: "texto", text: info.selectionText || ""};
  const v = await check(item);
  const msg = v.error ? `Não deu para verificar: ${v.error}` :
    ({bloquear: "Perigoso — não continue", avisar: "Suspeito — tenha cuidado", revisar: "Duvidoso — confira com calma",
      permitir: "Nada suspeito encontrado"})[v.action] + (v.answer ? ` (${v.answer}${v.p ? ", " + Math.round(v.p * 100) + "%" : ""})` : "");
  chrome.storage.local.set({last: {msg, at: Date.now(), action: v.action}});
  try {
    await chrome.scripting.executeScript({target: {tabId: tab.id}, func: (m, a) => {
      const d = document.createElement("div");
      d.textContent = "✳ Sentinel: " + m;
      d.style.cssText = `position:fixed;z-index:2147483647;right:18px;bottom:18px;max-width:360px;padding:12px 16px;border-radius:14px;font:600 13px system-ui;color:#fff;box-shadow:0 12px 30px rgba(0,0,0,.25);background:${a === "permitir" ? "#2f6b4f" : a === "revisar" ? "#8a6d1d" : "#93485b"}`;
      document.documentElement.appendChild(d); setTimeout(() => d.remove(), 6000);
    }, args: [msg, v.action]});
  } catch (e) { /* página protegida (chrome://): o resultado fica no popup */ }
});
