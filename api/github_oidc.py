from __future__ import annotations

import time

import httpx
from fastapi import HTTPException
from jose import jwt

from config import get_settings

ISSUER = 'https://token.actions.githubusercontent.com'
AUDIENCE = 'alphapulse-otc-scanner'
_cache: dict[str, object] = {'expires': 0.0, 'jwks': None}


async def _jwks() -> dict:
    now = time.time()
    if _cache['jwks'] and float(_cache['expires']) > now:
        return _cache['jwks']  # type: ignore[return-value]
    async with httpx.AsyncClient(timeout=10.0) as client:
        config = (await client.get(f'{ISSUER}/.well-known/openid-configuration')).json()
        jwks = (await client.get(config['jwks_uri'])).json()
    _cache['jwks'] = jwks
    _cache['expires'] = now + 3600
    return jwks


async def verify_github_actions_token(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        keys = (await _jwks()).get('keys', [])
        key = next(k for k in keys if k.get('kid') == header.get('kid'))
        claims = jwt.decode(
            token,
            key,
            algorithms=[header.get('alg', 'RS256')],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={'verify_at_hash': False},
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail='Invalid GitHub Actions OIDC token') from exc

    expected_repo = get_settings().github_actions_repository.strip().lower()
    if not expected_repo or str(claims.get('repository', '')).lower() != expected_repo:
        raise HTTPException(status_code=403, detail='GitHub repository is not allowed')
    if claims.get('event_name') not in {'schedule', 'workflow_dispatch'}:
        raise HTTPException(status_code=403, detail='Only scheduled/manual workflow runs are allowed')
    return claims
