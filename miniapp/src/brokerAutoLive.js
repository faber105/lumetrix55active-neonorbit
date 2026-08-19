import { TG, TG_ID, apiFetch } from "./api";

let lastLivePositionId = null;
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

  timer = window.setTimeout(tick, 350);
  return () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    installed = false;
  };
}
