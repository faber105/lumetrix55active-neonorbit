import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch, TG_ID } from "./api";
import "./sessionEnhancer.css";

const n=v=>Number.isFinite(Number(v))?Number(v):0;
const money=v=>Number.isFinite(Number(v))?Number(v).toFixed(2):"—";
const signed=v=>Number.isFinite(Number(v))?`${Number(v)>=0?"+":""}${Number(v).toFixed(2)}`:"—";
const dt=v=>{const d=v?new Date(v):null;return d&&!Number.isNaN(d.getTime())?d.toLocaleString([], {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"}):"—"};
const tone=v=>String(v||"").toLowerCase();

function Metric({label,value,sub,className=""}){return <div className={`sx-metric ${className}`}><small>{label}</small><b>{value}</b>{sub&&<span>{sub}</span>}</div>}

function TradeRow({leg,index}){
  const result=String(leg.result||"PENDING").toUpperCase();
  return <article className="sx-trade">
    <div className="sx-trade-head"><div><small>Сделка #{index+1} · уровень {n(leg.martingale_level)}/{Math.max(n(leg.martingale_level),0)}</small><b>{leg.pair||leg.asset||"—"} · {leg.direction||"—"}</b></div><span className={`sx-result ${tone(result)}`}>{result}</span></div>
    <div className="sx-trade-grid">
      <span><small>Ставка</small><b>{money(leg.amount)}</b></span>
      <span><small>Payout</small><b>{leg.payout==null?"—":`${money(leg.payout)}%`}</b></span>
      <span><small>P/L</small><b className={n(leg.pnl)>=0?"sx-pos":"sx-neg"}>{signed(leg.pnl)}</b></span>
      <span><small>Перекрытие</small><b>{n(leg.martingale_level)>0?`Да · L${n(leg.martingale_level)}`:"Нет"}</b></span>
    </div>
    <div className="sx-times"><span>Открыта {dt(leg.opened_at||leg.created_at)}</span><span>Закрыта {dt(leg.closed_at)}</span></div>
  </article>
}

function SessionDetail({detail,onClose}){
  const s=detail?.session||{},m=detail?.metrics||{},legs=detail?.legs||[],events=detail?.events||[];
  return createPortal(<div className="sx-overlay" role="dialog" aria-modal="true">
    <div className="sx-sheet">
      <div className="sx-sheet-head"><div><small>ПОЛНЫЙ ОТЧЁТ AUTO</small><h2>Сессия #{s.id}</h2><span>{dt(s.created_at)} → {dt(s.ended_at||s.updated_at)}</span></div><button type="button" onClick={onClose}>✕</button></div>
      <div className="sx-balance-row">
        <Metric label="Баланс до" value={money(m.start_balance??s.start_balance)}/>
        <Metric label="Баланс после" value={money(m.end_balance??s.current_balance)}/>
        <Metric label="Изменение" value={signed(m.balance_change)} className={n(m.balance_change)>=0?"sx-positive":"sx-negative"}/>
      </div>
      <div className="sx-summary-grid">
        <Metric label="Итог P/L" value={signed(m.net_profit??s.profit)} className={n(m.net_profit??s.profit)>=0?"sx-positive":"sx-negative"}/>
        <Metric label="WIN / LOSS" value={`${n(m.wins)} / ${n(m.losses)}`} sub={m.winrate==null?"Winrate —":`${m.winrate}% winrate`}/>
        <Metric label="Всего ставок" value={money(m.total_staked)}/>
        <Metric label="Перекрытий" value={n(m.covered_trades)}/>
        <Metric label="Gross +" value={`+${money(m.gross_wins)}`}/>
        <Metric label="Gross −" value={`-${money(m.gross_losses)}`}/>
      </div>
      <section className="sx-info"><h3>Параметры сессии</h3><div className="sx-info-grid">
        <span><small>Статус</small><b>{s.status||"—"}</b></span><span><small>Режим</small><b>{s.mode==="profit"?"До профита":"По WIN"}</b></span>
        <span><small>Стратегия</small><b>{s.strategy||"—"}</b></span><span><small>Таймфрейм</small><b>{s.timeframe||"—"}</b></span>
        <span><small>Базовая ставка</small><b>{money(s.base_amount)}</b></span><span><small>Макс. перекрытий</small><b>{n(s.max_martingale)}</b></span>
        <span><small>Минус-серий</small><b>{n(s.failed_series)}/{n(s.max_failed_series)}</b></span><span><small>Причина остановки</small><b>{s.stop_reason||"—"}</b></span>
      </div></section>
      <section className="sx-info"><h3>Все сделки</h3>{legs.length?<div className="sx-trades">{legs.map((leg,i)=><TradeRow key={leg.id||i} leg={leg} index={i}/>)}</div>:<p className="sx-empty">В этой сессии сделок не было.</p>}</section>
      <section className="sx-info"><h3>Хронология работы робота</h3><div className="sx-events">{events.map((e,i)=><article key={e.id||i}><time>{dt(e.created_at)}</time><i>{e.stage}</i><p>{e.message}</p></article>)}</div></section>
    </div>
  </div>,document.body)
}

function ActiveTelemetry({state,target}){
  if(!state?.session||!target)return null;
  const s=state.session,r=state.runtime||{},m=s.metrics||{},notes=state.screen_notifications||state.events||[];
  return createPortal(<div className="sx-active">
    <div className="sx-active-title"><div><small>LIVE TELEMETRY</small><b>Подробно о работе бота</b></div><span>{s.stage}</span></div>
    <div className="sx-balance-row compact">
      <Metric label="Баланс до" value={money(s.start_balance)}/><Metric label="Сейчас" value={money(s.current_balance??r.balance)}/><Metric label="Изменение" value={signed((s.current_balance??r.balance)!=null&&s.start_balance!=null?n(s.current_balance??r.balance)-n(s.start_balance):null)} className={n(s.current_balance??r.balance)-n(s.start_balance)>=0?"sx-positive":"sx-negative"}/>
    </div>
    <div className="sx-live-grid">
      <span><small>Текущая ставка</small><b>{money(s.current_bet_amount)}</b></span>
      <span><small>Следующая ставка</small><b>{money(s.next_bet_amount)}</b></span>
      <span><small>WIN / LOSS</small><b>{n(m.wins)} / {n(m.losses)}</b></span>
      <span><small>Перекрытий</small><b>{n(m.covered_trades)}</b></span>
      <span><small>Всего поставлено</small><b>{money(m.total_staked)}</b></span>
      <span><small>Серия убытка</small><b>{money(s.current_series_loss)}</b></span>
      <span><small>Сканировано</small><b>{n(r.scanned_count)||"—"}</b></span>
      <span><small>Payout ≥92%</small><b>{Array.isArray(r.eligible_assets)?r.eligible_assets.length:"—"}</b></span>
    </div>
    {r.pair&&<div className="sx-current-setup"><small>Текущий сетап</small><b>{r.pair} · {r.payout_percent?`${money(r.payout_percent)}% payout`:""}</b><span>{r.entry_time?`Вход ${dt(r.entry_time)}`:"Поиск подтверждённого входа"}</span></div>}
    <div className="sx-live-log"><small>ЖИВЫЕ УВЕДОМЛЕНИЯ</small>{notes.slice(0,12).map((e,i)=><div key={`${e.id||i}-${e.stage}`}><time>{dt(e.created_at)}</time><i>{e.stage}</i><span>{e.message}</span></div>)}</div>
  </div>,target)
}

export default function SessionEnhancer(){
  const[state,setState]=useState(null),[detail,setDetail]=useState(null),[target,setTarget]=useState(null);
  const loadState=useCallback(async()=>{if(!TG_ID)return;try{setState(await apiFetch(`/api/auto/state?drive=false&_=${Date.now()}`))}catch{}},[]);
  useEffect(()=>{loadState();const timer=setInterval(loadState,1200);return()=>clearInterval(timer)},[loadState]);
  useEffect(()=>{let stopped=false;const wire=()=>{if(stopped)return;setTarget(document.querySelector(".live-session"));document.querySelectorAll(".session-history article").forEach(node=>{if(node.dataset.sxBound)return;const match=node.textContent?.match(/#(\d+)/);if(!match)return;node.dataset.sxBound="1";node.classList.add("sx-history-click");node.setAttribute("role","button");node.setAttribute("tabindex","0");const open=async()=>{try{setDetail(await apiFetch(`/api/auto/history/${match[1]}?_=${Date.now()}`))}catch{}};node.addEventListener("click",open);node.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open()}})})};wire();const observer=new MutationObserver(wire);observer.observe(document.body,{childList:true,subtree:true});return()=>{stopped=true;observer.disconnect()}},[]);
  return <>{state?.active&&<ActiveTelemetry state={state} target={target}/>} {detail&&<SessionDetail detail={detail} onClose={()=>setDetail(null)}/>}</>;
}
