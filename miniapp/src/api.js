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

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
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
  return body;
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
