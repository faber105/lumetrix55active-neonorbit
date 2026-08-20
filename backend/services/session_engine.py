from __future__ import annotations
import json, math
from datetime import datetime, timezone
from sqlalchemy import text
from backend.models.db_models import AsyncSessionLocal, PaperPosition, SignalResult, utcnow
from backend.services.auto_trade import MIN_AUTO_PAYOUT, eligible_auto_assets, get_auto_trade_control, get_demo_account_snapshot, maybe_execute_signal, process_pending_auto_trade, update_auto_trade_control
from backend.services.control import admin_id
from backend.services.pocketoption_otc import OTC_ASSETS
from backend.services.positions import reconcile_positions
from backend.services.signal_engine import signal_engine
from backend.services.signal_store import save_signal
from backend.services.strategies import AUTO_STRATEGIES, STRATEGY_LABELS
from backend.services.trade_mode import set_execution_mode, set_trade_account_mode
from backend.services.trade_runtime import get_trade_runtime, reset_trade_runtime, update_trade_runtime
COUNT_TIMEFRAMES={'15s','1m','3m'};PROFIT_TIMEFRAME='5m';PROFIT_STRATEGIES=set(AUTO_STRATEGIES)|{'smart_confluence'};COUNT_MIN_CONFIDENCE=74.0;PROFIT_MIN_CONFIDENCE=82.0;MAX_SESSION_AMOUNT=50000.0;_SCHEMA_READY=False
async def ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:return
    statements=["""CREATE TABLE IF NOT EXISTS auto_trade_sessions (id BIGSERIAL PRIMARY KEY,telegram_id BIGINT NOT NULL,mode VARCHAR(16) NOT NULL,status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',stage VARCHAR(32) NOT NULL DEFAULT 'SCANNING',strategy VARCHAR(40) NOT NULL,timeframe VARCHAR(8) NOT NULL,target_wins INTEGER,target_profit DOUBLE PRECISION,base_amount DOUBLE PRECISION NOT NULL,max_martingale INTEGER NOT NULL DEFAULT 3,max_failed_series INTEGER NOT NULL DEFAULT 1,wins INTEGER NOT NULL DEFAULT 0,failed_series INTEGER NOT NULL DEFAULT 0,total_legs INTEGER NOT NULL DEFAULT 0,current_level INTEGER NOT NULL DEFAULT 0,current_series_loss DOUBLE PRECISION NOT NULL DEFAULT 0,profit DOUBLE PRECISION NOT NULL DEFAULT 0,start_balance DOUBLE PRECISION,current_balance DOUBLE PRECISION,pending_signal_id INTEGER,active_position_id INTEGER,last_message TEXT,stop_reason VARCHAR(64),created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,ended_at TIMESTAMP)""","CREATE INDEX IF NOT EXISTS ix_auto_trade_sessions_active ON auto_trade_sessions (telegram_id,status,id DESC)","""CREATE TABLE IF NOT EXISTS auto_trade_legs (id BIGSERIAL PRIMARY KEY,session_id BIGINT NOT NULL,series_no INTEGER NOT NULL,martingale_level INTEGER NOT NULL,signal_id INTEGER NOT NULL,position_id INTEGER,pair VARCHAR(40),asset VARCHAR(40),direction VARCHAR(8),amount DOUBLE PRECISION NOT NULL,payout DOUBLE PRECISION,result VARCHAR(16) NOT NULL DEFAULT 'PENDING',pnl DOUBLE PRECISION NOT NULL DEFAULT 0,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,opened_at TIMESTAMP,closed_at TIMESTAMP)""","CREATE INDEX IF NOT EXISTS ix_auto_trade_legs_session ON auto_trade_legs (session_id,id)","""CREATE TABLE IF NOT EXISTS auto_trade_events (id BIGSERIAL PRIMARY KEY,session_id BIGINT NOT NULL,stage VARCHAR(32) NOT NULL,message TEXT NOT NULL,payload TEXT,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""","CREATE INDEX IF NOT EXISTS ix_auto_trade_events_session ON auto_trade_events (session_id,id DESC)"]
    async with AsyncSessionLocal() as db:
        for statement in statements:await db.execute(text(statement))
        await db.commit()
    _SCHEMA_READY=True
def _iso(value):return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00','Z') if isinstance(value,datetime) else value
def _serialize(row):
    if row is None:return None
    data=dict(row)
    for k,v in list(data.items()):data[k]=_iso(v)
    return data
async def _event(sid,stage,message,payload=None):
    async with AsyncSessionLocal() as db:
        await db.execute(text('INSERT INTO auto_trade_events (session_id,stage,message,payload) VALUES (:sid,:stage,:message,:payload)'),{'sid':sid,'stage':stage,'message':message,'payload':json.dumps(payload or {},ensure_ascii=False)});await db.commit()
_ALLOWED={'status','stage','wins','failed_series','total_legs','current_level','current_series_loss','profit','current_balance','pending_signal_id','active_position_id','last_message','stop_reason','ended_at'}
async def _update(sid,**changes):
    changes={k:v for k,v in changes.items() if k in _ALLOWED}
    if not changes:return
    changes['updated_at']=utcnow();sets=', '.join(f'{k}=:{k}' for k in changes)
    async with AsyncSessionLocal() as db:await db.execute(text(f'UPDATE auto_trade_sessions SET {sets} WHERE id=:id'),{**changes,'id':sid});await db.commit()
async def _active():
    await ensure_schema();tid=admin_id()
    if tid<=0:return None
    async with AsyncSessionLocal() as db:row=(await db.execute(text("SELECT * FROM auto_trade_sessions WHERE telegram_id=:tid AND status='ACTIVE' ORDER BY id DESC LIMIT 1"),{'tid':tid})).mappings().first()
    return _serialize(row)
async def _latest():
    await ensure_schema();tid=admin_id()
    async with AsyncSessionLocal() as db:row=(await db.execute(text('SELECT * FROM auto_trade_sessions WHERE telegram_id=:tid ORDER BY id DESC LIMIT 1'),{'tid':tid})).mappings().first()
    return _serialize(row)
async def _legs(sid,limit=100):
    async with AsyncSessionLocal() as db:rows=(await db.execute(text('SELECT * FROM auto_trade_legs WHERE session_id=:sid ORDER BY id DESC LIMIT :lim'),{'sid':sid,'lim':limit})).mappings().all()
    return [_serialize(x) for x in rows]
async def _events(sid,limit=40):
    async with AsyncSessionLocal() as db:rows=(await db.execute(text('SELECT id,stage,message,payload,created_at FROM auto_trade_events WHERE session_id=:sid ORDER BY id DESC LIMIT :lim'),{'sid':sid,'lim':limit})).mappings().all()
    out=[]
    for row in rows:
        item=_serialize(row)
        try:item['payload']=json.loads(item.get('payload') or '{}')
        except Exception:item['payload']={}
        out.append(item)
    return out
async def session_state(*,refresh_balance=False):
    session=await _active() or await _latest()
    if not session:return {'active':False,'session':None,'legs':[],'events':[],'runtime':await get_trade_runtime()}
    if refresh_balance:
        try:
            snap=await get_demo_account_snapshot(force=True)
            if snap.get('balance') is not None:await _update(int(session['id']),current_balance=float(snap['balance']));session['current_balance']=float(snap['balance'])
        except Exception:pass
    return {'active':session.get('status')=='ACTIVE','session':session,'legs':await _legs(int(session['id'])),'events':await _events(int(session['id'])),'runtime':await get_trade_runtime(),'min_payout':MIN_AUTO_PAYOUT}
def _validate_config(config):
    mode=str(config.get('mode') or 'count').lower();strategy=str(config.get('strategy') or 'trend_pulse');amount=round(float(config.get('amount') or 1),2);max_m=int(config.get('max_martingale',3))
    if amount<1 or amount>MAX_SESSION_AMOUNT:raise ValueError('Amount must be between 1 and 50000')
    if max_m<0 or max_m>3:raise ValueError('Martingale covers must be between 0 and 3')
    if mode=='count':
        tf=str(config.get('timeframe') or '1m');target=int(config.get('target_wins') or 5)
        if strategy not in AUTO_STRATEGIES:raise ValueError('Unknown AUTO strategy')
        if tf not in COUNT_TIMEFRAMES:raise ValueError('Count mode timeframe must be 15s, 1m or 3m')
        if target<5 or target>25:raise ValueError('Target wins must be between 5 and 25')
        return {'mode':mode,'strategy':strategy,'timeframe':tf,'target_wins':target,'target_profit':None,'amount':amount,'max_martingale':max_m,'max_failed_series':1}
    if mode=='profit':
        target=round(float(config.get('target_profit') or 1),2);failed=int(config.get('max_failed_series') or 1)
        if strategy not in PROFIT_STRATEGIES:raise ValueError('Unknown profit-mode strategy')
        if target<=0:raise ValueError('Target profit must be positive')
        if failed<1 or failed>10:raise ValueError('Failed-series limit must be between 1 and 10')
        return {'mode':mode,'strategy':strategy,'timeframe':PROFIT_TIMEFRAME,'target_wins':None,'target_profit':target,'amount':amount,'max_martingale':max_m,'max_failed_series':failed}
    raise ValueError('Unknown session mode')
async def start_session(config):
    await ensure_schema()
    if await _active():raise ValueError('An AUTO session is already active')
    control=await get_auto_trade_control()
    if control is None or not control.enabled:raise ValueError('Enable Autotrading in Admin first')
    v=_validate_config(config);await set_trade_account_mode('demo');await set_execution_mode('auto');await update_auto_trade_control(enabled=True,regular_enabled=True,vip_enabled=True,amount=v['amount'],max_open_positions=1);snap=await get_demo_account_snapshot(force=True);balance=snap.get('balance');tid=admin_id()
    async with AsyncSessionLocal() as db:
        sid=(await db.execute(text("""INSERT INTO auto_trade_sessions (telegram_id,mode,status,stage,strategy,timeframe,target_wins,target_profit,base_amount,max_martingale,max_failed_series,start_balance,current_balance,last_message) VALUES (:tid,:mode,'ACTIVE','SCANNING',:strategy,:tf,:tw,:tp,:amount,:mm,:mf,:balance,:balance,:msg) RETURNING id"""),{'tid':tid,'mode':v['mode'],'strategy':v['strategy'],'tf':v['timeframe'],'tw':v['target_wins'],'tp':v['target_profit'],'amount':v['amount'],'mm':v['max_martingale'],'mf':v['max_failed_series'],'balance':balance,'msg':'Сессия запущена · сканирую рынок'})).scalar_one();await db.commit()
    await reset_trade_runtime('SCANNING','AUTO сессия запущена · ищу подтверждённую ситуацию');await _event(int(sid),'SCANNING','AUTO сессия запущена',v);return await session_state()
async def stop_session(reason='USER_STOP'):
    s=await _active()
    if not s:return await session_state()
    await _update(int(s['id']),status='STOPPED',stage='STOPPED',stop_reason=reason,last_message='Сессия остановлена',ended_at=utcnow(),pending_signal_id=None,active_position_id=None);await reset_trade_runtime('IDLE','AUTO сессия остановлена');await _event(int(s['id']),'STOPPED','Сессия остановлена',{'reason':reason});return await session_state(refresh_balance=True)
def _next_amount(s,payout):
    base=float(s['base_amount']);level=int(s.get('current_level') or 0)
    if level<=0:return base
    ratio=max(float(payout)/100,.01);recovery=float(s.get('current_series_loss') or 0);target=base*ratio;return min(MAX_SESSION_AMOUNT,max(base,math.ceil(((recovery+target)/ratio)*100)/100))
async def _register_open(s,signal,trade,amount,payout):
    sid=int(s['id']);series=int(s.get('wins') or 0)+int(s.get('failed_series') or 0)+1;level=int(s.get('current_level') or 0)
    async with AsyncSessionLocal() as db:
        await db.execute(text("""INSERT INTO auto_trade_legs (session_id,series_no,martingale_level,signal_id,position_id,pair,asset,direction,amount,payout,result,opened_at) VALUES (:sid,:series,:level,:signal,:position,:pair,:asset,:direction,:amount,:payout,'PENDING',:opened)"""),{'sid':sid,'series':series,'level':level,'signal':int(signal['id']),'position':int(trade['position_id']),'pair':signal['pair'],'asset':signal['asset'],'direction':signal['direction'],'amount':amount,'payout':payout,'opened':utcnow()});await db.commit()
    await _update(sid,stage='OPEN',active_position_id=int(trade['position_id']),pending_signal_id=None,total_legs=int(s.get('total_legs') or 0)+1,last_message=f"Сделка открыта · {signal['pair']} · уровень {level}/{s['max_martingale']}");await _event(sid,'OPEN',f"Сделка открыта {signal['pair']} {signal['direction']}",{'amount':amount,'payout':payout,'level':level})
async def _settle(s):
    pid=s.get('active_position_id')
    if not pid:return s
    async with AsyncSessionLocal() as db:position=await db.get(PaperPosition,int(pid))
    if position is None or position.status!='CLOSED':return s
    sid=int(s['id'])
    async with AsyncSessionLocal() as db:leg=(await db.execute(text('SELECT * FROM auto_trade_legs WHERE session_id=:sid AND position_id=:pid ORDER BY id DESC LIMIT 1'),{'sid':sid,'pid':int(pid)})).mappings().first()
    if not leg or str(leg['result'])!='PENDING':await _update(sid,active_position_id=None);return (await _active()) or s
    result=position.result.value;amount=float(leg['amount']);payout=float(leg['payout'] or MIN_AUTO_PAYOUT);pnl=amount*payout/100 if result==SignalResult.WIN.value else (-amount if result==SignalResult.LOSS.value else 0);profit=round(float(s.get('profit') or 0)+pnl,2);wins=int(s.get('wins') or 0);failed=int(s.get('failed_series') or 0);level=int(s.get('current_level') or 0);series_loss=float(s.get('current_series_loss') or 0);status='ACTIVE';stage='SCANNING';reason=None;ended=None
    if result==SignalResult.WIN.value:
        wins+=1;level=0;series_loss=0;message=f'WIN +{pnl:.2f} · ищу следующую ситуацию'
        if s['mode']=='count' and wins>=int(s['target_wins']):status='COMPLETED';stage='COMPLETED';reason='TARGET_WINS';ended=utcnow();message='Цель по успешным сделкам достигнута'
        if s['mode']=='profit' and profit>=float(s['target_profit']):status='COMPLETED';stage='COMPLETED';reason='TARGET_PROFIT';ended=utcnow();message='Целевой профит достигнут'
    elif result==SignalResult.LOSS.value:
        series_loss+=amount
        if level<int(s['max_martingale']):level+=1;stage='MARTINGALE';message=f"LOSS · готовлю перекрытие {level}/{s['max_martingale']} только на новом подтверждённом сетапе"
        else:
            failed+=1;level=0;series_loss=0
            if s['mode']=='count':status='STOPPED';stage='STOPPED';reason='MARTINGALE_EXHAUSTED';ended=utcnow();message='Серия закрыта в минус · лимит перекрытий исчерпан'
            elif failed>=int(s['max_failed_series']):status='STOPPED';stage='STOPPED';reason='FAILED_SERIES_LIMIT';ended=utcnow();message='Достигнут лимит полностью проигранных серий'
            else:stage='SCANNING';message='Полная минусовая серия учтена · продолжаю поиск'
    else:message='DRAW · повторяю текущий уровень на следующем подтверждённом сетапе'
    try:snap=await get_demo_account_snapshot(force=True);balance=snap.get('balance')
    except Exception:balance=s.get('current_balance')
    async with AsyncSessionLocal() as db:await db.execute(text('UPDATE auto_trade_legs SET result=:result,pnl=:pnl,closed_at=:closed WHERE id=:id'),{'result':result,'pnl':pnl,'closed':utcnow(),'id':int(leg['id'])});await db.commit()
    await _update(sid,status=status,stage=stage,wins=wins,failed_series=failed,current_level=level,current_series_loss=series_loss,profit=profit,current_balance=balance,active_position_id=None,pending_signal_id=None,last_message=message,stop_reason=reason,ended_at=ended);await _event(sid,'CLOSED',f'Сделка закрыта · {result}',{'pnl':pnl,'profit':profit,'level':int(leg['martingale_level'])})
    if status!='ACTIVE':await reset_trade_runtime('IDLE',message);await _event(sid,stage,message,{'wins':wins,'failed_series':failed,'profit':profit})
    else:await reset_trade_runtime(stage,message)
    return (await _active()) or (await _latest()) or s
async def _load_signal(signal_id):
    from backend.models.db_models import Signal
    from backend.services.signal_store import serialize_signal
    async with AsyncSessionLocal() as db:row=await db.get(Signal,int(signal_id))
    return serialize_signal(row) if row else None
async def session_tick():
    await ensure_schema();s=await _active()
    if not s:return {'status':'IDLE'}
    control=await get_auto_trade_control()
    if control is None or not control.enabled:await stop_session('ADMIN_DISABLED');return {'status':'STOPPED','reason':'ADMIN_DISABLED'}
    await reconcile_positions();s=await _settle(s)
    if s.get('status')!='ACTIVE':return {'status':s.get('status'),'session_id':s.get('id')}
    if s.get('active_position_id'):await _update(int(s['id']),stage='OPEN',last_message='Сделка открыта · слежу за графиком и экспирацией');return {'status':'OPEN','position_id':s.get('active_position_id')}
    if s.get('pending_signal_id'):
        pending=await _load_signal(int(s['pending_signal_id']));trade=await process_pending_auto_trade()
        if trade.get('status')=='OPEN' and pending:
            runtime=await get_trade_runtime();amount=float(runtime.get('amount') or s['base_amount']);await _register_open(s,pending,trade,amount,runtime.get('payout_percent'));return {'status':'OPEN','trade':trade}
        if trade.get('status') in {'WAIT_ENTRY','SCHEDULED','OPENING'}:
            runtime=await get_trade_runtime();await _update(int(s['id']),stage=str(trade.get('status')),last_message=runtime.get('message') or 'Жду точное время входа');return trade
        await _update(int(s['id']),pending_signal_id=None,stage='SCANNING',last_message='Сигнал пропущен · продолжаю поиск');s=(await _active()) or s
    assets,snap=await eligible_auto_assets(list(OTC_ASSETS.keys()));balance=snap.get('balance')
    if balance is not None:await _update(int(s['id']),current_balance=float(balance))
    if not assets:
        message=f'Жду OTC пары с выплатой ≥{MIN_AUTO_PAYOUT:g}%';await _update(int(s['id']),stage='WAIT_PAYOUT',last_message=message);await update_trade_runtime(stage='WAIT_PAYOUT',balance=balance,min_payout=MIN_AUTO_PAYOUT,message=message);return {'status':'WAIT_PAYOUT','min_payout':MIN_AUTO_PAYOUT}
    strategy=str(s['strategy']);tf=str(s['timeframe'])
    if s['mode']=='profit' and strategy=='smart_confluence':candidate=await signal_engine.scan_best(PROFIT_TIMEFRAME,assets);threshold=PROFIT_MIN_CONFIDENCE
    else:candidate=await signal_engine.scan_strategy(tf,assets,strategy);threshold=PROFIT_MIN_CONFIDENCE if s['mode']=='profit' else COUNT_MIN_CONFIDENCE
    if not candidate or float(candidate.get('confidence') or 0)<threshold:
        label='Smart Confluence' if strategy=='smart_confluence' else STRATEGY_LABELS.get(strategy,strategy);message=f'Сканирую {len(assets)} пар · {label} · сетапа ≥{threshold:.0f}% пока нет';await _update(int(s['id']),stage='SCANNING',last_message=message);await update_trade_runtime(stage='SCANNING',strategy=strategy,timeframe=tf,balance=balance,eligible_assets=assets,message=message);return {'status':'SCANNING','assets':len(assets),'threshold':threshold}
    signal,duplicate=await save_signal(candidate,is_vip=False)
    if duplicate:await _update(int(s['id']),stage='SCANNING',last_message='Сетап уже обработан · жду новую точку входа');return {'status':'DUPLICATE'}
    try:payout=float((snap.get('payouts',{}) or {}).get(signal['asset']))
    except Exception:payout=MIN_AUTO_PAYOUT
    amount=_next_amount(s,payout);await update_auto_trade_control(amount=amount,max_open_positions=1);await _update(int(s['id']),stage='SIGNAL_FOUND',pending_signal_id=int(signal['id']),last_message=f"Сигнал найден {signal['pair']} · {signal['direction']} · жду {signal['entry_time']}");await _event(int(s['id']),'SIGNAL_FOUND',f"Найден сигнал {signal['pair']} {signal['direction']}",{'confidence':signal['confidence'],'amount':amount,'payout':payout});await update_trade_runtime(stage='SIGNAL_FOUND',pending_signal_id=int(signal['id']),pair=signal['pair'],asset=signal['asset'],strategy=signal['strategy'],timeframe=signal['timeframe'],payout_percent=payout,balance=balance,entry_time=signal['entry_time'],expiry_time=signal['expiry_time'],amount=amount,message='Подтверждённый сетап найден · готовлю точный вход');trade=await maybe_execute_signal(signal)
    if trade.get('status')=='OPEN':await _register_open(s,signal,trade,amount,trade.get('payout') or payout)
    elif trade.get('status') in {'SCHEDULED','WAIT_ENTRY'}:await _update(int(s['id']),stage='WAIT_ENTRY',pending_signal_id=int(signal['id']),last_message='Сигнал найден · жду точное время входа')
    else:await _update(int(s['id']),pending_signal_id=None,stage='SCANNING',last_message=f"Вход пропущен: {trade.get('status')} · продолжаю поиск")
    return {'status':trade.get('status'),'signal':signal,'trade':trade}
async def session_history(limit=30):
    await ensure_schema();tid=admin_id()
    async with AsyncSessionLocal() as db:rows=(await db.execute(text('SELECT * FROM auto_trade_sessions WHERE telegram_id=:tid ORDER BY id DESC LIMIT :lim'),{'tid':tid,'lim':max(1,min(100,limit))})).mappings().all()
    return [_serialize(x) for x in rows]
