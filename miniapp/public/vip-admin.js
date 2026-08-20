(() => {
  const PANEL_ID = 'vip-admin-control-panel';
  const STYLE_ID = 'vip-admin-control-style';
  let busy = false;
  let pollTimer = null;

  const initData = () => {
    try {
      return window.Telegram?.WebApp?.initData || new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') || new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch {
      return '';
    }
  };

  const request = async (path, options = {}) => {
    const headers = { ...(options.headers || {}) };
    const tg = initData();
    if (tg) headers['X-Telegram-Init-Data'] = tg;
    const response = await fetch(path, { ...options, headers, cache: 'no-store' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.detail || body?.error || `HTTP ${response.status}`);
    return body;
  };

  const patch = (payload) => request('/api/admin/state', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  function addStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID}{margin-top:14px;padding:16px;display:grid;gap:14px}
      #${PANEL_ID} .vip-admin-head{display:flex;align-items:center;justify-content:space-between;gap:12px}
      #${PANEL_ID} .vip-admin-title{display:grid;gap:3px}
      #${PANEL_ID} .vip-admin-title small{opacity:.65;font-size:11px;letter-spacing:.08em}
      #${PANEL_ID} .vip-admin-title b{font-size:16px}
      #${PANEL_ID} .vip-toggle{width:48px;height:28px;border:0;border-radius:999px;background:#2a3040;padding:3px;display:flex;align-items:center;cursor:pointer}
      #${PANEL_ID} .vip-toggle span{width:22px;height:22px;border-radius:50%;background:white;display:block;transition:.18s;transform:translateX(0)}
      #${PANEL_ID} .vip-toggle.on{background:#7c83ff}
      #${PANEL_ID} .vip-toggle.on span{transform:translateX(20px)}
      #${PANEL_ID} .vip-frequency{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end}
      #${PANEL_ID} label{display:grid;gap:6px;font-size:12px;opacity:.9}
      #${PANEL_ID} select{width:100%;background:#111725;color:#fff;border:1px solid rgba(255,255,255,.11);border-radius:12px;padding:11px 12px;font-size:14px}
      #${PANEL_ID} .vip-save,#${PANEL_ID} .vip-now{border:0;border-radius:12px;padding:11px 14px;font-weight:700;cursor:pointer}
      #${PANEL_ID} .vip-save{background:#7c83ff;color:white}
      #${PANEL_ID} .vip-now{background:rgba(255,255,255,.08);color:white;width:100%}
      #${PANEL_ID} .vip-meta{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      #${PANEL_ID} .vip-meta div{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:10px;display:grid;gap:3px}
      #${PANEL_ID} .vip-meta small{opacity:.55;font-size:10px}
      #${PANEL_ID} .vip-meta b{font-size:12px;word-break:break-word}
      #${PANEL_ID} .vip-note{font-size:12px;opacity:.7;line-height:1.45}
      #${PANEL_ID} .vip-feedback{font-size:12px;min-height:16px}
      #${PANEL_ID} button:disabled,#${PANEL_ID} select:disabled{opacity:.5;cursor:default}
    `;
    document.head.appendChild(style);
  }

  function formatTime(value) {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function createPanel(host) {
    if (document.getElementById(PANEL_ID)) return document.getElementById(PANEL_ID);
    addStyle();
    const panel = document.createElement('section');
    panel.id = PANEL_ID;
    panel.className = 'glass';
    panel.innerHTML = `
      <div class="vip-admin-head">
        <div class="vip-admin-title"><small>VIP SIGNALS</small><b>VIP 5M сигналы и уведомления</b></div>
        <button type="button" class="vip-toggle" aria-label="Включить VIP сигналы"><span></span></button>
      </div>
      <div class="vip-frequency">
        <label>Частота проверки
          <select class="vip-interval">
            <option value="60">каждую 1 минуту</option>
            <option value="180">каждые 3 минуты</option>
            <option value="300">каждые 5 минут</option>
            <option value="600">каждые 10 минут</option>
            <option value="900">каждые 15 минут</option>
            <option value="1800">каждые 30 минут</option>
            <option value="3600">каждый час</option>
          </select>
        </label>
        <button type="button" class="vip-save">Сохранить</button>
      </div>
      <div class="vip-meta">
        <div><small>ПОСЛЕДНИЙ СТАТУС</small><b class="vip-status">—</b></div>
        <div><small>СЛЕДУЮЩАЯ ПРОВЕРКА</small><b class="vip-next">—</b></div>
      </div>
      <button type="button" class="vip-now">Проверить VIP сигнал сейчас</button>
      <div class="vip-feedback" aria-live="polite"></div>
      <div class="vip-note">VIP работает на 5m. Бот проверяет рынок с выбранной частотой, публикует только подтверждённый сигнал и отправляет Telegram-уведомление пользователям с включённым VIP.</div>
    `;
    host.insertAdjacentElement('afterend', panel);

    const toggle = panel.querySelector('.vip-toggle');
    const select = panel.querySelector('.vip-interval');
    const save = panel.querySelector('.vip-save');
    const now = panel.querySelector('.vip-now');
    const feedback = panel.querySelector('.vip-feedback');

    toggle.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      const next = !toggle.classList.contains('on');
      toggle.disabled = true;
      feedback.textContent = 'Сохраняю…';
      try {
        const state = await patch({ vip_enabled: next });
        render(panel, state);
        feedback.textContent = next ? 'VIP сигналы включены.' : 'VIP сигналы выключены.';
      } catch (e) {
        feedback.textContent = `Ошибка: ${e.message}`;
      } finally {
        busy = false;
        toggle.disabled = false;
      }
    });

    save.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      save.disabled = true;
      select.disabled = true;
      feedback.textContent = 'Сохраняю частоту…';
      try {
        const state = await patch({ vip_interval_seconds: Number(select.value) });
        render(panel, state);
        feedback.textContent = 'Частота VIP обновлена.';
      } catch (e) {
        feedback.textContent = `Ошибка: ${e.message}`;
      } finally {
        busy = false;
        save.disabled = false;
        select.disabled = false;
      }
    });

    now.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      now.disabled = true;
      feedback.textContent = 'Сканирую реальный VIP 5m рынок…';
      try {
        const result = await request('/api/home/vip-scan-now', { method: 'POST' });
        const state = await request('/api/admin/state');
        render(panel, state);
        const notified = Number(result?.notified || 0);
        feedback.textContent = result?.status === 'SIGNAL'
          ? `VIP сигнал найден. Уведомлений отправлено: ${notified}.`
          : (result?.status === 'DUPLICATE'
              ? 'Такой VIP сигнал уже опубликован для этой 5m свечи.'
              : 'Подтверждённого VIP входа пока нет. Следующая проверка запланирована.');
      } catch (e) {
        feedback.textContent = `Ошибка VIP: ${e.message}`;
      } finally {
        busy = false;
        now.disabled = false;
      }
    });

    return panel;
  }

  function render(panel, state) {
    if (!panel || !state) return;
    const toggle = panel.querySelector('.vip-toggle');
    const select = panel.querySelector('.vip-interval');
    toggle.classList.toggle('on', Boolean(state.vip_enabled));
    toggle.setAttribute('aria-pressed', state.vip_enabled ? 'true' : 'false');
    const seconds = String(Number(state.vip_interval_seconds || 300));
    if ([...select.options].some(o => o.value === seconds)) select.value = seconds;
    panel.querySelector('.vip-status').textContent = state.last_vip_status || 'ОЖИДАНИЕ';
    panel.querySelector('.vip-next').textContent = formatTime(state.next_vip_at);
  }

  async function refreshPanel() {
    const panel = document.getElementById(PANEL_ID);
    if (!panel || busy) return;
    try {
      render(panel, await request('/api/admin/state'));
    } catch {}
  }

  function ensurePanel() {
    const adminHost = document.querySelector('.admin-simple');
    if (!adminHost) {
      const stale = document.getElementById(PANEL_ID);
      if (stale) stale.remove();
      return;
    }
    const panel = createPanel(adminHost);
    refreshPanel();
    if (!pollTimer) pollTimer = setInterval(refreshPanel, 4000);
    return panel;
  }

  const observer = new MutationObserver(() => ensurePanel());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(ensurePanel, 0);
})();
