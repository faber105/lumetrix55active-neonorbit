from functools import lru_cache
import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    project_name: str = 'AlphaPulse OTC'
    bot_token: str
    admin_telegram_id: int
    mini_app_url: str = 'http://localhost:3000'
    public_api_base_url: str = 'http://localhost:8080'
    referral_url: str = 'https://pocketoption.com/'
    support_username: str = ''
    signal_timezone: str = 'Europe/Rome'
    max_verification_attempts: int = Field(default=3, ge=1, le=20)
    min_registration_seconds: int = Field(default=120, ge=0)
    require_subscription_for_signals: bool = False
    database_url: str = 'postgresql+asyncpg://user:pass@postgres:5432/signals_db'
    jwt_secret: str
    admin_token: str
    jwt_expires_hours: int = 24
    pocket_option_ssid: str = ''
    pocket_option_demo: bool = True
    otc_data_provider: str = 'pocketoptionapi_async'
    otc_assets: str = 'EURUSD_otc,GBPUSD_otc,USDJPY_otc,AUDUSD_otc,USDCAD_otc,EURGBP_otc,EURJPY_otc,AUDCAD_otc,AUDCHF_otc,NZDUSD_otc'
    otc_timeframes: str = '1m,3m,5m'
    scan_interval_seconds: int = Field(default=15, ge=5)
    confidence_threshold: float = Field(default=0.72, ge=0.5, le=0.99)
    strategy_threshold: float = Field(default=0.62, ge=0.5, le=0.99)
    ml_weight: float = Field(default=0.25, ge=0.0, le=0.7)
    max_active_signals: int = Field(default=12, ge=1, le=100)
    signal_cooldown_seconds: int = Field(default=120, ge=0)
    entry_lead_seconds: int = Field(default=5, ge=0, le=30)
    model_dir: str = 'models_online'
    online_ml_min_samples: int = Field(default=40, ge=10)
    github_actions_repository: str = 'faber105/lumetrix55active-neonorbit'
    cryptobot_api_token: str = ''
    usdt_trc20_wallet: str = ''
    cf_tunnel_token: str = ''

    @property
    def async_database_url(self) -> str:
        value = self.database_url.strip()
        if value.startswith('postgres://'):
            value = 'postgresql://' + value[len('postgres://'):]
        if value.startswith('postgresql://'):
            value = 'postgresql+asyncpg://' + value[len('postgresql://'):]
        if 'channel_binding=' in value:
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
            parts = urlsplit(value)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'channel_binding']
            value = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return value

    @property
    def effective_public_api_base_url(self) -> str:
        explicit = self.public_api_base_url.strip().rstrip('/')
        if explicit and 'localhost' not in explicit and 'example' not in explicit:
            return explicit
        production = os.getenv('VERCEL_PROJECT_PRODUCTION_URL', '').strip()
        current = os.getenv('VERCEL_URL', '').strip()
        host = production or current
        return f'https://{host}' if host else explicit

    @property
    def effective_mini_app_url(self) -> str:
        explicit = self.mini_app_url.strip().rstrip('/')
        if explicit and 'localhost' not in explicit and 'example' not in explicit:
            return explicit
        return self.effective_public_api_base_url or explicit

    @property
    def cooldown_seconds(self) -> int:
        return self.signal_cooldown_seconds

    @property
    def parsed_otc_assets(self) -> list[str]:
        return [x.strip() for x in self.otc_assets.split(',') if x.strip()]

    @property
    def parsed_otc_timeframes(self) -> list[str]:
        allowed = {'1m', '3m', '5m'}
        return [x.strip() for x in self.otc_timeframes.split(',') if x.strip() in allowed]


@lru_cache
def get_settings() -> Settings:
    return Settings()
