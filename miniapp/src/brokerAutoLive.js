import { TG, TG_ID, apiFetch, patchJson, postJson } from "./api";

let lastLivePositionId = null;
let installed = false;

function styleBox(node) {
  node.style.marginTop = "14px";
  node.style.padding = "13px";
  node.style.border = "1px solid rgba(124,131,255,.26)";
  node.style.borderRadius = "14px";
  node.style.background = "rgba(14,20,34,.72)";
}

function renderAdminRuntime(state) {
  const panel = Array.from(document.querySelectorAll(".panel"))
    .find((node) => node.textContent?.includes("Авто-сделки Pocket"));
  if (!panel || !state) return;

  let account = panel.querySelector("[data-alpha-account-mode]");
  if (!account) {
    account = document.createElement("div");
    account.dataset.alphaAccountMode = "1";
    styleBox(account);
    const title = document.createElement("div");
    title.textContent = "СЧЁТ ДЛЯ АВТО-СДЕЛОК";
    title.style.fontSize = "12px";
    title.style.fontWeight = "800";
    title.style.letterSpacing = ".12em";
    title.style.marginBottom = "10px";
    account.appendChild(title);

    const chips = document.createElement("div");
    chips.className = "chips";
    for (const mode of ["demo", "real"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.mode = mode;
      button.textContent = mode === "demo" ? "Demo" : "Real";
      button.onclick = async () => {
        button.disabled = true;
        try {
          await patchJson("/api/admin/state", { trade_account_mode: mode });
        } finally {
          button.disabled = false;
        }
      };
      chips.appendChild(button);
    }
    account.appendChild(chips);
    const note = document.createElement("div");
    note.dataset.accountNote = "1";
    note.style.fontSize = "12px";
    note.style.lineHeight = "1.45";
    note.style.marginTop = "9px";
    note.style.opacity = ".76";
    account.appendChild(note);

    const actions = panel.querySelector(".admin-actions");
    if (actions) panel.insertBefore(account, actions);
    else panel.appendChild(account);
  }

  account.querySelectorAll("button[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.trade_account_mode);
  });
  const note = account.querySelector("[data-account-note]");
  if (note) {
    const connected = String(state.trade_account || "—").toUpperCase();
    if (state.trade_account_mode === "real") {
      note.textContent = `Выбран REAL. Подключён Pocket: ${connected}. Сигнал ищется автоматически, но реальный ордер требует ручного подтверждения в Pocket Option.`;
    } else if (state.account_matches_mode) {
      note.textContent = `Выбран DEMO. Подключён Pocket: ${connected}. Подтверждённый сигнал может открыться автоматически.`;
    } else {
      note.textContent = `Выбран DEMO, но подключён Pocket: ${connected}. Авто-ордер заблокирован до совпадения режима счёта.`;
    }
  }

  let hunt = panel.querySelector("[data-alpha-hunt]");
  if (!hunt) {
    hunt = document.createElement("div");
    hunt.dataset.alphaHunt = "1";
    styleBox(hunt);
    const actions = panel.querySelector(".admin-actions");
    if (actions) actions.insertAdjacentElement("afterend", hunt);
    else panel.appendChild(hunt);
  }
  hunt.replaceChildren();
  const huntActive = Boolean(state.hunt?.active);
  if (huntActive) {
    const text = document.createElement("div");
    const kind = state.hunt.kind === "vip" ? "VIP" : "обычный";
    text.textContent = `🔎 Идёт поиск: ${kind} сигнал · ${state.selected_strategy} · ${state.selected_timeframe}. Scanner продолжает проверку до свежего подтверждённого сетапа.`;
    text.style.fontSize = "13px";
    text.style.lineHeight = "1.45";
    hunt.appendChild(text);
    const stop = document.createElement("button");
    stop.type = "button";
    stop.className = "secondary";
    stop.textContent = "Остановить поиск";
    stop.style.marginTop = "10px";
    stop.onclick = async () => {
      stop.disabled = true;
      try { await postJson("/api/admin/hunt-stop"); }
      finally { stop.disabled = false; }
    };
    hunt.appendChild(stop);
    const oldMessage = panel.querySelector(".admin-actions ~ .saved");
    if (oldMessage) oldMessage.textContent = "Поиск запущен — продолжаю сканировать до подтверждённого сетапа.";
  } else if (state.hunt?.status === "HUNT_FOUND") {
    const text = document.createElement("div");
    const execution = state.latest_execution;
    text.textContent = execution?.status === "OPEN"
      ? "✅ Сетап найден, Demo-сделка открыта. Переключаю в Live."
      : state.trade_account_mode === "real"
        ? "✅ Сетап найден. Для REAL подтверди ордер вручную в Pocket Option."
        : "✅ Сетап найден.";
    text.style.fontSize = "13px";
    text.style.lineHeight = "1.45";
    hunt.appendChild(text);
  } else {
    hunt.style.display = "none";
    return;
  }
  hunt.style.display = "block";
}

export function installBrokerAutoLive() {
  if (installed || !TG_ID) return;
  installed = true;
  let stopped = false;
  let timer = null;
  let adminTimer = null;

  const openLiveTab = () => {
    const button = Array.from(document.querySelectorAll(".bottom-nav button"))
      .find((node) => node.textContent?.includes("Live"));
    button?.click();
  };

  const tick = async () => {
    try {
      const rows = await apiFetch("/api/live/active");
      const livePosition = Array.isArray(rows)
        ? rows.find((position) => position.status === "OPEN" && ["broker", "auto"].includes(position.source))
        : null;
      if (livePosition && livePosition.id !== lastLivePositionId) {
        lastLivePositionId = livePosition.id;
        openLiveTab();
        TG?.HapticFeedback?.notificationOccurred?.("success");
      }
    } catch {
      // Network/auth failures are transient; the next poll retries automatically.
    }
    if (!stopped) timer = window.setTimeout(tick, 900);
  };

  const adminTick = async () => {
    try {
      if (document.body.textContent?.includes("Авто-сделки Pocket")) {
        const state = await apiFetch("/api/admin/state");
        renderAdminRuntime(state);
        if (state.latest_execution?.status === "OPEN" && state.latest_execution?.position_id) {
          if (state.latest_execution.position_id !== lastLivePositionId) {
            lastLivePositionId = state.latest_execution.position_id;
            openLiveTab();
          }
        }
      }
    } catch {
      // Admin UI may not be open or user may not have admin rights.
    }
    if (!stopped) adminTimer = window.setTimeout(adminTick, 1000);
  };

  timer = window.setTimeout(tick, 350);
  adminTimer = window.setTimeout(adminTick, 500);
  return () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    if (adminTimer) window.clearTimeout(adminTimer);
    installed = false;
  };
}
