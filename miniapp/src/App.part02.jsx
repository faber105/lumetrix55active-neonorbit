                    <div style={{ fontSize: 16, fontWeight: 700, color: indColor(v, k) }}>
                      {typeof v === "number" ? v.toFixed(1) : v}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
      {!data && !loading && (
        <div style={{ textAlign: "center", color: "#5c6bc0", padding: 30 }}>No market data available</div>
      )}
    </div>
  );
}

// ─── SETTINGS TAB ────────────────────────────────────────────────────────────
function SettingsTab() {
  const [vip, setVip] = useState(true);
  const [freq, setFreq] = useState("standard");
  const [mode, setMode] = useState("vip");
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState("");

  useEffect(() => {
    if (!TG_USER_ID) { setLoaded(true); return; }
    fetch(`${BACKEND}/api/settings/user/${TG_USER_ID}`)
      .then(r => r.json())
      .then(d => {
        setVip(d.vip_enabled ?? true);
        setFreq(d.notification_frequency || "standard");
        setMode(d.signal_mode || "vip");
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  const persist = async (patch) => {
    if (!TG_USER_ID) return;
    setSaved("Saving...");
    try {
      const r = await fetch(`${BACKEND}/api/settings/user/${TG_USER_ID}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch)
      });
      if (!r.ok) throw new Error();
      setSaved("Saved");
      setTimeout(() => setSaved(""), 1200);
    } catch {
      setSaved("Save failed");
    }
  };

  const changeVip = (value) => { setVip(value); persist({ vip_enabled: value }); };
  const changeFreq = (value) => { setFreq(value); persist({ notification_frequency: value }); };
  const changeMode = (value) => { setMode(value); persist({ signal_mode: value }); };

  return (
    <div style={{ opacity: loaded ? 1 : .7 }}>
      {saved && <div style={{ textAlign: "right", color: saved === "Save failed" ? "#ff4d6d" : "#00e5a0", fontSize: 11, marginBottom: 6 }}>{saved}</div>}
      <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "16px 18px", marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontWeight: 700, color: "#e8eaf6", marginBottom: 2 }}>🔥 VIP Signals</div>
            <div style={{ fontSize: 12, color: "#8892b0" }}>Telegram notifications for confidence ≥ 80%</div>
          </div>
          <div onClick={() => changeVip(!vip)} style={{
            width: 44, height: 24, borderRadius: 12, background: vip ? "#3f51b5" : "#232b3e",
            cursor: "pointer", position: "relative", transition: "background .2s"
          }}>
            <div style={{
              width: 18, height: 18, borderRadius: "50%", background: "#fff",
              position: "absolute", top: 3, left: vip ? 23 : 3, transition: "left .2s"
            }} />
          </div>
        </div>
      </div>

      <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "16px 18px", marginBottom: 12 }}>
        <div style={{ fontWeight: 700, color: "#e8eaf6", marginBottom: 12 }}>📡 Frequency</div>
        {["rarely","standard","often"].map(f => (
          <div key={f} onClick={() => changeFreq(f)} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
            borderBottom: f !== "often" ? "1px solid #1a1f2e" : "none", cursor: "pointer"
          }}>
            <div style={{
              width: 18, height: 18, borderRadius: "50%",
              border: `2px solid ${freq === f ? "#3f51b5" : "#232b3e"}`,
              background: freq === f ? "#3f51b5" : "transparent",
              display: "flex", alignItems: "center", justifyContent: "center"
            }}>
              {freq === f && <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#fff" }} />}
            </div>
            <span style={{ color: freq === f ? "#e8eaf6" : "#8892b0", textTransform: "capitalize", fontSize: 14 }}>{f}</span>
          </div>
        ))}
      </div>

      <div style={{ background: "#141824", border: "1px solid #232b3e", borderRadius: 14, padding: "16px 18px" }}>
        <div style={{ fontWeight: 700, color: "#e8eaf6", marginBottom: 12 }}>⚙️ Signal Mode</div>
        {["all","vip","mixed"].map(m => (
          <div key={m} onClick={() => changeMode(m)} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 0",
            borderBottom: m !== "mixed" ? "1px solid #1a1f2e" : "none", cursor: "pointer"
          }}>
            <div style={{
              width: 18, height: 18, borderRadius: "50%",
              border: `2px solid ${mode === m ? "#3f51b5" : "#232b3e"}`,
              background: mode === m ? "#3f51b5" : "transparent",
              display: "flex", alignItems: "center", justifyContent: "center"
            }}>
              {mode === m && <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#fff" }} />}
            </div>
            <span style={{ color: mode === m ? "#e8eaf6" : "#8892b0", textTransform: "capitalize", fontSize: 14 }}>{m}</span>
          </div>
        ))}
      </div>
    </div>
