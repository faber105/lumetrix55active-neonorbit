(() => {
  const nativeFetch = window.fetch.bind(window);
  let stopped = false;
  let running = false;
  let timer = null;

  // The React dashboard polls /api/auto/state frequently. That endpoint used to
  // drive the full market scanner by default, so every UI refresh could block on
  // dozens of OTC analyses. Force dashboard reads to be read-only; trading ticks
  // run independently below.
  window.fetch = (input, init = {}) => {
    try {
      const method = String(init?.method || "GET").toUpperCase();
      const raw = typeof input === "string" ? input : input?.url;
      if (method === "GET" && raw && raw.includes("/api/auto/state")) {
        const url = new URL(raw, window.location.origin);
        if (!url.searchParams.has("drive")) url.searchParams.set("drive", "false");
        const next = raw.startsWith("http://") || raw.startsWith("https://") ? url.toString() : `${url.pathname}${url.search}${url.hash}`;
        return nativeFetch(next, init);
      }
    } catch (_) {}
    return nativeFetch(input, init);
  };

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
      await nativeFetch("/api/auto/tick", {
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
      // A failed trading tick is retried, while dashboard reads remain responsive.
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
