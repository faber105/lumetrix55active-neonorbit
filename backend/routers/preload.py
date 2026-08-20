from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.services.preload_next import preload_state, set_preload_enabled
from backend.telegram_auth import TelegramMiniAppUser, admin_user

router = APIRouter()


class PreloadPatch(BaseModel):
    enabled: bool


@router.get('/state')
async def state(_: TelegramMiniAppUser = Depends(admin_user)):
    return await preload_state()


@router.patch('/state')
async def patch_state(data: PreloadPatch, _: TelegramMiniAppUser = Depends(admin_user)):
    await set_preload_enabled(data.enabled)
    return await preload_state()
