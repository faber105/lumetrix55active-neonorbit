        position: "fixed", bottom: 0, left: 0, right: 0,
        background: "#0d1117", borderTop: "1px solid #232b3e",
        display: "flex", padding: "8px 0 12px"
      }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            flex: 1, background: "none", border: "none", cursor: "pointer",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
            opacity: tab === t.id ? 1 : .45, transition: "opacity .2s"
          }}>
            <span style={{ fontSize: 18 }}>{t.icon}</span>
            <span style={{ fontSize: 10, color: tab === t.id ? "#5c6bc0" : "#8892b0", fontWeight: tab === t.id ? 700 : 400, letterSpacing: .5 }}>{t.label}</span>
          </button>
        ))}
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </div>
  );
}
