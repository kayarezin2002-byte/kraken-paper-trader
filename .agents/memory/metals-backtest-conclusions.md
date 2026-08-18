---
name: Metals backtest conclusions
description: 365-day GOLD/SILVER entry-gate backtest results, structural condition dependency, and the live consecutive-loss pause bug.
---

365-day backtest (Aug 2025→Aug 2026, python_bot/metals_backtest.py, Yahoo GC=F/SI=F 1h futures as single source for indicators AND fills; results in python_bot/backtest_results/) comparing 6/6, any-5/6, any-4/6 and ignore-condition variants:

- **Structural fact:** the 1h Trend condition is logically implied by MACD Momentum + Price-vs-MA both passing (all computed from the same 1h series). Likewise RSI≥50 is empirically implied. Only **4h Trend** and **Volume** are genuinely independent relaxations — most "ignore X" variants are identical to 6/6.
- Gold best: 4/6 ignoring 4h Trend + Volume (n=176, ROI 25.6%, PF 1.29, DD 13%) or 5/6-ignore-Volume for lowest DD; strict 6/6 was weakest after costs.
- Silver best: any-4/6 (n=201, ROI 59%, PF 1.41, DD 9%) and 4/6 ignoring 1h Trend + MACD (PF 1.44, DD 7.3%); all relaxations beat 6/6.
- Every variant profitable fee-free; 2bp/side costs hurt Gold's strict gate most (ROI 12%→2.5%).
- Volume filter is actively harmful (passed on 59-68% of winners vs 69-73% of losers).
- User decides gate changes (Task: apply chosen rules); do not auto-modify the live strategy.

**Live-engine bug found:** the consecutive-loss pause (coin_metrics streak ≥3) can never un-pause — the streak only breaks on a win, but no trades occur while paused → any asset permanently halts after its first 3-loss streak. Backtester deviates deliberately (next-UTC-day unpause, documented in metals_backtest.py).

**Backtester QC that caught real bugs:** deterministic synthetic-candle tests (gap-through-stop fills at open not SL level; entry-candle exit check `i >= entryIdx`); architect review flagged gap fills + risk-rule divergence. Rerun via `python3 metals_backtest.py run --days N --cost-bps X`.
