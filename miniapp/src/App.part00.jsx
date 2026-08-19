import { useState, useEffect, useCallback } from "react";

const BACKEND = window.location.origin;
const TG_USER_ID = window.Telegram?.WebApp?.initDataUnsafe?.user?.id ?? null;


const PAIRS = ["EUR/USD","GBP/USD","USD/JPY","USD/CHF","AUD/USD","USD/CAD","NZD/USD","EUR/GBP","EUR/JPY","GBP/JPY"];
const TIMEFRAMES = ["1m","5m","15m","1h"];

function Badge({ type }) {
  const color = type === "BUY" ? "#00e5a0" : "#ff4d6d";
  const bg = type === "BUY" ? "rgba(0,229,160,0.12)" : "rgba(255,77,109,0.12)";
  return (
    <span style={{
      background: bg, color, border: `1px solid ${color}`,
      borderRadius: 6, padding: "2px 10px", fontWeight: 700, fontSize: 13,
      letterSpacing: 1
    }}>{type}</span>
  );
}

function ConfBar({ value }) {
  const color = value >= 80 ? "#00e5a0" : value >= 60 ? "#f5c542" : "#ff4d6d";
  return (
    <div style={{ background: "#1a1f2e", borderRadius: 6, height: 7, overflow: "hidden", margin: "6px 0" }}>
      <div style={{ width: `${value}%`, background: color, height: "100%", borderRadius: 6, transition: "width .5s" }} />
    </div>
  );
}

function SignalCard({ s, mini }) {
  return (
    <div style={{
      background: "linear-gradient(135deg,#141824 0%,#1a1f2e 100%)",
      border: "1px solid #232b3e", borderRadius: 14, padding: mini ? "12px 14px" : "16px 18px",
      marginBottom: 10
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ fontWeight: 800, fontSize: mini ? 14 : 16, color: "#e8eaf6", letterSpacing: .5 }}>
          {s.pair}{s.is_vip ? <span style={{ marginLeft: 7, color: "#f5c542", fontSize: 11 }}>VIP</span> : null}
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {s.timeframe && <span style={{ color: "#5c6bc0", fontSize: 11 }}>{s.timeframe}</span>}
          <Badge type={s.direction} />
        </div>
      </div>
      <ConfBar value={s.confidence} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#8892b0" }}>
        <span>Confidence: <b style={{ color: "#e8eaf6" }}>{s.confidence}%</b></span>
        {s.created_at && <span>{new Date(s.created_at).toLocaleTimeString()}</span>}
      </div>
      {!mini && (s.strategy_label || s.strategy || s.entry_time) && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid #232b3e", display: "grid", gap: 4, fontSize: 12, color: "#8892b0" }}>
          {(s.strategy_label || s.strategy) && <div>Strategy: <b style={{ color: "#e8eaf6" }}>{s.strategy_label || s.strategy}</b></div>}
          {s.entry_time && <div>Entry: <b style={{ color: "#00e5a0" }}>{new Date(s.entry_time).toLocaleTimeString()}</b>{s.expiry_time ? <> · Expiry: <b style={{ color: "#e8eaf6" }}>{new Date(s.expiry_time).toLocaleTimeString()}</b></> : null}</div>}
          {s.result && <div>Result: <b style={{ color: s.result === "WIN" ? "#00e5a0" : s.result === "LOSS" ? "#ff4d6d" : "#f5c542" }}>{s.result}</b></div>}
        </div>
      )}
      {!mini && s.reason && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#8892b0", borderTop: "1px solid #232b3e", paddingTop: 8 }}>
          {s.reason}
        </div>
      )}
      {!mini && s.indicators && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          {Object.entries(s.indicators).map(([k, v]) => (
            <span key={k} style={{ background: "#0d1117", border: "1px solid #232b3e", borderRadius: 6, padding: "2px 8px", fontSize: 11, color: "#8892b0" }}>
              {k}: <b style={{ color: "#e8eaf6" }}>{typeof v === "number" ? v.toFixed(2) : v}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── SIGNALS TAB ─────────────────────────────────────────────────────────────
function SignalsTab() {
  const [pair, setPair] = useState("EUR/USD");
  const [tf, setTf] = useState("5m");
  const [loading, setLoading] = useState(false);
  const [signal, setSignal] = useState(null);
  const [error, setError] = useState(null);

  const analyze = async () => {
    setLoading(true); setError(null); setSignal(null);
    try {
      const r = await fetch(`${BACKEND}/api/signals/analyze`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pair, timeframe: tf, user_id: TG_USER_ID })
      });
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      setSignal(d);
    } catch (e) {
      setError(e.message || "Connection error");
    }
    setLoading(false);
  };

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <label style={{ display: "block", fontSize: 11, color: "#5c6bc0", marginBottom: 6, textTransform: "uppercase", letterSpacing: 1 }}>Currency Pair</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {PAIRS.map(p => (
            <button key={p} onClick={() => setPair(p)} style={{
              background: pair === p ? "#3f51b5" : "#1a1f2e",
              border: `1px solid ${pair === p ? "#3f51b5" : "#232b3e"}`,
              borderRadius: 8, padding: "5px 10px", color: pair === p ? "#fff" : "#8892b0",
              fontSize: 12, cursor: "pointer", fontWeight: pair === p ? 700 : 400
            }}>{p}</button>
          ))}
        </div>
      </div>
      <div style={{ marginBottom: 16 }}>
        <label style={{ display: "block", fontSize: 11, color: "#5c6bc0", marginBottom: 6, textTransform: "uppercase", letterSpacing: 1 }}>Timeframe</label>
        <div style={{ display: "flex", gap: 6 }}>
          {TIMEFRAMES.map(t => (
            <button key={t} onClick={() => setTf(t)} style={{
