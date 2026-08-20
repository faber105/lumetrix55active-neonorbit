(() => {
  const TARGET = 'ЖИВЫЕ УВЕДОМЛЕНИЯ';

  function removeLiveNotifications() {
    const nodes = document.querySelectorAll('small,h1,h2,h3,h4,div,span');
    for (const node of nodes) {
      if ((node.textContent || '').trim().toUpperCase() !== TARGET) continue;
      const block = node.closest('section') || node.closest('.glass') || node.parentElement;
      if (!block) continue;
      if ((block.textContent || '').toUpperCase().includes('ЖУРНАЛ СЕССИИ')) continue;
      block.remove();
    }
  }

  const observer = new MutationObserver(removeLiveNotifications);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', removeLiveNotifications, { once: true });
  setTimeout(removeLiveNotifications, 0);
})();
