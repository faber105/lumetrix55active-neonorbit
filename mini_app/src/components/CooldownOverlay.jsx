import { TimerReset } from 'lucide-react';

export default function CooldownOverlay({ seconds }) {
  if (!seconds) return null;
  const progress = Math.max(0, Math.min(100, ((15 - seconds) / 15) * 100));
  return (
    <div className="fixed inset-x-3 bottom-24 z-30 rounded-lg border border-terminal-border bg-terminal-card/95 p-4 shadow-2xl backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="grid h-12 w-12 place-items-center rounded-full bg-terminal-green/15 text-terminal-green"><TimerReset size={24} /></div>
        <div className="flex-1"><div className="text-sm text-terminal-muted">Cooldown между сигналами</div><div className="mono mt-1 text-3xl font-bold">00:{String(seconds).padStart(2, '0')}</div></div>
      </div>
      <div className="mt-3 h-2 rounded-full bg-terminal-bg"><div className="h-2 rounded-full bg-terminal-green transition-all" style={{ width: `${progress}%` }} /></div>
    </div>
  );
}
