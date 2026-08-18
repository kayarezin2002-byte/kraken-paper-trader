---
name: Opportunity system backtest conclusions
description: Final results of the 6/8 weighted-score opportunity system vs original 6/6 for crypto. Neither system works at £100/0.62% RT fees.
---

## The Core Finding

Neither the new opportunity system nor the original strict 6/6 is viable for crypto at **£100 capital + 0.62% round-trip Kraken taker fees**. This is a structural fee problem, not a signal problem.

## OOS Comparison (corrected metrics — all bugs fixed)

| Market | Orig 6/6 Trd | Orig PF | Orig OOS ROI | New 6/8 Trd | New PF | New OOS ROI |
|--------|-------------|---------|-------------|------------|--------|-------------|
| BTC    | 215         | 0.660   | -91.2%      | 543        | 0.315  | -96.3%      |
| ETH    | 211         | 0.652   | -81.3%      | 557        | 0.492  | -93.7%      |
| SOL    | 219         | 0.750   | -64.6%      | 560        | 0.503  | -94.3%      |
| XRP    | 227         | 0.767   | -79.8%      | 577        | 0.529  | -92.4%      |
| LINK   | 230         | 0.615   | -77.5%      | 527        | 0.475  | -93.6%      |

**New system is worse on every metric.** More trades → worse fee drag.

## Key Bugs Fixed During This Research

1. `consec_losses` was never reset → permanent lockout after first bad streak (false low trade counts)
2. `pnl` field excluded entry fee → PF looked higher than ROI (fixed: `net_pnl = pnl - entry_fee`)
3. OOS ROI used `STARTING_BALANCE` not IS-ending balance (fixed: pass `is_end_bal`)
4. `candidate_score` selected "best" config even when all IS PF < 1.0 (fixed: return -998 if PF ≤ 1.0)
5. `build_pre` trend stored as `bytearray` → `bytearray[i]` returns int, not str → all trend checks broke

## Structural Math

At 0.62% RT and £100 account, each trade costs ~£0.62 in fees. To break even: need win rate × avg_win > 0.62. With avg position ~£10 (10% of balance), need 6.2% net move just to cover fees. Essentially a 0% edge game that becomes negative immediately.

## Hybrid Regime Algorithm (Aug 2026 follow-up — hybrid_research.py)

A full regime-based hybrid (4h EMA50/EMA200 regime engine → trend-pullback / Bollinger range MR / breakout; 30m & 15m entries; 6/8 & 7/8; 1.5R/2R; look-ahead-free HTF sync; IS/OOS by open time) was backtested on BTC/ETH/SOL/XRP/LINK, 3y Binance data, 0.62% RT costs. **Every one of 45 configs had negative expectancy IS and OOS; all classified REJECT.** Gross edge ≈ +0.05R/trade, costs ≈ 0.62R/trade → avg R ≈ −0.6. Breakout generated ~90% of trades (best gross component); range MR almost never fired (neutral-regime + band-touch + RSI + 4h filter rarely coincide on crypto). 7/8 traded ~2.4×/wk-per-coin less than 6/8's ~5.5 but lost less — extra 6/8 trades were noise. Current 6/6 (PF ~0.5–0.7) beat every hybrid config net of fees. Same structural fee conclusion as before; signal quality is not the binding constraint.

## Next Research Options (user to decide)

1. **Metals only** — Gold/Silver at 0.30% RT fees. Last available data showed Gold IS PF ~1.09, Silver IS PF ~1.31 (but metrics had bugs; need clean rerun). Need to fix Yahoo Finance 422 fetch issue.
2. **Maker orders** — 0.14% taker → ~0.30% RT equivalent. Structural problem halved. But requires limit order placement logic (no public API fill guarantee).
3. **Increase capital** — Break-even trade count scales inversely. At £500, each trade is 5× larger and fees are proportionally the same but the system survives longer.
4. **FX pairs** — EUR/USD, GBP/USD, USD/JPY (deferred by user in Session 3). Low spreads, possible 0.05-0.10% RT equivalent.

## Metals Research Blocked

Yahoo Finance returns 422 (Unprocessable Entity) for GC=F and SI=F on the Replit container. The updated `fetch_yahoo()` function retries with browser-like headers across both query1/query2 endpoints. May need a workaround (e.g., yfinance package, Stooq CSV endpoint, or running metals research separately when the API is accessible).
