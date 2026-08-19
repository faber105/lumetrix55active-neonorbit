  );
}

// ─── STATS TAB ───────────────────────────────────────────────────────────────
function StatsTab() {
  const [stats, setStats] = useState(null);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    fetch(`${BACKEND}/api/stats/summary`).then(r => r.json()).then(setStats).catch(() => {});
    fetch(`${BACKEND}/api/signals/history?limit=20`).then(r => r.json()).then(d => setHistory(Array.isArray(d) ? d : d.signals || [])).catch(() => {});
  }, []);

  const winrate = stats?.winrate ?? (history.length ? Math.round(history.filter(s => s.result === "WIN").length / history.filter(s => s.result).length * 100) : null);
  const vipWinrate = stats?.vip_winrate ?? null;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
        {[
          { label: "Winrate", value: winrate != null ? `${winrate}%` : "—", color: winrate >= 60 ? "#00e5a0" : "#f5c542" },
          { label: "VIP Winrate", value: vipWinrate != null ? `${vipWinrate}%` : "—", color: "#f5c542" },
          { label: "Total Signals", value: stats?.total ?? history.length, color: "#5c6bc0" },
          { label: "VIP Signals", value: stats?.vip_total ?? "—", color: "#3f51b5" },
        ].map((item, i) => (
          <div key={i} style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "14px 16px", textAlign: "center" }}>
            <div style={{ fontSize: 11, color: "#5c6bc0", marginBottom: 6, textTransform: "uppercase", letterSpacing: 1 }}>{item.label}</div>
            <div style={{ fontSize: 26, fontWeight: 800, color: item.color }}>{item.value}</div>
          </div>
        ))}
      </div>

      <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "14px 16px" }}>
        <div style={{ fontSize: 11, color: "#5c6bc0", textTransform: "uppercase", letterSpacing: 1, marginBottom: 10 }}>Last 20 Signals</div>
        {history.length === 0
          ? <div style={{ textAlign: "center", color: "#5c6bc0", padding: 20, fontSize: 13 }}>No history yet</div>
          : history.map((s, i) => (
            <div key={i} style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "8px 0", borderBottom: i < history.length - 1 ? "1px solid #1a1f2e" : "none"
            }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "#e8eaf6" }}>{s.pair}</span>
                <Badge type={s.direction} />
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "#8892b0" }}>{s.confidence}%</span>
                {s.result && (
                  <span style={{ fontSize: 12, fontWeight: 700, color: s.result === "WIN" ? "#00e5a0" : "#ff4d6d" }}>{s.result}</span>
                )}
              </div>
            </div>
          ))
        }
      </div>
    </div>
  );
}

// ─── MAIN APP ─────────────────────────────────────────────────────────────────
const TABS = [
  { id: "signals", label: "Signals", icon: "📡" },
  { id: "vip", label: "VIP", icon: "🔥" },
  { id: "market", label: "Market AI", icon: "📈" },
  { id: "settings", label: "Settings", icon: "⚙️" },
  { id: "stats", label: "Stats", icon: "📊" },
];

export default function App() {
  const [tab, setTab] = useState("vip");

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (tg) { tg.ready(); tg.expand(); try { tg.disableVerticalSwipes?.(); } catch {} }
  }, []);

  return (
    <div style={{
      minHeight: "100vh", background: "#0d1117",
      fontFamily: "'SF Pro Display', -apple-system, BlinkMacSystemFont, sans-serif",
      color: "#e8eaf6", paddingBottom: 70
    }}>
      {/* Header */}
      <div style={{
        background: "linear-gradient(135deg,#0d1117 0%,#141824 100%)",
        borderBottom: "1px solid #232b3e", padding: "14px 18px",
        display: "flex", alignItems: "center", gap: 10, position: "sticky", top: 0, zIndex: 10
      }}>
        <div style={{ width: 32, height: 32, borderRadius: 10, background: "linear-gradient(135deg,#3f51b5,#5c6bc0)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16 }}>⚡</div>
        <div>
          <div style={{ fontWeight: 800, fontSize: 16, letterSpacing: .5 }}>AlphaPulse</div>
          <div style={{ fontSize: 10, color: "#5c6bc0", letterSpacing: 1, textTransform: "uppercase" }}>AI Forex Engine</div>
        </div>
        <div style={{ marginLeft: "auto", background: "rgba(0,229,160,.1)", border: "1px solid rgba(0,229,160,.3)", borderRadius: 20, padding: "3px 10px", fontSize: 11, color: "#00e5a0", display: "flex", gap: 5, alignItems: "center" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#00e5a0", animation: "pulse 2s infinite" }} />
          LIVE
        </div>
      </div>

      {/* Content */}
      <div style={{ padding: "16px 16px 0" }}>
        {tab === "signals" && <SignalsTab />}
        {tab === "vip" && <VipTab />}
        {tab === "market" && <MarketTab />}
        {tab === "settings" && <SettingsTab />}
        {tab === "stats" && <StatsTab />}
      </div>

      {/* Bottom Nav */}
      <div style={{
