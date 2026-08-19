from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StrategyDecision:
    strategy: str
    direction: str | None
    score: float
    regime: str
    reason: str
    features: dict[str, float]


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff(); gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean(); loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean(); rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df['close'].shift(1)
    tr = pd.concat([df['high']-df['low'],(df['high']-prev_close).abs(),(df['low']-prev_close).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up=df['high'].diff(); down=-df['low'].diff(); plus_dm=pd.Series(np.where((up>down)&(up>0),up,0.0),index=df.index); minus_dm=pd.Series(np.where((down>up)&(down>0),down,0.0),index=df.index); atr=_atr(df,period).replace(0,np.nan); plus_di=100*plus_dm.ewm(alpha=1/period,adjust=False).mean()/atr; minus_di=100*minus_dm.ewm(alpha=1/period,adjust=False).mean()/atr; dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0,np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean().fillna(0)


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    df=frame.copy(); c=df['close'].astype(float); df['ema9']=_ema(c,9); df['ema20']=_ema(c,20); df['ema50']=_ema(c,50); df['ema200']=_ema(c,200); df['rsi']=_rsi(c,14); ema12,ema26=_ema(c,12),_ema(c,26); df['macd']=ema12-ema26; df['macd_signal']=_ema(df['macd'],9); df['macd_hist']=df['macd']-df['macd_signal']; df['atr']=_atr(df,14); mid=c.rolling(20).mean(); std=c.rolling(20).std(ddof=0); df['bb_mid']=mid; df['bb_upper']=mid+2*std; df['bb_lower']=mid-2*std; df['donchian_high']=df['high'].shift(1).rolling(20).max(); df['donchian_low']=df['low'].shift(1).rolling(20).min(); df['adx']=_adx(df,14); df['body']=(df['close']-df['open']).abs(); df['range']=(df['high']-df['low']).replace(0,np.nan); df['body_ratio']=(df['body']/df['range']).fillna(0); df['momentum3']=c.pct_change(3).fillna(0); df['momentum10']=c.pct_change(10).fillna(0)
    return df.replace([np.inf,-np.inf],np.nan).ffill().fillna(0)


def feature_snapshot(df: pd.DataFrame, timeframe_seconds: int) -> dict[str,float]:
    row=df.iloc[-1]; close=max(abs(float(row['close'])),1e-12); atr=float(row['atr']); width=max(float(row['bb_upper']-row['bb_lower']),1e-12); bb_pos=(float(row['close'])-float(row['bb_lower']))/width
    return {'rsi':float(row['rsi'])/100.0,'macd_hist_pct':float(row['macd_hist'])/close,'ema20_50_pct':float(row['ema20']-row['ema50'])/close,'ema9_20_pct':float(row['ema9']-row['ema20'])/close,'ema50_200_pct':float(row['ema50']-row['ema200'])/close,'atr_pct':atr/close,'bb_position':float(np.clip(bb_pos,-1,2)),'adx':float(row['adx'])/100.0,'body_atr':float(row['body'])/max(atr,1e-12),'body_ratio':float(row['body_ratio']),'momentum3':float(row['momentum3']),'momentum10':float(row['momentum10']),'distance_high_atr':(float(row['close'])-float(row['donchian_high']))/max(atr,1e-12),'distance_low_atr':(float(row['close'])-float(row['donchian_low']))/max(atr,1e-12),'timeframe_norm':min(timeframe_seconds/300.0,1.0)}


def detect_regime(df: pd.DataFrame) -> str:
    row=df.iloc[-1]; atr=max(float(row['atr']),1e-12); close=float(row['close']); breakout=((close>float(row['donchian_high']) or close<float(row['donchian_low'])) and float(row['body'])>=0.70*atr)
    if breakout:return 'breakout'
    if float(row['adx'])>=22 and abs(float(row['ema20']-row['ema50']))/atr>=0.20:return 'trend'
    return 'range'


def ema_macd_trend(df: pd.DataFrame, features: dict[str,float]) -> StrategyDecision:
    row,prev=df.iloc[-1],df.iloc[-2]; bull=float(row['ema9'])>float(row['ema20'])>float(row['ema50']); bear=float(row['ema9'])<float(row['ema20'])<float(row['ema50']); macd_up=float(row['macd_hist'])>0 and float(row['macd_hist'])>=float(prev['macd_hist']); macd_down=float(row['macd_hist'])<0 and float(row['macd_hist'])<=float(prev['macd_hist']); rsi=float(row['rsi']); adx=float(row['adx']); score=0.0; direction=None; parts=[]
    if bull and macd_up and 48<=rsi<=72: direction='CALL'; score=0.52+min(adx/100,0.22)+min(abs(features['ema20_50_pct'])*900,0.14); parts=['EMA 9>20>50','MACD momentum up',f'RSI {rsi:.0f}',f'ADX {adx:.0f}']
    elif bear and macd_down and 28<=rsi<=52: direction='PUT'; score=0.52+min(adx/100,0.22)+min(abs(features['ema20_50_pct'])*900,0.14); parts=['EMA 9<20<50','MACD momentum down',f'RSI {rsi:.0f}',f'ADX {adx:.0f}']
    return StrategyDecision('EMA_MACD_TREND',direction,min(score,0.92),'trend',', '.join(parts),features)


def rsi_bollinger_reversal(df: pd.DataFrame, features: dict[str,float]) -> StrategyDecision:
    row,prev=df.iloc[-1],df.iloc[-2]; rsi=float(row['rsi']); direction=None; score=0.0; parts=[]
    if float(prev['close'])<=float(prev['bb_lower']) and float(row['close'])>float(row['bb_lower']) and rsi<=38: direction='CALL'; score=0.60+min((40-rsi)/100,0.12)+min(float(row['body_ratio'])*0.10,0.08); parts=['lower Bollinger rejection',f'RSI {rsi:.0f}','close returned inside band']
    elif float(prev['close'])>=float(prev['bb_upper']) and float(row['close'])<float(row['bb_upper']) and rsi>=62: direction='PUT'; score=0.60+min((rsi-60)/100,0.12)+min(float(row['body_ratio'])*0.10,0.08); parts=['upper Bollinger rejection',f'RSI {rsi:.0f}','close returned inside band']
    return StrategyDecision('RSI_BOLLINGER_REVERSAL',direction,min(score,0.90),'range',', '.join(parts),features)


def donchian_atr_breakout(df: pd.DataFrame, features: dict[str,float]) -> StrategyDecision:
    row=df.iloc[-1]; close,atr=float(row['close']),max(float(row['atr']),1e-12); body_atr=float(row['body'])/atr; direction=None; score=0.0; parts=[]
    if close>float(row['donchian_high']) and body_atr>=0.70 and float(row['macd_hist'])>0: direction='CALL'; score=0.61+min((body_atr-0.7)*0.12,0.15)+min(float(row['adx'])/250,0.12); parts=['20-bar high breakout',f'body {body_atr:.1f} ATR','MACD positive']
    elif close<float(row['donchian_low']) and body_atr>=0.70 and float(row['macd_hist'])<0: direction='PUT'; score=0.61+min((body_atr-0.7)*0.12,0.15)+min(float(row['adx'])/250,0.12); parts=['20-bar low breakout',f'body {body_atr:.1f} ATR','MACD negative']
    return StrategyDecision('DONCHIAN_ATR_BREAKOUT',direction,min(score,0.93),'breakout',', '.join(parts),features)


def choose_strategy(frame: pd.DataFrame, timeframe_seconds: int) -> StrategyDecision:
    if len(frame)<80:return StrategyDecision('NONE',None,0.0,'unknown','not enough candles',{})
    df=enrich(frame); features=feature_snapshot(df,timeframe_seconds); regime=detect_regime(df); decisions=[ema_macd_trend(df,features),rsi_bollinger_reversal(df,features),donchian_atr_breakout(df,features)]; ranked=sorted(decisions,key=lambda d:d.score+(0.08 if d.regime==regime else 0.0),reverse=True); best=ranked[0]
    return StrategyDecision(best.strategy,best.direction,best.score,regime,best.reason,best.features)
