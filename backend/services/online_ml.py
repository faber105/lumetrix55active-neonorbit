"""Tiny online logistic model persisted in Postgres, one model per strategy."""
from __future__ import annotations

import asyncio, json, math, os
from typing import Dict, List
from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, MLState, utcnow

FEATURE_COUNT = 12


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


MIN_SAMPLES_FOR_INFLUENCE = _env_int('ML_MIN_SAMPLES', 40)
LEARNING_RATE = _env_float('ML_LEARNING_RATE', 0.035)
L2 = _env_float('ML_L2', 0.0005)


class OnlineStrategyModel:
    def __init__(self, strategy: str):
        self.strategy=strategy
        self.weights=[0.0]*FEATURE_COUNT
        self.bias=0.0; self.samples=0; self.wins=0; self.losses=0
        self._lock=asyncio.Lock(); self._hydrated=False

    async def hydrate(self):
        if self._hydrated: return
        async with self._lock:
            if self._hydrated: return
            async with AsyncSessionLocal() as db:
                state=await db.get(MLState, self.strategy)
                if state and state.payload:
                    try:
                        data=json.loads(state.payload)
                        weights=list(map(float,data.get('weights',[])))
                        if len(weights)==FEATURE_COUNT:
                            self.weights=weights; self.bias=float(data.get('bias',0.0))
                            self.samples=int(data.get('samples',0)); self.wins=int(data.get('wins',0)); self.losses=int(data.get('losses',0))
                    except Exception:
                        pass
            self._hydrated=True

    async def _persist(self):
        payload=json.dumps({'version':2,'strategy':self.strategy,'weights':self.weights,'bias':self.bias,'samples':self.samples,'wins':self.wins,'losses':self.losses},separators=(',',':'))
        async with AsyncSessionLocal() as db:
            state=await db.get(MLState,self.strategy)
            if state is None:
                state=MLState(strategy=self.strategy,payload=payload,samples=self.samples,updated_at=utcnow()); db.add(state)
            else:
                state.payload=payload; state.samples=self.samples; state.updated_at=utcnow()
            await db.commit()

    @staticmethod
    def _sigmoid(z: float) -> float:
        z=max(-30.0,min(30.0,z)); return 1.0/(1.0+math.exp(-z))

    @staticmethod
    def _clean(features: List[float]) -> List[float]:
        vals=[max(-5.0,min(5.0,float(x))) for x in features[:FEATURE_COUNT]]
        vals.extend([0.0]*(FEATURE_COUNT-len(vals))); return vals

    def probability_setup_wins(self, features: List[float]) -> float:
        x=self._clean(features); return self._sigmoid(self.bias+sum(w*v for w,v in zip(self.weights,x)))

    def influence_ready(self): return self.samples>=MIN_SAMPLES_FOR_INFLUENCE

    async def learn(self, features: List[float], won: bool) -> float:
        await self.hydrate(); x=self._clean(features); y=1.0 if won else 0.0
        async with self._lock:
            p=self.probability_setup_wins(x); error=y-p; lr=LEARNING_RATE/math.sqrt(1.0+self.samples/200.0)
            for i in range(FEATURE_COUNT): self.weights[i]+=lr*(error*x[i]-L2*self.weights[i])
            self.bias+=lr*error; self.samples+=1
            if won: self.wins+=1
            else: self.losses+=1
            await self._persist()
            return self.probability_setup_wins(x)

    def stats(self):
        resolved=self.wins+self.losses
        return {'strategy':self.strategy,'samples':self.samples,'wins':self.wins,'losses':self.losses,'winrate':round(self.wins/resolved*100,2) if resolved else None,'influence_ready':self.influence_ready()}

_models: Dict[str,OnlineStrategyModel]={}
def get_model(strategy: str):
    if strategy not in _models: _models[strategy]=OnlineStrategyModel(strategy)
    return _models[strategy]
