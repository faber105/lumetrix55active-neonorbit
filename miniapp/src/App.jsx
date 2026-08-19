import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CandleChart from "./CandleChart";
import { TG, TG_ID, apiFetch, patchJson, postJson } from "./api";

const STRATEGIES = [
  { id: "ema_trend", short: "EMA", name: "EMA Trend", desc: "EMA20/50/200 + MACD + RSI" },
  { id: "bollinger_reversal", short: "BB", name: "Bollinger Reversal", desc: "Bollinger Bands + RSI" },
  { id: "atr_breakout", short: "ATR", name: "ATR Breakout", desc: "ATR volatility + breakout momentum" },
];
const TFS = ["1m", "5m", "15m", "1h"];
const PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY"];

const strategyName = (id) => STRATEGIES.find((s) => s.id === id)?.name || id;
const timeFmt = (value) => (value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
const priceFmt = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? n.toFixed(3) : n.toFixed(5);
};

function usePolling(fn, delay, enabled = true) {
  const fnRef = useRef(fn);
  useEffect(() => { fnRef.current = fn; }, [fn]);
  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let timer = null;
    const run = async () => {
      try { await fnRef.current(); } catch { /* UI owns errors */ }
      if (!stopped) timer = window.setTimeout(run, delay);
    };
    run();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
  }, [delay, enabled]);
}

function DirectionBadge({ direction }) {
  const buy = direction === "BUY";
  return <span className={`direction ${buy ? "buy" : "sell"}`}>{buy ? "▲ CALL" : "▼ PUT"}</span>;
}

function ResultBadge({ result }) {
  if (!result) return null;
  return <span className={`result ${String(result).toLowerCase()}`}>{result}</span>;
}

function SectionTitle({ title, sub, action }) {
  return <div className="section-title"><div><h2>{title}</h2>{sub && <p>{sub}</p>}</div>{action}</div>;
}

function StrategySelector({ value, onChange }) {
  return <div className="strategy-grid">{STRATEGIES.map((s) => (
    <button key={s.id} className={`strategy-card ${value === s.id ? "active" : ""}`} onClick={() => onChange(s.id)}>
      <span className="strategy-short">{s.short}</span><b>{s.name}</b><small>{s.desc}</small>
    </button>
  ))}</div>;
}

function Chips({ items, value, onChange }) {
  return <div className="chips">{items.map((item) => <button key={item} className={value === item ? "active" : ""} onClick={() => onChange(item)}>{item}</button>)}</div>;
}

function Toggle({ value, onChange }) {
  return <button aria-label="toggle" className={`toggle ${value ? "on" : ""}`} onClick={() => onChange(!value)}><span /></button>;
}

function SignalCard({ signal, onTake, busy = false, compact = false }) {
  if (!signal) return null;
  const expired = signal.expiry_time && Date.now() >= new Date(signal.expiry_time).getTime();
  return <article className={`signal-card ${signal.is_vip ? "vip" : ""} ${compact ? "compact" : ""}`}>
    <div className="signal-top">
      <div><div className="signal-pair">{signal.pair}</div><div className="muted tiny">{strategyName(signal.strategy)} · {signal.timeframe}</div></div>
      <div className="row gap"><span className={`quality ${signal.is_vip ? "gold" : ""}`}>{signal.confidence}%</span><DirectionBadge direction={signal.direction} /></div>
    </div>
    <div className="confidence"><span style={{ width: `${Math.min(100, Number(signal.confidence) || 0)}%` }} /></div>
    <div className="signal-times"><span>Вход <b>{timeFmt(signal.entry_time)}</b></span><span>Закрытие <b>{timeFmt(signal.expiry_time)}</b></span></div>
    {!compact && signal.reason && <p className="reason">{signal.reason}</p>}
    <div className="signal-bottom">
      <ResultBadge result={signal.result} />
      {signal.is_vip && <span className="vip-label">VIP</span>}
      {onTake && signal.result === "PENDING" && !expired && <button className="primary small" disabled={busy} onClick={() => onTake(signal)}>{busy ? "Открываю…" : "Взял сигнал"}</button>}
    </div>
  </article>;
}

function Empty({ children }) { return <div className="empty">{children}</div>; }
function ErrorBox({ children }) { return children ? <div className="error-box">{children}</div> : null; }

function useTakeSignal(onDone) {
  const [taking, setTaking] = useState(null);
  const take = useCallback(async (signal) => {
    setTaking(signal.id);
    try {
      const position = await postJson("/api/live/take", { signal_id: signal.id });
      TG?.HapticFeedback?.notificationOccurred?.("success");
      onDone?.(position);
      return position;
    } catch (e) {
      TG?.HapticFeedback?.notificationOccurred?.("error");
      throw e;
    } finally { setTaking(null); }
  }, [onDone]);
  return { take, taking };
}

function Signals({ onOpenLive }) {
  const [strategy, setStrategy] = useState("ema_trend");
  const [tf, setTf] = useState("1m");
  const [latest, setLatest] = useState(null);
  const [feed, setFeed] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { take, taking } = useTakeSignal((position) => onOpenLive(position.id));

  const loadFeed = useCallback(async () => {
    const rows = await apiFetch("/api/live/feed?kind=regular&limit=12");
    setFeed(Array.isArray(rows) ? rows : []);
  }, []);
  usePolling(loadFeed, 2200, true);

  const scan = async () => {
    setBusy(true); setError(""); setLatest(null);
    try {
      const data = await postJson("/api/signals/scan-strategy", { strategy, timeframe: tf, min_confidence: 72 });
      if (data.status !== "SIGNAL" || !data.signal) throw new Error("Сейчас нет подтверждённого сетапа по этой стратегии.");
      setLatest(data.signal);
      loadFeed();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const handleTake = async (signal) => {
    setError("");
    try { await take(signal); } catch (e) { setError(e.message); }
  };

  return <>
    <SectionTitle title="Сигналы" sub="Выбери одну стратегию — бот проверит все OTC-пары и найдёт лучший подтверждённый сетап." />
    <div className="panel"><label>Стратегия</label><StrategySelector value={strategy} onChange={setStrategy} /><label className="top-gap">Таймфрейм</label><Chips items={TFS} value={tf} onChange={setTf} /><button className="primary full top-gap" onClick={scan} disabled={busy}>{busy ? "Анализирую все пары…" : "⚡ Анализировать все пары"}</button></div>
    <ErrorBox>{error}</ErrorBox>
    {latest && <div className="top-gap"><SignalCard signal={latest} onTake={handleTake} busy={taking === latest.id} /></div>}
    <SectionTitle title="Последние сигналы" sub="Обновляются автоматически" />
    <div className="stack">{feed.length ? feed.map((s) => <SignalCard key={s.id} signal={s} onTake={handleTake} busy={taking === s.id} compact />) : <Empty>Обычных сигналов пока нет.</Empty>}</div>
  </>;
}

function VIP({ onOpenLive }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");
  const { take, taking } = useTakeSignal((position) => onOpenLive(position.id));
  const load = useCallback(async () => {
    try { setItems(await apiFetch("/api/live/feed?kind=vip&limit=20")); setError(""); } catch (e) { setError(e.message); }
  }, []);
  usePolling(load, 1800, true);
  const handleTake = async (signal) => { try { await take(signal); } catch (e) { setError(e.message); } };
  return <>
    <SectionTitle title="VIP" sub="Высокая уверенность ≥80%. Автоматический VIP-скан управляется админом и не создаёт сигнал, если сетап не подтверждён." />
    <ErrorBox>{error}</ErrorBox>
    <div className="stack">{items.length ? items.map((s) => <SignalCard key={s.id} signal={s} onTake={handleTake} busy={taking === s.id} />) : <Empty>Подтверждённого VIP-сигнала сейчас нет.</Empty>}</div>
  </>;
}

function Live({ requestedPositionId }) {
  const [positions, setPositions] = useState([]);
  const [selectedId, setSelectedId] = useState(requestedPositionId || null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => { if (requestedPositionId) setSelectedId(requestedPositionId); }, [requestedPositionId]);
  const loadPositions = useCallback(async () => {
    try {
      const rows = await apiFetch("/api/live/active");
      setPositions(Array.isArray(rows) ? rows : []);
      if (!selectedId && rows?.length) setSelectedId((rows.find((p) => p.status === "OPEN") || rows[0]).id);
    } catch (e) { setError(e.message); }
  }, [selectedId]);
  usePolling(loadPositions, 1800, Boolean(TG_ID));

  const loadDetail = useCallback(async () => {
    if (!selectedId) return;
    try { setDetail(await apiFetch(`/api/live/position/${selectedId}?count=50`)); setError(""); }
    catch (e) { setError(e.message); }
  }, [selectedId]);
  usePolling(loadDetail, 900, Boolean(selectedId && TG_ID));

  if (!TG_ID) return <><SectionTitle title="Live" sub="Открой Mini App внутри Telegram" /><Empty>Live-позиции доступны только внутри Telegram.</Empty></>;
  const p = detail?.position;
  const progress = p ? Math.max(0, Math.min(100, 100 - (Number(detail.seconds_to_expiry || 0) / Math.max(1, (new Date(p.expiry_time) - new Date(p.entry_time)) / 1000)) * 100)) : 0;
  return <>
    <SectionTitle title="Live сделка" sub="Текущая свеча и цена обновляются примерно раз в секунду с Pocket Option." />
    <ErrorBox>{error}</ErrorBox>
    <div className="position-tabs">{positions.map((pos) => <button key={pos.id} className={selectedId === pos.id ? "active" : ""} onClick={() => setSelectedId(pos.id)}><span>{pos.pair}</span><small>{pos.status === "OPEN" ? "LIVE" : pos.result}</small></button>)}</div>
    {!p ? <Empty>Возьми обычный или VIP-сигнал — активная позиция появится здесь.</Empty> : <div className="live-card">
      <div className="live-head"><div><div className="signal-pair">{p.pair}</div><div className="muted tiny">{strategyName(p.strategy)} · {p.timeframe} · {p.source.toUpperCase()}</div></div><div className="row gap"><DirectionBadge direction={p.direction} /><ResultBadge result={p.status === "CLOSED" ? p.result : detail.floating_result} /></div></div>
      <div className="live-metrics"><div><small>Вход</small><b>{priceFmt(p.entry_price)}</b></div><div><small>Сейчас</small><b>{priceFmt(detail.current_price)}</b></div><div><small>До закрытия</small><b>{p.status === "OPEN" ? `${detail.seconds_to_expiry}s` : "0s"}</b></div></div>
      <div className="expiry-progress"><span style={{ width: `${p.status === "CLOSED" ? 100 : progress}%` }} /></div>
      <CandleChart candles={detail.candles || []} entryPrice={p.entry_price} currentPrice={detail.current_price} />
      <div className="live-foot"><span>Открыта: <b>{timeFmt(p.entry_time)}</b></span><span>Экспирация: <b>{timeFmt(p.expiry_time)}</b></span>{p.status === "CLOSED" && <span>Close: <b>{priceFmt(p.close_price)}</b></span>}</div>
    </div>}
  </>;
}

function Market() {
  const [pair, setPair] = useState("EUR/USD");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const analyze = async () => {
    setLoading(true); setError("");
    try { setData(await apiFetch(`/api/market/analysis?pair=${encodeURIComponent(pair)}`)); }
    catch (e) { setError(e.message); setData(null); }
    finally { setLoading(false); }
  };
  return <>
    <SectionTitle title="Market AI" sub="Анализ выбранной OTC-пары по нескольким таймфреймам." />
    <div className="panel"><label>Валютная пара</label><Chips items={PAIRS} value={pair} onChange={setPair} /><button className="primary full top-gap" onClick={analyze} disabled={loading}>{loading ? "Анализ…" : "📈 Анализировать пару"}</button></div>
    <ErrorBox>{error}</ErrorBox>
    {data && <div className="panel top-gap"><div className="signal-pair">{data.pair}</div><div className="market-grid">{Object.entries(data.timeframes || {}).map(([tf, item]) => <div key={tf}><small>{tf}</small><DirectionBadge direction={item.direction} /><b>{item.confidence}%</b></div>)}</div><div className="indicator-row">{Object.entries(data.indicators || {}).map(([k, v]) => <span key={k}>{k}: <b>{v}</b></span>)}</div></div>}
  </>;
}

function SettingsPane() {
  const [settings, setSettings] = useState({ vip_enabled: true, notification_frequency: "standard", signal_mode: "all" });
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (TG_ID) apiFetch(`/api/settings/user/${TG_ID}`).then(setSettings).catch(() => {}); }, []);
  const save = async (next) => {
    setSettings(next); setSaved(false);
    if (!TG_ID) return;
    try { const data = await patchJson(`/api/settings/user/${TG_ID}`, next); setSettings(data); setSaved(true); setTimeout(() => setSaved(false), 1200); } catch { /* no-op */ }
  };
  return <div className="panel"><div className="setting-row"><div><b>VIP уведомления</b><small>Получать VIP-сигналы в Telegram</small></div><Toggle value={settings.vip_enabled} onChange={(v) => save({ ...settings, vip_enabled: v })} /></div><label>Режим сигналов</label><Chips items={["all", "vip", "mixed"]} value={settings.signal_mode} onChange={(v) => save({ ...settings, signal_mode: v })} /><label className="top-gap">Частота</label><Chips items={["rarely", "standard", "often"]} value={settings.notification_frequency} onChange={(v) => save({ ...settings, notification_frequency: v })} />{saved && <div className="saved">✓ Сохранено</div>}</div>;
}

function StatsPane() {
  const [stats, setStats] = useState(null);
  const [history, setHistory] = useState([]);
  const load = useCallback(async () => {
    const [s, h] = await Promise.all([apiFetch("/api/stats/summary"), apiFetch("/api/signals/history?limit=12")]);
    setStats(s); setHistory(Array.isArray(h) ? h : []);
  }, []);
  usePolling(load, 5000, true);
  return <><div className="stats-grid"><div><small>Winrate</small><b>{stats?.winrate ?? "—"}{stats?.winrate != null ? "%" : ""}</b></div><div><small>VIP Winrate</small><b>{stats?.vip_winrate ?? "—"}{stats?.vip_winrate != null ? "%" : ""}</b></div><div><small>Signals</small><b>{stats?.total ?? "—"}</b></div><div><small>VIP</small><b>{stats?.vip_total ?? "—"}</b></div></div><div className="stack top-gap">{history.map((s) => <SignalCard key={s.id} signal={s} compact />)}</div></>;
}

function AdminPane({ onAdminState }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const data = await apiFetch("/api/admin/state"); setState(data); onAdminState?.(true); setError(""); }
    catch (e) { onAdminState?.(false); setError(e.status === 403 ? "Нет доступа к админке" : e.message); }
  }, [onAdminState]);
  usePolling(load, 1600, true);

  const patch = async (changes) => {
    setError("");
    try { const data = await patchJson("/api/admin/state", changes); setState((prev) => ({ ...prev, ...data })); } catch (e) { setError(e.message); }
  };
  const action = async (kind) => {
    setBusy(kind); setMessage(""); setError("");
    try {
      const data = await postJson(kind === "vip" ? "/api/admin/vip-now" : "/api/admin/scan-now");
      setMessage(data.status === "SIGNAL" ? `${data.vip ? "VIP" : "Сигнал"}: ${data.signal?.pair} ${data.signal?.direction} ${data.signal?.confidence}%` : "Сейчас подтверждённого сетапа нет.");
      load();
    } catch (e) { setError(e.message); }
    finally { setBusy(""); }
  };

  if (!state) return <><ErrorBox>{error}</ErrorBox><Empty>Проверяю доступ к админке…</Empty></>;
  const mins = Math.max(1, Math.round((state.vip_interval_seconds || 300) / 60));
  return <>
    <div className="admin-status"><div><span className={`dot ${state.market?.configured ? "ok" : "bad"}`} />Pocket <b>{state.market?.configured ? "ONLINE" : "OFFLINE"}</b></div><div>Открытых позиций <b>{state.open_positions}</b></div><div>VIP через <b>{state.vip_seconds_remaining ?? "—"}s</b></div></div>
    <div className="panel"><label>Глобальная стратегия</label><StrategySelector value={state.selected_strategy} onChange={(v) => patch({ selected_strategy: v })} /><label className="top-gap">Таймфрейм scanner</label><Chips items={TFS} value={state.selected_timeframe} onChange={(v) => patch({ selected_timeframe: v })} /><div className="setting-row top-gap"><div><b>Обычные сигналы</b><small>Автоматический scanner</small></div><Toggle value={state.regular_enabled} onChange={(v) => patch({ regular_enabled: v })} /></div><div className="setting-row"><div><b>VIP scanner</b><small>Проверяет все пары по выбранной стратегии</small></div><Toggle value={state.vip_enabled} onChange={(v) => patch({ vip_enabled: v })} /></div><label>VIP интервал</label><div className="interval-row"><input type="number" min="1" max="1440" value={mins} onChange={(e) => patch({ vip_interval_seconds: Math.max(1, Number(e.target.value) || 1) * 60 })} /><span>мин.</span><Chips items={["1", "3", "5", "10", "15"]} value={String(mins)} onChange={(v) => patch({ vip_interval_seconds: Number(v) * 60 })} /></div><div className="admin-actions"><button className="secondary" disabled={busy} onClick={() => action("regular")}>{busy === "regular" ? "Сканирую…" : "Сигнал сейчас"}</button><button className="primary" disabled={busy} onClick={() => action("vip")}>{busy === "vip" ? "VIP анализ…" : "🔥 VIP сейчас"}</button></div>{message && <div className="saved">{message}</div>}<ErrorBox>{error}</ErrorBox></div>
    <div className="panel top-gap"><div className="kv"><span>Последний VIP статус</span><b>{state.last_vip_status || "—"}</b></div><div className="kv"><span>Последний scanner</span><b>{timeFmt(state.last_scan_at)}</b></div><div className="kv"><span>Pocket auth</span><b>{state.market?.auth_format || "—"}</b></div>{state.latest_signal && <div className="top-gap"><SignalCard signal={state.latest_signal} compact /></div>}</div>
  </>;
}

function More({ isAdmin, setIsAdmin }) {
  const [page, setPage] = useState(isAdmin ? "admin" : "stats");
  useEffect(() => { if (!isAdmin && page === "admin") setPage("stats"); }, [isAdmin, page]);
  return <><SectionTitle title="Меню" sub="Статистика, уведомления и управление" /><div className="subnav"><button className={page === "stats" ? "active" : ""} onClick={() => setPage("stats")}>Статистика</button><button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>Настройки</button>{isAdmin && <button className={page === "admin" ? "active" : ""} onClick={() => setPage("admin")}>Админ</button>}</div>{page === "stats" && <StatsPane />}{page === "settings" && <SettingsPane />}{page === "admin" && <AdminPane onAdminState={setIsAdmin} />}</>;
}

const NAV = [
  ["signals", "Сигналы", "⚡"],
  ["vip", "VIP", "🔥"],
  ["live", "Live", "📊"],
  ["market", "Market", "📈"],
  ["more", "Меню", "☰"],
];

export default function App() {
  const [tab, setTab] = useState("signals");
  const [livePositionId, setLivePositionId] = useState(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [marketLive, setMarketLive] = useState(false);

  useEffect(() => {
    TG?.ready(); TG?.expand();
    try { TG?.disableVerticalSwipes?.(); } catch { /* no-op */ }
    document.documentElement.style.background = "#090d16";
    apiFetch("/api/market/health").then((x) => setMarketLive(Boolean(x.configured))).catch(() => setMarketLive(false));
    if (TG_ID) apiFetch("/api/admin/state").then(() => setIsAdmin(true)).catch(() => setIsAdmin(false));
  }, []);

  const openLive = (positionId) => { setLivePositionId(positionId); setTab("live"); };
  return <div className="app-shell">
    <header className="header"><div className="logo">A</div><div><b>AlphaPulse</b><small>OTC AI signal engine</small></div><div className={`live-pill ${marketLive ? "ok" : ""}`}><span />{marketLive ? "POCKET LIVE" : "CONNECTING"}</div></header>
    {!TG_ID && <div className="telegram-warning">Открой Mini App из Telegram-бота, чтобы брать сигналы и видеть свои позиции.</div>}
    <main className="content">{tab === "signals" && <Signals onOpenLive={openLive} />}{tab === "vip" && <VIP onOpenLive={openLive} />}{tab === "live" && <Live requestedPositionId={livePositionId} />}{tab === "market" && <Market />}{tab === "more" && <More isAdmin={isAdmin} setIsAdmin={setIsAdmin} />}</main>
    <nav className="bottom-nav">{NAV.map(([id, label, icon]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><span>{icon}</span><small>{label}</small></button>)}</nav>
  </div>;
}
