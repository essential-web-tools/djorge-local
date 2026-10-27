const PRESETS = {"web.whatsapp.com": "WhatsApp Web", "mail.google.com": "Gmail", "outlook.live.com": "Outlook",
  "web.telegram.org": "Telegram Web", "www.instagram.com": "Instagram", "www.messenger.com": "Messenger",
  "www.facebook.com": "Facebook", "www.linkedin.com": "LinkedIn", "x.com": "X / Twitter", "discord.com": "Discord"};
const DEF = {server: "", key: "", profile: "equilibrado", mask: true, failOpen: true, sites: {}, stats: {checked: 0, warned: 0, blocked: 0}};
const $ = id => document.getElementById(id);
let S;
const toast = t => { const e = $("toast"); e.textContent = t; e.classList.add("on"); setTimeout(() => e.classList.remove("on"), 2200); };
const save = async patch => { Object.assign(S, patch); await chrome.storage.local.set(patch); await chrome.runtime.sendMessage({type: "register"}); };

function siteRow(host, s) {
  const d = document.createElement("div"); d.className = "site";
  const opt = (k, t, tip) => `<label class="sw" title="${tip}"><input type="checkbox" data-k="${k}" ${s[k] ? "checked" : ""}> ${t}</label>`;
  d.innerHTML = `<div><h3></h3><div class="opts">
    ${opt("enabled", "ligado", "Desligado, o Sentinel não lê nada deste site.")}
    ${opt("messages", "mensagens", "Lê as mensagens novas que aparecem na tela.")}
    ${opt("links", "links", "Confere os links antes de você clicar.")}
    ${opt("notifications", "notificações", "Confere as notificações que o site mostra enquanto está aberto.")}
    ${opt("page", "o próprio site", "Avisa em tela cheia se o endereço deste site parecer golpe.")}</div>
    ${PRESETS[host] ? "" : `<label style="margin-top:8px">onde estão as mensagens (seletor CSS, opcional) <span class="qi" title="Para sites que não estão na lista: ex.: .chat-message p. Sem isso, o Sentinel confere só links e o próprio site.">?</span></label><input type="text" data-k="selector" placeholder=".mensagem">`}
    <div class="opts" style="margin-top:8px"><span class="hint">suspeito:</span><select data-k="onWarn"><option value="alertar">alertar</option><option value="desfocar">desfocar</option><option value="ocultar">ocultar</option></select>
    <span class="hint">perigoso:</span><select data-k="onBlock"><option value="alertar">alertar</option><option value="desfocar">desfocar</option><option value="ocultar">ocultar</option></select></div></div>
    <button class="rm" title="Remove o site e a permissão">remover ✕</button>`;
  d.querySelector("h3").textContent = (PRESETS[host] ? PRESETS[host] + " · " : "") + host;
  d.querySelector("[data-k=onWarn]").value = s.onWarn || "alertar";
  const se = d.querySelector("[data-k=selector]"); if (se) se.value = s.selector || "";
  d.querySelector("[data-k=onBlock]").value = s.onBlock || "ocultar";
  d.addEventListener("change", async e => {
    const k = e.target.dataset.k; if (!k) return;
    s[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    await save({sites: S.sites}); toast("salvo · recarregue a aba do site");
  });
  d.querySelector(".rm").onclick = async () => {
    delete S.sites[host];
    await chrome.permissions.remove({origins: [`https://${host}/*`]}).catch(() => {});
    await save({sites: S.sites}); render();
  };
  return d;
}

async function addSite(host) {
  host = host.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) && !/^(localhost|127\.0\.0\.1)$/.test(host)) return toast("endereço inválido");
  const local = host === "localhost" || host === "127.0.0.1";
  const ok = await chrome.permissions.request({origins: local ? [`http://${host}/*`, `https://${host}/*`] : [`https://${host}/*`]});
  if (!ok) return toast("permissão negada");
  S.sites[host] = S.sites[host] || {enabled: true, messages: true, links: true, notifications: true, page: false, onWarn: "alertar", onBlock: "ocultar"};
  await save({sites: S.sites}); render(); toast("protegido · recarregue a aba do site");
}

function render() {
  $("presets").innerHTML = "";
  for (const [h, n] of Object.entries(PRESETS)) {
    const c = document.createElement("span"); c.className = "chip" + (S.sites[h] ? " on" : ""); c.textContent = n;
    c.title = S.sites[h] ? "Já protegido" : "Proteger " + h;
    c.onclick = () => S.sites[h] ? null : addSite(h);
    $("presets").append(c);
  }
  const l = $("list"); l.innerHTML = "";
  const hosts = Object.keys(S.sites);
  if (!hosts.length) l.innerHTML = `<p class="hint">Nenhum site ainda. Comece pelo WhatsApp Web ou Gmail.</p>`;
  for (const h of hosts) l.append(siteRow(h, S.sites[h]));
  const st = S.stats || {};
  $("stats").textContent = `Verificados: ${st.checked || 0} · suspeitos: ${st.warned || 0} · perigosos: ${st.blocked || 0}`;
}

(async () => {
  S = {...DEF, ...(await chrome.storage.local.get(DEF))};
  for (const k of ["server", "key", "profile"]) $(k).value = S[k];
  $("mask").checked = S.mask; $("failOpen").checked = S.failOpen;
  $("profile").onchange = e => save({profile: e.target.value}).then(() => toast("salvo"));
  $("mask").onchange = e => save({mask: e.target.checked});
  $("failOpen").onchange = e => save({failOpen: e.target.checked});
  $("add").onclick = () => addSite($("custom").value);
  $("custom").onkeydown = e => { if (e.key === "Enter") addSite($("custom").value); };
  $("save").onclick = async () => {
    const server = $("server").value.trim().replace(/\/+$/, "");
    let u; try { u = new URL(server); } catch (e) { return toast("endereço inválido"); }
    if (u.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(u.hostname)) return toast("use https:// (ou localhost)");
    if (!(await chrome.permissions.request({origins: [u.origin + "/*"]}))) return toast("permissão negada");
    await save({server, key: $("key").value.trim()});
    $("conn").textContent = "testando…";
    const r = await chrome.runtime.sendMessage({type: "test"});
    $("conn").innerHTML = r.ok ? `<span class="ok">● conectado</span>` : `<span class="bad">● ${r.error}</span>`;
  };
  render();
})();
