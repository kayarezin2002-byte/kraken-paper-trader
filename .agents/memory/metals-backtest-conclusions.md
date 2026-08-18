---
name: Metals backtest conclusions
description: 365-day GOLD/SILVER entry-gate backtest results, structural condition dependency, and the live consecutive-loss pause bug.
---

365-day backtest (Aug 2025→Aug 2026, python_bot/metals_backtest.py, Yahoo GC=F/SI=F 1h futures as single source for indicators AND fills; results in python_bot/backtest_results/) comparing 6/6, any-5/6, any-4/6 and ignore-condition variants:

- **Structural fact:** the 1h Trend condition is logically implied by MACD Momentum + Price-vs-MA both passing (all computed from the same 1h series). Likewise RSI≥50 is empirically implied. Only **4h Trend** and **Volume** are genuinely independent relaxations — most "ignore X" variants are identical to 6/6.
- Gold best: any-5/6 (ROI +26.6%, Sharpe 1.73) — **APPLIED Aug 2026** (user chose any-5/6 for both Gold and Silver).
- Silver best: any-5/6 also applied (matching Gold; individual Silver data also favoured relaxed gates).
- Gate now in `refresh_metal()` (python_bot/paper_trader.py): `pass_count >= 5` with direction non-neutral; constant `METAL_ENTRY_MIN_PASS = 5`.
- Every variant profitable fee-free; 2bp/side costs hurt Gold's strict gate most (ROI 12%→2.5%).
- Volume filter is actively harmful (passed on 59-68% of winners vs 69-73% of losers).
- To revert to 6/6 or try another gate: change `METAL_ENTRY_MIN_PASS` inside `refresh_metal()` or add a required-index filter.

**Live-engine bug found:** the consecutive-loss pause (coin_metrics streak ≥3) can never un-pause — the streak only breaks on a win, but no trades occur while paused → any asset permanently halts after its first 3-loss streak. Backtester deviates deliberately (next-UTC-day unpause, documented in metals_backtest.py).

**GOLD live gate (Aug 2026):** switched to independent directional ≥5/6 (both sides scored every scan; higher score wins conflicts, tie waits; SILVER stays 6/6). Verified equivalent to the backtested "any 5/6" on 365d of data — identical 171-trade set (`precompute_directional` in metals_backtest.py, results in backtest_results/gold_directional_5of6.json) — because a qualifying SHORT needs a fully bearish 1h, which excludes the bullish-bias preselection cases.

**Backtester QC that caught real bugs:** deterministic synthetic-candle tests (gap-through-stop fills at open not SL level; entry-candle exit check `i >= entryIdx`); architect review flagged gap fills + risk-rule divergence. Rerun via `python3 metals_backtest.py run --days N --cost-bps X`.
