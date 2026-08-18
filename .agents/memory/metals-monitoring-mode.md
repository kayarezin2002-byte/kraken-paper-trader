---
name: Metals paper-trading mode
description: GOLD/SILVER paper accounts trade in PAPER mode with an unvalidated strategy; USD accounting; data-source facts.
---

GOLD and SILVER are USD-denominated ($100) paper accounts. As of Aug 2026 the user explicitly enabled PAPER execution for metals despite the strategy being unvalidated — the strict 6/6 signal gate opens simulated trades, always labelled "UNVALIDATED STRATEGY — PAPER TRADING ONLY". Global flags `PAPER_TRADING=True` / `LIVE_TRADING=False` are enforced by `_assert_paper_only()` on every open/close path; no real orders are possible.

**Why:** User instruction (Aug 2026) to enable metals paper execution; strategy still has no validated backtest, so the unvalidated warning must stay until one passes.

**How to apply:** Metals entry gate is 6/6 (not the crypto 6/8 weighted gate). Keep the currency split honest: metals are USD end to end (balance, risk, P&L, activity messages, UI `$`); crypto is GBP; the dashboard's combined £ totals cover the 4 crypto accounts only. Metals resets are fixed at $100. Data-source facts: gold-api.com gives true spot XAU/XAG in USD with no key; Yahoo v8 chart GC=F/SI=F 1h candles are COMEX futures (indicators/ATR come from futures, fills from spot — known basis approximation; label honestly, can be flaky); Stooq blocks bots. Spot-feed failures must fail closed (`_block_stale_opportunity` sets DANGER/BLOCKED + API_ERROR).
