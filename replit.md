# Kraken Paper Trader

A mobile-friendly BTC/GBP paper-trading dashboard that uses Kraken's public market data to simulate a multi-timeframe strategy without placing real orders.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server
- `pnpm --filter @workspace/kraken-paper-trader run dev` — run the dashboard
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `python3 python_bot/paper_trader.py refresh` — manually refresh the Python engine
- `python3 python_bot/paper_trader.py trades` — inspect persisted simulated trades
- No Kraken API key is required; the engine uses public endpoints only.

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5 wrapper around the Python paper-trading engine
- Persistence: Python standard-library SQLite database at `python_bot/paper_trader.sqlite3`
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `python_bot/paper_trader.py` — Kraken public-data polling, indicators, strategy, risk controls, SQLite persistence
- `artifacts/api-server/src/routes/paperTrader.ts` — HTTP bridge from the generated API contract to the Python engine
- `lib/api-spec/openapi.yaml` — source of truth for dashboard state and trade-history contracts
- `artifacts/kraken-paper-trader/src/pages/` — dashboard and history screens

## Architecture decisions

- The bot excludes Kraken's in-progress OHLC row before calculating indicators or creating entries.
- Entry evaluation is gated by the completed 1-hour candle timestamp; price refreshes do not create repeated entries.
- Short paper positions are capped to current account notional value so the simulation does not use leverage.
- The simulator never imports or accepts Kraken private trading credentials.

## Product

- Live BTC/GBP price and 1-hour / 4-hour trend confirmation
- EMA 20, EMA 50, RSI, MACD, ATR, and volume snapshots
- Virtual £100 account with ATR stops, 2:1 targets, 1% risk, one open position maximum, daily-loss and consecutive-loss guardrails
- Persistent trade history with performance metrics and mobile-friendly dashboard

## User preferences

- Paper trading only; never add real-order submission or Kraken private API authentication without explicit new requirements.

## Gotchas

- If the OpenAPI contract changes, run `pnpm --filter @workspace/api-spec run codegen` before typechecking the API or dashboard.
- The API workflow must be running for the dashboard's generated hooks to load live state.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
