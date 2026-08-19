from __future__ import annotations

import logging
from datetime import datetime

from api.models.database import AsyncSessionLocal
from api.models.ml_state import MLState
from signal_engine.online_ml import online_model

logger = logging.getLogger(__name__)


async def load_online_model_from_db() -> bool:
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, 1)
        if row is None or not row.payload:
            return False
        try:
            online_model.loads(row.payload)
            return True
        except Exception as exc:
            logger.warning('Could not restore online ML state from DB: %s', exc)
            return False


async def save_online_model_to_db() -> None:
    payload = online_model.dumps()
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, 1)
        if row is None:
            row = MLState(id=1, payload=payload, samples=online_model.samples, updated_at=datetime.utcnow())
            db.add(row)
        else:
            row.payload = payload
            row.samples = online_model.samples
            row.updated_at = datetime.utcnow()
        await db.commit()
