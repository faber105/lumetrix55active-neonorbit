const buildApi = String(import.meta.env.VITE_API_BASE || "").trim();
const queryApi = new URLSearchParams(window.location.search).get("api") || "";
const CDN_API = "https://birthday-map-race-packing.trycloudflare.com";
const isCloudflareCdn = /(?:^|\.)(?:workers\.dev|pages\.dev)$/i.test(window.location.hostname);
if (queryApi && !isCloudflareCdn) {
  try { localStorage.setItem("ap_api_base", queryApi); } catch {}
}
let savedApi = "";
try { savedApi = localStorage.getItem("ap_api_base") || ""; } catch {}
export const API = String(buildApi || (isCloudflareCdn ? CDN_API : "") || queryApi || savedApi || window.location.origin).replace(/\/$/, "");

export function getTelegramWebApp() {
  return window.Telegram?.WebApp || null;
}

function launchInitData() {
  const fromHash = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("tgWebAppData");
  if (fromHash) return fromHash;
  return new URLSearchParams(window.location.search).get("tgWebAppData") || "";
}

export function getTelegramInitData() {
  return getTelegramWebApp()?.initData || launchInitData();
}

function getTelegramUserId() {
  const direct = getTelegramWebApp()?.initDataUnsafe?.user?.id;
  if (direct) return direct;
  const initData = getTelegramInitData();
  if (!initData) return null;
  try {
    const rawUser = new URLSearchParams(initData).get("user");
    return rawUser ? JSON.parse(rawUser)?.id ?? null : null;
  } catch {
    return null;
  }
}

export const TG = getTelegramWebApp();
export const TG_ID = getTelegramUserId();

export function telegramHeaders(extra = {}) {
  const initData = getTelegramInitData();
  return {
    ...(initData ? { "X-Telegram-Init-Data": initData } : {}),
    ...extra,
  };
}

const ISO_WITHOUT_ZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;
const TIME_FIELD = /(?:_time|_at|time|date)$/i;

function normalizeTimeValue(value, key = "") {
  if (Array.isArray(value)) return value.map((item) => normalizeTimeValue(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalizeTimeValue(v, k)]));
  }
  if (typeof value === "string" && TIME_FIELD.test(key) && ISO_WITHOUT_ZONE.test(value)) {
    return `${value}Z`;
  }
  return value;
}

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    mode: "cors",
    cache: options.cache || "no-store",
    headers: telegramHeaders(options.headers || {}),
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message = body?.detail || body?.error || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return normalizeTimeValue(body);
}

export function postJson(path, payload = {}, extraHeaders = {}) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(payload),
  });
}

export function patchJson(path, payload = {}) {
  return apiFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function connectAutoRealtime({ onState, onStatus } = {}) {
  let socket = null;
  let stopped = false;
  let retryTimer = null;
  let pollTimer = null;
  let retryDelay = 250;
  let lastSequence = 0;

  const report = (value) => {
    try { onStatus?.(value); } catch {}
  };

  const scheduleReconnect = () => {
    if (stopped || retryTimer) return;
    startPolling();
    report({ connected: false, driving: false, reconnecting: true, transport: "polling" });
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      connect();
    }, retryDelay);
    retryDelay = Math.min(5000, Math.round(retryDelay * 1.7));
  };

  const stopPolling = () => {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const state = await apiFetch("/api/auto/state?drive=false");
      onState?.(state);
      report({ connected: true, driving: true, reconnecting: false, transport: "polling" });
    } catch {
      report({ connected: false, driving: false, reconnecting: true, transport: "polling" });
    }
  };

  const startPolling = () => {
    if (stopped || pollTimer) return;
    void poll();
    pollTimer = window.setInterval(poll, 750);
  };

  const connect = async () => {
    if (stopped || !getTelegramInitData()) return;
    let config;
    try {
      config = await postJson("/api/live/realtime-token");
    } catch {
      startPolling();
      scheduleReconnect();
      return;
    }
    if (config?.transport !== "wss" || !config?.url || !config?.token) {
      startPolling();
      return;
    }
    socket = new WebSocket(config.url);
    report({ connected: false, driving: false, reconnecting: true, transport: "wss" });

    socket.addEventListener("open", () => {
      retryDelay = 250;
      socket?.send(JSON.stringify({ type: "auth", token: config.token, last_sequence: lastSequence }));
    });
    socket.addEventListener("message", (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message?.type === "ready") {
          stopPolling();
          report({ connected: true, driving: true, reconnecting: false, transport: "wss" });
        } else if (message?.type === "auto_state" && message.data) {
          lastSequence = Math.max(lastSequence, Number(message.data.sequence || 0));
          onState?.(normalizeTimeValue(message.data));
        }
      } catch {}
    });
    socket.addEventListener("close", scheduleReconnect);
    socket.addEventListener("error", () => {
      try { socket?.close(); } catch {}
    });
  };

  connect();
  return () => {
    stopped = true;
    if (retryTimer) window.clearTimeout(retryTimer);
    stopPolling();
    try { socket?.close(1000, "Mini App closed realtime stream"); } catch {}
  };
}

let timezoneSyncPromise = null;
export function syncDeviceTimezone() {
  if (!TG_ID) return Promise.resolve(null);
  if (timezoneSyncPromise) return timezoneSyncPromise;
  let name = "";
  try {
    name = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {}
  const offsetMinutes = -new Date().getTimezoneOffset();
  timezoneSyncPromise = postJson("/api/settings/timezone", {
    name,
    offset_minutes: offsetMinutes,
  }).catch(() => null);
  return timezoneSyncPromise;
}

if (TG_ID) {
  setTimeout(() => { syncDeviceTimezone(); }, 0);
}
