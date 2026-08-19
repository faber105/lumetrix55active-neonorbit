              background: tf === t ? "#3f51b5" : "#1a1f2e",
              border: `1px solid ${tf === t ? "#3f51b5" : "#232b3e"}`,
              borderRadius: 8, padding: "5px 14px", color: tf === t ? "#fff" : "#8892b0",
              fontSize: 13, cursor: "pointer", fontWeight: tf === t ? 700 : 400
            }}>{t}</button>
          ))}
        </div>
      </div>
      <button onClick={analyze} disabled={loading} style={{
        width: "100%", padding: "13px", background: loading ? "#232b3e" : "linear-gradient(90deg,#3f51b5,#5c6bc0)",
        border: "none", borderRadius: 12, color: "#fff", fontSize: 15, fontWeight: 700,
        cursor: loading ? "not-allowed" : "pointer", marginBottom: 16, letterSpacing: .5
      }}>
        {loading ? "⏳ Analyzing..." : "⚡ Analyze Market"}
      </button>
      {error && <div style={{ background: "rgba(255,77,109,.1)", border: "1px solid #ff4d6d", borderRadius: 10, padding: "10px 14px", color: "#ff4d6d", fontSize: 13, marginBottom: 12 }}>{error}</div>}
      {signal && <SignalCard s={signal} />}
    </div>
  );
}

// ─── VIP TAB ──────────────────────────────────────────────────────────────────
function VipTab() {
  const [signals, setSignals] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BACKEND}/api/signals/vip?limit=20`);
      const d = await r.json();
      setSignals(Array.isArray(d) ? d : d.signals || []);
    } catch { setSignals([]); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 13, color: "#8892b0" }}>Auto-updated every 30s</div>
        </div>
        <button onClick={load} style={{ background: "#1a1f2e", border: "1px solid #232b3e", borderRadius: 8, padding: "5px 12px", color: "#5c6bc0", fontSize: 12, cursor: "pointer" }}>↻ Refresh</button>
      </div>
      {loading ? <div style={{ textAlign: "center", color: "#5c6bc0", padding: 40 }}>Loading...</div>
        : signals.length === 0
          ? <div style={{ textAlign: "center", color: "#5c6bc0", padding: 40, fontSize: 14 }}>
              <div style={{ fontSize: 32, marginBottom: 10 }}>🔍</div>
              No VIP signals right now.<br />AI is monitoring the market.
            </div>
          : signals.map((s, i) => <SignalCard key={i} s={s} />)
      }
    </div>
  );
}

// ─── MARKET AI TAB ────────────────────────────────────────────────────────────
function MarketTab() {
  const [pair, setPair] = useState("EUR/USD");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async (p) => {
    setLoading(true); setData(null);
    try {
      const r = await fetch(`${BACKEND}/api/market/analysis?pair=${encodeURIComponent(p)}`);
      setData(await r.json());
    } catch { setData(null); }
    setLoading(false);
  };

  useEffect(() => { load(pair); }, [pair]);

  const indColor = (v, name) => {
    if (name === "RSI") return v > 70 ? "#ff4d6d" : v < 30 ? "#00e5a0" : "#f5c542";
    return "#e8eaf6";
  };

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
        {PAIRS.map(p => (
          <button key={p} onClick={() => setPair(p)} style={{
            background: pair === p ? "#3f51b5" : "#1a1f2e",
            border: `1px solid ${pair === p ? "#3f51b5" : "#232b3e"}`,
            borderRadius: 8, padding: "5px 10px", color: pair === p ? "#fff" : "#8892b0",
            fontSize: 12, cursor: "pointer", fontWeight: pair === p ? 700 : 400
          }}>{p}</button>
        ))}
      </div>
      {loading && <div style={{ textAlign: "center", color: "#5c6bc0", padding: 30 }}>Analyzing...</div>}
      {data && !loading && (
        <>
          {/* Multi-timeframe */}
          <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "14px 16px", marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: "#5c6bc0", textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>Multi-Timeframe</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {["1m","5m","15m","1h"].map(tf => {
                const t = data.timeframes?.[tf];
                return (
                  <div key={tf} style={{ background: "#1a1f2e", borderRadius: 10, padding: "10px 12px", border: "1px solid #232b3e" }}>
                    <div style={{ fontSize: 11, color: "#5c6bc0", marginBottom: 4 }}>{tf}</div>
                    {t ? <>
                      <Badge type={t.direction} />
                      <div style={{ fontSize: 11, color: "#8892b0", marginTop: 4 }}>{t.confidence}% conf</div>
                    </> : <span style={{ fontSize: 12, color: "#3d4a5c" }}>No data</span>}
                  </div>
                );
              })}
            </div>
          </div>
          {/* Indicators */}
          {data.indicators && (
            <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "14px 16px" }}>
              <div style={{ fontSize: 11, color: "#5c6bc0", textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>Indicators</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                {Object.entries(data.indicators).map(([k, v]) => (
                  <div key={k} style={{ background: "#1a1f2e", borderRadius: 10, padding: "10px 12px", border: "1px solid #232b3e", textAlign: "center" }}>
                    <div style={{ fontSize: 10, color: "#5c6bc0", marginBottom: 4 }}>{k}</div>
