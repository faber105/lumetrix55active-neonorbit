from fastapi import APIRouter
router=APIRouter()
@router.get('/status')
async def status(): return {'enabled':False,'reason':'Vercel serverless uses HTTP polling; broker feed remains read-only inside backend requests'}
