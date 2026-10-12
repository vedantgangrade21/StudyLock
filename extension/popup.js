const KEY = "studylock";

function fmt(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

async function render() {
  const store = await chrome.storage.local.get(KEY);
  const raw = store[KEY];
  const badge = document.getElementById("badge");
  const clock = document.getElementById("clock");
  const sub = document.getElementById("sub");

  if (!raw || !raw.active) {
    badge.textContent = "no session";
    clock.textContent = "--:--";
    sub.textContent = "Distraction blocking is off. Start a session in StudyLock.";
    return;
  }
  const left = (raw.remaining || 0) - (Date.now() - (raw.savedAt || 0)) / 1000;
  if (left <= 0) {
    badge.textContent = "no session";
    clock.textContent = "--:--";
    sub.textContent = "Session finished.";
    return;
  }
  badge.textContent = "session in progress";
  clock.textContent = fmt(left);
  const n = (raw.rules?.blocked_domains?.length || 0) +
            Object.keys(raw.rules?.restricted_domains || {}).length;
  sub.innerHTML = (raw.label ? `Working on <span class="task">${raw.label}</span>.<br>` : "") +
                  `${n} sites shielded${raw.hardened ? " &middot; hardened" : ""}.`;
}

chrome.runtime.sendMessage({ type: "refresh" }, () => void chrome.runtime.lastError);
render();
setInterval(render, 500);
