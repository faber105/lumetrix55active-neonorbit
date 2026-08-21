(() => {
  const ID = 'ap-preload-next-control';
  const ACTIVE_ID = 'ap-preload-active-control';
  const STYLE = 'ap-preload-next-style';
  const STORE = 'ap_preload_enabled_desired';
  const nativeFetch = window.fetch.bind(window);
  let enabled = false;
  let lastState = null;
  let busy = false;
  let syncing = false;
  let desired = null;

  try {
    const saved = localStorage.getItem(STORE);
    if (saved === '1' || saved === '0') desired = saved === '1';
  } catch {}

  const initData = () => {
    try {
      return window.Telegram?.WebApp?.initData ||
        new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') ||
        new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch { return ''; }
  };

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const tg = initData();
    if (tg) headers['X-Telegram-Init-Data'] = tg;
    const response = await nativeFetch(path, { ...options, headers, cache: 'no-store' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.detail || body?.error || `HTTP ${response.status}`);
    return body;
  }

  function addStyle() {
    if (document.getElementById(STYLE)) return;
    const style = document.createElement('style');
    style.id = STYLE;
    style.textContent = `
      #${ID}{display:grid;gap:10px;padding:13px 14px;border:1px solid rgba(117,104,255,.22);border-radius:17px;background:rgba(117,104,255,.055);margin:4px 0 2px}
      #${ID} .ap-preload-row,#${ACTIVE_ID}{display:flex;align-items:center;justify-content:space-between;gap:14px}
      #${ID} .ap-preload-copy,#${ACTIVE_ID} .ap-preload-copy{display:grid;gap:4px;min-width:0}
      #${ID} .ap-preload-copy b,#${ACTIVE_ID} .ap-preload-copy b{font-size:12px;color:#f5f7ff}
      #${ID} .ap-preload-copy small,#${ACTIVE_ID} .ap-preload-copy small{font-size:9px;line-height:1.45;color:#8f9bb8}
      .ap-preload-switch{flex:0 0 50px;width:50px;height:29px;border:0;border-radius:999px;padding:3px;background:#2b3244;position:relative}
      .ap-preload-switch span{display:block;width:23px;height:23px;border-radius:50%;background:#fff;transition:.18s;transform:translateX(0)}
      .ap-preload-switch.on{background:linear-gradient(135deg,#5369f2,#8159f8)}
      .ap-preload-switch.on span{transform:translateX(21px)}
      .ap-preload-switch:disabled{opacity:.65}
      #${ID} .ap-preload-status{font-size:9px;color:#8d99b6;min-height:13px}
      #${ID} .ap-preload-status.ready{color:#6ee9bf}
      #${ID} .ap-preload-status.warn{color:#f5cb78}
      #${ACTIVE_ID}{grid-column:1/-1;padding:10px 12px;border:1px solid rgba(117,104,255,.24);border-radius:13px;background:rgba(117,104,255,.05)}
    `;
    document.head.appendChild(style);
  }

  function modeInfo() {
    const builder = document.querySelector('.session-builder');
    if (!builder) return { mode: lastState?.mode || null, timeframe: lastState?.timeframe || null };
    const buttons = [...builder.querySelectorAll('.segmented button')];
    const activeMode = buttons.find(b => b.classList.contains('active') && /WIN|профит/i.test(b.textContent || ''));
    const mode = /профит/i.test(activeMode?.textContent || '') ? 'profit' : 'count';
    const tfButton = buttons.find(b => b.classList.contains('active') && /^(15s|1m|3m)$/i.test((b.textContent || '').trim()));
    return { mode, timeframe: tfButton?.textContent?.trim() || (mode === 'profit' ? '5m' : lastState?.timeframe) };
  }

  function description() {
    const { mode, timeframe } = modeInfo();
    if (mode === 'profit') return 'ON: за 2 минуты до закрытия активной 5m сделки начинается поиск и подготовка следующего входа.';
    return `ON: следующий ${timeframe || 'AUTO'} вход анализируется заранее до закрытия текущей сделки.`;
  }

  function statusText() {
    const c = lastState?.candidate;
    if (!enabled) return ['OFF · ранний поиск не запущен', ''];
    if (c?.status === 'PREPARED') return ['Следующий вход подготовлен', 'ready'];
    if (c?.status === 'WAIT_CLOSE') return ['Сетап готов · жду закрытие текущей сделки', 'warn'];
    if (c?.status === 'SEARCHING') return ['Ранний анализ следующего входа активен', 'warn'];
    return ['ON · сервер подтвердил ранний поиск', 'ready'];
  }

  function renderAll() {
    const [text, tone] = statusText();
    const panel = document.getElementById(ID);
    if (panel) {
      const sw = panel.querySelector('.ap-preload-switch');
      sw?.classList.toggle('on', enabled);
      sw?.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      if (sw) sw.disabled = busy;
      const copy = panel.querySelector('.ap-preload-copy small');
      if (copy) copy.textContent = description();
      const status = panel.querySelector('.ap-preload-status');
      if (status) {
        status.className = `ap-preload-status ${tone}`.trim();
        status.textContent = text;
      }
    }
    const active = document.getElementById(ACTIVE_ID);
    if (active) {
      const sw = active.querySelector('.ap-preload-switch');
      sw?.classList.toggle('on', enabled);
      sw?.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      if (sw) sw.disabled = busy;
      const b = active.querySelector('.ap-preload-copy b');
      const small = active.querySelector('.ap-preload-copy small');
      if (b) b.textContent = enabled ? 'Ранний поиск: ON' : 'Ранний поиск: OFF';
      if (small) small.textContent = text;
    }
  }

  async function patch(value, { remember = true } = {}) {
    if (busy) return false;
    busy = true;
    renderAll();
    try {
      const next = await request('/api/auto-preload/state', {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: Boolean(value) }),
      });
      lastState = next;
      enabled = Boolean(next?.enabled);
      desired = enabled;
      if (remember) {
        try { localStorage.setItem(STORE, enabled ? '1' : '0'); } catch {}
      }
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.('success');
      return true;
    } catch (e) {
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.('error');
      const status = document.querySelector(`#${ID} .ap-preload-status`);
      if (status) status.textContent = `Ошибка синхронизации: ${e.message}`;
      return false;
    } finally {
      busy = false;
      renderAll();
    }
  }

  async function toggle() {
    await patch(!enabled);
  }

  async function syncDesiredBeforeStart() {
    if (syncing || desired === null || desired === enabled) return;
    syncing = true;
    try { await patch(desired, { remember: false }); }
    finally { syncing = false; }
  }

  async function load() {
    try {
      const state = await request(`/api/auto-preload/state?_=${Date.now()}`);
      lastState = state;
      enabled = Boolean(state?.enabled);
      if (desired === null) {
        desired = enabled;
        try { localStorage.setItem(STORE, enabled ? '1' : '0'); } catch {}
      } else if (desired !== enabled && !busy) {
        await syncDesiredBeforeStart();
      }
      renderAll();
    } catch {}
  }

  function makeSwitch() {
    const sw = document.createElement('button');
    sw.type = 'button';
    sw.className = 'ap-preload-switch';
    sw.setAttribute('aria-label', 'Ранний поиск следующего входа');
    sw.innerHTML = '<span></span>';
    sw.addEventListener('click', toggle);
    return sw;
  }

  function ensureBuilderControl() {
    const builder = document.querySelector('.session-builder');
    if (!builder) {
      document.getElementById(ID)?.remove();
      return;
    }
    let panel = document.getElementById(ID);
    if (!panel) {
      panel = document.createElement('div');
      panel.id = ID;
      const row = document.createElement('div');
      row.className = 'ap-preload-row';
      const copy = document.createElement('div');
      copy.className = 'ap-preload-copy';
      copy.innerHTML = '<b>Заранее готовить следующий вход</b><small></small>';
      row.append(copy, makeSwitch());
      const status = document.createElement('div');
      status.className = 'ap-preload-status';
      panel.append(row, status);
      const modeBlock = builder.firstElementChild;
      if (modeBlock?.nextSibling) builder.insertBefore(panel, modeBlock.nextSibling);
      else builder.prepend(panel);
    }
  }

  function ensureActiveControl() {
    const kpis = document.querySelector('.live-session .session-kpis');
    if (!kpis) {
      document.getElementById(ACTIVE_ID)?.remove();
      return;
    }
    let node = document.getElementById(ACTIVE_ID);
    if (!node) {
      node = document.createElement('div');
      node.id = ACTIVE_ID;
      const copy = document.createElement('div');
      copy.className = 'ap-preload-copy';
      copy.innerHTML = '<b>Ранний поиск</b><small></small>';
      node.append(copy, makeSwitch());
      kpis.appendChild(node);
    }
  }

  function ensureControls() {
    addStyle();
    ensureBuilderControl();
    ensureActiveControl();
    renderAll();
  }

  // Guarantee that the desired slider state is on the server before a new AUTO
  // session starts. This prevents a visually ON control from starting with
  // auto_preload_config.enabled=false.
  window.fetch = async (input, init = {}) => {
    try {
      const method = String(init?.method || 'GET').toUpperCase();
      const raw = typeof input === 'string' ? input : input?.url;
      if (method === 'POST' && raw && raw.includes('/api/auto/start')) {
        await syncDesiredBeforeStart();
      }
    } catch {}
    return nativeFetch(input, init);
  };

  const start = () => {
    ensureControls();
    load();
    // Deliberately no MutationObserver: React journal/timer updates should not
    // trigger DOM-wide callbacks and freeze the Mini App.
    setInterval(ensureControls, 800);
    setInterval(load, 2500);
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
