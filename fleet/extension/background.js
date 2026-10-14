// Fleet Redirect Watcher
//
// One job: notice when a watched tab leaves the target site, raise an OS
// notification, and jump to that exact window when the notification is clicked.
//
// Must match PORT in launcher.py.
const SERVER_PORT = 8765;
const SERVER = `http://127.0.0.1:${SERVER_PORT}`;

// ---------------------------------------------------------------- config ----

// Fetched from the launcher on first handshake, then cached so the extension
// keeps working after the launcher is closed.
async function getConfig({ refresh = false } = {}) {
  if (!refresh) {
    const { cfg } = await chrome.storage.local.get("cfg");
    if (cfg) return cfg;
  }
  try {
    const res = await fetch(`${SERVER}/config`, { cache: "no-store" });
    const cfg = await res.json();
    await chrome.storage.local.set({ cfg });
    return cfg;
  } catch (err) {
    const { cfg } = await chrome.storage.local.get("cfg");
    return cfg || null;
  }
}

// ----------------------------------------------------------- host compare ---

function baseDomain(host) {
  const labels = host.split(".");
  if (labels.length <= 2) return host;
  if (/^\d+$/.test(labels[labels.length - 1])) return host; // bare IP
  return labels.slice(-2).join(".");
}

function sameSite(host, allowedHost, mode) {
  if (!host || !allowedHost) return false;
  if (mode === "exact") return host === allowedHost;
  return baseDomain(host) === baseDomain(allowedHost);
}

function isInternal(url) {
  return !/^https?:\/\//i.test(url) || url.startsWith(SERVER);
}

// --------------------------------------------------- watched tab tracking ---
// storage.session survives service worker restarts but clears on browser exit,
// which is exactly the lifetime we want.

async function readSession() {
  const { watched = {}, targets = {} } = await chrome.storage.session.get([
    "watched",
    "targets",
  ]);
  return { watched, targets };
}

async function watchTab(tabId, allowedHost) {
  const { watched } = await readSession();
  watched[tabId] = { allowedHost, lastAlertHost: null };
  await chrome.storage.session.set({ watched });
}

// ------------------------------------------------------------- handshake ----
// The launcher opens http://127.0.0.1:PORT/start?slot=N. We read the slot
// number, remember it for this profile, then send the tab on to the real site.

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url;
  if (!url || !url.startsWith(`${SERVER}/start`)) return;

  const slot = new URL(url).searchParams.get("slot");
  if (slot) await chrome.storage.local.set({ slot });

  const cfg = await getConfig({ refresh: true });
  if (!cfg || !cfg.target_url) return;

  const allowedHost = cfg.allowed_host || new URL(cfg.target_url).hostname;
  await watchTab(tabId, allowedHost);
  chrome.tabs.update(tabId, { url: cfg.target_url });
});

// --------------------------------------------------------- redirect watch ---

chrome.webNavigation.onCommitted.addListener(async (details) => {
  if (details.frameId !== 0) return; // main frame only, ignore iframes/ads

  const { watched } = await readSession();
  const entry = watched[details.tabId];
  if (!entry) return;
  if (isInternal(details.url)) return;

  let host;
  try {
    host = new URL(details.url).hostname.toLowerCase();
  } catch (err) {
    return;
  }

  const cfg = await getConfig();
  const mode = (cfg && cfg.match_mode) || "base";

  if (sameSite(host, entry.allowedHost, mode)) {
    if (entry.lastAlertHost) {
      entry.lastAlertHost = null; // came home, so re-arm
      watched[details.tabId] = entry;
      await chrome.storage.session.set({ watched });
    }
    return;
  }

  if (entry.lastAlertHost === host) return; // already told you about this one

  entry.lastAlertHost = host;
  watched[details.tabId] = entry;
  await chrome.storage.session.set({ watched });

  let tab;
  try {
    tab = await chrome.tabs.get(details.tabId);
  } catch (err) {
    return;
  }

  await alert(tab, details.url, host);
});

// ---------------------------------------------------------- notification ----

async function alert(tab, url, host) {
  const { slot } = await chrome.storage.local.get("slot");
  const label = slot ? `Profile ${slot}` : "A window";
  const notificationId = `redirect:${tab.id}:${Date.now()}`;

  await chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: "icon128.png",
    title: `${label} was redirected`,
    message: host,
    contextMessage: "Click to open that window",
    priority: 2,
    requireInteraction: true,
  });

  const { targets } = await readSession();
  targets[notificationId] = { windowId: tab.windowId, tabId: tab.id };
  await chrome.storage.session.set({ targets });

  // Best effort console log in the launcher. Ignored if it has been closed.
  fetch(`${SERVER}/event`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot: slot || "?", url }),
  }).catch(() => {});
}

chrome.notifications.onClicked.addListener(async (notificationId) => {
  const { targets } = await readSession();
  const target = targets[notificationId];
  chrome.notifications.clear(notificationId);
  if (!target) return;

  delete targets[notificationId];
  await chrome.storage.session.set({ targets });

  try {
    await chrome.tabs.update(target.tabId, { active: true });
    await chrome.windows.update(target.windowId, {
      focused: true,
      drawAttention: true,
    });
  } catch (err) {
    // Tab or window was closed in the meantime.
  }
});

chrome.notifications.onClosed.addListener(async (notificationId) => {
  const { targets } = await readSession();
  if (!targets[notificationId]) return;
  delete targets[notificationId];
  await chrome.storage.session.set({ targets });
});

// ----------------------------------------------------------- housekeeping ---

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { watched } = await readSession();
  if (!watched[tabId]) return;
  delete watched[tabId];
  await chrome.storage.session.set({ watched });
});
