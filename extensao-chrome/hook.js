// Djorge Sentinel — Guarda · mundo da página: segura a notificação até o Sentinel responder.
// Sem resposta em 1,5 s, a notificação aparece normalmente (nada se perde).
(() => {
  const N = window.Notification;
  if (!N || N.__dj) return;
  let seq = 0;
  const pend = new Map();
  addEventListener("message", e => {
    const d = e.data;
    if (e.source !== window || !d || d.dj !== "notif-v") return;
    const p = pend.get(d.id);
    if (p) { pend.delete(d.id); clearTimeout(p.t); if (d.show) p.open(d.title); else p.drop(); }
  });
  class DjNotification extends EventTarget {
    constructor(title, options = {}) {
      super();
      const id = ++seq;
      this.title = title; this.body = options.body || ""; this.tag = options.tag || ""; this.data = options.data;
      this.icon = options.icon || ""; this._real = null; this._closed = false;
      for (const ev of ["click", "close", "show", "error"]) this["on" + ev] = null;
      const open = t => {
        if (this._closed) return;
        const n = new N(t || title, options);
        this._real = n;
        for (const ev of ["click", "close", "show", "error"]) n.addEventListener(ev, e => {
          const h = this["on" + ev]; if (typeof h === "function") h.call(this, e);
          this.dispatchEvent(new Event(ev));
        });
      };
      const drop = () => { this._closed = true; };
      const t = setTimeout(() => { pend.delete(id); open(title); }, 1500);
      pend.set(id, {open, drop, t});
      window.postMessage({dj: "notif", id, title: String(title).slice(0, 300), body: String(this.body).slice(0, 1500)}, location.origin);
    }
    close() { this._closed = true; if (this._real) this._real.close(); }
    static get permission() { return N.permission; }
    static requestPermission(cb) { return N.requestPermission(cb); }
    static get maxActions() { return N.maxActions; }
  }
  DjNotification.__dj = true;
  try { Object.defineProperty(window, "Notification", {value: DjNotification, configurable: true, writable: true}); } catch (e) {}
})();
