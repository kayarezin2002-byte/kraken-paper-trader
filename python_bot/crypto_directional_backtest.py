#!/usr/bin/env python3
"""365-day LONG/SHORT backtest for BTC / ETH / SOL / XRP.

ANALYSIS ONLY — never touches the live/paper engine or its database.

Reuses the metals_backtest engine (same EMA/RSI/ATR maths as paper_trader.py,
same risk model: 1% risk, ATR*1.5 stop, 2R target, one position per asset,
re-entry protection, daily-loss / consecutive-loss pauses, next-open entries,
conservative same-candle SL+TP resolution).

Data: Binance USDT 1h klines (Kraken's public OHLC only spans ~30 days at 1h).
Live accounts trade GBP pairs on Kraken; USDT history is the standard proxy —
relative behaviour is what matters here.

Variants per asset, evaluated for LONG-only, SHORT-only and COMBINED
(independent dual-direction scoring, one position per asset, tie = no trade):
  - current live rule: weighted score >= 6/8 (4h=2, 1h=2, RSI/MACD/MA/Vol=1)
  - 6/6 strict, any 5/6, any 4/6
  - condition-specific: 5/6 ignoring each single condition
  - weighted >= 5/8 (mild relaxation of the live rule)

Costs: run once at 0 bps (paper-parity) and once at --cost-bps per side.

Usage:
    python3 crypto_directional_backtest.py fetch
    python3 crypto_directional_backtest.py run --days 365 --cost-bps 31
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metals_backtest import (  # noqa: E402
    CONDITION_NAMES,
    STARTING_BALANCE,
    metrics,
    precompute,
    simulate,
)
from opportunity_research import fetch_binance  # noqa: E402

ASSETS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
CACHE_DIR = "/tmp/crypto_backtest_cache"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results")
WEIGHTS = [2, 2, 1, 1, 1, 1]  # 4h, 1h, RSI, MACD, PriceVsMA, Volume → max 8
FETCH_DAYS = 420              # 365d window + indicator warm-up


def fetch_rows(symbol: str, use_cache: bool = True) -> list[list[float]]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"{symbol}.json")
    if use_cache and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 6 * 3600:
        with open(cache) as fh:
            return json.load(fh)
    rows = fetch_binance(symbol, FETCH_DAYS)
    # normalise to metals engine shape [ts,o,h,l,c,c,v,1], drop forming candle
    out = [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
            float(r[4]), float(r[6] if len(r) > 6 else r[5]), 1]
           for r in rows if float(r[0]) < time.time()]
    out = out[:-1]
    with open(cache, "w") as fh:
        json.dump(out, fh)
    return out


# ---------------------------------------------------------------------------
# Dual-direction condition table (both sides every candle)
# ---------------------------------------------------------------------------

def dual_conditions(rows: list[list[float]]) -> list[dict[str, Any]]:
    base = precompute(rows)  # indicators + trends, direction ignored below
    out: list[dict[str, Any]] = []
    for ev in base:
        i = ev["i"]
        t4, t1 = ev["trend4h"], ev["trend1h"]
        rsi, macd, sig = ev["rsi"], ev["macd"], ev["macdSignal"]
        e20, e50, av = ev["ema20"], ev["ema50"], ev["avgVolume"]
        close, vol = ev["close"], ev["volume"]
        vol_ok = av > 0 and vol >= av * 0.7
        lc = [t4 == "BULLISH", t1 == "BULLISH",
              rsi is not None and rsi >= 50,
              macd is not None and sig is not None and macd > sig,
              e20 is not None and e50 is not None and close > e20 > e50,
              vol_ok]
        sc = [t4 == "BEARISH", t1 == "BEARISH",
              rsi is not None and rsi <= 50,
              macd is not None and sig is not None and macd < sig,
              e20 is not None and e50 is not None and close < e20 < e50,
              vol_ok]
        out.append({**ev, "longConds": lc, "shortConds": sc,
                    "longW": sum(w for w, c in zip(WEIGHTS, lc) if c),
                    "shortW": sum(w for w, c in zip(WEIGHTS, sc) if c)})
    return out


def _qualifies(ev: dict[str, Any], side: str, spec: dict[str, Any]) -> bool:
    conds = ev["longConds"] if side == "LONG" else ev["shortConds"]
    if "weighted" in spec:
        w = ev["longW"] if side == "LONG" else ev["shortW"]
        return w >= spec["weighted"]
    if "minPass" in spec:
        return sum(conds) >= spec["minPass"]
    return all(conds[k] for k in spec["required"])


def make_evals(dual: list[dict[str, Any]], spec: dict[str, Any],
               mode: str) -> list[dict[str, Any]]:
    """mode: 'LONG' | 'SHORT' (single-direction) | 'COMBINED' (live dual rule).

    COMBINED: both sides scored independently; both qualifying resolves by the
    strictly higher raw pass-count (tie = no trade) — mirrors the live engine.
    Hard rule kept in all modes: never trade against a clearly opposite 4h trend.
    """
    out = []
    for ev in dual:
        lq = _qualifies(ev, "LONG", spec)
        sq = _qualifies(ev, "SHORT", spec)
        # 4h counter-trend veto (live hard rule)
        if ev["trend4h"] == "BEARISH":
            lq = False
        if ev["trend4h"] == "BULLISH":
            sq = False
        decision = "NEUTRAL"
        if mode == "LONG":
            decision = "LONG" if lq else "NEUTRAL"
        elif mode == "SHORT":
            decision = "SHORT" if sq else "NEUTRAL"
        else:
            ls, ss = sum(ev["longConds"]), sum(ev["shortConds"])
            if lq and (not sq or ls > ss):
                decision = "LONG"
            elif sq and (not lq or ss > ls):
                decision = "SHORT"
        conds = (ev["longConds"] if decision == "LONG"
                 else ev["shortConds"] if decision == "SHORT" else [False] * 6)
        out.append({**ev, "direction": decision, "conds": conds,
                    "passCount": sum(conds)})
    return out


def build_variants() -> dict[str, dict[str, Any]]:
    v: dict[str, dict[str, Any]] = {
        "LIVE weighted >=6/8": {"weighted": 6},
        "weighted >=5/8": {"weighted": 5},
        "6/6 strict": {"required": list(range(6))},
        "any 5/6": {"minPass": 5},
        "any 4/6": {"minPass": 4},
    }
    for k, name in enumerate(CONDITION_NAMES):
        v[f"5/6 ignore {name}"] = {"required": [x for x in range(6) if x != k]}
    return v


def run(days: int, cost_bps: float, use_cache: bool = True) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    window_start = time.time() - days * 86400
    variants = build_variants()
    results: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "windowDays": days, "costBpsPerSide": cost_bps,
        "note": "Binance USDT 1h data; same engine/risk model as live paper bot; "
                "4h counter-trend veto enforced in all variants.",
        "assets": {},
    }
    for asset, symbol in ASSETS.items():
        rows = fetch_rows(symbol, use_cache)
        dual = dual_conditions(rows)
        window_days = min(days, (rows[-1][0] - rows[0][0]) / 86400)
        entry: dict[str, Any] = {"candles": len(rows), "byVariant": {}}
        for vname, spec in variants.items():
            ventry: dict[str, Any] = {}
            for mode in ("LONG", "SHORT", "COMBINED"):
                evals = make_evals(dual, spec, mode)
                # required=[] → gate_signal passes whenever direction != NEUTRAL
                free = simulate(rows, evals, [], window_start, 0.0)
                cost = simulate(rows, evals, [], window_start, cost_bps)
                ventry[mode] = {
                    "feeFree": metrics(free, window_days),
                    "withCosts": metrics(cost, window_days),
                }
            entry["byVariant"][vname] = ventry
        results["assets"][asset] = entry
        print(f"{asset}: done ({len(rows)} candles)", file=sys.stderr)

    out_path = os.path.join(RESULTS_DIR, "crypto_directional_backtest.json")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"Wrote {out_path}", file=sys.stderr)

    # Compact console summary (fee-free, matching the paper accounts)
    hdr = f"{'variant':<26}{'dir':<9}{'n':>4}{'/mo':>6}{'ROI%':>8}{'PF':>7}{'win%':>6}{'DD%':>7}{'Shrp':>6}{'exp':>8}{'hrs':>6}{'maxL':>5}"
    for asset, entry in results["assets"].items():
        print(f"\n=== {asset} (fee-free / paper parity) ===")
        print(hdr)
        for vname, ventry in entry["byVariant"].items():
            for mode in ("LONG", "SHORT", "COMBINED"):
                m = ventry[mode]["feeFree"]
                pf = m["profitFactor"]
                print(f"{vname:<26}{mode:<9}{m['trades']:>4}{m['tradesPerMonth'] or 0:>6}"
                      f"{m['roiPct']:>8}{('inf' if pf == float('inf') else pf if pf is not None else '—'):>7}"
                      f"{m['winRatePct'] or 0:>6}{m['maxDrawdownPct'] or 0:>7}"
                      f"{m['sharpe'] if m['sharpe'] is not None else '—':>6}"
                      f"{m['expectancy'] if m['expectancy'] is not None else '—':>8}"
                      f"{m['avgDurationH'] or 0:>6}{m['maxConsecLosses']:>5}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["fetch", "run"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--cost-bps", type=float, default=31.0,
                   help="per-side cost in bps (default 31bp = Kraken taker 0.26% + 0.05% slippage)")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()
    if args.command == "fetch":
        for asset, sym in ASSETS.items():
            rows = fetch_rows(sym, use_cache=not args.no_cache)
            print(f"{asset}: {len(rows)} candles "
                  f"({datetime.fromtimestamp(rows[0][0], timezone.utc):%Y-%m-%d} → "
                  f"{datetime.fromtimestamp(rows[-1][0], timezone.utc):%Y-%m-%d})")
    else:
        run(args.days, args.cost_bps, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
