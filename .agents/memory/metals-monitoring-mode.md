---
name: Metals monitoring mode
description: GOLD/SILVER paper accounts are monitoring-only; data-source facts and the invariant.
---

GOLD and SILVER are dashboard accounts in MONITORING mode — they must never open paper trades or accept custom balances until a metals strategy passes validated backtesting AND the user explicitly approves enabling trading.

**Why:** No strategy has been validated on metals; earlier metals backtests had buggy metrics.

**How to apply:** Any change touching metals must preserve the no-trade, fixed-£100 invariant end to end (bot refresh, reset paths, UI status). Data-source facts: gold-api.com gives true spot XAU/XAG in USD with no key; Yahoo v8 chart GC=F/SI=F 1h candles are COMEX futures (label honestly, can be flaky); Stooq blocks bots. Metals display USD, crypto GBP.
