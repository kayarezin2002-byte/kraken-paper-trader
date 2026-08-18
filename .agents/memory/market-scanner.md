---
name: Crypto market scanner design
description: Whole-market 30-asset scanner — account separation, limits, and contract gotchas
---

## Design decisions (Aug 2026)
- Scanner trades a separate **$1,000 USD SCANNER paper account** (all 30 assets priced in USD on Kraken; only 15 have GBP pairs; DOGE = XDG base). BTC/ETH/SOL/XRP are ranked by the scanner but **never traded by it** — they stay with their £ accounts. GOLD/SILVER untouched.
- Limits: max 3 positions, 0.5% risk/trade, 1.5% total risk, max 2 same-direction, $250k min 24h USD volume (liquidity gate disables trading, never display).
- Gate is the same ACTIVE 4/6 engine on 15m candles; 1h trend aggregated from 15m. One batch Ticker call per scan; per-asset candle fetch only when a new 15m candle is due. Exits managed every 120s tick at live ticker price.
- Scanner closes insert into the shared `trades` table with strategy='ACTIVE', entry_mode='SCANNER'.

## Gotchas
- **exitReason is an enum in the OpenAPI contract** — any new close path must use an existing value (e.g. `MAX_HOLD_TIME`, not `MAX_HOLD`) or all-trades responses 500 on Zod parse.
- Position dicts stored keyed-by-ticker must have `ticker` re-injected when embedded in API payloads (Zod requires it).
- Frontend: any useMemo merging multiple query results must list **all** query datas as deps.
- SQLite DB file is `python_bot/paper_trader.sqlite3` (env `PAPER_TRADER_DB`), and `sqlite3` CLI is not installed — use python.
