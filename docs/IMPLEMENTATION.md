# Implementation notes

## Single-process bot responsibilities

The aiogram process owns:

1. Telegram verification/menu handlers.
2. One persistent read-only Pocket Option market-data connection.
3. OTC scanner loop.
4. Signal publisher.
5. Expired-signal resolver.
6. Incremental model updates.

FastAPI remains a separate HTTP process for the Telegram Mini App, but **there is only one Telegram bot** and no separate verification/signal bot.

## Data integrity rule

There is deliberately no random/mock candle fallback in production signal generation. `PocketOptionOTCProvider.fetch_1m()` raises when authentication, connection, symbol or candle history fails. Scanner catches the error and emits zero signals for that asset.

## Online learning label

The model target is market direction, not user profit:

`y = 1` when expiry close > actual entry candle open, otherwise `0`.

The same stored feature snapshot used at decision time becomes the training row after the outcome becomes known. This avoids leakage from future candles.
