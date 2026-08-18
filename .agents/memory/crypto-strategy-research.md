---
name: Crypto strategy research (IS/OOS)
description: Conclusions of the 365d train/test entry-logic study for BTC/ETH/SOL/XRP and the XRP SHORT verification.
---

# Crypto entry-logic research — conclusions (Aug 2026)

Method: 70% development / 30% untouched out-of-sample chronological split, fixed
menu of 6 strategy families (no parameter sweeps), same engine as live bot,
fee-free + 31bp/side costs. Script: `python_bot/crypto_strategy_research.py`;
results `python_bot/backtest_results/crypto_strategy_research.json`.

**Rules the user approved to judge candidates:** DEV PF >= 1.20, positive
expectancy, positive OOS ROI, OOS PF >= 1.0; otherwise "NO EDGE FOUND" and the
direction stays disabled — never trade a losing config just for frequency.

Fee-free (paper parity) passes:
- BTC LONG 6/6 strict — DEV PF 1.28, OOS barely positive (PF 1.06) → marginal.
- BTC SHORT breakout20+vol — DEV PF 1.21, OOS PF 1.30.
- SOL SHORT 6/6 strict — DEV PF 1.21, OOS PF 1.46 (strongest consistency).
- XRP LONG 6/6 strict — DEV PF 1.27, OOS PF 3.56 (only n=11 OOS — thin).
- XRP SHORT breakout20+vol — DEV PF 1.34, OOS PF 1.44.
- ETH: NO EDGE FOUND in any family/direction.

XRP SHORT +32.6% (weighted >=6/8, full-year fee-free) verification: mostly
in-sample — DEV +28.3%/PF 1.32 but OOS collapses to +3.3%/PF 1.09, and with
31bp costs it loses heavily both windows. Not look-ahead (engine audited:
causal indicators, next-open entries, endpoint positions force-liquidated) but
NOT robust; do not trust the headline number.

**With realistic Kraken costs (31bp/side) NOTHING passes on the 1h timeframe**
— avg win too small vs fees. Paper accounts are fee-free so paper results
stand, but this system must not go live on Kraken taker fees as-is.

Engine audit fix: `metals_backtest.simulate` now returns `openPosition`/
`lastRow`; windowed studies must force-liquidate endpoint positions
(`_liquidate_endpoint`) or ROI is truncated.
