(() => {
  const nativeFetch = window.fetch.bind(window);
  let preloadEnabled = false;
  let busy = false;
  let timer = null;

  function initData() {
    try {
      return window.Telegram?.WebApp?.initData ||
        new URLSearchParams(location.hash.replace(/^#/, '')).get('tgWebAppData') ||
        new URLSearchParams(location.search).get('tgWebAppData') || '';
    } catch { return ''; }
  }

  async function loadPreload() {
    if (busy || !document.querySelector('.ap-preload')) return;
    const tg = initData();
    if (!tg) return;
    busy = true;
    try {
      const response = await nativeFetch(`/api/auto-preload/state?_=${Date.now()}`, {
        headers: { 'X-Telegram-Init-Data': tg },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const state = await response.json();
      preloadEnabled = Boolean(state?.enabled);
      renderPreload();
    } catch (_) {
    } finally {
      busy = false;
    }
  }

  function renderPreload() {
    document.querySelectorAll('.ap-preload').forEach((node) => {
      const title = node.querySelector('strong');
      const badge = title?.querySelector('b');
      const small = node.querySelector('small');
      if (badge) badge.textContent = preloadEnabled ? 'ON' : 'OFF';
      if (small) small.textContent = preloadEnabled
        ? 'Следующий вход анализируется параллельно'
        : 'Ранний поиск выключен';
      node.classList.toggle('enabled', preloadEnabled);
      node.classList.toggle('disabled', !preloadEnabled);
      const visual = node.querySelector('i');
      if (visual) visual.style.opacity = preloadEnabled ? '1' : '.45';
    });
  }

  const observer = new MutationObserver(() => {
    renderPreload();
    if (document.querySelector('.ap-preload')) loadPreload();
  });

  function start() {
    observer.observe(document.documentElement, { childList: true, subtree: true });
    renderPreload();
    loadPreload();
    timer = setInterval(() => {
      if (document.hidden) return;
      if (document.querySelector('.ap-preload')) loadPreload();
    }, 700);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();

  window.addEventListener('pagehide', () => {
    observer.disconnect();
    if (timer) clearInterval(timer);
  }, { once: true });
})();
