import { Activity, BarChart3, UserRound } from 'lucide-react';
import { useState } from 'react';
import clsx from 'clsx';
import AnimatedLogo from './components/AnimatedLogo';
import Signals from './pages/Signals';
import Statistics from './pages/Statistics';
import Profile from './pages/Profile';

const tabs = [
  { id: 'signals', label: 'Сигналы', icon: Activity, component: Signals },
  { id: 'stats', label: 'Статистика', icon: BarChart3, component: Statistics },
  { id: 'profile', label: 'Профиль', icon: UserRound, component: Profile }
];

export default function App() {
  const [active, setActive] = useState('signals');
  const tab = tabs.find((item) => item.id === active) || tabs[0];
  const Page = tab.component;

  return (
    <div className="app-shell mx-auto min-h-screen max-w-xl bg-terminal-bg text-terminal-text">
      <AnimatedLogo />
      <Page />
      <nav className="fixed inset-x-0 bottom-0 z-50 mx-auto max-w-xl border-t border-terminal-border bg-terminal-card/95 px-3 pb-4 pt-2 backdrop-blur">
        <div className="grid grid-cols-3 gap-2">
          {tabs.map((item) => {
            const Icon = item.icon;
            const selected = active === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActive(item.id)}
                className={clsx(
                  'flex h-14 flex-col items-center justify-center gap-1 rounded-lg text-xs transition',
                  selected ? 'bg-terminal-green text-black' : 'text-terminal-muted hover:bg-terminal-hover hover:text-white'
                )}
                title={item.label}
              >
                <Icon size={20} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

