# AlphaPulse — unified `alphapulsesbot`

This repository is based on the second uploaded `alphapulsesbot.zip` project. The original Mini App design is preserved (`Signals / VIP / Market AI / Settings / Stats`).

Architecture: one FastAPI backend, one Telegram bot (webhook on Vercel), Neon Postgres, a read-only Pocket Option OTC adapter, three independent strategies (EMA Trend, Bollinger+RSI Reversal, ATR Breakout), and persistent online ML stored in Postgres. GitHub Actions calls the serverless scanner in short windows because Vercel Hobby cannot run a permanent polling worker.

No synthetic/random candles are used. If `POCKET_OPTION_SSID` is unavailable, the scanner returns no data rather than inventing a signal. The application does not place trades.
