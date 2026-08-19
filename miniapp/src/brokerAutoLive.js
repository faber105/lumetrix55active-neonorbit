import { TG, TG_ID, apiFetch } from "./api";

let lastBrokerPositionId = null;
let installed = false;

export function installBrokerAutoLive() {
  if (installed || !TG_ID) return;
  installed = true;
  let stopped = false;
  let timer = null;

  const openLiveTab = () => {
    const button = Array.from(document.querySelectorAll(".bottom-nav button"))
      .find((node) => node.textContent?.includes("Live"));
    button?.click();
  };

  const tick = async () => {
    try {
      const rows = await apiFetch("/api/live/active");
      const brokerPosition = Array.isArray(rows)
        ? rows.find((position) => position.status === "OPEN" && position.source === "broker")
        : null;
      if (brokerPosition && brokerPosition.id !== lastBrokerPositionId) {
        lastBrokerPositionId = brokerPosition.id;
        openLiveTab();
        TG?.HapticFeedback?.notificationOccurred?.("success");
      }
    } catch {
      // Network/auth failures are transient; the next poll retries automatically.
    }
    if (!stopped) timer = window.setTimeout(tick, 1400);
  };

  timer = window.setTimeout(tick, 700);
  return () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    installed = false;
  };
}
