import { useEffect, useMemo, useState } from "react";
import { TG_ID, apiFetch } from "./api";

const STAGES = {
  IDLE: ["Ожидание", "neutral"],
  SCANNING: ["Сканирую рынок", "scan"],
  SIGNAL_FOUND: ["Сигнал найден", "found"],
  WAIT_ENTRY: ["Жду точное время входа", "wait"],
  OPENING: ["Отправляю ордер", "opening"],
  OPEN: ["Сделка открыта · LIVE", "open"],
  CLOSED: ["Сделка закрыта", "closed"],
  PAYOUT_TOO_LOW: ["Выплата ниже порога", "warn"],
  MISSED_ENTRY: ["Вход пропущен", "warn"],
  FAILED: ["Ошибка исполнения", "error"],
};

function money(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toLocaleString([], { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function localTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function AutoTradeMonitor() {
  const [state, setState] = useState(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!TG_ID) return undefined;
    let stopped = false;
    let timer = null;
    const load = async () => {
      try {
        const data = await apiFetch("/api/admin/state");
        if (!stopped) setState(data);
      } catch (error) {
        if (!stopped && (error?.status === 401 || error?.status === 403)) setState(null);
      }
      if (!stopped) timer = window.setTimeout(load, 900);
    };
    load();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);

  const runtime = state?.auto_runtime || {};
  const stage = String(runtime.stage || "IDLE");
  const visible = Boolean(state?.auto_trade_enabled) || !["IDLE", "CLOSED"].includes(stage) || stage === "OPEN";
  const countdown = useMemo(() => {
    if (!runtime.entry_time || !["SIGNAL_FOUND", "WAIT_ENTRY"].includes(stage)) return null;
    const target = new Date(runtime.entry_time).getTime();
    if (!Number.isFinite(target)) return null;
    return Math.max(0, (target - now) / 1000);
  }, [runtime.entry_time, stage, now]);

  if (!state || !visible) return null;
  const [stageLabel, tone] = STAGES[stage] || [stage, "neutral"];
  const payout = Number(runtime.payout_percent);
  const balance = state.pocket_balance ?? runtime.balance;
  const minPayout = Number(state.min_auto_payout || 92);
  const active = ["SCANNING", "SIGNAL_FOUND", "WAIT_ENTRY", "OPENING"].includes(stage);

  return (
    <aside className={`auto-monitor ${tone}`}>
      <div className="auto-monitor-head">
        <div className="auto-monitor-title">
          <span className={`auto-dot ${active ? "pulse" : ""}`} />
          <div><b>AUTO · DEMO</b><small>{stageLabel}</small></div>
        </div>
        <div className="auto-balance"><small>Баланс</small><b>{money(balance)}</b></div>
      </div>
      <div className="auto-monitor-grid">
        <div><small>Пара</small><b>{runtime.pair || (stage === "SCANNING" ? "ищу…" : "—")}</b></div>
        <div><small>Выплата</small><b className={Number.isFinite(payout) && payout >= minPayout ? "good" : ""}>{Number.isFinite(payout) ? `${payout.toFixed(1)}%` : `≥${minPayout}%`}</b></div>
        <div><small>Вход</small><b>{localTime(runtime.entry_time)}</b></div>
        <div><small>До входа</small><b>{countdown == null ? "—" : `${countdown.toFixed(countdown < 10 ? 1 : 0)}s`}</b></div>
      </div>
      <div className="auto-monitor-foot">
        <span>{runtime.message || `Сканирую только пары с payout ≥${minPayout}%`}</span>
        {runtime.strategy && <b>{runtime.strategy} · {runtime.timeframe}</b>}
      </div>
    </aside>
  );
}
