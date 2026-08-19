from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

PlanCode = Literal['week','month','year']
PaymentProvider = Literal['stars','crypto']
TradeResult = Literal['WIN','LOSS']
Timeframe = Literal['1m','3m','5m']

class UserSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:int; username:str|None=None; first_name:str|None=None; language_code:str|None=None; created_at:datetime; is_banned:bool; verification_status:str='NEW'
class AuthTelegramRequest(BaseModel): initData:str
class AuthResponse(BaseModel): access_token:str; token_type:str='bearer'; user:UserSchema
class SignalSchema(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; asset:str; asset_category:str; direction:Literal['CALL','PUT']; timeframe:str; duration_sec:int; open_price:Decimal|None; close_price:Decimal|None; confidence:float; indicator_score:float; ml_confidence:float; created_at:datetime; expires_at:datetime; result:str; agent_id:str; strategy:str='unknown'; market_regime:str='unknown'; data_source:str='unknown'; entry_time:datetime
class SignalAnalyzeRequest(BaseModel): asset:str=Field(min_length=2,max_length=20); category:Literal['otc','forex','crypto','stocks','commodities','indices']='otc'; timeframe:Timeframe
class SignalAnalyzeResponse(BaseModel): status:Literal['SIGNAL','NO_SIGNAL']; signal:SignalSchema|None=None; message:str
class SessionStartRequest(BaseModel): goal_amount:Decimal=Field(gt=0); trade_amount:Decimal=Field(gt=0); timeframe_filter:Timeframe|None=None
class SessionEndRequest(BaseModel): session_id:int; status:Literal['completed','cancelled']='completed'
class SessionMarkRequest(BaseModel): session_id:int; signal_id:int; result:TradeResult
class SessionSchema(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; user_id:int; goal_amount:Decimal; trade_amount:Decimal; timeframe_filter:str|None; started_at:datetime; ended_at:datetime|None; status:str; total_trades:int; wins:int; losses:int; pnl:Decimal; goal_reached:bool
class SessionTradeSchema(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:int; session_id:int; signal_id:int; user_id:int; result:str; trade_amount:Decimal; pnl:Decimal; marked_at:datetime
class SessionMarkResponse(BaseModel): session:SessionSchema; trade:SessionTradeSchema; pnl_delta:Decimal; goal_reached:bool
class SessionStatsResponse(BaseModel): total_sessions:int; total_trades:int; wins:int; losses:int; winrate:float; total_pnl:Decimal; best_streak:int
class SubscriptionPlan(BaseModel): code:PlanCode; title:str; days:int; price_usd:Decimal; stars_amount:int; badge:str|None=None
class SubscriptionStatus(BaseModel): is_active:bool; plan:str|None=None; expires_at:datetime|None=None
class SubscriptionCreateRequest(BaseModel): plan:PlanCode; provider:PaymentProvider
class SubscriptionCreateResponse(BaseModel): payment_id:int; provider:PaymentProvider; status:str; payment_url:str|None=None; provider_payment_id:str|None=None; wallet:str|None=None; amount:Decimal; currency:str; message:str|None=None
class SubscriptionConfirmRequest(BaseModel): payment_id:int; provider_data:dict[str,Any]=Field(default_factory=dict)
class SubscriptionConfirmResponse(BaseModel): payment_id:int; payment_status:str; subscription:SubscriptionStatus; message:str
class UserStatsResponse(BaseModel): sessions:int; winrate:float; total_pnl:Decimal; active_subscription:SubscriptionStatus
class AdminPaymentConfirmRequest(BaseModel): payment_id:int
class AdminSignalResultRequest(BaseModel): result:Literal['WIN','LOSS','PENDING']
