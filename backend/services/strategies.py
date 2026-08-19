"""Three independent OTC strategies.

Each strategy evaluates independently. The scanner may compare confirmed candidates and choose the strongest current market setup; there is no indicator voting between strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional
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

    def as_dict(self) -> dict:
        return asdict(self)


STRATEGY_LABELS = {
    "ema_trend": "EMA Trend + MACD + RSI",
    "bollinger_reversal": "Bollinger + RSI Reversal",
    "atr_breakout": "ATR Volatility Breakout",
}


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) == 0:
        return values
    alpha = 2.0 / (period + 1.0)
    out = np.empty(len(values), dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(values), 50.0, dtype=float)
    if len(values) < 2:
        return out
    delta = np.diff(values, prepend=values[0])
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    avg_gain = _ema(gains, period)
    avg_loss = _ema(losses, period)
    rs = avg_gain / np.maximum(avg_loss, 1e-12)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return _ema(tr, period)


def _macd(close: np.ndarray):
    fast = _ema(close, 12)
    slow = _ema(close, 26)
    line = fast - slow
    signal = _ema(line, 9)
    hist = line - signal
    return line, signal, hist


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(period - 1, len(values)):
        out[i] = values[i - period + 1 : i + 1].mean()
    return out


def _std(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i in range(period - 1, len(values)):
        out[i] = values[i - period + 1 : i + 1].std(ddof=0)
    return out


def _clip(v: float, lo: float = -5.0, hi: float = 5.0) -> float:
    return float(max(lo, min(hi, v)))


def _base(candles: list) -> dict:
    if len(candles) < 80:
        raise ValueError("At least 80 candles are required")
    op = np.asarray([float(c["open"]) for c in candles], dtype=float)
    hi = np.asarray([float(c["high"]) for c in candles], dtype=float)
    lo = np.asarray([float(c["low"]) for c in candles], dtype=float)
    cl = np.asarray([float(c["close"]) for c in candles], dtype=float)
    ema20 = _ema(cl, 20)
    ema50 = _ema(cl, 50)
    ema200 = _ema(cl, 200)
    rsi = _rsi(cl, 14)
    atr = _atr(hi, lo, cl, 14)
    macd, macd_sig, macd_hist = _macd(cl)
    mid = _sma(cl, 20)
    sd = _std(cl, 20)
    upper = mid + 2.0 * sd
    lower = mid - 2.0 * sd
    return {"open": op,"high": hi,"low": lo,"close": cl,"ema20": ema20,"ema50": ema50,"ema200": ema200,"rsi": rsi,"atr": atr,"macd": macd,"macd_signal": macd_sig,"macd_hist": macd_hist,"bb_mid": mid,"bb_upper": upper,"bb_lower": lower}


def _features(x: dict) -> List[float]:
    i = -1
    close=x["close"]; op=x["open"]; hi=x["high"]; lo=x["low"]
    atr=max(float(x["atr"][i]),1e-9)
    mid=float(x["bb_mid"][i]) if not math.isnan(float(x["bb_mid"][i])) else float(close[i])
    sd=max((float(x["bb_upper"][i])-float(x["bb_lower"][i]))/4.0,1e-9)
    high20=float(np.max(hi[-21:-1])); low20=float(np.min(lo[-21:-1])); range20=max(high20-low20,1e-9)
    body=float(close[i]-op[i]); upper_wick=float(hi[i]-max(op[i],close[i])); lower_wick=float(min(op[i],close[i])-lo[i]); slope=float(x["ema20"][i]-x["ema20"][-6])/atr
    return [_clip((float(x["rsi"][i])-50.0)/10.0),_clip((float(close[i])-float(x["ema20"][i]))/atr),_clip((float(x["ema20"][i])-float(x["ema50"][i]))/atr),_clip((float(x["ema50"][i])-float(x["ema200"][i]))/atr),_clip(float(x["macd_hist"][i])/atr),_clip((float(close[i])-mid)/sd),_clip((atr/max(abs(float(close[i])),1e-9))*1000.0),_clip((float(close[i])-low20)/range20*2.0-1.0),_clip(body/atr),_clip(upper_wick/atr),_clip(lower_wick/atr),_clip(slope)]


def ema_trend(candles: list) -> Optional[StrategyCandidate]:
    x=_base(candles); i=-1; price=float(x["close"][i]); atr=max(float(x["atr"][i]),1e-9); e20,e50,e200=float(x["ema20"][i]),float(x["ema50"][i]),float(x["ema200"][i]); rsi=float(x["rsi"][i]); hist=float(x["macd_hist"][i]); prev_hist=float(x["macd_hist"][i-1]); near_ema20=abs(price-e20)<=0.65*atr
    bull=e20>e50>e200 and price>=e20 and near_ema20 and 48<=rsi<=68 and hist>prev_hist
    bear=e20<e50<e200 and price<=e20 and near_ema20 and 32<=rsi<=52 and hist<prev_hist
    if not bull and not bear: return None
    direction="BUY" if bull else "SELL"; separation=min(abs(e20-e50)/atr,2.0); slope=abs(e20-float(x["ema20"][-6]))/atr; momentum=min(abs(hist)/atr*15.0,1.5); confidence=min(70.0+6.0*min(separation,1.5)+4.0*min(slope,1.5)+3.0*momentum,91.0)
    reason=f"EMA20/50/200 aligned {'up' if bull else 'down'}; price pulled back to EMA20; RSI {rsi:.1f}; MACD momentum re-confirmed."
    return StrategyCandidate("ema_trend",direction,round(confidence,1),reason,_features(x),{"rsi":round(rsi,1),"ema20":e20,"ema50":e50,"ema200":e200,"macd_hist":hist,"atr":atr})


def bollinger_reversal(candles: list) -> Optional[StrategyCandidate]:
    x=_base(candles); i=-1; price=float(x["close"][i]); prev_close=float(x["close"][i-1]); upper,lower=float(x["bb_upper"][i]),float(x["bb_lower"][i]); prev_upper,prev_lower=float(x["bb_upper"][i-1]),float(x["bb_lower"][i-1])
    if any(math.isnan(v) for v in [upper,lower,prev_upper,prev_lower]): return None
    atr=max(float(x["atr"][i]),1e-9); rsi=float(x["rsi"][i]); ema_gap=abs(float(x["ema20"][i])-float(x["ema50"][i]))/atr; ranging=ema_gap<=1.25
    bull=ranging and prev_close<=prev_lower and price>lower and rsi<=38; bear=ranging and prev_close>=prev_upper and price<upper and rsi>=62
    if not bull and not bear: return None
    direction="BUY" if bull else "SELL"; excursion=abs(prev_close-(prev_lower if bull else prev_upper))/atr; confidence=min(71.0+min(excursion*8.0,8.0)+min(abs(rsi-50.0)/4.0,8.0)+max(0.0,(1.25-ema_gap)*3.0),92.0)
    reason=f"Price moved outside the {'lower' if bull else 'upper'} Bollinger Band and closed back inside; RSI {rsi:.1f}; EMA gap indicates a non-trending/range regime."
    return StrategyCandidate("bollinger_reversal",direction,round(confidence,1),reason,_features(x),{"rsi":round(rsi,1),"bb_upper":upper,"bb_lower":lower,"ema_gap_atr":round(ema_gap,3),"atr":atr})


def atr_breakout(candles: list) -> Optional[StrategyCandidate]:
    x=_base(candles); i=-1; price=float(x["close"][i]); prev_price=float(x["close"][i-1]); atr=max(float(x["atr"][i]),1e-9); atr_avg=float(np.mean(x["atr"][-21:-1])); high20=float(np.max(x["high"][-21:-1])); low20=float(np.min(x["low"][-21:-1])); hist=float(x["macd_hist"][i]); rsi=float(x["rsi"][i]); expansion=atr/max(atr_avg,1e-9)
    bull=prev_price<=high20 and price>high20 and expansion>=1.05 and hist>0 and 52<=rsi<=78; bear=prev_price>=low20 and price<low20 and expansion>=1.05 and hist<0 and 22<=rsi<=48
    if not bull and not bear: return None
    direction="BUY" if bull else "SELL"; level=high20 if bull else low20; break_size=abs(price-level)/atr; confidence=min(72.0+min((expansion-1.0)*18.0,9.0)+min(break_size*9.0,7.0)+min(abs(hist)/atr*12.0,5.0),93.0)
    reason=f"20-candle {'resistance' if bull else 'support'} broken; ATR expanded to {expansion:.2f}x its recent mean; RSI {rsi:.1f} and MACD confirm breakout momentum."
    return StrategyCandidate("atr_breakout",direction,round(confidence,1),reason,_features(x),{"rsi":round(rsi,1),"breakout_level":level,"atr_expansion":round(expansion,3),"macd_hist":hist,"atr":atr})


STRATEGIES: Dict[str, Callable[[list], Optional[StrategyCandidate]]] = {"ema_trend":ema_trend,"bollinger_reversal":bollinger_reversal,"atr_breakout":atr_breakout}

def evaluate(strategy: str, candles: list) -> Optional[StrategyCandidate]:
    if strategy not in STRATEGIES: raise ValueError(f"Unknown strategy: {strategy}")
    return STRATEGIES[strategy](candles)


def indicator_snapshot(candles: list) -> dict:
    x=_base(candles); i=-1; rsi=float(x['rsi'][i]); e20,e50=float(x['ema20'][i]),float(x['ema50'][i]); hist=float(x['macd_hist'][i]); trend='BULLISH' if e20>=e50 else 'BEARISH'; direction='BUY' if (e20>=e50 and hist>=0) else 'SELL' if (e20<e50 and hist<0) else ('BUY' if rsi<45 else 'SELL'); strength=min(95.0,55.0+abs(e20-e50)/max(float(x['atr'][i]),1e-9)*12.0+min(abs(hist)/max(float(x['atr'][i]),1e-9)*20.0,15.0))
    return {'direction':direction,'confidence':round(strength,1),'trend':trend,'indicators':{'RSI':round(rsi,1),'EMA':'Bull' if e20>=e50 else 'Bear','MACD':'Positive' if hist>=0 else 'Negative'}}
