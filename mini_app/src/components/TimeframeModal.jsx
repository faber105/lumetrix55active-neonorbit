import { ArrowRight, Clock3, X } from 'lucide-react';

const timeframes = [
  { value: '1m', label: '1m', hint: 'Быстрый вход' },
  { value: '3m', label: '3m', hint: 'Фильтр шума' },
  { value: '5m', label: '5m', hint: 'Более спокойный сетап' }
];

export default function TimeframeModal({ open, asset, marketTitle, selectedTimeframe, analyzing, onClose, onSelect }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-[70] flex items-end bg-black/75 px-3 pb-[calc(env(safe-area-inset-bottom,0px)+18px)] pt-[calc(env(safe-area-inset-top,0px)+88px)] backdrop-blur">
      <section className="terminal-scroll w-full overflow-y-auto rounded-lg border border-terminal-border bg-terminal-card p-4 shadow-2xl">
        <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-xs uppercase text-terminal-muted"><Clock3 size={14} />Выбрать таймфрейм</div><h2 className="mt-1 text-2xl font-extrabold">Время сигнала</h2></div><button type="button" onClick={onClose} className="grid h-10 w-10 place-items-center rounded-lg bg-terminal-hover" title="Закрыть"><X size={20} /></button></div>
        <div className="mt-4 rounded-lg border border-terminal-green/35 bg-terminal-green/10 p-4"><div className="text-xs uppercase text-terminal-muted">Выбранная пара</div><div className="mono mt-1 text-3xl font-extrabold">{asset}</div><div className="mt-1 text-sm text-terminal-muted">{marketTitle}</div></div>
        <div className="mt-4 grid grid-cols-2 gap-2">{timeframes.map((item) => { const selected = selectedTimeframe === item.value; return <button key={item.value} type="button" disabled={analyzing} onClick={() => onSelect(item.value)} className={`min-h-[86px] rounded-lg border p-3 text-left transition disabled:opacity-50 ${selected ? 'border-terminal-green bg-terminal-green text-black' : 'border-terminal-border bg-terminal-bg hover:border-terminal-green/60'}`}><div className="flex items-center justify-between gap-2"><span className="mono text-2xl font-extrabold">{item.label}</span><ArrowRight size={18} /></div><div className={`mt-2 text-xs font-bold ${selected ? 'text-black/70' : 'text-terminal-muted'}`}>{item.hint}</div></button>; })}</div>
        <div className="mt-4 rounded-lg border border-terminal-border bg-terminal-bg px-3 py-3 text-sm leading-6 text-terminal-muted">Нажми на таймфрейм, и AlphaPulse сразу запустит анализ рынка.</div>
      </section>
    </div>
  );
}
