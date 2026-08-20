from __future__ import annotations

import os
import time

import httpx
from fastapi import HTTPException
from jose import jwt

ISSUER = 'https://token.actions.githubusercontent.com'
AUDIENCE = 'alphapulsesbot-scanner'
_cache = {'expires': 0.0, 'jwks': None}


async def _jwks():
    if _cache['jwks'] and _cache['expires'] > time.time():
        return _cache['jwks']
    async with httpx.AsyncClient(timeout=10) as client:
        cfg = (await client.get(f'{ISSUER}/.well-known/openid-configuration')).json()
        keys = (await client.get(cfg['jwks_uri'])).json()
    _cache.update({'expires': time.time() + 3600, 'jwks': keys})
    return keys


def _allowed_repository() -> str:
    explicit = os.getenv('GITHUB_ACTIONS_REPOSITORY', '').strip()
    if explicit:
        return explicit.lower()

    owner = os.getenv('VERCEL_GIT_REPO_OWNER', '').strip()
    slug = os.getenv('VERCEL_GIT_REPO_SLUG', '').strip()
    if owner and slug:
        return f'{owner}/{slug}'.lower()

    # Canonical production source. This fallback keeps scheduled scanner OIDC
    # working when Vercel does not inject a custom GITHUB_ACTIONS_REPOSITORY env.
    return 'faber105/lumetrix55active-neonorbit'


async def verify(token: str):
    try:
        header = jwt.get_unverified_header(token)
        key = next(k for k in (await _jwks())['keys'] if k.get('kid') == header.get('kid'))
        claims = jwt.decode(
            token,
            key,
            algorithms=[header.get('alg', 'RS256')],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={'verify_at_hash': False},
        )
    except Exception as exc:
        raise HTTPException(401, 'Invalid GitHub Actions OIDC token') from exc

    if claims.get('repository', '').lower() != _allowed_repository():
        raise HTTPException(403, 'Repository not allowed')
    if claims.get('event_name') not in {'schedule', 'workflow_dispatch', 'push'}:
        raise HTTPException(403, 'Event not allowed')
    if claims.get('event_name') == 'push' and claims.get('ref') != 'refs/heads/main':
        raise HTTPException(403, 'Push ref not allowed')
    return claims
