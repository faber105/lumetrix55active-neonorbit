from __future__ import annotations
import os,time,httpx
from fastapi import HTTPException
from jose import jwt
ISSUER='https://token.actions.githubusercontent.com'; AUDIENCE='alphapulsesbot-scanner'; _cache={'expires':0.0,'jwks':None}
async def _jwks():
    if _cache['jwks'] and _cache['expires']>time.time(): return _cache['jwks']
    async with httpx.AsyncClient(timeout=10) as c:
        cfg=(await c.get(f'{ISSUER}/.well-known/openid-configuration')).json(); keys=(await c.get(cfg['jwks_uri'])).json()
    _cache.update({'expires':time.time()+3600,'jwks':keys}); return keys
async def verify(token:str):
    try:
        h=jwt.get_unverified_header(token); key=next(k for k in (await _jwks())['keys'] if k.get('kid')==h.get('kid'))
        claims=jwt.decode(token,key,algorithms=[h.get('alg','RS256')],audience=AUDIENCE,issuer=ISSUER,options={'verify_at_hash':False})
    except Exception as e: raise HTTPException(401,'Invalid GitHub Actions OIDC token') from e
    if claims.get('repository','').lower()!=os.getenv('GITHUB_ACTIONS_REPOSITORY','').lower(): raise HTTPException(403,'Repository not allowed')
    if claims.get('event_name') not in {'schedule','workflow_dispatch','push'}: raise HTTPException(403,'Event not allowed')
    if claims.get('event_name')=='push' and claims.get('ref')!='refs/heads/main': raise HTTPException(403,'Push ref not allowed')
    return claims
