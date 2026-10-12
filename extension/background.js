// StudyLock Shield - service worker.
// Polls the local guard for session status and mirrors it into storage so the
// content script can decide instantly, at document_start, without a round trip.
//
// Each poll doubles as a heartbeat: it tells the guard this browser's shield is
// still alive. If the extension is removed or disabled, the heartbeats stop and
// the guard closes the browser for the rest of the session.

const VENDOR = navigator.userAgent.includes("Edg/") ? "edge" : "chrome";
const ENDPOINT = "http://127.0.0.1:47821/status?src=shield&b=" + VENDOR;
const KEY = "studylock";

let lastPoll = 0;

async function poll() {
  lastPoll = Date.now();
  try {
    const res = await fetch(ENDPOINT, { cache: "no-store" });
    const data = await res.json();
    if (data && data.active) {
      await chrome.storage.local.set({
        [KEY]: {
          active: true,
          remaining: data.remaining,
          label: data.label || "",
          hardened: !!data.hardened,
          blocking: data.blocking !== false,
          phaseRemaining: data.phase_remaining || 0,
          rules: data.rules || { blocked_domains: [], restricted_domains: {} },
          savedAt: Date.now(),
        },
      });
    } else {
      // The guard answered and says no session is running: that is the one
      // and only thing that clears the shield.
      await chrome.storage.local.set({ [KEY]: { active: false, savedAt: Date.now() } });
    }
    return true;
  } catch (err) {
    // Guard unreachable (killed, crashed, machine asleep). We deliberately do
    // NOT unblock: the cached session keeps running down its own clock.
    return false;
  }
}

// Throttled wake-up poll: browsing activity keeps the heartbeat frequent even
// when the service worker would otherwise be asleep.
function nudge() {
  if (Date.now() - lastPoll > 5000) poll();
}

function arm() {
  chrome.alarms.create("studylock-poll", { periodInMinutes: 1 });
  poll();
}

chrome.runtime.onInstalled.addListener(arm);
chrome.runtime.onStartup.addListener(arm);
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "studylock-poll") poll();
});

chrome.tabs.onUpdated.addListener(nudge);
chrome.tabs.onActivated.addListener(nudge);
chrome.tabs.onCreated.addListener(nudge);
chrome.windows.onFocusChanged.addListener(nudge);

// Content scripts ping on load and every few seconds; each ping wakes this
// worker and refreshes the cache.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "refresh") {
    poll().then(async () => {
      const store = await chrome.storage.local.get(KEY);
      sendResponse(store[KEY] || { active: false });
    });
    return true;
  }
});

arm();
