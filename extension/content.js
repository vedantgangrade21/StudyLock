// StudyLock Shield - page-side enforcement.
// Runs at document_start on every page. Reads the cached session from storage,
// decides whether this exact URL is allowed, and if not drops a full-screen
// overlay that cannot be dismissed, scrolled past or removed.

(() => {
  const KEY = "studylock";
  const HOST_ID = "studylock-shield-root";

  let session = null;      // { activeUntil: epoch ms, rules, label }
  let verdict = null;      // { title, note } when blocked
  let host = null;         // overlay host element
  let shadow = null;
  let timerNode = null;
  let observer = null;
  let lastUrl = location.href;

  // ---------------------------------------------------------------- rules

  const hostname = () => location.hostname.toLowerCase().replace(/^www\./, "");

  const domainMatches = (h, dom) => h === dom || h.endsWith("." + dom);

  function matchAny(patterns, path) {
    for (const p of patterns || []) {
      try {
        if (new RegExp(p).test(path)) return true;
      } catch (e) { /* a bad pattern must never open a hole */ }
    }
    return false;
  }

  function decide(rules) {
    if (!rules) return null;
    const h = hostname();
    const path = location.pathname + location.search;

    // The allowlist wins over every block rule, always.
    for (const dom of rules.allowed_domains || []) {
      if (domainMatches(h, dom)) return null;
    }

    const restricted = rules.restricted_domains || {};
    for (const dom of Object.keys(restricted)) {
      if (!domainMatches(h, dom)) continue;
      const rule = restricted[dom];
      const v = { title: rule.label || dom, note: rule.note || "", links: rule.links || [] };
      if (matchAny(rule.deny_paths, path)) return v;
      if (matchAny(rule.allow_paths, path)) return null;
      return v;
    }

    for (const dom of rules.blocked_domains || []) {
      if (domainMatches(h, dom)) {
        return { title: dom, note: "This site is on your distraction list." };
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- overlay

  function fmt(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
  }

  const MARKUP = `
    <style>
      :host { all: initial; }
      .wrap {
        position: fixed; inset: 0; z-index: 2147483647;
        display: flex; align-items: center; justify-content: center;
        background: radial-gradient(120% 120% at 50% 0%, #14243a 0%, #070b11 60%);
        font-family: "Segoe UI", system-ui, sans-serif; color: #e8eef7;
        -webkit-user-select: none; user-select: none;
      }
      .card { text-align: center; max-width: 560px; padding: 40px 44px; }
      .badge {
        display: inline-block; font-size: 11px; letter-spacing: .22em;
        text-transform: uppercase; color: #7dd3fc;
        border: 1px solid rgba(125,211,252,.35); border-radius: 999px;
        padding: 6px 14px; margin-bottom: 26px;
      }
      h1 { font-size: 30px; font-weight: 600; margin: 0 0 12px; line-height: 1.25; }
      .site { color: #9fb3c8; font-size: 15px; margin: 0 0 26px; line-height: 1.55; }
      .clock { font-size: 62px; font-weight: 200; letter-spacing: .02em;
               font-variant-numeric: tabular-nums; margin: 0; color: #fff; }
      .clock-label { font-size: 12px; letter-spacing: .18em; text-transform: uppercase;
                     color: #64798f; margin-top: 10px; }
      .foot { margin-top: 34px; font-size: 12.5px; color: #55697e; line-height: 1.6; }
      .task { color: #7dd3fc; }
      .links { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; margin: 0 0 28px; }
      .links a { color: #06121c; background: #7dd3fc; text-decoration: none; font-size: 14px;
                 font-weight: 600; padding: 10px 18px; border-radius: 8px; cursor: pointer; }
      .links a:hover { background: #a5e4fd; }
    </style>
    <div class="wrap">
      <div class="card">
        <div class="badge">StudyLock &middot; session in progress</div>
        <h1 id="sl-title">This page is blocked</h1>
        <p class="site" id="sl-note"></p>
        <div class="links" id="sl-links"></div>
        <p class="clock" id="sl-clock">--:--</p>
        <div class="clock-label">remaining</div>
        <p class="foot">There is no bypass. Close the tab and get back to it.<br>
           <span id="sl-task"></span></p>
      </div>
    </div>`;

  function mount() {
    if (host && host.isConnected) return;
    const root = document.documentElement;
    if (!root) return;
    host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "all:initial;position:fixed;inset:0;z-index:2147483647;";
    shadow = host.attachShadow({ mode: "closed" });
    shadow.innerHTML = MARKUP;
    root.appendChild(host);
    timerNode = shadow.getElementById("sl-clock");
    paint();

    if (!observer) {
      // If anything (the page, or devtools) removes the overlay, put it back.
      observer = new MutationObserver(() => {
        if (verdict && (!host || !host.isConnected)) mount();
      });
      observer.observe(root, { childList: true, subtree: false });
    }
  }

  function paint() {
    if (!shadow || !verdict) return;
    shadow.getElementById("sl-title").textContent = verdict.title
      ? verdict.title + " is blocked right now"
      : "This page is blocked";
    shadow.getElementById("sl-note").textContent = verdict.note || "";
    const links = shadow.getElementById("sl-links");
    links.textContent = "";
    for (const l of verdict.links || []) {
      if (!/^https:\/\/discord\.com\/channels\/\d+$/.test(l.url)) continue;  // only our own deep links
      const a = document.createElement("a");
      a.textContent = "Go to " + l.name;
      a.href = l.url;
      a.addEventListener("click", (e) => { e.preventDefault(); location.href = l.url; });
      links.appendChild(a);
    }
    const task = shadow.getElementById("sl-task");
    task.innerHTML = session && session.label
      ? 'You are meant to be working on <span class="task">' + escapeHtml(session.label) + "</span>."
      : "";
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function unmount() {
    if (observer) { observer.disconnect(); observer = null; }
    if (host) { host.remove(); host = null; shadow = null; timerNode = null; }
  }

  function quietMedia() {
    if (!verdict) return;
    for (const el of document.querySelectorAll("video, audio")) {
      try { el.pause(); el.muted = true; } catch (e) { /* ignore */ }
    }
  }

  // ---------------------------------------------------------------- status

  function fromStore(raw) {
    if (!raw || !raw.active) return null;
    const elapsed = (Date.now() - (raw.savedAt || 0)) / 1000;
    const left = (raw.remaining || 0) - elapsed;
    if (left <= 0) return null;
    // On a Pomodoro break nothing is blocked. If the guard goes quiet mid-break
    // we resume blocking the moment that break was due to end, so killing it
    // during a break buys five minutes at most.
    const blocking = raw.blocking !== false || elapsed >= (raw.phaseRemaining || 0);
    return { activeUntil: Date.now() + left * 1000, blocking: blocking,
             rules: raw.rules, label: raw.label || "" };
  }

  function apply() {
    const wasBlocked = !!verdict;
    if (session && session.blocking && Date.now() < session.activeUntil) {
      verdict = decide(session.rules);
    } else {
      verdict = null;
    }

    if (verdict) {
      if (!wasBlocked) { try { window.stop(); } catch (e) { /* ignore */ } }
      mount();
      paint();
      quietMedia();
    } else if (wasBlocked) {
      // Session finished (or we navigated somewhere allowed) - clean slate.
      unmount();
      location.reload();   // the page was stopped mid-load; bring it back properly
    }
  }

  async function refreshFromStorage() {
    try {
      const store = await chrome.storage.local.get(KEY);
      session = fromStore(store[KEY]);
    } catch (e) { /* keep the last known session */ }
    apply();
  }

  function pingWorker() {
    try {
      chrome.runtime.sendMessage({ type: "refresh" }, (resp) => {
        if (chrome.runtime.lastError) return;   // worker asleep; storage still valid
        if (resp) session = fromStore(resp);
        apply();
      });
    } catch (e) { /* extension context torn down */ }
  }

  // ---------------------------------------------------------------- wiring

  refreshFromStorage();
  pingWorker();

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[KEY]) {
      session = fromStore(changes[KEY].newValue);
      apply();
    }
  });

  // SPA navigation (Discord, YouTube) never reloads the document.
  for (const method of ["pushState", "replaceState"]) {
    const original = history[method];
    history[method] = function () {
      const out = original.apply(this, arguments);
      queueMicrotask(apply);
      return out;
    };
  }
  addEventListener("popstate", apply);
  addEventListener("hashchange", apply);

  setInterval(() => {
    if (location.href !== lastUrl) { lastUrl = location.href; apply(); }
    if (verdict) {
      mount();
      quietMedia();
      if (timerNode && session) timerNode.textContent = fmt(session.activeUntil - Date.now());
      if (session && Date.now() >= session.activeUntil) { session = null; apply(); }
    }
  }, 500);

  setInterval(pingWorker, 4000);

  document.addEventListener("DOMContentLoaded", () => { if (verdict) { mount(); quietMedia(); } });
})();
