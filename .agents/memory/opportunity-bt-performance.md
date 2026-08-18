---
name: opportunity_research.py performance and correctness rules
description: Hard-won performance and correctness lessons for the fast backtest engine in opportunity_research.py.
---

## Rule 1 — Build pre-computed dicts ONCE per candle series, not per config run

`build_pre(candles)` is O(n) but expensive for 100k-candle series. The main loop must pre-build all unique series (c15, c30, c1h, c2h, c4h) **before** the config loop and pass pre-built dicts into `run_opportunity_bt` / `run_original_66_bt`. Building inside each config run multiplies cost by ×8.

**Why:** 5 markets × 8 configs × 2 calls = 80 build_pre calls timed out at 290s. Pre-building cuts it to 5×5=25 calls.

## Rule 2 — Never store trend as bytearray; use plain list of single-char strings

`bytearray[i]` returns an integer (e.g. `ord("B")` = 66). Comparing `bytearray[i] == "B"` always fails silently. All trend-dependent logic breaks: trend always reads as NEUTRAL, pullback/breakout detection is disabled, 6/6 reference shows 0 trades.

**Why:** This caused catastrophic silent failure — 6/8 and 7/8 thresholds gave identical trade counts, and the original 6/6 showed 0 trades.

**How to apply:** `trend = ["N"] * n` and `trend[i] = "B"` or `"R"` — plain list.

## Rule 3 — Use deque-based O(n) sliding window for rolling max/min/mean

`max(highs[i-20:i])` inside a loop creates 100k Python list objects (slice allocation = ~1-2s for 100k candles). Replace with monotone deque sliding window:

```python
from collections import deque
dq_h = deque()
for i in range(n):
    while dq_h and dq_h[0] < i - 20: dq_h.popleft()
    high20[i] = highs[dq_h[0]] if dq_h else 0.0
    while dq_h and highs[dq_h[-1]] <= highs[i]: dq_h.pop()
    dq_h.append(i)
```

Same pattern for running ATR mean: accumulate sum in a deque window (O(n)) instead of list comprehension (O(20n)).

## Rule 4 — MACD cross fan-out is faster than nested per-candle loop

Detect each crossing once and fan-out to the next 5 indices, rather than checking a 5-candle backward window for every candle. O(n) amortised vs O(5n) with inner loop.

## Rule 5 — Use integer day numbers for daily reset, not datetime.fromtimestamp

`datetime.fromtimestamp(...).date().isoformat()` in a 100k-candle hot loop is slow (~2μs each). Use `ts // 86400` (pre-computed in `build_pre` as `day_num` list) for the daily reset check. O(1) integer comparison.

## Rule 6 — Backtest functions must not access raw candle lists in the hot loop

Once pre-computed dicts are built, all per-candle values (timestamps, highs, lows, closes, day_num) come from pre-built arrays. The `run_opportunity_bt` and `run_original_66_bt` signatures accept `pre_e: dict, pre_c: dict` and never call `build_pre` internally or index `entry_candles[i]`.

## Rule 7 — net_pnl must deduct both entry and exit fees

`pnl` from the raw price calc deducts only exit fee (subtracted from gain). Entry fee is charged separately to `balance` at open. The stored `"pnl"` in every trade record must be:
```python
net_pnl = pnl - open_pos["entry_fee"]
```
Failing to do this makes `compute_metrics` PF inconsistent with balance-curve ROI.
