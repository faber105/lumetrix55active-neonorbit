(() => {
  const ID = 'ap-preload-next-control';
  const STYLE = 'ap-preload-next-style';
  let busy = false;
  let enabled = false;
  let lastState = null;
  let scheduled = false;

  const initData = () => {
    try {
      return window.Telegram?.WebApp?.initData || new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') || new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch { return ''; }
  };

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const tg = initData();
    if (tg) headers['X-Telegram-Init-Data'] = tg;
    const response = await fetch(path, { ...options, headers, cache: 'no-store' });
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
      #${ID} .ap-preload-row{display:flex;align-items:center;justify-content:space-between;gap:14px}
      #${ID} .ap-preload-copy{display:grid;gap:4px;min-width:0}
      #${ID} .ap-preload-copy b{font-size:12px;color:#f5f7ff}
      #${ID} .ap-preload-copy small{font-size:9px;line-height:1.45;color:#8f9bb8}
      #${ID} .ap-preload-switch{flex:0 0 50px;width:50px;height:29px;border:0;border-radius:999px;padding:3px;background:#2b3244;position:relative}
      #${ID} .ap-preload-switch span{display:block;width:23px;height:23px;border-radius:50%;background:#fff;transition:.18s;transform:translateX(0)}
      #${ID} .ap-preload-switch.on{background:linear-gradient(135deg,#5369f2,#8159f8)}
      #${ID} .ap-preload-switch.on span{transform:translateX(21px)}
      #${ID} .ap-preload-status{font-size:9px;color:#8d99b6;min-height:13px}
      #${ID} .ap-preload-status.ready{color:#6ee9bf}
      #${ID} .ap-preload-status.warn{color:#f5cb78}
      .session-kpis .ap-preload-kpi{border-color:rgba(117,104,255,.24)!important}
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
    if (mode === 'profit') return 'ON: за 2 минуты до закрытия текущей 5m сделки бот начинает анализ, подтверждает следующий сетап и готовит вход на следующую 5-минутку.';
    const tf = timeframe || 'выбранной';
    return `ON: пока текущая ${tf} сделка ещё открыта, бот заранее анализирует следующий вход и готовит его к следующей границе свечи.`;
  }

  function render(panel) {
    if (!panel) return;
    const sw = panel.querySelector('.ap-preload-switch');
    const copy = panel.querySelector('.ap-preload-copy small');
    const status = panel.querySelector('.ap-preload-status');
    sw?.classList.toggle('on', enabled);
    sw?.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    if (copy) copy.textContent = description();
    const c = lastState?.candidate;
    if (!enabled) {
      status.className = 'ap-preload-status';
      status.textContent = 'OFF · обычный цикл: закрытие → новый анализ';
    } else if (c?.status === 'PREPARED') {
      status.className = 'ap-preload-status ready';
      status.textContent = `Следующий вход подготовлен${c.entry_time ? ` · ${new Date(c.entry_time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}` : ''}`;
    } else if (c?.status === 'WAIT_CLOSE') {
      status.className = 'ap-preload-status warn';
      status.textContent = 'Сетап готов · жду результат текущей сделки';
    } else if (c?.status === 'SEARCHING') {
      status.className = 'ap-preload-status warn';
      status.textContent = 'Ранний анализ следующего входа активен';
    } else {
      status.className = 'ap-preload-status';
      status.textContent = 'ON · ранний анализ включён';
    }
  }

  async function load() {
    try {
      lastState = await request('/api/auto-preload/state');
      enabled = Boolean(lastState?.enabled);
      render(document.getElementById(ID));
      enhanceActiveKpi();
    } catch {}
  }

  async function toggle(panel) {
    if (busy) return;
    busy = true;
    const sw = panel.querySelector('.ap-preload-switch');
    if (sw) sw.disabled = true;
    try {
      lastState = await request('/api/auto-preload/state', {
        method: 'PATCH',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ enabled: !enabled }),
      });
      enabled = Boolean(lastState?.enabled);
      window.Telegram?.WebApp?.HapticFeedback?.impactOccurred?.('light');
      render(panel);
    } catch (e) {
      const status = panel.querySelector('.ap-preload-status');
      if (status) status.textContent = `Ошибка: ${e.message}`;
    } finally {
      busy = false;
      if (sw) sw.disabled = false;
    }
  }

  function ensureControl() {
    addStyle();
    const builder = document.querySelector('.session-builder');
    if (!builder) {
      document.getElementById(ID)?.remove();
      enhanceActiveKpi();
      return;
    }
    let panel = document.getElementById(ID);
    if (!panel) {
      panel = document.createElement('div');
      panel.id = ID;
      panel.innerHTML = `
        <div class="ap-preload-row">
          <div class="ap-preload-copy"><b>Заранее готовить следующий вход</b><small></small></div>
          <button type="button" class="ap-preload-switch" aria-label="Ранний анализ следующего входа"><span></span></button>
        </div>
        <div class="ap-preload-status"></div>`;
      const modeBlock = builder.firstElementChild;
      if (modeBlock?.nextSibling) builder.insertBefore(panel, modeBlock.nextSibling); else builder.prepend(panel);
      panel.querySelector('.ap-preload-switch')?.addEventListener('click', () => toggle(panel));
    }
    render(panel);
  }

  function enhanceActiveKpi() {
    const kpis = document.querySelector('.live-session .session-kpis');
    if (!kpis) return;
    let node = kpis.querySelector('.ap-preload-kpi');
    if (!node) {
      node = document.createElement('div');
      node.className = 'ap-preload-kpi';
      node.innerHTML = '<small>Ранний следующий вход</small><b>—</b>';
      kpis.appendChild(node);
    }
    const b = node.querySelector('b');
    if (b) b.textContent = enabled ? 'ON' : 'OFF';
  }

  function scheduleEnsure() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      ensureControl();
      render(document.getElementById(ID));
      enhanceActiveKpi();
    });
  }

  const rootObserver = new MutationObserver(scheduleEnsure);
  const start = () => {
    const root = document.getElementById('root');
    if (root) rootObserver.observe(root, { childList: true, subtree: true });
    scheduleEnsure();
    load();
    setInterval(load, 4000);
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
