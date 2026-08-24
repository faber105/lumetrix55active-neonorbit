(() => {
  const nativeFetch = window.fetch.bind(window);
  let preloadEnabled = false;
  let inFlight = false;
  let stopped = false;
  let timer = null;

  function initData() {
    try {
      return window.Telegram?.WebApp?.initData ||
        new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') ||
        new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch { return ''; }
  }

  function renderPreload() {
    const node = document.querySelector('.ap-preload');
    if (!node) return;
    const badge = node.querySelector('strong b');
    const small = node.querySelector('small');
    if (badge && badge.textContent !== (preloadEnabled ? 'ON' : 'OFF')) {
      badge.textContent = preloadEnabled ? 'ON' : 'OFF';
    }
    const text = preloadEnabled ? 'Следующий вход анализируется параллельно' : 'Ранний поиск выключен';
    if (small && small.textContent !== text) small.textContent = text;
    node.classList.toggle('enabled', preloadEnabled);
    node.classList.toggle('disabled', !preloadEnabled);
    const visual = node.querySelector('i');
    if (visual) visual.style.opacity = preloadEnabled ? '1' : '.45';
  }

  async function poll() {
    if (stopped) return;
    if (document.hidden || inFlight || !document.querySelector('.ap-preload')) {
      timer = setTimeout(poll, 900);
      return;
    }
    const tg = initData();
    if (!tg) {
      timer = setTimeout(poll, 900);
      return;
    }
    inFlight = true;
    try {
      const response = await nativeFetch(`/api/auto/state?drive=false&_=${Date.now()}`, {
        headers: { 'X-Telegram-Init-Data': tg },
        cache: 'no-store',
      });
      if (response.ok) {
        const state = await response.json();
        preloadEnabled = Boolean(state?.preload_enabled);
        renderPreload();
      }
    } catch (_) {
    } finally {
      inFlight = false;
      timer = setTimeout(poll, 900);
    }
  }

  function start() {
    renderPreload();
    timer = setTimeout(poll, 250);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();

  window.addEventListener('pagehide', () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  }, { once: true });
})();
