(() => {
  let stopped = false;
  let running = false;
  let timer = null;

  function getInitData() {
    const tg = window.Telegram?.WebApp;
    if (tg?.initData) return tg.initData;
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("tgWebAppData");
    if (hash) return hash;
    return new URLSearchParams(window.location.search).get("tgWebAppData") || "";
  }

  function schedule(ms) {
    if (timer) clearTimeout(timer);
    if (!stopped) timer = setTimeout(runTick, ms);
  }

  async function runTick() {
    if (stopped || running) return;
    const initData = getInitData();
    if (!initData) {
      schedule(1500);
      return;
    }

    running = true;
    try {
      await fetch("/api/auto/tick", {
        method: "POST",
        headers: {
          "X-Telegram-Init-Data": initData,
          "Content-Type": "application/json",
        },
        body: "{}",
        cache: "no-store",
        credentials: "same-origin",
      });
    } catch (_) {
      // State polling stays independent; a failed tick is retried on the next cycle.
    } finally {
      running = false;
      schedule(document.hidden ? 1800 : 850);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule(50);
  });
  window.addEventListener("pagehide", () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  }, { once: true });

  schedule(250);
})();
