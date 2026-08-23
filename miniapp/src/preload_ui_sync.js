import { apiFetch } from "./api";

let stopped = false;
let lastEnabled = null;
let timer = null;

function render(enabled) {
  const root = document.querySelector(".ap-preload");
  if (!root) return;
  root.classList.toggle("enabled", Boolean(enabled));
  root.classList.toggle("disabled", !enabled);

  const title = root.querySelector("strong");
  if (title) {
    title.innerHTML = `РАННИЙ ПОИСК <b>${enabled ? "ON" : "OFF"}</b>`;
  }

  const subtitle = root.querySelector("small");
  if (subtitle) {
    subtitle.textContent = enabled
      ? "Следующий вход анализируется параллельно"
      : "Ранний анализ выключен";
  }

  const indicator = root.querySelector(":scope > i");
  if (indicator) indicator.setAttribute("aria-label", enabled ? "ON" : "OFF");
}

async function sync() {
  if (stopped) return;
  try {
    const state = await apiFetch(`/api/auto-preload/state?_=${Date.now()}`);
    const enabled = Boolean(state?.enabled);
    lastEnabled = enabled;
    render(enabled);
  } catch {
    if (lastEnabled !== null) render(lastEnabled);
  } finally {
    timer = window.setTimeout(sync, 900);
  }
}

const observer = new MutationObserver(() => {
  if (lastEnabled !== null) render(lastEnabled);
});

function start() {
  observer.observe(document.documentElement, { childList: true, subtree: true });
  void sync();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}

window.addEventListener("beforeunload", () => {
  stopped = true;
  observer.disconnect();
  if (timer) window.clearTimeout(timer);
}, { once: true });
