import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CandleChart from "./CandleChart";
import { TG, TG_ID, apiFetch, patchJson, postJson } from "./api";

const STRATEGIES = [
  { id: "ema_trend", short: "EMA", name: "Trend + EMA", desc: "EMA 20/50/200 · MACD · RSI" },
  { id: "bollinger_reversal", short: "BB", name: "Bollinger Reversal", desc: "Bollinger Bands · RSI · возврат" },
  { id: "atr_breakout", short: "ATR", name: "ATR Breakout", desc: "Пробой · волатильность · импульс" },
];
const TIMEFRAMES = ["1m", "5m", "15m", "1h"];
const PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY"];
const STRATEGY_NAME = Object.fromEntries(STRATEGIES.map((item) => [item.id, item.name]));

const normalizeDate = (value) => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};
const timeFmt = (value) => normalizeDate(value)?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) || "—";
const dateTimeFmt = (value) => normalizeDate(value)?.toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) || "—";
const priceFmt = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? n.toFixed(3) : n.toFixed(5);
};
const safeMessage = (error) => error?.message || "Неизвестная ошибка";

function usePolling(fn, delay, enabled = true) {
  const fnRef = useRef(fn);
  useEffect(() => { fnRef.current = fn; }, [fn]);
  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let timer = null;
    const tick = async () => {
      try { await fnRef.current(); } catch { /* UI state owns errors */ }
      if (!stopped) timer = window.setTimeout(tick, delay);
    };
    tick();
    return () => { stopped = true; if (timer) window.clearTimeout(timer); };
  }, [delay, enabled]);
}

function Toggle({ value, onChange, disabled = false }) {
  return <button type="button" className={`toggle ${value ? "on" : ""}`} disabled={disabled} onClick={() => !disabled && onChange(!value)}><span /></button>;
}

function Segmented({ items, value, onChange, disabled = false }) {
  return <div className="segmented">{items.map((item) => {
    const key = typeof item === "string" ? item : item.value;
    const label = typeof item === "string" ? item : item.label;
    return <button type="button" key={key} disabled={disabled} className={value === key ? "active" : ""} onClick={() => onChange(key)}>{label}</button>;
  })}</div>;
}

function StatusPill({ tone = "neutral", children }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function ErrorBox({ children }) {
  return children ? <div className="notice error">{children}</div> : null;
}

function InfoBox({ children, tone = "info" }) {
  return children ? <div className={`notice ${tone}`}>{children}</div> : null;
}

function SectionHeader({ title, sub, right }) {
  return <div className="section-header"><div><h2>{title}</h2>{sub && <p>{sub}</p>}</div>{right}</div>;
}

function StrategyPicker({ value, onChange }) {
  return <div className="strategy-grid">{STRATEGIES.map((item) => (
    <button type="button" key={item.id} className={`strategy-card ${value === item.id ? "active" : ""}`} onClick={() => onChange(item.id)}>
      <span>{item.short}</span><b>{item.name}</b><small>{item.desc}</small>
    </button>
  ))}</div>;
}

function SelectField({ label, value, onChange, children }) {
  return <label className="field"><span>{label}</span><select value={value} onChange={(e) => onChange(e.target.value)}>{children}</select></label>;
}

function DirectionBadge({ direction }) {
  const call = direction === "BUY";
  return <span className={`direction ${call ? "call" : "put"}`}>{call ? "▲ CALL" : "▼ PUT"}</span>;
}

function ResultBadge({ value }) {
  const result = String(value || "PENDING").toLowerCase();
  return <span className={`result ${result}`}>{String(value || "PENDING")}</span>;
}

function SignalCard({ signal, onConfirm, confirmBusy = false, compact = false, executionLabel }) {
  if (!signal) return null;
  const expired = signal.expiry_time && Date.now() >= new Date(signal.expiry_time).getTime();
  return <article className={`signal-card ${signal.is_vip ? "vip" : ""} ${compact ? "compact" : ""}`}>
    <div className="signal-card-head">
      <div><div className="signal-pair">{signal.pair}</div><div className="meta">{STRATEGY_NAME[signal.strategy] || signal.strategy} · {signal.timeframe}</div></div>
      <div className="signal-badges"><span className="confidence-number">{Number(signal.confidence || 0).toFixed(1)}%</span><DirectionBadge direction={signal.direction} /></div>
    </div>
    <div className="quality-bar"><span style={{ width: `${Math.min(100, Number(signal.confidence) || 0)}%` }} /></div>
    <div className="signal-info-grid">
      <div><small>Анализ</small><b>{dateTimeFmt(signal.created_at)}</b></div>
      <div><small>Вход</small><b>{timeFmt(signal.entry_time)}</b></div>
      <div><small>Экспирация</small><b>{timeFmt(signal.expiry_time)}</b></div>
      <div><small>Цена</small><b>{priceFmt(signal.entry_price ?? signal.analysis_price)}</b></div>
    </div>
    {!compact && signal.reason && <p className="signal-reason">{signal.reason}</p>}
    <div className="signal-actions">
      <ResultBadge value={signal.result} />
      {signal.is_vip && <StatusPill tone="vip">VIP</StatusPill>}
      {onConfirm && signal.result === "PENDING" && !expired && (
        <button type="button" className="button small primary" disabled={confirmBusy} onClick={() => onConfirm(signal)}>
          {confirmBusy ? "Открываю…" : executionLabel || "Подтвердить сделку"}
        </button>
      )}
    </div>
  </article>;
}

function MarketChart({ market, positionDetail, loading }) {
  const active = positionDetail?.position;
  const candles = active ? positionDetail?.candles || [] : market?.candles || [];
  const currentPrice = active ? positionDetail?.current_price : market?.current_price;
  const entryPrice = active ? active.entry_price : null;
  return <section className="panel chart-panel">
    <div className="chart-title-row">
      <div><small>{active ? "LIVE СДЕЛКА" : "ЖИВОЙ ГРАФИК"}</small><b>{active?.pair || market?.pair || "Рынок"}</b></div>
      <div className="chart-price"><small>Сейчас</small><b>{priceFmt(currentPrice)}</b></div>
    </div>
    {loading && !candles.length ? <div className="skeleton chart-skeleton" /> : <CandleChart candles={candles} entryPrice={entryPrice} currentPrice={currentPrice} />}
    {active && <div className="live-strip">
      <div><small>Направление</small><DirectionBadge direction={active.direction} /></div>
      <div><small>До закрытия</small><b>{Math.max(0, Number(positionDetail?.seconds_to_expiry || 0))}s</b></div>
      <div><small>Сейчас</small><ResultBadge value={active.status === "CLOSED" ? active.result : positionDetail?.floating_result} /></div>
    </div>}
  </section>;
}

function TradingControls({ isAdmin, adminState, patchAdmin, busy }) {
  if (!isAdmin) {
    return <section className="panel compact-panel"><div className="setting-row"><div><b>Автоторговля</b><small>Доступна только владельцу подключённой Pocket-сессии. Сигналы и статистика работают для всех пользователей.</small></div><Toggle value={false} disabled /></div></section>;
  }
  const selectedReal = adminState?.trade_account_mode === "real";
  return <section className="panel trade-panel">
    <div className="setting-row"><div><b>Автоторговля</b><small>{adminState?.auto_trade_enabled ? "Разрешена для подтверждённых сигналов" : "Сделки не отправляются брокеру"}</small></div><Toggle value={Boolean(adminState?.auto_trade_enabled)} disabled={busy} onChange={(value) => patchAdmin({ auto_trade_enabled: value })} /></div>
    <div className="two-col controls-grid">
      <div><label>Исполнение</label><Segmented items={[{ value: "auto", label: "AUTO" }, { value: "confirm", label: "CONFIRM" }]} value={adminState?.execution_mode || "confirm"} onChange={(value) => patchAdmin({ execution_mode: value })} /></div>
      <div><label>Счёт Pocket</label><Segmented items={[{ value: "demo", label: "DEMO" }, { value: "real", label: "REAL" }]} value={adminState?.trade_account_mode || "demo"} onChange={(value) => patchAdmin({ trade_account_mode: value })} /></div>
    </div>
    <div className="trade-meta-row"><span>Подключено: <b>{String(adminState?.trade_account || "—").toUpperCase()}</b></span><span>Сумма: <b>{adminState?.trade_amount ?? 1}</b></span><span>Лимит: <b>{adminState?.max_open_positions ?? 1}</b></span></div>
    {selectedReal && <InfoBox tone="warning">REAL выбран только как режим отслеживания. Backend не отправляет автоматические реальные ордера: после сигнала откройте сделку вручную в Pocket Option, а AlphaPulse подхватит её в Live.</InfoBox>}
    {!adminState?.account_matches_mode && <InfoBox tone="warning">Выбранный режим счёта не совпадает с текущей Pocket-сессией. Для автосделки DEMO нужна активная demo-сессия.</InfoBox>}
  </section>;
}

function SignalsTab({ isAdmin, adminState, patchAdmin }) {
  const [pair, setPair] = useState(() => localStorage.getItem("ap_pair") || "EUR/USD");
  const [timeframe, setTimeframe] = useState(() => localStorage.getItem("ap_tf") || "1m");
  const [strategy, setStrategy] = useState(() => localStorage.getItem("ap_strategy") || "ema_trend");
  const [signal, setSignal] = useState(null);
  const [feed, setFeed] = useState([]);
  const [market, setMarket] = useState(null);
  const [positions, setPositions] = useState([]);
  const [positionDetail, setPositionDetail] = useState(null);
  const [status, setStatus] = useState({ code: "WAITING", text: "Готов к анализу" });
  const [error, setError] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [scanActive, setScanActive] = useState(false);
  const scanRef = useRef(false);

  useEffect(() => { localStorage.setItem("ap_pair", pair); }, [pair]);
  useEffect(() => { localStorage.setItem("ap_tf", timeframe); }, [timeframe]);
  useEffect(() => { localStorage.setItem("ap_strategy", strategy); }, [strategy]);

  const loadMarket = useCallback(async () => {
    try { const data = await apiFetch(`/api/market/candles?pair=${encodeURIComponent(pair)}&timeframe=${timeframe}&count=60`); setMarket(data); setError(""); } catch (e) { setError(safeMessage(e)); }
  }, [pair, timeframe]);
  usePolling(loadMarket, 1800, Boolean(TG_ID));

  const loadFeed = useCallback(async () => {
    try { const rows = await apiFetch("/api/live/feed?kind=regular&limit=8"); setFeed(Array.isArray(rows) ? rows : []); } catch { /* non-blocking */ }
  }, []);
  usePolling(loadFeed, 2200, Boolean(TG_ID));

  const loadPositions = useCallback(async () => {
    try {
      const rows = await apiFetch("/api/live/active");
      const list = Array.isArray(rows) ? rows : [];
      setPositions(list);
      const open = list.find((item) => item.status === "OPEN") || list[0];
      if (open) {
        const detail = await apiFetch(`/api/live/position/${open.id}?count=60`);
        setPositionDetail(detail);
        if (open.status === "OPEN") setStatus({ code: "OPEN", text: `Сделка открыта · ${open.pair}` });
        else if (!scanRef.current) setStatus({ code: "CLOSED", text: `Последняя сделка: ${open.result}` });
      } else setPositionDetail(null);
    } catch { /* transient market polling error */ }
  }, []);
  usePolling(loadPositions, 900, Boolean(TG_ID));

  const executeSignal = useCallback(async (found, explicit = false) => {
    if (!found) return null;
    if (!isAdmin) {
      if (!explicit) return null;
      const tracked = await postJson("/api/live/take", { signal_id: found.id });
      setStatus({ code: "OPEN", text: "Сигнал добавлен в Live-отслеживание" });
      return { status: "TRACKING", position_id: tracked.id };
    }
    if (!adminState?.auto_trade_enabled) return null;
    if (!explicit && adminState?.execution_mode !== "auto") {
      setStatus({ code: "FOUND", text: "Ситуация найдена · ожидается подтверждение" });
      return { status: "CONFIRMATION_REQUIRED" };
    }
    const result = await postJson(`/api/admin/execute/${found.id}`);
    const trade = result?.trade || {};
    if (trade.status === "OPEN") { setStatus({ code: "OPEN", text: `DEMO сделка открыта · ${found.pair}` }); TG?.HapticFeedback?.notificationOccurred?.("success"); }
    else if (trade.status === "REAL_CONFIRMATION_REQUIRED") setStatus({ code: "FOUND", text: "Сигнал готов · откройте REAL сделку вручную в Pocket" });
    else if (trade.status === "DUPLICATE") setStatus({ code: "FOUND", text: "Эта сделка уже обрабатывалась" });
    else if (trade.status === "DISABLED") setStatus({ code: "FOUND", text: "Сигнал найден, но автоторговля выключена" });
    else setStatus({ code: "FOUND", text: `Сигнал найден · ${trade.status || "ожидание"}` });
    return trade;
  }, [adminState?.auto_trade_enabled, adminState?.execution_mode, isAdmin]);

  const acceptFoundSignal = useCallback(async (found) => {
    setSignal(found); setStatus({ code: "FOUND", text: `Ситуация найдена · ${found.pair}` }); loadFeed();
    if (isAdmin && adminState?.auto_trade_enabled && adminState?.execution_mode === "auto") {
      try { await executeSignal(found, false); } catch (e) { setError(safeMessage(e)); setStatus({ code: "ERROR", text: "Ошибка открытия сделки" }); }
    }
  }, [adminState?.auto_trade_enabled, adminState?.execution_mode, executeSignal, isAdmin, loadFeed]);

  const analyzePair = async () => {
    setActionBusy("pair"); setError(""); setStatus({ code: "ANALYZING", text: `Анализ ${pair} · ${timeframe}` });
    try {
      const data = await postJson("/api/signals/analyze", { pair, timeframe, strategy, min_confidence: 0 });
      if (data.status !== "SIGNAL" || !data.signal) { setSignal(null); setStatus({ code: "NO_SIGNAL", text: data.reason || "Условия стратегии не подтверждены" }); return; }
      await acceptFoundSignal(data.signal);
    } catch (e) { setError(safeMessage(e)); setStatus({ code: "ERROR", text: "Ошибка анализа" }); }
    finally { setActionBusy(""); }
  };

  const stopScan = () => { scanRef.current = false; setScanActive(false); setStatus({ code: "WAITING", text: "Сканирование остановлено" }); };

  const scanMarket = async () => {
    if (scanRef.current) { stopScan(); return; }
    scanRef.current = true; setScanActive(true); setError(""); setSignal(null); setStatus({ code: "SCANNING", text: "Сканирую все OTC-пары до подтверждённого сетапа…" });
    let attempt = 0;
    try {
      while (scanRef.current) {
        attempt += 1; setStatus({ code: "SCANNING", text: `Поиск сетапа · проход ${attempt}` });
        const data = await postJson("/api/signals/scan-strategy", { strategy, timeframe, min_confidence: 72 });
        if (data.status === "SIGNAL" && data.signal && !data.duplicate) { scanRef.current = false; setScanActive(false); await acceptFoundSignal(data.signal); return; }
        await new Promise((resolve) => window.setTimeout(resolve, 6500));
      }
    } catch (e) { setError(safeMessage(e)); setStatus({ code: "ERROR", text: "Сканирование остановлено из-за ошибки" }); scanRef.current = false; setScanActive(false); }
  };
  useEffect(() => () => { scanRef.current = false; }, []);

  const confirm = async (found) => { setConfirmBusy(true); setError(""); try { await executeSignal(found, true); } catch (e) { setError(safeMessage(e)); setStatus({ code: "ERROR", text: "Не удалось открыть/отследить сделку" }); } finally { setConfirmBusy(false); } };

  const statusTone = { WAITING: "neutral", ANALYZING: "info", SCANNING: "info", FOUND: "success", OPEN: "success", CLOSED: "neutral", NO_SIGNAL: "warning", ERROR: "danger" }[status.code] || "neutral";
  const showConfirm = Boolean(signal && ((isAdmin && adminState?.auto_trade_enabled && adminState?.execution_mode === "confirm") || !isAdmin));

  return <div className="page-stack">
    <SectionHeader title="Сигналы" sub="Одна точка управления: пара, стратегия, сканирование, сделка и Live-график." right={<StatusPill tone={statusTone}>{status.text}</StatusPill>} />
    <section className="panel controls-panel">
      <div className="two-col"><SelectField label="Торговая пара" value={pair} onChange={setPair}>{PAIRS.map((item) => <option key={item}>{item}</option>)}</SelectField><div className="field"><span>Таймфрейм</span><Segmented items={TIMEFRAMES} value={timeframe} onChange={setTimeframe} /></div></div>
      <div className="field"><span>Стратегия</span><StrategyPicker value={strategy} onChange={setStrategy} /></div>
      <div className="action-grid"><button type="button" className="button secondary" disabled={Boolean(actionBusy) || scanActive} onClick={analyzePair}>{actionBusy === "pair" ? "Анализирую…" : "Получить сигнал"}</button><button type="button" className={`button primary ${scanActive ? "danger-button" : ""}`} disabled={Boolean(actionBusy)} onClick={scanMarket}>{scanActive ? "Остановить сканирование" : "Сканировать рынок"}</button></div>
    </section>
    <TradingControls isAdmin={isAdmin} adminState={adminState} patchAdmin={patchAdmin} busy={Boolean(actionBusy)} />
    <ErrorBox>{error}</ErrorBox>
    {signal && <section><SectionHeader title="Найденный сигнал" sub="Сигнал создан только если условия выбранной стратегии реально подтверждены." /><SignalCard signal={signal} onConfirm={showConfirm ? confirm : null} confirmBusy={confirmBusy} executionLabel={!isAdmin ? "Отслеживать" : adminState?.trade_account_mode === "real" ? "Подтвердить сигнал" : "Открыть DEMO"} /></section>}
    <MarketChart market={market} positionDetail={positionDetail} loading={!market} />
    {positions.length > 0 && <section className="panel compact-panel"><SectionHeader title="Состояние сделки" sub="Обновляется автоматически" /><div className="position-summary">{positions.slice(0, 4).map((item) => <div key={item.id}><span>{item.pair}</span><small>{item.source?.toUpperCase()} · {timeFmt(item.entry_time)}</small><ResultBadge value={item.status === "OPEN" ? "LIVE" : item.result} /></div>)}</div></section>}
    <section><SectionHeader title="Последние обычные сигналы" sub="История сохраняется после деплоя и перезапуска" /><div className="card-list">{feed.length ? feed.map((item) => <SignalCard key={item.id} signal={item} compact />) : <div className="empty-state">Сигналов пока нет.</div>}</div></section>
  </div>;
}

function VipTab({ isAdmin, adminState, refreshAdmin }) {
  const [items, setItems] = useState([]); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { const rows = await apiFetch("/api/live/feed?kind=vip&limit=20"); setItems(Array.isArray(rows) ? rows : []); setError(""); } catch (e) { setError(safeMessage(e)); } }, []);
  usePolling(load, 1800, Boolean(TG_ID));
  const hunt = async () => { if (!isAdmin) return; setBusy(true); setError(""); try { await postJson("/api/admin/vip-now"); await refreshAdmin(); } catch (e) { setError(safeMessage(e)); } finally { setBusy(false); } };
  const active = Boolean(adminState?.hunt?.active && adminState?.hunt?.kind === "vip");
  return <div className="page-stack"><SectionHeader title="VIP-сигналы" sub="Отдельный поток сетапов с confidence ≥ 80%. История и результат считаются отдельно." right={<StatusPill tone={active ? "info" : "vip"}>{active ? "ИДЁТ ПОИСК" : "VIP"}</StatusPill>} />{isAdmin && <section className="panel vip-control"><div><b>{active ? "VIP scanner ищет сетап" : "Запустить поиск VIP"}</b><small>Проверяет все пары по глобальной стратегии, пока не появится свежая подтверждённая ситуация.</small></div><button type="button" className="button primary" disabled={busy || active} onClick={hunt}>{busy ? "Запускаю…" : active ? "Поиск активен" : "Запустить VIP scanner"}</button></section>}<ErrorBox>{error}</ErrorBox>{items[0] && <section><SectionHeader title="Последний VIP" sub="Самый свежий подтверждённый VIP-сигнал" /><SignalCard signal={items[0]} /></section>}<section><SectionHeader title="VIP история" sub="Не смешивается с обычной лентой" /><div className="card-list">{items.length ? items.map((item) => <SignalCard key={item.id} signal={item} compact />) : <div className="empty-state">Подтверждённых VIP-сигналов пока нет.</div>}</div></section></div>;
}

function MiniStat({ label, value, sub }) { return <div className="stat-card"><small>{label}</small><b>{value ?? "—"}</b>{sub && <span>{sub}</span>}</div>; }
function Breakdown({ title, rows, nameMap = {} }) { return <section className="panel"><SectionHeader title={title} /><div className="breakdown">{(rows || []).length ? rows.map((row) => <div key={row.key}><div><b>{nameMap[row.key] || row.key}</b><small>{row.total} сигналов</small></div><span>{row.winrate == null ? "—" : `${row.winrate}%`}</span></div>) : <div className="empty-state">Недостаточно данных.</div>}</div></section>; }

function StatsTab() {
  const [stats, setStats] = useState(null); const [error, setError] = useState("");
  const load = useCallback(async () => { try { setStats(await apiFetch("/api/stats/summary")); setError(""); } catch (e) { setError(safeMessage(e)); } }, []);
  usePolling(load, 3500, Boolean(TG_ID));
  return <div className="page-stack"><SectionHeader title="Статистика" sub="Обычные, VIP и автоторговые результаты считаются раздельно." /><ErrorBox>{error}</ErrorBox><div className="stats-grid"><MiniStat label="Все сигналы" value={stats?.total} sub={stats?.winrate == null ? "Winrate —" : `Winrate ${stats.winrate}%`} /><MiniStat label="Обычные" value={stats?.regular?.total} sub={stats?.regular?.winrate == null ? "Winrate —" : `Winrate ${stats.regular.winrate}%`} /><MiniStat label="VIP" value={stats?.vip?.total} sub={stats?.vip?.winrate == null ? "Winrate —" : `Winrate ${stats.vip.winrate}%`} /><MiniStat label="Сделки" value={stats?.trading?.opened} sub={stats?.trading?.winrate == null ? "Winrate —" : `Winrate ${stats.trading.winrate}%`} /></div><Breakdown title="По стратегиям" rows={stats?.by_strategy} nameMap={STRATEGY_NAME} /><Breakdown title="По таймфреймам" rows={stats?.by_timeframe} /><Breakdown title="По парам" rows={stats?.by_pair} /><section className="panel"><SectionHeader title="ML scoring" sub="ML используется как дополнительный scoring/filter и не заменяет правила стратегии." /><div className="ml-grid">{Object.entries(stats?.ml || {}).map(([key, value]) => <div key={key}><b>{STRATEGY_NAME[key] || key}</b><small>Samples: {value.samples ?? 0}</small><span>{value.winrate == null ? "Winrate —" : `${value.winrate}%`}</span><StatusPill tone={value.influence_ready ? "success" : "neutral"}>{value.influence_ready ? "SCORING ACTIVE" : "COLLECTING"}</StatusPill></div>)}</div></section></div>;
}

function AdminTab({ state, patchAdmin, refreshAdmin }) {
  const [message, setMessage] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(""); const [diagnostics, setDiagnostics] = useState(null);
  if (!state) return <div className="empty-state">Загружаю админку…</div>;
  const action = async (kind) => { setBusy(kind); setError(""); setMessage(""); try { const data = await postJson(kind === "vip" ? "/api/admin/vip-now" : "/api/admin/scan-now"); setMessage(data.status === "SIGNAL" ? `Сигнал найден: ${data.signal?.pair}` : "Поиск запущен и будет продолжаться до свежего сетапа."); await refreshAdmin(); } catch (e) { setError(safeMessage(e)); } finally { setBusy(""); } };
  const stop = async () => { setBusy("stop"); try { await postJson("/api/admin/hunt-stop"); setMessage("Поиск остановлен."); await refreshAdmin(); } catch (e) { setError(safeMessage(e)); } finally { setBusy(""); } };
  const demoTest = async () => { setBusy("test"); setError(""); setMessage(""); try { await patchAdmin({ trade_account_mode: "demo", execution_mode: "auto", auto_trade_enabled: true, auto_trade_regular: true }); const data = await postJson("/api/admin/scan-now"); setMessage(data.status === "SIGNAL" ? `DEMO test: сигнал найден, статус сделки ${data.auto_trade?.status || "—"}` : "DEMO test запущен: scanner ищет реальный подтверждённый сетап и откроет тестовую DEMO-сделку автоматически."); await refreshAdmin(); } catch (e) { setError(safeMessage(e)); } finally { setBusy(""); } };
  const runDiagnostics = async () => { setBusy("diag"); try { setDiagnostics(await apiFetch("/api/admin/diagnostics")); setError(""); } catch (e) { setError(safeMessage(e)); } finally { setBusy(""); } };
  return <div className="page-stack"><SectionHeader title="Админ" sub="Scanner, автоторговля, диагностика и системное состояние. Доступ проверяется на backend." right={<StatusPill tone={state.market?.configured ? "success" : "danger"}>{state.market?.configured ? "POCKET READY" : "POCKET OFF"}</StatusPill>} /><div className="admin-kpis"><div><small>Scanner</small><b>{state.hunt?.active ? `HUNT ${String(state.hunt.kind).toUpperCase()}` : "IDLE"}</b></div><div><small>Открыто</small><b>{state.open_positions ?? 0}</b></div><div><small>Последний scan</small><b>{timeFmt(state.last_scan_at)}</b></div></div><section className="panel"><SectionHeader title="Глобальный scanner" sub="Эти параметры использует фоновой scanner GitHub Actions." /><div className="field"><span>Стратегия</span><StrategyPicker value={state.selected_strategy} onChange={(value) => patchAdmin({ selected_strategy: value })} /></div><div className="two-col"><div className="field"><span>Таймфрейм</span><Segmented items={TIMEFRAMES} value={state.selected_timeframe} onChange={(value) => patchAdmin({ selected_timeframe: value })} /></div><div className="field"><span>VIP интервал</span><Segmented items={[{ value: "60", label: "1m" }, { value: "180", label: "3m" }, { value: "300", label: "5m" }, { value: "600", label: "10m" }]} value={String(state.vip_interval_seconds || 300)} onChange={(value) => patchAdmin({ vip_interval_seconds: Number(value) })} /></div></div><div className="setting-row"><div><b>Обычный scanner</b><small>Порог {state.regular_confidence ?? 72}%</small></div><Toggle value={Boolean(state.regular_enabled)} onChange={(value) => patchAdmin({ regular_enabled: value })} /></div><div className="setting-row"><div><b>VIP scanner</b><small>Порог {state.vip_confidence ?? 80}%</small></div><Toggle value={Boolean(state.vip_enabled)} onChange={(value) => patchAdmin({ vip_enabled: value })} /></div><div className="action-grid three"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => action("regular")}>Искать сигнал</button><button type="button" className="button primary" disabled={Boolean(busy)} onClick={() => action("vip")}>Искать VIP</button><button type="button" className="button ghost" disabled={Boolean(busy) || !state.hunt?.active} onClick={stop}>Стоп</button></div></section><section className="panel"><SectionHeader title="Торговый контур" sub="AUTO и CONFIRM разделены на backend. Один signal_id не может открыть две одинаковые сделки." /><div className="setting-row"><div><b>Автоторговля</b><small>Master switch</small></div><Toggle value={Boolean(state.auto_trade_enabled)} onChange={(value) => patchAdmin({ auto_trade_enabled: value })} /></div><div className="two-col controls-grid"><div><label>Исполнение</label><Segmented items={[{ value: "auto", label: "AUTO" }, { value: "confirm", label: "CONFIRM" }]} value={state.execution_mode || "confirm"} onChange={(value) => patchAdmin({ execution_mode: value })} /></div><div><label>Счёт</label><Segmented items={[{ value: "demo", label: "DEMO" }, { value: "real", label: "REAL" }]} value={state.trade_account_mode || "demo"} onChange={(value) => patchAdmin({ trade_account_mode: value })} /></div></div><div className="two-col"><label className="field"><span>Сумма сделки</span><input type="number" min="1" step="1" value={state.trade_amount ?? 1} onChange={(e) => patchAdmin({ trade_amount: Math.max(1, Number(e.target.value) || 1) })} /></label><div className="field"><span>Макс. позиций</span><Segmented items={["1", "2", "3"]} value={String(state.max_open_positions || 1)} onChange={(value) => patchAdmin({ max_open_positions: Number(value) })} /></div></div><div className="setting-row"><div><b>Обычный сигнал → сделка</b><small>Использует тот же Signal Engine</small></div><Toggle value={Boolean(state.auto_trade_regular)} onChange={(value) => patchAdmin({ auto_trade_regular: value })} /></div><div className="setting-row"><div><b>VIP → сделка</b><small>Отдельный toggle, общая торговая инфраструктура</small></div><Toggle value={Boolean(state.auto_trade_vip)} onChange={(value) => patchAdmin({ auto_trade_vip: value })} /></div><button type="button" className="button demo-test full" disabled={Boolean(busy)} onClick={demoTest}>{busy === "test" ? "Запускаю DEMO test…" : "Запустить тестовую DEMO автосделку"}</button><InfoBox>Тест не генерирует fake-сигнал: scanner ждёт реальную ситуацию по выбранной стратегии и только после подтверждённого сетапа отправляет DEMO-ордер.</InfoBox></section><section className="panel"><SectionHeader title="Диагностика" sub="Без токенов, SID и секретов." right={<button type="button" className="button small ghost" onClick={runDiagnostics} disabled={Boolean(busy)}>Проверить</button>} /><div className="kv-list"><div><span>Pocket source</span><b>{state.market?.configured ? "READY" : "OFF"}</b></div><div><span>Подключённый счёт</span><b>{String(state.trade_account || "—").toUpperCase()}</b></div><div><span>Выбранный счёт</span><b>{String(state.trade_account_mode || "—").toUpperCase()}</b></div><div><span>Execution mode</span><b>{String(state.execution_mode || "—").toUpperCase()}</b></div><div><span>Последняя auto execution</span><b>{state.latest_execution?.status || "—"}</b></div></div>{diagnostics && <pre className="diagnostic-json">{JSON.stringify(diagnostics, null, 2)}</pre>}</section>{message && <InfoBox tone="success">{message}</InfoBox>}<ErrorBox>{error}</ErrorBox></div>;
}

const BASE_NAV = [{ id: "signals", label: "Сигналы", icon: "⚡" }, { id: "vip", label: "VIP", icon: "🔥" }, { id: "stats", label: "Статистика", icon: "▥" }];

export default function App() {
  const [tab, setTab] = useState("signals"); const [isAdmin, setIsAdmin] = useState(false); const [adminState, setAdminState] = useState(null); const [marketState, setMarketState] = useState(null); const [bootError, setBootError] = useState(""); const patchBusy = useRef(false);
  const refreshAdmin = useCallback(async () => { if (!TG_ID) return null; try { const data = await apiFetch("/api/admin/state"); setIsAdmin(true); setAdminState(data); return data; } catch (e) { if (e.status === 403) { setIsAdmin(false); setAdminState(null); return null; } throw e; } }, []);
  const patchAdmin = useCallback(async (changes) => { if (patchBusy.current) return adminState; patchBusy.current = true; try { const data = await patchJson("/api/admin/state", changes); setAdminState(data); return data; } finally { patchBusy.current = false; } }, [adminState]);
  const loadMarketHealth = useCallback(async () => { try { setMarketState(await apiFetch("/api/market/health")); setBootError(""); } catch (e) { setBootError(safeMessage(e)); } }, []);
  usePolling(loadMarketHealth, 4500, true); usePolling(refreshAdmin, 1300, Boolean(TG_ID && isAdmin));
  useEffect(() => { TG?.ready?.(); TG?.expand?.(); try { TG?.disableVerticalSwipes?.(); } catch { /* optional */ } document.documentElement.style.background = "#080c15"; if (TG_ID) refreshAdmin().catch((e) => setBootError(safeMessage(e))); }, [refreshAdmin]);
  const nav = useMemo(() => isAdmin ? [...BASE_NAV, { id: "admin", label: "Админ", icon: "≡" }] : BASE_NAV, [isAdmin]);
  useEffect(() => { if (tab === "admin" && !isAdmin) setTab("signals"); }, [isAdmin, tab]);
  return <div className="app-shell"><header className="topbar"><div className="brand"><div className="brand-mark">A</div><div><b>AlphaPulse</b><small>OTC signal & trading engine</small></div></div><div className={`connection ${marketState?.configured ? "online" : "offline"}`}><span />{marketState?.configured ? "POCKET READY" : "POCKET OFF"}</div></header>{!TG_ID && <div className="telegram-warning">Открой Mini App из Telegram-бота — авторизация и сделки доступны только через Telegram initData.</div>}{bootError && <div className="global-error">{bootError}</div>}<main className="content">{tab === "signals" && <SignalsTab isAdmin={isAdmin} adminState={adminState} patchAdmin={patchAdmin} />}{tab === "vip" && <VipTab isAdmin={isAdmin} adminState={adminState} refreshAdmin={refreshAdmin} />}{tab === "stats" && <StatsTab />}{tab === "admin" && isAdmin && <AdminTab state={adminState} patchAdmin={patchAdmin} refreshAdmin={refreshAdmin} />}</main><nav className={`bottom-nav ${isAdmin ? "admin-nav" : ""}`}>{nav.map((item) => <button type="button" key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><span>{item.icon}</span><small>{item.label}</small></button>)}</nav></div>;
}
