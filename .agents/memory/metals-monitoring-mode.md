---
name: Metals & directional paper-trading mode
description: Current entry gates for all six paper assets and the paper-only safety posture.
---

All six assets now score BOTH directions independently on every scan (Aug 2026):
- Crypto (BTC/ETH/SOL/XRP): weighted directional score (4h=2, 1h=2, RSI/MACD/MA/Vol=1, max 8), entry gate ≥6 each direction; 4h counter-trend veto, RANGE/DANGER modes unchanged.
- GOLD: ≥5/6 conditions either direction (backtest-validated). SILVER: strict 6/6 both directions (unvalidated — do NOT loosen without backtest evidence).
- Per-asset gates live in `DIRECTIONAL_THRESHOLDS`; ties between qualifying directions mean WAIT, never random.
- Portfolio risk ceiling `MAX_TOTAL_OPEN_RISK_PERCENT = 2.0` (£/$ aggregated 1:1, documented paper-mode simplification); blocked entries must surface entryStatus BLOCKED, not just a log line.
- Opposite-direction signal while a position is open is LOG ONLY ("Strong opposite signal detected") — no auto-reversal (no validated reversal rule).

**Why:** user wants symmetric long/short opportunity capture judged by forward paper data; safety posture (LIVE_TRADING=False hard gate, `_assert_paper_only()` on every open/close, metals labelled UNVALIDATED, $100 accounts) must never be weakened.

**How to apply:** any gate change needs backtest evidence first; keep SILVER at 6/6 until then; preserve trade history via nullable ALTER TABLE migrations, never table rebuilds.
