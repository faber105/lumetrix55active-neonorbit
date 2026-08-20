"""Deterministic OTC strategy rules used by manual, VIP and AUTO modes.

The engine uses independent setup models instead of indicator voting. Each model
must satisfy its own market-regime, momentum and volatility conditions before it
can emit a candidate. Confidence is a heuristic quality score, not a promised
win rate. Every emitted setup is persisted and reconciled against Pocket Option
candles so the online model can learn from actual outcomes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Dict, Iterable, List, Optional
import math

import numpy as np


@dataclass
class StrategyCandidate:
    strategy: str
    direction: str
    confidence: float
    reason: str
    features: List[float]
    indicators: dict
    confirmations: List[str]

    def as_dict(self) -> dict:
        return asdict(self)


STRATEGY_LABELS = {
    "trend_pulse": "Trend Pulse · EMA + ADX/DMI + MACD",
    "range_reversal": "Range Reversal · Bollinger + RSI + ADX",
    "volatility_breakout": "Volatility Breakout · Donchian + ATR + DMI",
    "momentum_retest": "Momentum Retest · EMA + DMI + MACD",
    "squeeze_breakout": "Squeeze Breakout · Bollinger Width + Donchian + ATR",
    "vip_confluence": "VIP 5M Confluence",
}

# The user-facing first AUTO mode intentionally exposes only these three.
AUTO_STRATEGIES = ("trend_pulse", "range_reversal", "volatility_breakout")

# Smart 5m mode has a wider internal arsenal but still requires one complete,
# independently confirmed setup. It does not average weak signals together.
SMART_STRATEGIES = AUTO_STRATEGIES + ("momentum_retest", "squeeze_breakout")
MANUAL_STRATEGIES = SMART_STRATEGIES


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) == 0:
        return values
    alpha = 2.0 / (period + 1.0)
    out = np.empty(len(values), dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rma(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) == 0:
        return values
    out = np.empty(len(values), dtype=float)
    out[0] = values[0]
    alpha = 1.0 / max(1, period)
    for i in range(1, len(values)):
        out[i] = out[i - 1] + alpha * (values[i] - out[i - 1])
    return out


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if period <= 0:
        return out
    for i in range(period - 1, len(values)):
        out[i] = float(np.mean(values[i - period + 1 : i + 1]))
    return out


def _std(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(period - 1, len(values)):
        out[i] = float(np.std(values[i - period + 1 : i + 1], ddof=0))
    return out


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    if len(values) < 2:
        return np.full(len(values), 50.0)
    delta = np.diff(values, prepend=values[0])
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    avg_gain = _rma(gains, period)
    avg_loss = _rma(losses, period)
    rs = avg_gain / np.maximum(avg_loss, 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    return np.maximum(
        high - low,
        np.maximum(np.abs(high - prev), np.abs(low - prev)),
    )


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    return _rma(_true_range(high, low, close), period)


def _dmi_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = _rma(_true_range(high, low, close), period)
    plus_di = 100.0 * _rma(plus_dm, period) / np.maximum(tr, 1e-12)
    minus_di = 100.0 * _rma(minus_dm, period) / np.maximum(tr, 1e-12)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-12)
    adx = _rma(dx, period)
    return plus_di, minus_di, adx


def _macd(close: np.ndarray):
    line = _ema(close, 12) - _ema(close, 26)
    signal = _ema(line, 9)
    return line, signal, line - signal


def _clip(value: float, lo: float = -5.0, hi: float = 5.0) -> float:
    return float(max(lo, min(hi, value)))


def _base(candles: list) -> dict:
    if len(candles) < 100:
        raise ValueError("At least 100 candles are required")

    op = np.asarray([float(c["open"]) for c in candles], dtype=float)
    hi = np.asarray([float(c["high"]) for c in candles], dtype=float)
    lo = np.asarray([float(c["low"]) for c in candles], dtype=float)
    cl = np.asarray([float(c["close"]) for c in candles], dtype=float)

    ema9 = _ema(cl, 9)
    ema20 = _ema(cl, 20)
    ema21 = _ema(cl, 21)
    ema50 = _ema(cl, 50)
    ema200 = _ema(cl, 200)
    rsi = _rsi(cl, 14)
    atr = _atr(hi, lo, cl, 14)
    plus_di, minus_di, adx = _dmi_adx(hi, lo, cl, 14)
    macd, macd_signal, macd_hist = _macd(cl)
    bb_mid = _sma(cl, 20)
    bb_sd = _std(cl, 20)
    bb_upper = bb_mid + 2.0 * bb_sd
    bb_lower = bb_mid - 2.0 * bb_sd
    bb_width = (bb_upper - bb_lower) / np.maximum(np.abs(bb_mid), 1e-12)

    return {
        "open": op,
        "high": hi,
        "low": lo,
        "close": cl,
        "ema9": ema9,
        "ema20": ema20,
        "ema21": ema21,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "atr": atr,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "adx": adx,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
    }


def _features(x: dict) -> List[float]:
    i = -1
    close = x["close"]
    atr = max(float(x["atr"][i]), 1e-9)
    mid = float(x["bb_mid"][i])
    if math.isnan(mid):
        mid = float(close[i])
    band_sd = max(
        (float(x["bb_upper"][i]) - float(x["bb_lower"][i])) / 4.0,
        1e-9,
    )
    high20 = float(np.max(x["high"][-21:-1]))
    low20 = float(np.min(x["low"][-21:-1]))
    range20 = max(high20 - low20, 1e-9)
    body = float(close[i] - x["open"][i])

    return [
        _clip((float(x["rsi"][i]) - 50.0) / 10.0),
        _clip((float(close[i]) - float(x["ema20"][i])) / atr),
        _clip((float(x["ema9"][i]) - float(x["ema21"][i])) / atr),
        _clip((float(x["ema50"][i]) - float(x["ema200"][i])) / atr),
        _clip(float(x["macd_hist"][i]) / atr),
        _clip((float(close[i]) - mid) / band_sd),
        _clip((atr / max(abs(float(close[i])), 1e-9)) * 1000.0),
        _clip((float(close[i]) - low20) / range20 * 2.0 - 1.0),
        _clip(body / atr),
        _clip((float(x["plus_di"][i]) - float(x["minus_di"][i])) / 10.0),
        _clip((float(x["adx"][i]) - 20.0) / 10.0),
        _clip((float(x["ema20"][i]) - float(x["ema20"][-6])) / atr),
    ]


def _candidate(name, direction, confidence, reason, x, confirmations):
    i = -1
    return StrategyCandidate(
        name,
        direction,
        round(max(0.0, min(96.0, confidence)), 1),
        reason,
        _features(x),
        {
            "rsi": round(float(x["rsi"][i]), 1),
            "adx": round(float(x["adx"][i]), 1),
            "plus_di": round(float(x["plus_di"][i]), 1),
            "minus_di": round(float(x["minus_di"][i]), 1),
            "macd_hist": float(x["macd_hist"][i]),
            "atr": float(x["atr"][i]),
            "ema20": float(x["ema20"][i]),
            "ema50": float(x["ema50"][i]),
            "ema200": float(x["ema200"][i]),
            "bb_width": float(x["bb_width"][i]) if not math.isnan(float(x["bb_width"][i])) else None,
        },
        list(confirmations),
    )


def trend_pulse(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles)
    i = -1
    price = float(x["close"][i])
    atr = max(float(x["atr"][i]), 1e-9)
    e9, e21, e50, e200 = (float(x[k][i]) for k in ("ema9", "ema21", "ema50", "ema200"))
    rsi = float(x["rsi"][i])
    adx = float(x["adx"][i])
    pdi = float(x["plus_di"][i])
    mdi = float(x["minus_di"][i])
    hist = float(x["macd_hist"][i])
    prev_hist = float(x["macd_hist"][i - 1])
    pullback = abs(price - e21) <= 0.85 * atr

    bull = e9 > e21 > e50 > e200 and adx >= 25 and pdi >= mdi + 3 and hist > 0 and hist >= prev_hist and 51 <= rsi <= 69 and price >= e9 and pullback
    bear = e9 < e21 < e50 < e200 and adx >= 25 and mdi >= pdi + 3 and hist < 0 and hist <= prev_hist and 31 <= rsi <= 49 and price <= e9 and pullback
    if not bull and not bear:
        return None
    direction = "BUY" if bull else "SELL"
    di_gap = abs(pdi - mdi)
    ema_sep = abs(e21 - e50) / atr
    confidence = 73.0 + min((adx - 25.0) * 0.45, 7.0) + min(di_gap * 0.22, 5.5) + min(ema_sep * 3.0, 4.5) + min(abs(hist) / atr * 12.0, 4.0)
    return _candidate("trend_pulse", direction, confidence, f"Strong {'bullish' if bull else 'bearish'} trend: EMA stack, ADX/DMI and MACD agree; RSI {rsi:.1f}; price returned to the EMA21 value zone.", x, ["EMA 9/21/50/200 aligned", f"ADX {adx:.1f}", "DMI direction confirmed", "MACD momentum confirmed", f"RSI {rsi:.1f}", "pullback near EMA21"])


def range_reversal(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles)
    i = -1
    price = float(x["close"][i]); prev_close = float(x["close"][i - 1]); op = float(x["open"][i]); hi = float(x["high"][i]); lo = float(x["low"][i])
    upper = float(x["bb_upper"][i]); lower = float(x["bb_lower"][i]); prev_upper = float(x["bb_upper"][i - 1]); prev_lower = float(x["bb_lower"][i - 1])
    if any(math.isnan(v) for v in (upper, lower, prev_upper, prev_lower)):
        return None
    atr = max(float(x["atr"][i]), 1e-9); rsi = float(x["rsi"][i]); adx = float(x["adx"][i]); ema_gap = abs(float(x["ema20"][i]) - float(x["ema50"][i])) / atr
    lower_wick = min(op, price) - lo; upper_wick = hi - max(op, price); body = max(abs(price - op), atr * 0.05)
    bull = adx <= 20 and ema_gap <= 1.2 and prev_close <= prev_lower and price > lower and rsi <= 38 and lower_wick >= body * 0.8
    bear = adx <= 20 and ema_gap <= 1.2 and prev_close >= prev_upper and price < upper and rsi >= 62 and upper_wick >= body * 0.8
    if not bull and not bear:
        return None
    direction = "BUY" if bull else "SELL"; excursion = abs(prev_close - (prev_lower if bull else prev_upper)) / atr
    confidence = 72.0 + min((20.0 - adx) * 0.45, 4.0) + min(abs(rsi - 50.0) * 0.28, 5.0) + min(excursion * 6.0, 5.0) + min((1.2 - ema_gap) * 2.5, 3.0)
    return _candidate("range_reversal", direction, confidence, f"Range reversal: price rejected the {'lower' if bull else 'upper'} Bollinger band, RSI reached an extreme and ADX {adx:.1f} confirms a non-trending regime.", x, ["Bollinger outer-band rejection", f"RSI {rsi:.1f}", f"ADX {adx:.1f} range regime", "rejection wick", "EMA20/50 gap controlled"])


def volatility_breakout(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles); i = -1
    price = float(x["close"][i]); prev = float(x["close"][i - 1]); op = float(x["open"][i]); atr = max(float(x["atr"][i]), 1e-9); atr_avg = float(np.mean(x["atr"][-21:-1])); expansion = atr / max(atr_avg, 1e-9)
    high20 = float(np.max(x["high"][-21:-1])); low20 = float(np.min(x["low"][-21:-1])); adx = float(x["adx"][i]); prev_adx = float(x["adx"][i - 1]); pdi = float(x["plus_di"][i]); mdi = float(x["minus_di"][i]); rsi = float(x["rsi"][i]); hist = float(x["macd_hist"][i]); body_strength = abs(price - op) / atr
    bull = prev <= high20 and price > high20 and expansion >= 1.08 and adx >= 25 and adx >= prev_adx and pdi > mdi and hist > 0 and 53 <= rsi <= 78 and body_strength >= 0.48
    bear = prev >= low20 and price < low20 and expansion >= 1.08 and adx >= 25 and adx >= prev_adx and mdi > pdi and hist < 0 and 22 <= rsi <= 47 and body_strength >= 0.48
    if not bull and not bear:
        return None
    direction = "BUY" if bull else "SELL"; level = high20 if bull else low20; break_size = abs(price - level) / atr
    confidence = 74.0 + min((expansion - 1.0) * 16.0, 6.0) + min((adx - 25.0) * 0.35, 5.0) + min(break_size * 8.0, 4.0) + min(body_strength * 2.0, 3.0)
    return _candidate("volatility_breakout", direction, confidence, f"Volatility breakout through Donchian {'resistance' if bull else 'support'} with ATR expansion {expansion:.2f}x, rising ADX and directional DMI/MACD confirmation.", x, ["Donchian 20 breakout", f"ATR expansion {expansion:.2f}x", f"ADX {adx:.1f} rising", "DMI direction confirmed", "MACD momentum", "strong breakout candle"])


def momentum_retest(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles); i = -1
    price = float(x["close"][i]); prev_price = float(x["close"][i - 1]); op = float(x["open"][i]); atr = max(float(x["atr"][i]), 1e-9); e20 = float(x["ema20"][i]); e50 = float(x["ema50"][i]); e200 = float(x["ema200"][i]); rsi = float(x["rsi"][i]); adx = float(x["adx"][i]); pdi = float(x["plus_di"][i]); mdi = float(x["minus_di"][i]); hist = float(x["macd_hist"][i]); prev_hist = float(x["macd_hist"][i - 1]); candle_body = abs(price - op) / atr; near_value = abs(price - e20) <= 0.75 * atr
    bull = e20 > e50 > e200 and adx >= 22 and pdi > mdi and 48 <= rsi <= 64 and near_value and hist > prev_hist and price > op and price >= e20 and prev_price <= price and candle_body >= 0.18
    bear = e20 < e50 < e200 and adx >= 22 and mdi > pdi and 36 <= rsi <= 52 and near_value and hist < prev_hist and price < op and price <= e20 and prev_price >= price and candle_body >= 0.18
    if not bull and not bear:
        return None
    direction = "BUY" if bull else "SELL"; di_gap = abs(pdi - mdi); slope = abs(e20 - float(x["ema20"][-5])) / atr
    confidence = 75.0 + min(max(adx - 22.0, 0.0) * 0.35, 5.0) + min(di_gap * 0.18, 4.5) + min(slope * 3.0, 4.0) + min(abs(hist - prev_hist) / atr * 15.0, 4.0)
    return _candidate("momentum_retest", direction, confidence, f"Momentum retest: established {'uptrend' if bull else 'downtrend'} held the EMA20 value zone; DMI, MACD acceleration and RSI {rsi:.1f} confirm continuation.", x, ["EMA20/50/200 trend intact", f"ADX {adx:.1f}", "DMI direction confirmed", "EMA20 retest held", "MACD re-acceleration", f"RSI {rsi:.1f}"])


def squeeze_breakout(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles); i = -1
    price = float(x["close"][i]); prev = float(x["close"][i - 1]); op = float(x["open"][i]); atr = max(float(x["atr"][i]), 1e-9); atr_recent = float(np.mean(x["atr"][-21:-1])); expansion = atr / max(atr_recent, 1e-9); high20 = float(np.max(x["high"][-21:-1])); low20 = float(np.min(x["low"][-21:-1])); adx = float(x["adx"][i]); prev_adx = float(x["adx"][i - 1]); pdi = float(x["plus_di"][i]); mdi = float(x["minus_di"][i]); hist = float(x["macd_hist"][i]); rsi = float(x["rsi"][i]); body_strength = abs(price - op) / atr
    widths = np.asarray(x["bb_width"][-60:-1], dtype=float); widths = widths[np.isfinite(widths)]; recent_widths = np.asarray(x["bb_width"][-12:-1], dtype=float); recent_widths = recent_widths[np.isfinite(recent_widths)]
    if len(widths) < 25 or len(recent_widths) < 5:
        return None
    baseline = float(np.median(widths)); squeeze_width = float(np.min(recent_widths)); squeeze = squeeze_width <= baseline * 0.72
    bull = squeeze and prev <= high20 and price > high20 and expansion >= 1.05 and adx >= 20 and adx > prev_adx and pdi > mdi and hist > 0 and 52 <= rsi <= 76 and body_strength >= 0.42
    bear = squeeze and prev >= low20 and price < low20 and expansion >= 1.05 and adx >= 20 and adx > prev_adx and mdi > pdi and hist < 0 and 24 <= rsi <= 48 and body_strength >= 0.42
    if not bull and not bear:
        return None
    direction = "BUY" if bull else "SELL"; compression = 1.0 - min(1.0, squeeze_width / max(baseline, 1e-12))
    confidence = 76.0 + min(compression * 10.0, 5.0) + min((expansion - 1.0) * 18.0, 5.0) + min(max(adx - 20.0, 0.0) * 0.25, 4.0) + min(body_strength * 2.0, 3.0)
    return _candidate("squeeze_breakout", direction, confidence, f"Bollinger squeeze released through Donchian {'resistance' if bull else 'support'}; ATR expanded {expansion:.2f}x and rising ADX/DMI confirm the move.", x, ["Bollinger width compression", "Donchian 20 breakout", f"ATR expansion {expansion:.2f}x", f"ADX {adx:.1f} rising", "DMI direction confirmed", "MACD momentum confirmed"])


def vip_confluence(candles: list) -> Optional[StrategyCandidate]:
    x = _base(candles); i = -1
    price = float(x["close"][i]); atr = max(float(x["atr"][i]), 1e-9); e20 = float(x["ema20"][i]); e50 = float(x["ema50"][i]); e200 = float(x["ema200"][i]); rsi = float(x["rsi"][i]); adx = float(x["adx"][i]); pdi = float(x["plus_di"][i]); mdi = float(x["minus_di"][i]); hist = float(x["macd_hist"][i]); prev_hist = float(x["macd_hist"][i - 1]); high20 = float(np.max(x["high"][-21:-1])); low20 = float(np.min(x["low"][-21:-1]))
    bp = sp = 0.0; bc: list[str] = []; sc: list[str] = []
    if e20 > e50 > e200: bp += 2.0; bc.append("EMA20/50/200 uptrend")
    if e20 < e50 < e200: sp += 2.0; sc.append("EMA20/50/200 downtrend")
    if adx >= 25 and pdi > mdi: bp += 1.5; bc.append(f"ADX/DMI {adx:.1f}")
    if adx >= 25 and mdi > pdi: sp += 1.5; sc.append(f"ADX/DMI {adx:.1f}")
    if hist > 0 and hist >= prev_hist: bp += 1.25; bc.append("MACD accelerating")
    if hist < 0 and hist <= prev_hist: sp += 1.25; sc.append("MACD accelerating")
    if 52 <= rsi <= 68: bp += 1.0; bc.append(f"RSI {rsi:.1f}")
    if 32 <= rsi <= 48: sp += 1.0; sc.append(f"RSI {rsi:.1f}")
    if price > high20: bp += 1.0; bc.append("20-bar high breakout")
    if price < low20: sp += 1.0; sc.append("20-bar low breakout")
    if price >= e20 and abs(price - e20) <= 0.8 * atr: bp += 0.75; bc.append("EMA20 value-zone support")
    if price <= e20 and abs(price - e20) <= 0.8 * atr: sp += 0.75; sc.append("EMA20 value-zone resistance")
    if max(bp, sp) < 5.0 or abs(bp - sp) < 1.5:
        return None
    bull = bp > sp; points = bp if bull else sp; direction = "BUY" if bull else "SELL"; confirmations = bc if bull else sc
    confidence = 79.0 + min((points - 5.0) * 4.2, 14.0) + min(max(adx - 25.0, 0.0) * 0.2, 3.0)
    return _candidate("vip_confluence", direction, confidence, f"VIP 5M confluence: {len(confirmations)} independent confirmations agree on {direction}; ADX {adx:.1f}, RSI {rsi:.1f}, trend/momentum and price-location filters aligned.", x, confirmations)


STRATEGIES: Dict[str, Callable[[list], Optional[StrategyCandidate]]] = {
    "trend_pulse": trend_pulse,
    "range_reversal": range_reversal,
    "volatility_breakout": volatility_breakout,
    "momentum_retest": momentum_retest,
    "squeeze_breakout": squeeze_breakout,
    "vip_confluence": vip_confluence,
}


def evaluate(strategy: str, candles: list) -> Optional[StrategyCandidate]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    return STRATEGIES[strategy](candles)


def evaluate_best(candles: list, strategies: Iterable[str] = MANUAL_STRATEGIES) -> Optional[StrategyCandidate]:
    candidates: list[StrategyCandidate] = []
    for strategy in strategies:
        candidate = evaluate(strategy, candles)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None
    return max(candidates, key=lambda c: float(c.confidence))


def indicator_snapshot(candles: list) -> dict:
    x = _base(candles); i = -1
    rsi = float(x["rsi"][i]); adx = float(x["adx"][i]); pdi = float(x["plus_di"][i]); mdi = float(x["minus_di"][i]); hist = float(x["macd_hist"][i]); e20 = float(x["ema20"][i]); e50 = float(x["ema50"][i])
    if adx >= 25 and pdi > mdi and e20 >= e50 and hist >= 0:
        direction = "BUY"; trend = "BULLISH"
    elif adx >= 25 and mdi > pdi and e20 < e50 and hist < 0:
        direction = "SELL"; trend = "BEARISH"
    else:
        direction = "BUY" if hist >= 0 else "SELL"; trend = "RANGE/TRANSITION"
    strength = min(95.0, 50.0 + max(adx - 20.0, 0.0) * 1.2 + abs(pdi - mdi) * 0.35 + min(abs(hist) / max(float(x["atr"][i]), 1e-9) * 20.0, 12.0))
    return {"direction": direction, "confidence": round(strength, 1), "trend": trend, "indicators": {"RSI": round(rsi, 1), "ADX": round(adx, 1), "DMI+": round(pdi, 1), "DMI-": round(mdi, 1), "EMA": "Bull" if e20 >= e50 else "Bear", "MACD": "Positive" if hist >= 0 else "Negative"}}
