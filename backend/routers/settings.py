from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.db_models import UserSettings
from backend.services.database import get_db
router=APIRouter()
class Update(BaseModel): vip_enabled:bool|None=None; notification_frequency:str|None=None; signal_mode:str|None=None
def ser(s): return {'telegram_id':s.telegram_id,'vip_enabled':s.vip_enabled,'notification_frequency':s.notification_frequency,'signal_mode':s.signal_mode}
async def row(db,tid):
    s=(await db.execute(select(UserSettings).where(UserSettings.telegram_id==tid))).scalar_one_or_none()
    if s is None: s=UserSettings(telegram_id=tid); db.add(s); await db.commit(); await db.refresh(s)
    return s
@router.get('/user/{telegram_id}')
async def get(telegram_id:int,db:AsyncSession=Depends(get_db)): return ser(await row(db,telegram_id))
@router.patch('/user/{telegram_id}')
async def patch(telegram_id:int,data:Update,db:AsyncSession=Depends(get_db)):
    s=await row(db,telegram_id)
    if data.vip_enabled is not None: s.vip_enabled=data.vip_enabled
    if data.notification_frequency in {'rarely','standard','often'}: s.notification_frequency=data.notification_frequency
    if data.signal_mode in {'all','vip','mixed'}: s.signal_mode=data.signal_mode
    await db.commit(); await db.refresh(s); return ser(s)
