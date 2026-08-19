import { ChevronRight, X } from 'lucide-react';
import { ASSETS, CATEGORY_META, CATEGORY_ORDER } from '../data/assets';

const visibleCategories = CATEGORY_ORDER.filter((category) => category !== 'commodities');

export default function FilterBar({ filters, onChange, onOpenCategory }) {
  return (
    <div className="space-y-4 border-b border-terminal-border bg-terminal-bg px-4 py-4">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs uppercase text-terminal-muted">1. Выбери рынок и актив</span>
          {(filters.category || filters.asset || filters.timeframe) && <button type="button" onClick={() => onChange({})} className="flex items-center gap-1 text-xs font-bold text-terminal-red"><X size={14} />Сброс</button>}
        </div>
        <div className="grid grid-cols-2 gap-2">
          {visibleCategories.map((category) => {
            const meta = CATEGORY_META[category]; const active = filters.category === category;
            return <button key={category} type="button" onClick={() => onOpenCategory(category)} className={`rounded-lg border p-3 text-left transition ${active ? 'border-terminal-green bg-terminal-green/12' : 'border-terminal-border bg-terminal-card'}`}>
              <div className="flex items-center justify-between gap-2"><div className="grid h-9 w-9 place-items-center rounded-lg bg-terminal-bg text-xs font-extrabold text-terminal-green">{meta.short}</div><ChevronRight size={17} className="text-terminal-muted" /></div>
              <div className="mt-3 font-bold">{meta.title}</div><div className="mt-1 text-xs text-terminal-muted">{ASSETS[category].length} активов</div>
            </button>;
          })}
        </div>
      </div>
    </div>
  );
}
