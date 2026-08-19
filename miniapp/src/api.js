export const API = window.location.origin;
export const TG = window.Telegram?.WebApp;
export const TG_ID = TG?.initDataUnsafe?.user?.id ?? null;

export function telegramHeaders(extra = {}) {
  const initData = TG?.initData || "";
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
