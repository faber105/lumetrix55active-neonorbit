(() => {
  const nativeFetch = window.fetch.bind(window);

  // The persistent OCI worker is the only AUTO driver. The Mini App must never
  // drive trading/scanning itself and must never request a broker balance refresh
  // while opening the AUTO screen. UI reads are always lightweight snapshots.
  window.fetch = (input, init = {}) => {
    try {
      const method = String(init?.method || "GET").toUpperCase();
      const raw = typeof input === "string" ? input : input?.url;
      if (method === "GET" && raw && raw.includes("/api/auto/state")) {
        const url = new URL(raw, window.location.origin);
        url.searchParams.set("drive", "false");
        url.searchParams.delete("refresh");
        const next = raw.startsWith("http://") || raw.startsWith("https://")
          ? url.toString()
          : `${url.pathname}${url.search}${url.hash}`;
        return nativeFetch(next, { ...init, cache: "no-store" });
      }
      if (method === "POST" && raw && raw.includes("/api/auto/tick")) {
        return Promise.resolve(new Response(JSON.stringify({ status: "WORKER_DRIVEN" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }));
      }
    } catch (_) {}
    return nativeFetch(input, init);
  };
})();
