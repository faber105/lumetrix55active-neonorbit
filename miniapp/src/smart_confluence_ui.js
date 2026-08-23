let smartSelected = true;

const SMART_NAME = "Smart Confluence";
const SMART_DESC = "5 стратегий · выбирает сильнейший подтверждённый сетап";

function markCardSmart() {
  const card = document.querySelector(".ap-strategy-card");
  if (!card || !smartSelected) return;
  const title = card.querySelector("strong");
  const desc = card.querySelector("small");
  const icon = card.querySelector("span b");
  if (title) title.textContent = SMART_NAME;
  if (desc) desc.textContent = SMART_DESC;
  if (icon) icon.textContent = "AI";
}

function installMenuItem() {
  const menu = document.querySelector(".ap-strategy-menu");
  if (!menu || menu.querySelector('[data-smart-confluence="1"]')) return;

  const button = document.createElement("button");
  button.type = "button";
  button.dataset.smartConfluence = "1";
  button.className = smartSelected ? "active" : "";
  button.innerHTML = `<span>AI</span><div><b>${SMART_NAME}</b><small>${SMART_DESC}</small></div><i>${smartSelected ? "✓" : ""}</i>`;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    smartSelected = true;
    try { localStorage.setItem("ap_auto_strategy", "smart_confluence"); } catch {}
    markCardSmart();
    menu.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    const check = button.querySelector("i");
    if (check) check.textContent = "✓";
    document.body.click();
  });
  menu.prepend(button);

  menu.querySelectorAll("button:not([data-smart-confluence='1'])").forEach((item) => {
    item.addEventListener("click", () => {
      smartSelected = false;
      try { localStorage.removeItem("ap_auto_strategy"); } catch {}
    }, { once: true });
  });
}

function rewriteAutoRequest(input, init) {
  const url = typeof input === "string" ? input : input?.url || "";
  if (!smartSelected || !/\/api\/auto\/(start|preview)(?:\?|$)/.test(url) || !init?.body) return init;
  try {
    const payload = JSON.parse(init.body);
    payload.strategy = "smart_confluence";
    return { ...init, body: JSON.stringify(payload) };
  } catch {
    return init;
  }
}

try {
  smartSelected = localStorage.getItem("ap_auto_strategy") !== "off";
} catch {}

const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init) => nativeFetch(input, rewriteAutoRequest(input, init));

const observer = new MutationObserver(() => {
  markCardSmart();
  installMenuItem();
});

function start() {
  observer.observe(document.documentElement, { childList: true, subtree: true });
  markCardSmart();
  installMenuItem();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();
