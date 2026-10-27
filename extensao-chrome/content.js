// Djorge Sentinel — Guarda · roda SÓ nos sites ligados pelo usuário.
// Lê apenas mensagens novas, links e a própria página (conforme a escolha), em lotes, sem travar a página.
(() => {
  if (window.__djSentinel) return;
  window.__djSentinel = true;

  // seletores de mensagens por site (só o texto da mensagem, sem o resto da interface)
  const ADAPTERS = {
    "web.whatsapp.com": "div.copyable-text[data-pre-plain-text] span.selectable-text, div[data-pre-plain-text] span[dir]",
    "mail.google.com": "div.a3s.aiL, h2.hP",
    "outlook.live.com": "div[aria-label='Corpo da mensagem'], div[aria-label='Message body']",
    "outlook.office.com": "div[aria-label='Corpo da mensagem'], div[aria-label='Message body']",
    "web.telegram.org": ".message .text-content, .Message .text-content, .message .translatable-message",
    "www.instagram.com": "div[role='row'] div[dir='auto']",
    "www.messenger.com": "div[role='row'] div[dir='auto']",
    "www.facebook.com": "div[role='row'] div[dir='auto'], div[data-ad-preview='message']",
    "www.linkedin.com": ".msg-s-event-listitem__body",
    "x.com": "div[data-testid='messageEntry'] span, div[data-testid='tweetText']",
    "discord.com": "div[id^='message-content-']"
  };
  const host = location.hostname;
  const KIND = host === "mail.google.com" || host.startsWith("outlook.") ? "email" : "texto";
  const seen = new WeakSet(), seenLinks = new Set();
  let cfg = null, pending = [], timer = 0;

  const send = items => new Promise(res => {
    try { chrome.runtime.sendMessage({type: "check", items}, r => res(chrome.runtime.lastError ? [] : r || [])); }
    catch (e) { res([]); }
  });

  // ---------- interface mínima (shadow DOM: o CSS do site não interfere) ----------
  const css = `:host{all:initial}.c{display:inline-flex;gap:6px;align-items:center;margin:4px 0;padding:4px 10px;border-radius:999px;
    font:600 12px/1.4 system-ui,sans-serif;color:#fff;background:#93485b;box-shadow:0 4px 12px rgba(0,0,0,.18);cursor:default}
    .c.r{background:#8a6d1d}.c button{all:unset;cursor:pointer;text-decoration:underline;font-weight:700}`;
  function chip(text, cls = "", onClick) {
    const h = document.createElement("span");
    h.setAttribute("data-dj", "1");
    const sh = h.attachShadow({mode: "closed"});
    sh.innerHTML = `<style>${css}</style><span class="c ${cls}"></span>`;
    const c = sh.querySelector(".c");
    c.textContent = text;
    if (onClick) { const b = document.createElement("button"); b.textContent = onClick[0]; b.onclick = e => { e.stopPropagation(); onClick[1](h); }; c.append(" · ", b); }
    return h;
  }
  const label = v => ({bloquear: "Perigoso", avisar: "Suspeito", revisar: "Duvidoso"})[v.action] +
    (v.answer ? `: ${v.answer}` : "") + (v.p ? ` (${Math.round(v.p * 100)}%)` : "");

  function treatMessage(el, v) {
    if (!v || v.action === "permitir" || !el.isConnected) return;
    const mode = v.action === "bloquear" ? cfg.onBlock : cfg.onWarn;
    const box = el.closest("[data-pre-plain-text]") || el;
    if (mode === "ocultar") {
      const prev = box.style.display;
      box.style.display = "none";
      box.before(chip("✳ Mensagem ocultada pelo Sentinel — " + label(v), v.action === "revisar" ? "r" : "",
        ["mostrar", h => { box.style.display = prev; h.remove(); }]));
    } else if (mode === "desfocar") {
      box.style.filter = "blur(6px)"; box.style.transition = "filter .2s";
      box.after(chip("✳ " + label(v), v.action === "revisar" ? "r" : "",
        ["mostrar", h => { box.style.filter = ""; h.remove(); }]));
    } else {
      box.style.outline = "2px solid #b56576"; box.style.outlineOffset = "2px"; box.style.borderRadius = "6px";
      box.after(chip("✳ " + label(v), v.action === "revisar" ? "r" : ""));
    }
  }

  function treatLink(a, v) {
    if (!v || v.action === "permitir" || !a.isConnected) return;
    a.dataset.djVerdict = v.action;
    a.style.textDecoration = "underline wavy #b56576";
    a.title = "Sentinel: " + label(v);
    if (v.action === "bloquear" || cfg.onWarn === "ocultar") a.dataset.djBlock = "1";
  }
  document.addEventListener("click", e => {           // links perigosos pedem confirmação
    const a = e.target.closest && e.target.closest("a[data-dj-block]");
    if (!a) return;
    if (!confirm(`Sentinel: este link parece perigoso (${a.dataset.djVerdict}).\n\n${a.href}\n\nAbrir mesmo assim?`)) {
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true);

  // ---------- coleta em lotes ----------
  function collect(root) {
    const sel = cfg.selector || ADAPTERS[host];
    if (cfg.messages && sel) {
      let els; try { els = root.querySelectorAll(sel); } catch (e) { els = []; }
      for (const el of els) {
        if (seen.has(el) || el.closest("[data-dj]")) continue;
        const t = (el.innerText || "").trim();
        if (t.length < 12) continue;
        seen.add(el);
        pending.push({el, item: {kind: KIND, text: t}});
      }
    }
    if (cfg.links) {
      for (const a of root.querySelectorAll("a[href^='http']")) {
        if (seen.has(a)) continue;
        seen.add(a);
        let u; try { u = new URL(a.href); } catch (e) { continue; }
        if (u.hostname === host || u.hostname.endsWith("." + host)) continue;
        const key = u.hostname + u.pathname.slice(0, 40);
        pending.push({el: a, link: true, key, item: {kind: "dominio", text: u.href.slice(0, 300)}});
      }
    }
    if (pending.length) { clearTimeout(timer); timer = setTimeout(flush, 350); }
  }
  const linkVerdict = new Map();
  async function flush() {
    const batch = pending.splice(0, 25);
    if (!batch.length) return;
    const ask = [], idx = [];
    batch.forEach((b, i) => {
      if (b.link && linkVerdict.has(b.key)) return;
      if (b.link) { if (seenLinks.has(b.key)) return; seenLinks.add(b.key); }
      ask.push(b.item); idx.push(i);
    });
    const out = ask.length ? await send(ask) : [];
    idx.forEach((i, j) => { if (batch[i].link) linkVerdict.set(batch[i].key, out[j]); else treatMessage(batch[i].el, out[j]); });
    batch.forEach(b => { if (b.link) treatLink(b.el, linkVerdict.get(b.key)); });
    if (pending.length) (window.requestIdleCallback || setTimeout)(flush);
  }

  // ---------- a própria página ----------
  async function checkPage() {
    const [v] = await send([{kind: "dominio", text: location.href.slice(0, 300)}]);
    if (!v || v.action !== "bloquear") return;
    const h = document.createElement("div");
    h.setAttribute("data-dj", "1");
    const sh = h.attachShadow({mode: "closed"});
    sh.innerHTML = `<style>.o{position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;background:rgba(246,243,238,.97);font:16px/1.6 system-ui}
      .b{max-width:520px;padding:34px;border-radius:28px;background:#fff;box-shadow:0 26px 60px rgba(38,34,30,.18);text-align:center;color:#26221e}
      h1{font:700 30px Georgia,serif;margin:6px 0}.s{font-size:44px;color:#b56576}button{font:700 14px system-ui;padding:12px 22px;border-radius:999px;border:0;cursor:pointer;margin:6px}
      .p{background:#93485b;color:#fff}.g{background:#efe9e0;color:#26221e}</style>
      <div class="o"><div class="b"><div class="s">✳</div><h1>Este site parece perigoso</h1>
      <p>O Sentinel classificou esta página como <b></b>. Golpes costumam imitar bancos, lojas e serviços conhecidos.</p>
      <button class="p">Sair daqui</button><button class="g">Continuar mesmo assim</button></div></div>`;
    sh.querySelector("b").textContent = label(v);
    sh.querySelector(".p").onclick = () => history.length > 1 ? history.back() : location.replace("about:blank");
    sh.querySelector(".g").onclick = () => h.remove();
    document.documentElement.appendChild(h);
  }

  // ---------- notificações (vêm do hook.js, que roda no mundo da página) ----------
  window.addEventListener("message", async e => {
    const d = e.data;
    if (e.source !== window || !d || d.dj !== "notif") return;
    const text = [d.title, d.body].filter(Boolean).join("\n");
    let show = true, title = d.title;
    if (cfg && cfg.notifications && text.length >= 8) {
      const [v] = await send([{kind: KIND, text}]);
      if (v && v.action === "bloquear") { title = "⛔ " + d.title; show = cfg.onBlock !== "ocultar"; }
      else if (v && (v.action === "avisar" || v.action === "revisar")) { title = "⚠ " + d.title; if (cfg.onWarn === "ocultar") show = false; }
    }
    window.postMessage({dj: "notif-v", id: d.id, show, title}, location.origin);
  });

  // ---------- início ----------
  chrome.runtime.sendMessage({type: "cfg"}, r => {
    if (chrome.runtime.lastError || !r || !r.site || !r.site.enabled) return;
    cfg = {onWarn: "alertar", onBlock: "ocultar", ...r.site};
    if (cfg.page) checkPage();
    collect(document);
    let t = 0;
    const added = new Set();
    new MutationObserver(muts => {
      for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1 && !n.hasAttribute("data-dj")) added.add(n);
      if (t) return;
      t = setTimeout(() => {
        t = 0;
        const roots = [...added]; added.clear();
        for (const n of roots) if (n.isConnected) collect(n.parentElement || n);
      }, 300);
    }).observe(document.body, {childList: true, subtree: true});
  });
})();
