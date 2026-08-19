import React from "react";

function range(values) {
  if (!values.length) return [0, 1];
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (max === min) {
    const pad = Math.max(Math.abs(max) * 0.0005, 0.00001);
    min -= pad;
    max += pad;
  }
  return [min, max];
}

export default function CandleChart({ candles = [], entryPrice = null, currentPrice = null, height = 240 }) {
  const data = candles.slice(-40);
  const width = 720;
  const pad = { l: 18, r: 70, t: 18, b: 26 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const values = data.flatMap((c) => [Number(c.high), Number(c.low)]).filter(Number.isFinite);
  if (Number.isFinite(Number(entryPrice))) values.push(Number(entryPrice));
  if (Number.isFinite(Number(currentPrice))) values.push(Number(currentPrice));
  const [min, max] = range(values);
  const y = (v) => pad.t + ((max - Number(v)) / (max - min)) * plotH;
  const step = data.length ? plotW / data.length : plotW;
  const bodyW = Math.max(3, Math.min(12, step * 0.58));
  const fmt = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return Math.abs(n) >= 100 ? n.toFixed(3) : n.toFixed(5);
  };

  if (!data.length) {
    return <div className="chart-empty">Ждём данные свечей…</div>;
  }

  const grid = [0, 0.25, 0.5, 0.75, 1].map((p) => {
    const yy = pad.t + p * plotH;
    const price = max - p * (max - min);
    return (
      <g key={p}>
        <line x1={pad.l} x2={width - pad.r} y1={yy} y2={yy} stroke="rgba(255,255,255,.07)" strokeWidth="1" />
        <text x={width - pad.r + 8} y={yy + 4} fill="#7f8ba8" fontSize="11">{fmt(price)}</text>
      </g>
    );
  });

  return (
    <div className="chart-shell">
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        <rect x="0" y="0" width={width} height={height} rx="16" fill="#0f1522" />
        {grid}
        {data.map((c, index) => {
          const open = Number(c.open);
          const close = Number(c.close);
          const high = Number(c.high);
          const low = Number(c.low);
          const x = pad.l + index * step + step / 2;
          const bullish = close >= open;
          const color = bullish ? "#2fe2a2" : "#ff5b78";
          const top = Math.min(y(open), y(close));
          const bodyH = Math.max(2, Math.abs(y(close) - y(open)));
          return (
            <g key={`${c.time}-${index}`}>
              <line x1={x} x2={x} y1={y(high)} y2={y(low)} stroke={color} strokeWidth="1.4" />
              <rect x={x - bodyW / 2} y={top} width={bodyW} height={bodyH} rx="1.5" fill={color} opacity="0.95" />
            </g>
          );
        })}
        {Number.isFinite(Number(entryPrice)) && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={y(entryPrice)} y2={y(entryPrice)} stroke="#7c83ff" strokeWidth="1.5" strokeDasharray="5 5" />
            <text x={pad.l + 6} y={y(entryPrice) - 6} fill="#9da2ff" fontSize="11" fontWeight="700">ENTRY {fmt(entryPrice)}</text>
          </g>
        )}
        {Number.isFinite(Number(currentPrice)) && (
          <g>
            <line x1={pad.l} x2={width - pad.r} y1={y(currentPrice)} y2={y(currentPrice)} stroke="#ffd166" strokeWidth="1.2" strokeDasharray="3 4" />
            <rect x={width - pad.r + 3} y={y(currentPrice) - 10} width="64" height="20" rx="7" fill="#ffd166" />
            <text x={width - pad.r + 8} y={y(currentPrice) + 4} fill="#161923" fontSize="10" fontWeight="800">{fmt(currentPrice)}</text>
          </g>
        )}
      </svg>
    </div>
  );
}
