# AlphaPulse OTC — unified Telegram bot

Объединённая версия проекта на базе `tradepocketbotsees`, с логикой верификации из `alphapulse_auth` и переработанным OTC signal engine.

## Что изменено

- **Один Telegram-бот**: верификация, пользовательское меню, OTC-сигналы и админ-команды работают в одном процессе.
- Отдельный `auth bot`, отдельный `data_service`, Celery/Beat и старые signal agents больше не нужны.
- Верификация хранится в общей PostgreSQL БД: `NEW -> CLICKED -> PENDING -> VERIFIED | BLOCKED`.
- Mini App использует проверенный Telegram `initData` и JWT; не верифицированный пользователь не получает доступ к signal API.
- Старые Yahoo/Binance источники и fallback со случайными свечами удалены из signal engine.
- OTC анализ работает только при наличии реальных свечей, полученных через Pocket Option web-session adapter. При ошибке источника бот пишет в лог и **не создаёт сигнал**.
- Сигнал содержит стратегию, режим рынка, confidence, время следующего входа, экспирацию и snapshot признаков.
- Закрытые сигналы автоматически получают `WIN / LOSS / DRAW`, после чего online-ML получает новый размеченный пример.
- Бот **не открывает сделки автоматически** и не вызывает order methods брокера.

## Важно про Pocket Option OTC data

У Pocket Option есть официальные OTC-страницы и 24-часовое расписание OTC-активов, но публично документированного candle/trading API для Pocket Option нет. На официальном сайте Pocket Option прямо указано, что платформа не предоставляет direct API access.

Поэтому проект использует **read-only адаптер к market stream веб-платформы** через пользовательский `POCKET_OPTION_SSID` и Python-пакет `pocketoptionapi-async==2.0.1` (MIT). В нашем коде адаптер вызывает только `get_candles(...)`. Функции размещения ордеров не используются.

Ссылки для проверки:
- https://pocketoption.com/en/assets-current
- https://pocketoption.com/en/assets-otc/
- https://pocketoption.com/blog/en/knowledge-base/learning/forex-trading-apis-for-2025-best-broker-trade-options/
- https://github.com/ChipaDevTeam/PocketOptionAPI
- https://pypi.org/project/pocketoptionapi-async/

`POCKET_OPTION_SSID` — чувствительный browser session credential. Не публикуйте его, не кладите в Git и используйте отдельную demo-сессию для market-data подключения, если она подходит для вашей конфигурации.

## Три стратегии

### 1. EMA + MACD Trend

Для трендового режима:
- EMA 9 / 20 / 50 направлены в одну сторону;
- MACD histogram подтверждает импульс;
- RSI фильтрует слишком перегретые входы;
- ADX и расстояние между EMA усиливают score.

### 2. RSI + Bollinger Reversal

Для флэта / mean reversion:
- предыдущая свеча выходит за Bollinger Band;
- следующая возвращается внутрь;
- RSI подтверждает перепроданность или перекупленность.

### 3. Donchian + ATR Breakout

Для импульсного пробоя:
- close пробивает максимум/минимум предыдущих 20 свечей;
- тело свечи >= 0.70 ATR;
- MACD подтверждает направление;
- ADX усиливает score.

## Как выбирается стратегия

`signal_engine/strategies.py` сначала вычисляет индикаторы и определяет market regime:

- `trend`
- `range`
- `breakout`

После этого прогоняются **все три** стратегии. Стратегия, подходящая текущему режиму, получает дополнительный приоритет, но более сильный сетап другой стратегии всё ещё может победить.

Если ни один сетап не проходит `STRATEGY_THRESHOLD` и итоговый `CONFIDENCE_THRESHOLD`, результат — **NO_SIGNAL**. Принудительного BUY/SELL нет.

## Время входа

Для таймфреймов `1m / 3m / 5m` сигнал привязывается к **следующей границе свечи**. Например, при анализе в `12:01:43` для `1m` вход будет `12:02:00`, если до границы достаточно времени. Если сигнал появился слишком поздно относительно `ENTRY_LEAD_SECONDS`, вход переносится ещё на одну свечу.

В Telegram время показывается в `SIGNAL_TIMEZONE` (по умолчанию `Europe/Rome`). В БД время хранится в UTC.

## Online ML

`signal_engine/online_ml.py` использует:

- `StandardScaler`
- `SGDClassifier(loss="log_loss")`
- 15 стабильных признаков из технического анализа.

Модель не обучается на искусственно сгенерированных свечах. После экспирации scanner получает фактические OTC candle prices, определяет направление `expiry_close > entry_open`, сохраняет WIN/LOSS/DRAW и вызывает `partial_fit`.

Модель хранится в:

```text
models_online/otc_online_direction.joblib
models_online/otc_online_meta.json
```

До `ONLINE_ML_MIN_SAMPLES` ML не влияет на signal confidence. После накопления истории ML подтверждает или ослабляет rule-based сигнал; он не переворачивает стратегию самостоятельно.

## Структура

```text
api/                       FastAPI + auth + verification + signals + sessions
bot/                       один aiogram Telegram bot
signal_engine/
  otc_provider.py          read-only Pocket Option candle adapter
  strategies.py            3 стратегии + regime detector
  otc_engine.py            выбор сигнала и confidence
  otc_scanner.py           scan -> publish -> resolve -> learn
  publisher.py             сохранение и Telegram-рассылка
  online_ml.py             incremental ML
mini_app/                  React/Vite Telegram Mini App
migrations/                fresh schema + upgrade existing DB
models_online/             persisted online model
```

## Настройка

1. Скопируйте `.env.example` в `.env`.
2. Заполните минимум:

```env
BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
JWT_SECRET=...
ADMIN_TOKEN=...
MINI_APP_URL=https://miniapp.example
PUBLIC_API_BASE_URL=https://api.example
MINI_APP_API_BASE_URL=https://api.example/api
REFERRAL_URL=https://...
POCKET_OPTION_SSID=...
```

3. Для свежей базы:

```bash
docker compose up --build
```

PostgreSQL применит `migrations/init.sql` автоматически при первом создании volume.

4. Если используется существующая БД старого AlphaPulse, сначала сделайте backup и примените:

```bash
psql "$DATABASE_URL_SYNC" -f migrations/upgrade_existing.sql
```

Либо выполните SQL из `migrations/upgrade_existing.sql` в вашем PostgreSQL клиенте до запуска новой версии.

## Как получить browser SSID

Формат SSID зависит от текущей веб-платформы Pocket Option и может меняться. Общая схема, используемая open-source клиентами:

1. Войдите в Pocket Option в браузере.
2. Откройте DevTools (`F12`) -> `Network` -> `WS`.
3. Перезагрузите торговую страницу.
4. Откройте активное WebSocket соединение и найдите auth/session message (часто выглядит как `42["auth", ...]`).
5. Скопируйте полный session/SSID value в `POCKET_OPTION_SSID`.

Если Pocket Option изменит внутренний WebSocket protocol, неофициальный adapter может перестать работать. В этом случае бот безопасно перестанет создавать OTC-сигналы, пока adapter не будет обновлён.

## Верификация

Пользователь:

1. `/start`
2. нажимает `Зарегистрироваться` -> `/go?uid=<telegram_id>` фиксирует клик и перенаправляет на `REFERRAL_URL`;
3. возвращается и нажимает `Я зарегистрировался`;
4. после минимального времени заявка получает `PENDING`;
5. админ подтверждает `/verify TELEGRAM_ID` или блокирует `/block TELEGRAM_ID`.

После `VERIFIED` пользователь получает Mini App и Telegram OTC-сигналы.

## Безопасность

- В архив **не включён настоящий `.env`** из исходных проектов.
- Старые секреты и токены из исходных архивов нужно считать скомпрометированными, если архивы куда-либо передавались; рекомендуется их ротировать.
- `POCKET_OPTION_SSID` не храните в репозитории.
- Автоматическое открытие сделок намеренно не реализовано.

## Ограничения

Сигнал — это статистический/технический анализ, а не гарантия результата. OTC pricing и условия брокера могут отличаться от обычного биржевого/межбанковского рынка; сначала проверяйте систему на demo и собирайте достаточную историю до оценки качества модели.
