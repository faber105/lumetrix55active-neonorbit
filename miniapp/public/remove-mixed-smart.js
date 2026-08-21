(() => {
  const activeMode = () => {
    const activeButtons = Array.from(document.querySelectorAll('.segmented button.active'));
    const modeButton = activeButtons.find((button) => {
      const text = (button.textContent || '').trim();
      return text === 'По WIN-сделкам' || text === 'До профита';
    });
    return (modeButton?.textContent || '').trim();
  };

  const applyStrategyVisibility = () => {
    const mode = activeMode();
    if (mode !== 'До профита') return;

    const nodes = document.querySelectorAll('button.strategy-choice, .strategy-card, .strategy-option');
    for (const node of nodes) {
      const text = (node.textContent || '').trim();
      if (!text.includes('Mixed Smart')) continue;
      node.remove();
    }
  };

  const sweep = () => requestAnimationFrame(applyStrategyVisibility);
  document.addEventListener('DOMContentLoaded', sweep, { once: true });
  document.addEventListener('click', () => setTimeout(sweep, 0), true);
  document.addEventListener('touchend', () => setTimeout(sweep, 0), true);
  [0, 80, 250, 700, 1500].forEach((ms) => setTimeout(sweep, ms));
})();
