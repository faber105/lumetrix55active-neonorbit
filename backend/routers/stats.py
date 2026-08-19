from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.db_models import Signal, SignalResult
from backend.services.database import get_db
from backend.services.online_ml import get_model
from backend.services.strategies import STRATEGY_LABELS
router=APIRouter()
async def build(db):
    total=(await db.execute(select(func.count(Signal.id)))).scalar() or 0
    vip_total=(await db.execute(select(func.count(Signal.id)).where(Signal.is_vip==True))).scalar() or 0
    wins=(await db.execute(select(func.count(Signal.id)).where(Signal.result==SignalResult.WIN))).scalar() or 0
    losses=(await db.execute(select(func.count(Signal.id)).where(Signal.result==SignalResult.LOSS))).scalar() or 0
    vw=(await db.execute(select(func.count(Signal.id)).where(Signal.is_vip==True,Signal.result==SignalResult.WIN))).scalar() or 0
    vl=(await db.execute(select(func.count(Signal.id)).where(Signal.is_vip==True,Signal.result==SignalResult.LOSS))).scalar() or 0
    ml={}
    for k in STRATEGY_LABELS:
        m=get_model(k); await m.hydrate(); ml[k]=m.stats()
    return {'total':total,'vip_total':vip_total,'wins':wins,'losses':losses,'winrate':round(wins/(wins+losses)*100,2) if wins+losses else None,'vip_winrate':round(vw/(vw+vl)*100,2) if vw+vl else None,'ml':ml}
@router.get('')
async def root(db:AsyncSession=Depends(get_db)): return await build(db)
@router.get('/summary')
async def summary(db:AsyncSession=Depends(get_db)): return await build(db)
