const $ = id => document.getElementById(id);
(async () => {
  const S = await chrome.storage.local.get({server: "", sites: {}, stats: {}, last: null});
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  let host = ""; try { host = new URL(tab.url).hostname; } catch (e) {}
  $("host").textContent = host || "esta página";
  const s = S.sites[host];
  if (!S.server) $("state").innerHTML = `<p>Configure o servidor primeiro.</p>`;
  else if (!/^https?:/.test(tab.url || "")) $("state").innerHTML = `<p>Páginas internas do Chrome não são verificadas.</p>`;
  else if (s && s.enabled) $("state").innerHTML = `<p class="ok"><b>● Protegido</b></p><p class="hint">${["messages", "links", "notifications", "page"].filter(k => s[k]).map(k => ({messages: "mensagens", links: "links", notifications: "notificações", page: "o site"})[k]).join(" · ")}</p>`;
  else {
    $("state").innerHTML = `<p>Não protegido.</p><button class="btn p" id="on">Proteger este site ✳</button>`;
    $("on").onclick = async () => {
      if (!(await chrome.permissions.request({origins: [`https://${host}/*`]}))) return;
      S.sites[host] = {enabled: true, messages: true, links: true, notifications: true, page: true, onWarn: "alertar", onBlock: "ocultar"};
      await chrome.storage.local.set({sites: S.sites});
      await chrome.runtime.sendMessage({type: "register"});
      chrome.tabs.reload(tab.id); window.close();
    };
  }
  const st = S.stats || {};
  $("c").textContent = st.checked || 0; $("w").textContent = st.warned || 0; $("b").textContent = st.blocked || 0;
  if (S.last && Date.now() - S.last.at < 3e5) $("last").textContent = "Última verificação: " + S.last.msg;
  $("opt").onclick = () => chrome.runtime.openOptionsPage();
})();
