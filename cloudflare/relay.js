const CACHE_TTL = new Map([
  ["/api/admin/state", 30000],
  ["/api/auto/state", 1800],
  ["/api/auto-preload/state", 3000],
]);

function cacheBase(pathname) {
  for (const key of CACHE_TTL.keys()) {
    if (pathname === key) return key;
  }
  return null;
}

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function cacheOwner(request) {
  const initData = request.headers.get("X-Telegram-Init-Data") || "";
  if (!initData) return "anon";
  try {
    const raw = new URLSearchParams(initData).get("user") || "";
    const user = JSON.parse(raw);
    if (user?.id) return String(user.id);
  } catch {}
  return "tg";
}

export class AlphaPulseRelay {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.bridge = null;
    this.pending = new Map();
    this.cache = new Map();
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/__bridge/connect") return this.acceptBridge(request);
    if (url.pathname === "/__bridge/health") {
      return jsonResponse({ bridge: this.bridge ? "ONLINE" : "OFFLINE", pending: this.pending.size });
    }
    if (url.pathname.startsWith("/api/")) return this.forwardApi(request, url);
    return jsonResponse({ error: "not found" }, 404);
  }

  acceptBridge(request) {
    if (!this.env.BRIDGE_SECRET) return jsonResponse({ error: "BRIDGE_SECRET missing" }, 503);
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return jsonResponse({ error: "websocket required" }, 426);
    }
    const auth = request.headers.get("Authorization") || "";
    if (auth !== `Bearer ${this.env.BRIDGE_SECRET}`) return jsonResponse({ error: "unauthorized" }, 401);

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    if (this.bridge) {
      try { this.bridge.close(1012, "replaced"); } catch {}
    }
    this.bridge = server;
    server.addEventListener("message", (event) => this.onBridgeMessage(event));
    server.addEventListener("close", () => { if (this.bridge === server) this.bridge = null; });
    server.addEventListener("error", () => { if (this.bridge === server) this.bridge = null; });
    server.send(JSON.stringify({ type: "ready", at: Date.now() }));
    return new Response(null, { status: 101, webSocket: client });
  }

  onBridgeMessage(event) {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message?.type !== "response" || !message.id) return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    pending.resolve(message);
  }

  async readCache(cacheKey) {
    if (!cacheKey) return null;
    const memory = this.cache.get(cacheKey);
    if (memory) return memory;
    try {
      const stored = await this.ctx.storage.get(cacheKey);
      if (stored) {
        this.cache.set(cacheKey, stored);
        return stored;
      }
    } catch {}
    return null;
  }

  async writeCache(cacheKey, value) {
    if (!cacheKey) return;
    this.cache.set(cacheKey, value);
    try { await this.ctx.storage.put(cacheKey, value); } catch {}
  }

  async forwardApi(request, url) {
    const base = cacheBase(url.pathname);
    const cacheKey = base ? `cache:${base}:${cacheOwner(request)}` : null;
    const ttl = base ? CACHE_TTL.get(base) : 0;
    const cached = request.method === "GET" && cacheKey ? await this.readCache(cacheKey) : null;
    if (cached && Date.now() - Number(cached.at || 0) <= ttl) {
      return new Response(cached.body, { status: cached.status, headers: cached.headers });
    }

    const bridge = this.bridge;
    if (!bridge || bridge.readyState !== 1) {
      if (cached) return new Response(cached.body, { status: cached.status, headers: cached.headers });
      return jsonResponse({ detail: "Windows worker bridge offline" }, 503);
    }

    const body = request.method === "GET" || request.method === "HEAD" ? "" : await request.text();
    const headers = {};
    for (const name of ["content-type", "x-telegram-init-data", "x-idempotency-key"]) {
      const value = request.headers.get(name);
      if (value) headers[name] = value;
    }
    const id = crypto.randomUUID();
    const payload = {
      type: "request",
      id,
      method: request.method,
      path: `${url.pathname}${url.search}`,
      headers,
      body,
    };

    const result = await new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        if (cached) {
          resolve({ ...cached, stale: true });
          return;
        }
        resolve({ status: 504, headers: { "content-type": "application/json" }, body: JSON.stringify({ detail: "Windows worker timeout" }) });
      }, 18000);
      this.pending.set(id, { resolve, timer });
      try { bridge.send(JSON.stringify(payload)); }
      catch {
        clearTimeout(timer);
        this.pending.delete(id);
        if (cached) {
          resolve({ ...cached, stale: true });
          return;
        }
        resolve({ status: 503, headers: { "content-type": "application/json" }, body: JSON.stringify({ detail: "Windows worker bridge offline" }) });
      }
    });

    const responseHeaders = new Headers(result.headers || {});
    responseHeaders.set("cache-control", "no-store");
    responseHeaders.delete("content-length");
    if (result.stale) responseHeaders.set("x-alphapulse-cache", "stale");
    const responseBody = result.body || "";
    const status = Number(result.status || 502);
    if (request.method === "GET" && cacheKey && status >= 200 && status < 300 && !result.stale) {
      await this.writeCache(cacheKey, { at: Date.now(), status, headers: [...responseHeaders.entries()], body: responseBody });
    }
    return new Response(responseBody, { status, headers: responseHeaders });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/__bridge/")) {
      const id = env.RELAY.idFromName("alphapulse");
      return env.RELAY.get(id).fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
};
