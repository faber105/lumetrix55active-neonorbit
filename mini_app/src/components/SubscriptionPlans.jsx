import { Copy, ExternalLink, Send, ShieldCheck, Wallet } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, ensureAuth } from '../api/client';

export default function SubscriptionPlans() {
  const [plans, setPlans] = useState([]);
  const [loadingPlan, setLoadingPlan] = useState('');
  const [cryptoPayment, setCryptoPayment] = useState(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api.get('/subscription/plans').then(({ data }) => setPlans(data)).catch((err) => setError(err.message));
  }, []);

  async function payStars(plan) {
    setLoadingPlan(`${plan.code}:stars`);
    setError('');
    setNotice('');
    try {
      await ensureAuth();
      const { data } = await api.post('/subscription/create', { plan: plan.code, provider: 'stars' });
      if (data.payment_url) {
        if (window.Telegram?.WebApp?.openInvoice) {
          window.Telegram.WebApp.openInvoice(data.payment_url, (status) => {
            if (status === 'paid') setNotice('Оплата Stars получена. Доступ активируется автоматически.');
          });
        } else {
          window.open(data.payment_url, '_blank');
        }
      }
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoadingPlan('');
    }
  }

  async function payCrypto(plan) {
    setLoadingPlan(`${plan.code}:crypto`);
    setError('');
    setNotice('');
    try {
      await ensureAuth();
      const { data } = await api.post('/subscription/create', { plan: plan.code, provider: 'crypto' });
      setCryptoPayment({ ...data, title: plan.title, days: plan.days });
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoadingPlan('');
    }
  }

  async function confirmCrypto() {
    if (!cryptoPayment) return;
    setLoadingPlan(`${cryptoPayment.payment_id}:confirm`);
    setError('');
    setNotice('');
    try {
      const { data } = await api.post('/subscription/confirm', { payment_id: cryptoPayment.payment_id });
      setCryptoPayment((value) => value ? { ...value, status: data.payment_status } : value);
      setNotice(data.message);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoadingPlan('');
    }
  }

  async function copyWallet() {
    if (!cryptoPayment?.wallet) return;
    await navigator.clipboard?.writeText(cryptoPayment.wallet);
    setNotice('Адрес кошелька скопирован.');
  }

  return (
    <section className="space-y-3">
      {error && <div className="rounded-md border border-terminal-red/50 bg-terminal-red/10 p-3 text-sm text-terminal-red">{error}</div>}
      {notice && <div className="rounded-md border border-terminal-green/50 bg-terminal-green/10 p-3 text-sm text-terminal-green">{notice}</div>}
      {cryptoPayment && (
        <article className="rounded-lg border border-terminal-green/50 bg-terminal-green/10 p-4">
          <div className="flex items-start gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-terminal-green/15 text-terminal-green"><Wallet size={20} /></div>
            <div className="min-w-0 flex-1"><div className="font-bold">Crypto оплата: {cryptoPayment.title}</div><div className="mt-1 text-sm text-terminal-muted">Переведи ровно {Number(cryptoPayment.amount).toFixed(2)} {cryptoPayment.currency}</div></div>
          </div>
          <button type="button" onClick={copyWallet} className="mono mt-4 flex w-full items-center justify-between gap-3 rounded-lg border border-terminal-border bg-terminal-bg p-3 text-left text-sm"><span className="min-w-0 break-all">{cryptoPayment.wallet}</span><Copy size={18} className="shrink-0 text-terminal-green" /></button>
          <button type="button" onClick={confirmCrypto} disabled={loadingPlan === `${cryptoPayment.payment_id}:confirm` || cryptoPayment.status === 'review'} className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-terminal-green font-extrabold text-black disabled:bg-terminal-hover disabled:text-terminal-muted"><Send size={18} />{cryptoPayment.status === 'review' ? 'Заявка на проверке' : loadingPlan === `${cryptoPayment.payment_id}:confirm` ? 'Отправляю...' : 'Я оплатил'}</button>
        </article>
      )}
      {plans.map((plan) => (
        <article key={plan.code} className="rounded-lg border border-terminal-border bg-terminal-card p-4">
          <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><ShieldCheck size={18} className="text-terminal-green" /><h3 className="font-bold">{plan.title}</h3></div><p className="mt-1 text-sm text-terminal-muted">{plan.days} дней доступа</p></div>{plan.badge && <span className="rounded-md bg-terminal-green/15 px-2 py-1 text-xs font-bold text-terminal-green">{plan.badge}</span>}</div>
          <div className="mt-4 flex items-end justify-between gap-3"><div><div className="mono text-2xl font-bold">${Number(plan.price_usd).toFixed(2)}</div><div className="text-xs text-terminal-muted">{plan.stars_amount} Stars</div></div><div className="grid min-w-[148px] gap-2"><button onClick={() => payStars(plan)} disabled={loadingPlan === `${plan.code}:stars`} className="flex h-11 items-center justify-center gap-2 rounded-md bg-terminal-green px-3 font-bold text-black disabled:opacity-50"><ExternalLink size={17} />{loadingPlan === `${plan.code}:stars` ? '...' : 'Stars'}</button><button onClick={() => payCrypto(plan)} disabled={loadingPlan === `${plan.code}:crypto`} className="flex h-11 items-center justify-center gap-2 rounded-md border border-terminal-border bg-terminal-bg px-3 font-bold text-white disabled:opacity-50"><Wallet size={17} />{loadingPlan === `${plan.code}:crypto` ? '...' : 'Crypto'}</button></div></div>
        </article>
      ))}
    </section>
  );
}
