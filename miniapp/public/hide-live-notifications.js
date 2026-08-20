(() => {
  const TARGET = 'ЖИВЫЕ УВЕДОМЛЕНИЯ';

  function removeLiveNotifications() {
    const nodes = document.querySelectorAll('small,h1,h2,h3,h4,strong');
    for (const node of nodes) {
      if ((node.textContent || '').trim().toUpperCase() !== TARGET) continue;

      const protectedBlock = node.closest('.live-session, .journal, .chart-card');
      if (protectedBlock) {
        const ownRow = node.parentElement;
        if (ownRow && ownRow !== protectedBlock && !ownRow.querySelector('.balance-card,.session-kpis,.robot-status')) ownRow.remove();
        else node.remove();
        continue;
      }

      const block = node.closest('section') || node.closest('.glass') || node.parentElement;
      if (!block) continue;
      const text = (block.textContent || '').toUpperCase();
      if (text.includes('ЖУРНАЛ СЕССИИ')) continue;
      if (block.classList?.contains('live-session')) continue;
      if (block.querySelector?.('.session-top,.balance-card,.session-kpis,.robot-status')) continue;
      block.remove();
    }
  }

  const run = () => {
    removeLiveNotifications();
    setTimeout(removeLiveNotifications, 350);
    setTimeout(removeLiveNotifications, 1200);
    setTimeout(removeLiveNotifications, 3000);
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, { once: true });
  else run();
})();
