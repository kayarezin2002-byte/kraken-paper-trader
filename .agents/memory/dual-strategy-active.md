---
name: Dual-strategy CORE+ACTIVE design
description: How the parallel ACTIVE (15m) strategy coexists with CORE, and the exit/transaction rules that must hold
---

- Each asset has TWO independent position slots in coin_state: `open_position` (CORE, 1h) and `active_position` (ACTIVE, 15m, raw 4/6 gate, 1.5×ATR stop, 1.5R target). Both count toward the 2% portfolio risk ceiling; a single asset can carry up to 2×1% risk by design (user-approved).
- **Exit management must run before any entry-data fetch.** ACTIVE SL/TP checks use the live price the caller already has; a 15m candle-fetch failure or CORE "waiting for data" early return must never skip position management. **Why:** a review round found breached stops staying open whenever the 15m feed failed.
- **Stop fills use the observed live price when it gapped through the stop** (adverse fill), never the stop price itself; TP fills at target (limit-order policy). Keeps the paper sim from understating losses on the 120s scan cadence.
- **Every early-return path that writes to SQLite must commit.** An uncommitted UPDATE before `return` holds the single-writer lock and causes "database is locked" everywhere else (found via order-dependent test failures).
- Score labels: CORE crypto is weighted x/8 (gate 6/8), metals raw x/6 (gate 5/6), ACTIVE raw x/6 (gate 4/6). All surfaces (dashboard, activity log, close messages) must use the strategy-correct denominator; `trades.trend_4h` is a strict enum — ACTIVE stores its 1h context there, never "N/A".
- Legacy CORE test suites stub `fetch_active_candles` to raise in setUp so ACTIVE never opens trades in them.

## Independence & configurable gate (Aug 2026 update)
- ACTIVE was always structurally independent of CORE — the "ENTRY ELIGIBLE = NO" confusion came from CORE's diagnostics. Fix was better diagnostics (per-engine EXECUTION block with the exact blocker), not decoupling.
- ACTIVE gate is configurable via `bot_config` table (CONSERVATIVE 5/6, NORMAL 4/6 default, AGGRESSIVE 3/6); never auto-switched; invalid stored values fall back to NORMAL.
- ACTIVE exit engine order: trailing/break-even + max-hold (6h) run pre-fetch at live price; SL/TP next; signal-reversal only after a new completed 15m candle. Stops may only tighten (max/min by direction).
- Est. costs recorded per closed trade (0.26% fee/side, half entry-spread slippage/side, `pnl_net`); aggregate stats must SELECT those columns explicitly — SELECTing a subset silently zeroes the guarded estCosts math.
- API routes are mounted under `/api` prefix (e.g. `/api/paper-trader/multi-state`); bare paths 404.
- UI shows "HIGH-CONFIDENCE"/"HI-CONF" for CORE; DB and API keep the `CORE` value.
