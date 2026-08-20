# AlphaPulse

AlphaPulse is a Telegram bot + Telegram Mini App for Pocket Option OTC market analysis, structured signals, VIP signals, live position tracking, statistics and guarded demo auto-trading.

## Production source

- Canonical repository: this repository
- Production branch: `main`
- Vercel project: `alphapulse-runtime-staging`
- Mini App/backend are deployed together from `main`
- Runtime credentials never belong in Git; production secrets are loaded server-side

## Mini App information architecture

Ordinary users see exactly three tabs:

1. **Signals** — pair/timeframe/strategy selection, manual analysis, market scan, signal card and live chart.
2. **VIP** — VIP scanner state and isolated VIP history.
3. **Statistics** — regular/VIP/trading breakdowns plus ML scoring state.

The **Admin** tab is returned only to the configured Telegram administrator and its API is protected by Telegram Mini App `initData` validation.

## Signal and trading flow

All signal flows use the same Pocket Option market adapter and the same strategy engine. A signal is persisted only when the selected strategy returns a confirmed setup; no random/fallback signal is generated.

Trading has two execution modes:

- `AUTO` — a confirmed signal may be sent automatically to the connected **DEMO** Pocket account when the master auto-trade switch is enabled.
- `CONFIRM` — the signal is shown first; a broker request is sent only after explicit admin confirmation.

Real-account mode is intentionally read/track only in this deployment. The backend does not automatically place real-money orders.

## Safety / reliability

- Auto trading defaults to OFF.
- Broker credentials stay server-side.
- One `signal_id` is claimed in the database before a broker call, preventing duplicate execution.
- Position and amount limits are enforced on the backend.
- Telegram admin access is checked on the backend, not only hidden in the UI.
- Market timestamps are stored as UTC and rendered in the device timezone.
- Scanner state and signal/trade history persist in Neon Postgres.

## Validation

`.github/workflows/validate.yml` verifies Python compilation/import, guarded trading primitives, the Vite/React build, the core Mini App flows and stale Telegram asset recovery.
