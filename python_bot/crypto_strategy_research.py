#!/usr/bin/env python3
"""Crypto entry-logic research: BTC/ETH/SOL/XRP, LONG and SHORT separately.

ANALYSIS ONLY — never touches the live/paper engine or its database.

Goal: test whether DIFFERENT entry logic (pullbacks, breakouts, RSI bands,
ATR/volatility filters) beats simple condition-count relaxations, with strict
overfitting control:

  * Chronological split of the 365-day window: first ~70% development (IS),
    last ~30% untouched out-of-sample (OOS).
  * A small, fixed menu of strategy families (no parameter sweeps).
  * Quality floor: PF >= 1.20, positive expectancy, positive OOS ROI.
  * Costs: fee-free (paper parity) AND 31bp/side (Kraken taker+slippage).

Engine identical to the live bot: signals at candle close, entry next open,
1% risk, ATR*1.5 stop, 2R target, one position, re-entry protection,
daily-loss and consecutive-loss pauses (via metals_backtest.simulate).

Look-ahead audit: every signal below uses only candle i data (indicators are
causal prefixes); breakout highs exclude the current candle; entries fill at
candle i+1's open. Same-candle SL+TP resolves conservatively to STOP_LOSS.

Usage: python3 crypto_strategy_research.py run [--days 365] [--cost-bps 31]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metals_backtest import metrics, precompute, simulate  # noqa: E402
from crypto_directional_backtest import ASSETS, fetch_rows  # noqa: E402

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results")
OOS_FRACTION = 0.30
PF_FLOOR = 1.20


# ---------------------------------------------------------------------------
# Feature table (causal — candle i uses data up to and including candle i)
# ---------------------------------------------------------------------------

def features(rows: list[list[float]]) -> list[dict[str, Any]]:
    base = precompute(rows)
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    hi20: deque[float] = deque(maxlen=20)   # prior 20 highs (excl. current)
    lo20: deque[float] = deque(maxlen=20)
    atr_hist: deque[float] = deque(maxlen=20)
    out = []
    for i, ev in enumerate(base):
        prior_high = max(hi20) if len(hi20) == 20 else None
        prior_low = min(lo20) if len(lo20) == 20 else None
        avg_atr = sum(atr_hist) / len(atr_hist) if len(atr_hist) == 20 else None
        out.append({
            **ev,
            "high": highs[i], "low": lows[i],
            "priorHigh20": prior_high, "priorLow20": prior_low,
            "avgAtr20": avg_atr,
        })
        hi20.append(highs[i]); lo20.append(lows[i])
        if ev["atr"] is not None:
            atr_hist.append(ev["atr"])
    return out


# ---------------------------------------------------------------------------
# Strategy families (fixed menu — no parameter search)
# Each returns True/False for a LONG signal at candle f; SHORT is mirrored.
# ---------------------------------------------------------------------------

def _ok(f: dict[str, Any]) -> bool:
    return (f["atr"] is not None and f["atr"] > 0 and f["ema20"] is not None
            and f["ema50"] is not None and f["rsi"] is not None
            and f["macd"] is not None and f["macdSignal"] is not None)


def _vol_ok(f: dict[str, Any]) -> bool:
    return f["avgVolume"] > 0 and f["volume"] >= f["avgVolume"] * 0.7


def _atr_sane(f: dict[str, Any]) -> bool:
    # volatility filter: skip abnormally violent candles (ATR > 3x its 20-avg)
    return f["avgAtr20"] is None or f["atr"] <= 3.0 * f["avgAtr20"]


def strict66(f: dict[str, Any], side: str) -> bool:
    if side == "LONG":
        return (f["trend4h"] == "BULLISH" and f["trend1h"] == "BULLISH"
                and f["rsi"] >= 50 and f["macd"] > f["macdSignal"]
                and f["close"] > f["ema20"] > f["ema50"] and _vol_ok(f))
    return (f["trend4h"] == "BEARISH" and f["trend1h"] == "BEARISH"
            and f["rsi"] <= 50 and f["macd"] < f["macdSignal"]
            and f["close"] < f["ema20"] < f["ema50"] and _vol_ok(f))


def strict_rsi_band(f: dict[str, Any], side: str) -> bool:
    """6/6 but avoid chasing: RSI capped (LONG 50-68, SHORT 32-50)."""
    if not strict66(f, side):
        return False
    return f["rsi"] <= 68 if side == "LONG" else f["rsi"] >= 32


def strict_atr_filter(f: dict[str, Any], side: str) -> bool:
    """6/6 plus the volatility sanity filter."""
    return strict66(f, side) and _atr_sane(f)


def pullback(f: dict[str, Any], side: str) -> bool:
    """Trend intact on 4h + EMA stack; price pulls back to/through EMA20 and
    closes back on the trend side with RSI in the reset zone."""
    if side == "LONG":
        return (f["trend4h"] == "BULLISH" and f["ema20"] > f["ema50"]
                and f["low"] <= f["ema20"] and f["close"] > f["ema20"]
                and 38 <= f["rsi"] <= 58 and _atr_sane(f))
    return (f["trend4h"] == "BEARISH" and f["ema20"] < f["ema50"]
            and f["high"] >= f["ema20"] and f["close"] < f["ema20"]
            and 42 <= f["rsi"] <= 62 and _atr_sane(f))


def breakout(f: dict[str, Any], side: str) -> bool:
    """Close beyond the prior 20-candle extreme with volume expansion,
    not against the 4h trend, volatility sane."""
    if f["priorHigh20"] is None or f["avgVolume"] <= 0:
        return False
    vol_exp = f["volume"] >= f["avgVolume"] * 1.5
    if side == "LONG":
        return (f["close"] > f["priorHigh20"] and vol_exp
                and f["trend4h"] != "BEARISH" and _atr_sane(f))
    return (f["close"] < f["priorLow20"] and vol_exp
            and f["trend4h"] != "BULLISH" and _atr_sane(f))


def trend_pullback_combo(f: dict[str, Any], side: str) -> bool:
    """Either a strict trend signal (RSI-capped) or a pullback re-entry —
    the natural 'more opportunities without junk' candidate."""
    return strict_rsi_band(f, side) or pullback(f, side)


STRATEGIES: dict[str, Callable[[dict[str, Any], str], bool]] = {
    "6/6 strict (baseline)": strict66,
    "6/6 + RSI band": strict_rsi_band,
    "6/6 + ATR filter": strict_atr_filter,
    "pullback": pullback,
    "breakout20 + vol": breakout,
    "trend|pullback combo": trend_pullback_combo,
}


def make_evals(feats: list[dict[str, Any]], fn, side: str) -> list[dict[str, Any]]:
    out = []
    for f in feats:
        sig = _ok(f) and fn(f, side)
        # hard rule kept: never trade against a clearly opposite 4h trend
        if sig and side == "LONG" and f["trend4h"] == "BEARISH":
            sig = False
        if sig and side == "SHORT" and f["trend4h"] == "BULLISH":
            sig = False
        out.append({**f, "direction": side if sig else "NEUTRAL",
                    "conds": [sig] * 6, "passCount": 6 if sig else 0})
    return out


def _liquidate_endpoint(sim: dict[str, Any], cost_bps: float) -> dict[str, Any]:
    """Force-close any position still open at the window's final candle at
    that candle's close (with exit costs), so endpoint ROI/PF are not
    truncated by an omitted open trade."""
    pos, last = sim.get("openPosition"), sim.get("lastRow")
    if pos is None or last is None or "entry" not in pos:
        return sim
    sgn = 1 if pos["direction"] == "LONG" else -1
    exit_price = last[4] * (1 - sgn * cost_bps / 10000)
    pnl = (exit_price - pos["entry"]) * pos["qty"] * sgn
    balance = max(0.0, sim["endingBalance"] + pnl)
    trade = {
        **{k: pos.get(k) for k in (
            "direction", "entry", "entryTs", "stopLoss", "takeProfit",
            "qty", "riskAmount", "signalTs", "conds", "passCount",
            "rsi", "macd", "macdSignal", "trend1h", "trend4h",
            "ema20", "volume", "atr")},
        "exit": exit_price, "exitTs": last[0], "exitReason": "WINDOW_END",
        "pnl": pnl, "rMultiple": pnl / pos["riskAmount"] if pos["riskAmount"] > 0 else 0.0,
        "durationH": (last[0] - pos["entryTs"]) / 3600,
        "win": pnl > 0, "ambiguous": False, "balanceAfter": balance,
    }
    return {**sim, "trades": sim["trades"] + [trade], "endingBalance": balance,
            "openPosition": None}


def run(days: int, cost_bps: float) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    now = time.time()
    t0 = now - days * 86400
    split_ts = t0 + days * 86400 * (1 - OOS_FRACTION)
    results: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "windowDays": days, "oosFraction": OOS_FRACTION, "costBpsPerSide": cost_bps,
        "splitAt": datetime.fromtimestamp(split_ts, timezone.utc).isoformat(),
        "assets": {},
    }
    dev_days = days * (1 - OOS_FRACTION)
    oos_days = days * OOS_FRACTION
    for asset, symbol in ASSETS.items():
        rows = fetch_rows(symbol)
        feats = features(rows)
        dev_rows = [r for r in rows if r[0] < split_ts]
        dev_feats = feats[:len(dev_rows)]
        aentry: dict[str, Any] = {}
        for sname, fn in STRATEGIES.items():
            sentry: dict[str, Any] = {}
            for side in ("LONG", "SHORT"):
                dev_evals = make_evals(dev_feats, fn, side)
                full_evals = make_evals(feats, fn, side)
                block: dict[str, Any] = {}
                for label, bps in (("feeFree", 0.0), ("withCosts", cost_bps)):
                    dev = _liquidate_endpoint(simulate(dev_rows, dev_evals, [], t0, bps), bps)
                    oos = _liquidate_endpoint(simulate(rows, full_evals, [], split_ts, bps), bps)
                    # OOS sim runs on full rows but only enters after split_ts;
                    # count only trades signalled in the OOS window
                    oos_trades = [t for t in oos["trades"] if t["signalTs"] >= split_ts]
                    block[label] = {
                        "dev": metrics(dev, dev_days),
                        "oos": metrics({**oos, "trades": oos_trades}, oos_days),
                    }
                sentry[side] = block
            aentry[sname] = sentry
        results["assets"][asset] = aentry
        print(f"{asset}: done", file=sys.stderr)

    out_path = os.path.join(RESULTS_DIR, "crypto_strategy_research.json")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"Wrote {out_path}", file=sys.stderr)

    # Verdict table (fee-free primary — paper accounts are fee-free;
    # withCosts shown for robustness)
    print(f"\n{'asset':<5}{'strategy':<24}{'dir':<7}"
          f"{'DEVn':>5}{'DEVROI%':>9}{'DEVPF':>7}{'OOSn':>6}{'OOSROI%':>9}{'OOSPF':>7}"
          f"{'cROI%':>8}{'cPF':>6}  verdict")
    for asset, aentry in results["assets"].items():
        for sname, sentry in aentry.items():
            for side in ("LONG", "SHORT"):
                d = sentry[side]["feeFree"]["dev"]
                o = sentry[side]["feeFree"]["oos"]
                oc = sentry[side]["withCosts"]["oos"]
                dpf, opf = d["profitFactor"], o["profitFactor"]
                passes = (
                    d["trades"] >= 15 and o["trades"] >= 5
                    and dpf is not None and dpf >= PF_FLOOR
                    and (d["expectancy"] or 0) > 0
                    and o["roiPct"] is not None and o["roiPct"] > 0
                    and opf is not None and opf >= 1.0
                )
                verdict = "PASS" if passes else "fail"
                print(f"{asset:<5}{sname:<24}{side:<7}"
                      f"{d['trades']:>5}{d['roiPct']:>9}{dpf if dpf is not None else '—':>7}"
                      f"{o['trades']:>6}{o['roiPct']:>9}{opf if opf is not None else '—':>7}"
                      f"{oc['roiPct']:>8}{oc['profitFactor'] if oc['profitFactor'] is not None else '—':>6}  {verdict}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["run"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--cost-bps", type=float, default=31.0)
    args = p.parse_args()
    run(args.days, args.cost_bps)


if __name__ == "__main__":
    main()
