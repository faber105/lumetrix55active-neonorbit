export function money(value) {
  const number = Number(value || 0);
  return `${number >= 0 ? '+' : '-'}$${Math.abs(number).toFixed(2)}`;
}

export function percent(value) {
  return `${Number(value || 0).toFixed(0)}%`;
}

export function dateTime(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
}

export function countdown(expiresAt) {
  const seconds = Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
  return `00:${String(seconds).padStart(2, '0')}`;
}

