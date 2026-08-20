(() => {
  const MODAL_ID = 'ap-session-report';
  const STYLE_ID = 'ap-session-report-style';
  let activeId = null;

  const initData = () => {
    try {
      return window.Telegram?.WebApp?.initData || new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') || new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch { return ''; }
  };

  async function request(path) {
    const headers = {};
    const tg = initData();
    if (tg) headers['X-Telegram-Init-Data'] = tg;
    const response = await fetch(path, { headers, cache: 'no-store' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.detail || body?.error || `HTTP ${response.status}`);
    return body;
  }

  const num = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  const money = v => Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '—';
  const signed = v => Number.isFinite(Number(v)) ? `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}` : '—';
  const local = v => {
    if (!v) return '—';
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' });
  };
  const esc = v => String(v ?? '—').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));

  function addStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .session-history article[data-session-enhanced="1"]{cursor:pointer;position:relative;padding-right:34px!important;transition:.16s transform,.16s border-color}
      .session-history article[data-session-enhanced="1"]:active{transform:scale(.985)}
      .session-history article[data-session-enhanced="1"]::after{content:'›';position:absolute;right:12px;top:50%;transform:translateY(-50%);font-size:27px;opacity:.5}
      .session-history article[data-session-enhanced="1"] .ap-more{display:block;margin-top:5px;font-size:11px;opacity:.62}
      #${MODAL_ID}{position:fixed;inset:0;z-index:99999;background:#070a12;overflow:auto;-webkit-overflow-scrolling:touch;padding:calc(14px + var(--ap-safe-top,0px)) 14px calc(95px + var(--ap-safe-bottom,0px));color:#f5f7ff}
      #${MODAL_ID} .ap-report-head{position:sticky;top:0;z-index:2;background:rgba(7,10,18,.94);backdrop-filter:blur(18px);display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0 14px}
      #${MODAL_ID} .ap-back{border:0;border-radius:13px;background:rgba(255,255,255,.08);color:white;padding:10px 13px;font-weight:750}
      #${MODAL_ID} .ap-title{flex:1;min-width:0} #${MODAL_ID} .ap-title small{display:block;opacity:.55;font-size:10px;letter-spacing:.08em} #${MODAL_ID} .ap-title b{display:block;font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #${MODAL_ID} .ap-status{border-radius:999px;padding:7px 9px;background:rgba(124,131,255,.15);font-size:10px;font-weight:800}
      #${MODAL_ID} .ap-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:10px 0}
      #${MODAL_ID} .ap-card{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.075);border-radius:15px;padding:12px;min-width:0}
      #${MODAL_ID} .ap-card small{display:block;opacity:.55;font-size:9px;letter-spacing:.06em;margin-bottom:5px} #${MODAL_ID} .ap-card b{font-size:15px;word-break:break-word}
      #${MODAL_ID} .ap-positive{color:#2fe2a2} #${MODAL_ID} .ap-negative{color:#ff6f87}
      #${MODAL_ID} .ap-section{margin-top:18px} #${MODAL_ID} .ap-section h3{font-size:15px;margin:0 0 9px}
      #${MODAL_ID} .ap-leg{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.065);border-radius:15px;padding:12px;margin-bottom:9px}
      #${MODAL_ID} .ap-leg-top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px} #${MODAL_ID} .ap-leg-top b{font-size:14px} #${MODAL_ID} .ap-result{font-size:11px;font-weight:850;border-radius:999px;padding:5px 8px;background:rgba(255,255,255,.07)}
      #${MODAL_ID} .ap-leg-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px} #${MODAL_ID} .ap-leg-grid div{background:rgba(255,255,255,.035);border-radius:10px;padding:8px} #${MODAL_ID} .ap-leg-grid small{display:block;opacity:.5;font-size:9px} #${MODAL_ID} .ap-leg-grid b{font-size:12px}
      #${MODAL_ID} .ap-event{display:grid;grid-template-columns:72px 74px 1fr;gap:8px;align-items:start;padding:9px 0;border-bottom:1px solid rgba(255,255,255,.055)} #${MODAL_ID} .ap-event time{font-size:10px;opacity:.5} #${MODAL_ID} .ap-event i{font-style:normal;font-size:9px;font-weight:800;opacity:.75} #${MODAL_ID} .ap-event p{margin:0;font-size:12px;line-height:1.35}
      #${MODAL_ID} .ap-loading,#${MODAL_ID} .ap-error{padding:28px 8px;text-align:center;opacity:.72}
      @media (min-width:700px){#${MODAL_ID} .ap-grid{grid-template-columns:repeat(4,minmax(0,1fr))}#${MODAL_ID}{max-width:780px;left:50%;right:auto;width:100%;transform:translateX(-50%)}}
    `;
    document.head.appendChild(style);
  }

  function closeReport() {
    document.getElementById(MODAL_ID)?.remove();
    activeId = null;
  }

  function card(label, value, cls='') {
    return `<div class="ap-card"><small>${esc(label)}</small><b class="${cls}">${esc(value)}</b></div>`;
  }

  function resultClass(result) {
    const r = String(result || '').toUpperCase();
    return r === 'WIN' ? 'ap-positive' : r === 'LOSS' ? 'ap-negative' : '';
  }

  function renderReport(root, data) {
    const s = data?.session || {};
    const m = data?.metrics || {};
    const legs = Array.isArray(data?.legs) ? data.legs : [];
    const events = Array.isArray(data?.events) ? data.events : [];
    const balanceAfter = m.end_balance ?? s.current_balance;
    const balanceBefore = m.start_balance ?? s.start_balance;
    const change = m.balance_change ?? (balanceBefore != null && balanceAfter != null ? num(balanceAfter) - num(balanceBefore) : null);
    const pnlClass = num(m.net_profit ?? s.profit) >= 0 ? 'ap-positive' : 'ap-negative';
    const changeClass = num(change) >= 0 ? 'ap-positive' : 'ap-negative';

    root.innerHTML = `
      <div class="ap-report-head">
        <button type="button" class="ap-back">‹ Назад</button>
        <div class="ap-title"><small>AUTO SESSION REPORT</small><b>Сессия #${esc(s.id)}</b></div>
        <span class="ap-status">${esc(s.status)}</span>
      </div>
      <div class="ap-grid">
        ${card('Баланс до сессии', money(balanceBefore))}
        ${card('Баланс после сессии', money(balanceAfter))}
        ${card('Изменение баланса', change == null ? '—' : signed(change), changeClass)}
        ${card('P/L сессии', signed(m.net_profit ?? s.profit), pnlClass)}
      </div>
      <div class="ap-grid">
        ${card('WIN', m.wins ?? 0, 'ap-positive')}
        ${card('LOSS', m.losses ?? 0, 'ap-negative')}
        ${card('DRAW', m.draws ?? 0)}
        ${card('Winrate', m.winrate == null ? '—' : `${m.winrate}%`)}
        ${card('Всего ставок', m.closed ?? s.total_legs ?? legs.length)}
        ${card('Сумма всех ставок', money(m.total_staked))}
        ${card('Перекрытий', m.covered_trades ?? 0)}
        ${card('Минус-серий', s.failed_series ?? 0)}
        ${card('Стартовая ставка', money(s.base_amount))}
        ${card('Макс. перекрытий', s.max_martingale ?? 0)}
        ${card('Стратегия', s.strategy || '—')}
        ${card('Таймфрейм', s.timeframe || '—')}
        ${card('Режим', s.mode === 'count' ? 'По WIN' : 'До профита')}
        ${card('Цель WIN', s.target_wins ?? '—')}
        ${card('Цель профита', s.target_profit != null ? money(s.target_profit) : '—')}
        ${card('Причина завершения', s.stop_reason || s.status || '—')}
      </div>
      <div class="ap-grid">
        ${card('Начало', local(s.created_at))}
        ${card('Завершение', local(s.ended_at || s.updated_at))}
        ${card('Валовая прибыль', `+${money(m.gross_wins)}`, 'ap-positive')}
        ${card('Валовый убыток', `-${money(m.gross_losses)}`, 'ap-negative')}
      </div>
      <section class="ap-section"><h3>Все сделки сессии (${legs.length})</h3>
        ${legs.length ? legs.map((leg, i) => `
          <article class="ap-leg">
            <div class="ap-leg-top"><b>#${i+1} · ${esc(leg.pair || leg.asset)}</b><span class="ap-result ${resultClass(leg.result)}">${esc(leg.result)}</span></div>
            <div class="ap-leg-grid">
              <div><small>Направление</small><b>${esc(leg.direction)}</b></div>
              <div><small>Ставка</small><b>${money(leg.amount)}</b></div>
              <div><small>Payout</small><b>${leg.payout == null ? '—' : `${money(leg.payout)}%`}</b></div>
              <div><small>P/L</small><b class="${num(leg.pnl)>=0?'ap-positive':'ap-negative'}">${signed(leg.pnl)}</b></div>
              <div><small>Уровень перекрытия</small><b>${esc(leg.martingale_level ?? 0)}</b></div>
              <div><small>Серия</small><b>${esc(leg.series_no ?? '—')}</b></div>
              <div><small>Открыта</small><b>${local(leg.opened_at || leg.created_at)}</b></div>
              <div><small>Закрыта</small><b>${local(leg.closed_at)}</b></div>
            </div>
          </article>`).join('') : '<div class="ap-card">Сделок в этой сессии нет.</div>'}
      </section>
      <section class="ap-section"><h3>Полная хронология работы бота (${events.length})</h3>
        ${events.length ? events.map(ev => `<div class="ap-event"><time>${esc(local(ev.created_at).split(', ').pop())}</time><i>${esc(ev.stage)}</i><p>${esc(ev.message)}</p></div>`).join('') : '<div class="ap-card">Событий нет.</div>'}
      </section>
    `;
    root.querySelector('.ap-back')?.addEventListener('click', closeReport);
  }

  async function openReport(sessionId) {
    if (!sessionId || activeId === sessionId) return;
    activeId = sessionId;
    addStyle();
    let root = document.getElementById(MODAL_ID);
    if (!root) {
      root = document.createElement('div');
      root.id = MODAL_ID;
      document.body.appendChild(root);
    }
    root.innerHTML = `<div class="ap-report-head"><button type="button" class="ap-back">‹ Назад</button><div class="ap-title"><small>AUTO SESSION REPORT</small><b>Сессия #${esc(sessionId)}</b></div></div><div class="ap-loading">Загружаю полный отчёт сессии…</div>`;
    root.querySelector('.ap-back')?.addEventListener('click', closeReport);
    try {
      const data = await request(`/api/auto/history/${encodeURIComponent(sessionId)}?_=${Date.now()}`);
      renderReport(root, data);
    } catch (e) {
      root.innerHTML = `<div class="ap-report-head"><button type="button" class="ap-back">‹ Назад</button><div class="ap-title"><small>AUTO SESSION REPORT</small><b>Сессия #${esc(sessionId)}</b></div></div><div class="ap-error">Не удалось открыть отчёт: ${esc(e.message)}</div>`;
      root.querySelector('.ap-back')?.addEventListener('click', closeReport);
    }
  }

  function enhanceHistory() {
    addStyle();
    document.querySelectorAll('.session-history article').forEach(article => {
      if (article.dataset.sessionEnhanced === '1') return;
      const text = article.textContent || '';
      const match = text.match(/#(\d+)/);
      if (!match) return;
      const id = match[1];
      article.dataset.sessionEnhanced = '1';
      article.setAttribute('role', 'button');
      article.setAttribute('tabindex', '0');
      article.title = 'Открыть полный отчёт сессии';
      const main = article.querySelector('div');
      if (main && !main.querySelector('.ap-more')) {
        const hint = document.createElement('small');
        hint.className = 'ap-more';
        hint.textContent = 'Нажми, чтобы посмотреть полный отчёт →';
        main.appendChild(hint);
      }
      article.addEventListener('click', () => openReport(id));
      article.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openReport(id); } });
    });
  }

  const observer = new MutationObserver(enhanceHistory);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeReport(); });
  setTimeout(enhanceHistory, 0);
})();
