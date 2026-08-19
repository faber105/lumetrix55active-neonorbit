import { Bell, LifeBuoy, Settings, UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import SubscriptionPlans from '../components/SubscriptionPlans';
import { api, ensureAuth } from '../api/client';
import { useTelegram } from '../hooks/useTelegram';
import { dateTime } from '../utils/format';

export default function Profile() {
  const { user: tgUser } = useTelegram(); const [me,setMe]=useState(null); const [stats,setStats]=useState(null); const [error,setError]=useState('');
  useEffect(()=>{async function load(){try{await ensureAuth();const [meRes,statsRes]=await Promise.all([api.get('/user/me'),api.get('/user/stats')]);setMe(meRes.data);setStats(statsRes.data)}catch(err){setError(err.response?.data?.detail||err.message)}}load()},[]);
  return <div className="pb-24"><header className="border-b border-terminal-border px-4 py-4"><h1 className="text-xl font-bold">Профиль</h1><p className="mt-1 text-sm text-terminal-muted">Аккаунт, подписка и настройки.</p></header><main className="space-y-4 px-4 py-4">{error&&<div className="rounded-lg border border-terminal-red/50 bg-terminal-red/10 p-3 text-sm text-terminal-red">{String(error)}</div>}<section className="rounded-lg border border-terminal-border bg-terminal-card p-4"><div className="flex items-center gap-3"><div className="grid h-12 w-12 place-items-center rounded-full bg-terminal-green/15 text-terminal-green"><UserRound size={24}/></div><div><div className="font-bold">@{tgUser?.username||me?.username||'trader'}</div><div className="text-sm text-terminal-muted">В системе с: {dateTime(me?.created_at)}</div></div></div><div className="mt-4 grid grid-cols-2 gap-2 text-sm"><ProfileMetric label="Сессий" value={stats?.sessions||0}/><ProfileMetric label="Winrate" value={`${stats?.winrate||0}%`}/><ProfileMetric label="Подписка" value={stats?.active_subscription?.is_active?'PRO':'Нет'}/><ProfileMetric label="До" value={dateTime(stats?.active_subscription?.expires_at)}/></div></section><section className="grid grid-cols-3 gap-2"><Tool icon={Settings} label="Язык"/><Tool icon={Bell} label="Уведомления"/><Tool icon={LifeBuoy} label="Поддержка"/></section><section><h2 className="mb-3 font-bold">Тарифы</h2><SubscriptionPlans/></section></main></div>;
}
function ProfileMetric({label,value}){return <div className="rounded-md bg-terminal-bg px-3 py-3"><div className="text-xs text-terminal-muted">{label}</div><div className="mt-1 truncate font-bold">{value}</div></div>}
function Tool({icon:Icon,label}){return <button className="flex h-20 flex-col items-center justify-center gap-2 rounded-lg border border-terminal-border bg-terminal-card text-sm" title={label}><Icon size={20} className="text-terminal-green"/>{label}</button>}
