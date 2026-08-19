import { Activity } from 'lucide-react';

export default function AnimatedLogo() {
  return (
    <div className="flex items-center gap-3 px-4 pb-4 pt-2">
      <div className="logo-orbit relative grid h-12 w-12 place-items-center rounded-full border border-terminal-green/50 bg-terminal-green/10">
        <span className="absolute h-8 w-8 rounded-full border border-terminal-green/30" />
        <Activity size={24} className="relative z-10 text-terminal-green" />
      </div>
      <div>
        <div className="text-lg font-extrabold tracking-normal">AlphaPulse</div>
        <div className="text-xs text-terminal-muted">Сигналы и активы</div>
      </div>
    </div>
  );
}

