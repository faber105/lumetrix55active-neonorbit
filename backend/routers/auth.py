from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.db_models import User, UserSettings
from backend.services.database import get_db

router=APIRouter(); REFERRAL_URL=os.getenv('REFERRAL_URL','https://pocketoption.com/'); ADMIN_SECRET=os.getenv('ADMIN_SECRET','change_me')
def now_naive(): return datetime.now(timezone.utc).replace(tzinfo=None)
class UserCreate(BaseModel): telegram_id:int; username:Optional[str]=None; full_name:Optional[str]=None
class StatusUpdate(BaseModel): telegram_id:int; status:str; secret:str
async def _get(db,tid): return (await db.execute(select(User).where(User.telegram_id==tid))).scalar_one_or_none()
def serialize(u): return {'telegram_id':u.telegram_id,'username':u.username,'full_name':u.full_name,'status':u.status,'click_time':u.click_time.isoformat() if u.click_time else None,'pending_time':u.pending_time.isoformat() if u.pending_time else None,'verified_time':u.verified_time.isoformat() if u.verified_time else None,'attempts_count':u.attempts_count,'created_at':u.created_at.isoformat() if u.created_at else None}
@router.get('/go')
async def go(uid:int=Query(...),db:AsyncSession=Depends(get_db)):
    u=await _get(db,uid)
    if not u: raise HTTPException(404,'User not found')
    if u.status=='BLOCKED': raise HTTPException(403,'Access blocked')
    if u.status=='NEW': u.status='CLICKED'; u.click_time=now_naive(); await db.commit()
    return RedirectResponse(REFERRAL_URL,302)
@router.post('/user')
async def create_user(data:UserCreate,db:AsyncSession=Depends(get_db)):
    u=await _get(db,data.telegram_id)
    if u:
        u.username=data.username or u.username; u.full_name=data.full_name or u.full_name
    else:
        u=User(telegram_id=data.telegram_id,username=data.username,full_name=data.full_name,status='NEW'); db.add(u); db.add(UserSettings(telegram_id=data.telegram_id))
    await db.commit(); await db.refresh(u); return serialize(u)
@router.get('/user/{telegram_id}')
async def get_user(telegram_id:int,db:AsyncSession=Depends(get_db)):
    u=await _get(db,telegram_id)
    if not u: raise HTTPException(404,'User not found')
    return serialize(u)
@router.post('/status')
async def status(data:StatusUpdate,db:AsyncSession=Depends(get_db)):
    if data.secret!=ADMIN_SECRET: raise HTTPException(403,'Invalid admin secret')
    if data.status not in {'NEW','CLICKED','PENDING','VERIFIED','BLOCKED'}: raise HTTPException(400,'Invalid status')
    u=await _get(db,data.telegram_id)
    if not u: raise HTTPException(404,'User not found')
    u.status=data.status
    if data.status=='PENDING': u.pending_time=now_naive()
    if data.status=='VERIFIED': u.verified_time=now_naive()
    await db.commit(); return {'ok':True,'telegram_id':data.telegram_id,'status':data.status}
@router.post('/attempt')
async def attempt(telegram_id:int,secret:str,db:AsyncSession=Depends(get_db)):
    if secret!=ADMIN_SECRET: raise HTTPException(403,'Invalid admin secret')
    u=await _get(db,telegram_id)
    if not u: raise HTTPException(404,'User not found')
    u.attempts_count+=1; await db.commit(); return {'attempts_count':u.attempts_count}
@router.get('/users')
async def users(secret:str,db:AsyncSession=Depends(get_db)):
    if secret!=ADMIN_SECRET: raise HTTPException(403,'Invalid admin secret')
    rows=(await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all(); return [serialize(u) for u in rows]
