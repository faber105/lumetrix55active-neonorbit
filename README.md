# AlphaPulse

Canonical source repository for the AlphaPulse Telegram bot, Mini App and FastAPI backend.

## Single-source architecture

Everything used by the production application lives in this repository and `main` is the canonical production branch:

- `backend/` — FastAPI API, market analysis, scanners, signal reconciliation and auto-trade guards.
- `bot/` — Telegram bot and webhook handling.
- `miniapp/` — Telegram Mini App frontend.
- `api/main.py` — Vercel FastAPI entry point.
- `vercel.json` — Vercel build/runtime configuration.
- `.github/workflows/validate.yml` — CI validation.
- `.github/workflows/otc-scan.yml` — scheduled OTC scanner trigger.

The old generated runtime-bundle deployment path is no longer part of the production workflow. Deployments should be created directly from this repository.

## Vercel Git deployment

Connect the Vercel project to `faber105/lumetrix55active-neonorbit` and set the Production Branch to `main`. After that every push to `main` is a new production deployment, while other branches can be used for previews.

`vercel.json` explicitly leaves Git deployment enabled for `main` and builds `miniapp/dist` from the same commit that contains the backend and bot code.

Runtime secrets must stay in Vercel environment variables / the existing secure runtime store. Do not commit `runtime_bootstrap.json`, `runtime_secrets.json`, `.env`, bot tokens or database credentials.

## Runtime

AlphaPulse includes the original Mini App sections, three signal strategies (EMA Trend, Bollinger/RSI Reversal and ATR Breakout), regular/VIP signals, market analysis, persistent ML state, exact candle reconciliation and guarded admin-only auto trading. Auto trading is disabled by default and must be explicitly enabled by the admin.
