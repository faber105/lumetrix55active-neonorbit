export const API = window.location.origin;

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

export function postJson(path, payload = {}) {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  let stopped = false;
  let timer = null;
  let inFlight = false;
  let failures = 0;

  const report = (value) => {
    try { onStatus?.(value); } catch {}
  };

  const schedule = (delay) => {
    if (stopped) return;
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(poll, delay);
  };

  const poll = async () => {
    if (stopped || inFlight || !getTelegramInitData()) return;
    if (document.hidden) {
      schedule(900);
      return;
    }
    inFlight = true;
    try {
      const data = await apiFetch(`/api/auto/state?drive=false&_=${Date.now()}`);
      failures = 0;
      try { onState?.(data); } catch {}
      report({ connected: true, driving: true, transport: "fast-http", reconnecting: false });
      schedule(data?.active ? 250 : 650);
    } catch (_) {
      failures += 1;
      report({ connected: false, driving: false, transport: "fast-http", reconnecting: true });
      schedule(Math.min(2500, 250 + failures * 250));
    } finally {
      inFlight = false;
    }
  };

  report({ connected: false, driving: false, transport: "fast-http", reconnecting: true });
  poll();
  const onVisible = () => {
    if (!document.hidden) poll();
  };
  document.addEventListener("visibilitychange", onVisible);

  return () => {
    stopped = true;
    document.removeEventListener("visibilitychange", onVisible);
    if (timer) window.clearTimeout(timer);
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
