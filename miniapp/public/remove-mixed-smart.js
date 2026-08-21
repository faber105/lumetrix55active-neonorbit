(() => {
  const removeMixedSmart = () => {
    const nodes = document.querySelectorAll('button, article, .strategy-card, .strategy-option');
    for (const node of nodes) {
      const text = (node.textContent || '').trim();
      if (!text.includes('Mixed Smart')) continue;
      const target = node.closest('button, .strategy-card, .strategy-option, article') || node;
      target.remove();
    }
  };

  const sweep = () => requestAnimationFrame(removeMixedSmart);
  document.addEventListener('DOMContentLoaded', sweep, { once: true });
  document.addEventListener('click', () => setTimeout(sweep, 0), true);
  document.addEventListener('touchend', () => setTimeout(sweep, 0), true);
  [0, 80, 250, 700, 1500].forEach(ms => setTimeout(sweep, ms));
})();
