from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from backend.models.db_models import AsyncSessionLocal, MLState, utcnow

Mode = Literal["demo", "real"]
_PREFIX = "__pocket_cred__"


def _key() -> bytes:
    secret = str(os.getenv("WORKER_SHARED_SECRET") or "").encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("WORKER_SHARED_SECRET is required for Pocket credential encryption")
    return hashlib.sha256(secret).digest()


def validate_wire_ssid(value: str, mode: Mode) -> dict:
    raw = str(value or "").strip()
    if len(raw) < 20 or not raw.startswith("42"):
        raise ValueError("Pocket SSID must be the full 42[\"auth\",{...}] frame")
    try:
        packet = json.loads(raw[2:])
    except Exception as exc:
        raise ValueError("Pocket SSID is not valid JSON auth data") from exc
    if not isinstance(packet, list) or len(packet) < 2 or packet[0] != "auth" or not isinstance(packet[1], dict):
        raise ValueError("Pocket SSID must contain an auth packet")
    payload = packet[1]
    if not payload.get("session") and not payload.get("sessionToken"):
        raise ValueError("Pocket auth packet has no session/sessionToken")
    if mode == "demo" and "isDemo" in payload:
        try:
            if int(payload.get("isDemo") or 0) != 1:
                raise ValueError("This SSID is not a DEMO session")
        except (TypeError, ValueError) as exc:
            raise ValueError("This SSID is not a DEMO session") from exc
    return payload


def _encrypt(raw: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(nonce, raw.encode("utf-8"), b"alphapulse-pocket-v1")
    return json.dumps({
        "v": 1,
        "n": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "c": base64.urlsafe_b64encode(encrypted).decode("ascii"),
    }, separators=(",", ":"))


def _decrypt(blob: str) -> str:
    data = json.loads(blob)
    nonce = base64.urlsafe_b64decode(data["n"])
    ciphertext = base64.urlsafe_b64decode(data["c"])
    return AESGCM(_key()).decrypt(nonce, ciphertext, b"alphapulse-pocket-v1").decode("utf-8")


async def save_pocket_credential(mode: Mode, ssid: str) -> None:
    validate_wire_ssid(ssid, mode)
    key = f"{_PREFIX}:{mode}"
    encrypted = _encrypt(ssid.strip())
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, key)
        if row is None:
            row = MLState(strategy=key, payload=encrypted, samples=0, updated_at=utcnow())
            db.add(row)
        else:
            row.payload = encrypted
            row.updated_at = utcnow()
        await db.commit()


async def load_pocket_credential(mode: Mode) -> str | None:
    key = f"{_PREFIX}:{mode}"
    async with AsyncSessionLocal() as db:
        row = await db.get(MLState, key)
    if row is None or not row.payload:
        return None
    try:
        return _decrypt(row.payload)
    except Exception:
        return None


async def credential_status() -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MLState).where(MLState.strategy.in_([
            f"{_PREFIX}:demo", f"{_PREFIX}:real"
        ])))).scalars().all()
    found = {str(row.strategy).rsplit(":", 1)[-1]: row for row in rows}
    return {
        "demo": {"configured": "demo" in found, "updated_at": found.get("demo").updated_at if found.get("demo") else None},
        "real": {"configured": "real" in found, "updated_at": found.get("real").updated_at if found.get("real") else None},
    }
