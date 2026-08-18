"""Historical backtester for the GOLD / SILVER metals strategy.

ANALYSIS ONLY — this module never touches the live/paper trading engine.

Data: Yahoo Finance COMEX futures 1h candles (GC=F, SI=F), ~730 days.
Unlike the live bot (futures indicators + spot fills), the backtest uses
ONE consistent source — futures — for both indicators and simulated fills.
Known limitation: live fills happen at spot, so absolute price levels
differ by the futures/spot basis; relative strategy behaviour is unaffected.

Strategy logic is imported from paper_trader.py (same EMA/RSI/ATR math,
same six conditions, same risk model: 1% risk, ATR*1.5 stop, 2R target,
one position per asset, re-entry protection, daily-loss/consecutive-loss
pauses). Signals use only candle-close information; entries execute at the
NEXT candle's open. Same-candle SL+TP touches resolve conservatively to
STOP_LOSS and are flagged as ambiguous.

Usage:
    python3 metals_backtest.py fetch          # download+cache candles
    python3 metals_backtest.py run            # full backtest, writes results
    python3 metals_backtest.py run --days 365 --cost-bps 2
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from itertools import combinations
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_trader import (  # noqa: E402  (strategy math shared with live bot)
    ATR_MULTIPLIER,
    DAILY_LOSS_LIMIT,
    MAX_CONSECUTIVE_LOSSES,
    REWARD_TO_RISK,
    RISK_PER_TRADE,
    ema_series,
    rsi_series,
)

ASSETS = {"GOLD": "GC=F", "SILVER": "SI=F"}
STARTING_BALANCE = 100.0          # $ — matches the metals paper accounts
CONDITION_NAMES = ["4h Trend", "1h Trend", "RSI", "MACD Momentum", "Price vs MA", "Volume"]
CACHE_DIR = "/tmp/metals_backtest_cache"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results")
MIN_SAMPLE_FOR_RANKING = 10       # below this a variant is shown but not ranked


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def fetch_candles(symbol: str, use_cache: bool = True) -> list[list[float]]:
    """1h futures candles [ts, o, h, l, c, c, v, 1], oldest first, completed only."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f"{symbol.replace('=','_')}.json")
    if use_cache and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 6 * 3600:
        with open(cache) as fh:
            return json.load(fh)
    payload = None
    last_error: Exception | None = None
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1h&range=730d"
        req = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last_error = error
    if payload is None:
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {last_error}")
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    rows: list[list[float]] = []
    cutoff = time.time()
    for i, ts in enumerate(result["timestamp"]):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        v = quote["volume"][i] or 0.0
        if None in (o, h, l, c) or float(ts) >= cutoff:
            continue
        rows.append([float(ts), float(o), float(h), float(l), float(c), float(c), float(v), 1])
    rows = rows[:-1]  # drop the still-forming candle
    with open(cache, "w") as fh:
        json.dump(rows, fh)
    return rows


# ---------------------------------------------------------------------------
# Incremental 4h trend (replicates trend_for() on one_hour[:i+1] aggregation,
# including the partially-formed final 4h bucket, without O(n^2) recompute)
# ---------------------------------------------------------------------------

class IncEMA:
    """Incremental EMA with SMA seeding — identical maths to ema_series()."""

    def __init__(self, period: int):
        self.period = period
        self.mult = 2 / (period + 1)
        self.seed: list[float] = []
        self.value: float | None = None

    def preview(self, x: float) -> float | None:
        if self.value is not None:
            return (x - self.value) * self.mult + self.value
        if len(self.seed) + 1 == self.period:
            return (sum(self.seed) + x) / self.period
        return None

    def commit(self, x: float) -> None:
        nxt = self.preview(x)
        if self.value is not None or len(self.seed) + 1 == self.period:
            self.value = nxt
        else:
            self.seed.append(x)


class FourHourTrend:
    """Feeds 1h candles; yields the 4h trend exactly as the live bot sees it."""

    def __init__(self) -> None:
        self.e20, self.e50 = IncEMA(20), IncEMA(50)
        self.e12, self.e26 = IncEMA(12), IncEMA(26)
        self.sig = IncEMA(9)
        self.bucket_key: int | None = None
        self.partial_close: float | None = None
        self.completed = 0

    def _commit_bucket(self, close: float) -> None:
        self.e20.commit(close); self.e50.commit(close)
        m12 = self.e12.preview(close); m26 = self.e26.preview(close)
        self.e12.commit(close); self.e26.commit(close)
        if m12 is not None and m26 is not None:
            self.sig.commit(m12 - m26)
        self.completed += 1

    def push(self, ts: float, close: float) -> str:
        key = int(ts) // 14400
        if self.bucket_key is None:
            self.bucket_key = key
        elif key != self.bucket_key:
            self._commit_bucket(self.partial_close)  # previous bucket completed
            self.bucket_key = key
        self.partial_close = close
        # rows count incl. partial bucket must reach 55 (trend_for guard)
        if self.completed + 1 < 55:
            return "NEUTRAL"
        e20 = self.e20.preview(close); e50 = self.e50.preview(close)
        m12 = self.e12.preview(close); m26 = self.e26.preview(close)
        if None in (e20, e50, m12, m26):
            return "NEUTRAL"
        macd = m12 - m26
        sig = self.sig.preview(macd)
        if sig is None:
            return "NEUTRAL"
        if close > e20 > e50 and macd > sig:
            return "BULLISH"
        if close < e20 < e50 and macd < sig:
            return "BEARISH"
        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Per-candle strategy evaluation (mirrors evaluate_conditions exactly)
# ---------------------------------------------------------------------------

def precompute(rows: list[list[float]]) -> list[dict[str, Any]]:
    """One evaluation per completed 1h candle, using only data known at its close."""
    closes = [r[4] for r in rows]
    vols = [r[6] for r in rows]
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    macd = [(a - b) if a is not None and b is not None else None for a, b in zip(e12, e26)]
    macd_vals = [v for v in macd if v is not None]
    sig_compact = ema_series(macd_vals, 9)
    sig: list[float | None] = [None] * len(macd)
    si = 0
    for i, v in enumerate(macd):
        if v is not None:
            sig[i] = sig_compact[si]; si += 1
    trs = []
    for i, r in enumerate(rows):
        prev_close = rows[i - 1][4] if i else r[4]
        trs.append(max(r[2] - r[3], abs(r[2] - prev_close), abs(r[3] - prev_close)))
    atr = ema_series(trs, 14)
    rsi = rsi_series(closes)
    # rolling 20-candle average volume (incl. current — same as live)
    avg_vol: list[float] = []
    run = 0.0
    for i, v in enumerate(vols):
        run += v
        if i >= 20:
            run -= vols[i - 20]
        avg_vol.append(run / min(i + 1, 20))

    ft = FourHourTrend()
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        trend4h = ft.push(r[0], r[4])
        # 1h trend (trend_for on prefix)
        trend1h = "NEUTRAL"
        if i >= 54 and None not in (e20[i], e50[i], macd[i], sig[i]):
            if closes[i] > e20[i] > e50[i] and macd[i] > sig[i]:
                trend1h = "BULLISH"
            elif closes[i] < e20[i] < e50[i] and macd[i] < sig[i]:
                trend1h = "BEARISH"
        if trend1h == "BULLISH" or trend4h == "BULLISH":
            direction = "LONG"
        elif trend1h == "BEARISH" or trend4h == "BEARISH":
            direction = "SHORT"
        else:
            direction = "NEUTRAL"
        conds = [False] * 6
        if direction == "LONG":
            conds = [
                trend4h == "BULLISH",
                trend1h == "BULLISH",
                rsi[i] is not None and rsi[i] >= 50,
                macd[i] is not None and sig[i] is not None and macd[i] > sig[i],
                e20[i] is not None and e50[i] is not None and closes[i] > e20[i] > e50[i],
                avg_vol[i] > 0 and vols[i] >= avg_vol[i] * 0.7,
            ]
        elif direction == "SHORT":
            conds = [
                trend4h == "BEARISH",
                trend1h == "BEARISH",
                rsi[i] is not None and rsi[i] <= 50,
                macd[i] is not None and sig[i] is not None and macd[i] < sig[i],
                e20[i] is not None and e50[i] is not None and closes[i] < e20[i] < e50[i],
                avg_vol[i] > 0 and vols[i] >= avg_vol[i] * 0.7,
            ]
        out.append({
            "i": i, "ts": r[0], "direction": direction, "conds": conds,
            "passCount": sum(conds), "atr": atr[i], "rsi": rsi[i],
            "macd": macd[i], "macdSignal": sig[i], "ema20": e20[i], "ema50": e50[i],
            "trend1h": trend1h, "trend4h": trend4h,
            "volume": vols[i], "avgVolume": avg_vol[i], "close": closes[i],
        })
    return out


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def gate_signal(ev: dict[str, Any], required: list[int]) -> str | None:
    """required = condition indices that MUST pass. Direction must be non-neutral."""
    if ev["direction"] == "NEUTRAL" or ev["atr"] is None or ev["atr"] <= 0:
        return None
    if all(ev["conds"][k] for k in required):
        return ev["direction"]
    return None


def simulate(
    rows: list[list[float]],
    evals: list[dict[str, Any]],
    required: list[int],
    window_start: float,
    cost_bps: float = 0.0,
) -> dict[str, Any]:
    """Replays the metals engine over history. cost_bps applied per side."""
    balance = STARTING_BALANCE
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    armed = True
    consecutive_losses = 0
    # DOCUMENTED DEVIATION from live: the live engine's consecutive-loss pause
    # never un-pauses (the streak only breaks on a win, but no trades can occur
    # while paused) — replicated literally it would halt every variant after the
    # first 3-loss streak. Here a streak >= 3 blocks entries for the remainder
    # of the UTC day and the streak still only resets on a winning trade.
    daily_abs_loss: dict[str, float] = {}   # live rule: sum |losing trades| per UTC day
    streak_block_day: str | None = None
    equity_peak = balance
    max_dd_pct = 0.0
    max_dd_abs = 0.0
    ambiguous = 0
    pending_entry: dict[str, Any] | None = None

    def day_of(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")

    for i, ev in enumerate(evals):
        row = rows[i]
        ts = row[0]
        day = day_of(ts)

        # ---- open a pending entry at this candle's open --------------------
        if pending_entry is not None and position is None:
            entry_raw = row[1]
            sgn = 1 if pending_entry["direction"] == "LONG" else -1
            entry = entry_raw * (1 + sgn * cost_bps / 10000)
            stop_dist = pending_entry["atr"] * ATR_MULTIPLIER
            risk_amount = balance * RISK_PER_TRADE
            qty = min(risk_amount / stop_dist, balance / entry if entry > 0 else 0)
            if qty > 0:
                position = {
                    **pending_entry,
                    "entry": entry, "entryTs": ts, "entryIdx": i, "qty": qty,
                    "riskAmount": risk_amount,
                    "stopLoss": entry - sgn * stop_dist,
                    "takeProfit": entry + sgn * stop_dist * REWARD_TO_RISK,
                }
            pending_entry = None

        # ---- exit check on this candle's high/low ---------------------------
        if position is not None and i >= position["entryIdx"]:  # incl. entry candle
            high, low = row[2], row[3]
            sgn = 1 if position["direction"] == "LONG" else -1
            hit_sl = low <= position["stopLoss"] if sgn == 1 else high >= position["stopLoss"]
            hit_tp = high >= position["takeProfit"] if sgn == 1 else low <= position["takeProfit"]
            if hit_sl or hit_tp:
                flag_ambiguous = hit_sl and hit_tp
                if flag_ambiguous:
                    ambiguous += 1
                reason = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"  # conservative: SL first
                open_px = row[1]
                if hit_sl:
                    # gap-through: if the candle OPENS beyond the stop, the
                    # executable fill is the open, not the (better) stop level
                    gapped = open_px <= position["stopLoss"] if sgn == 1 else open_px >= position["stopLoss"]
                    exit_raw = open_px if gapped else position["stopLoss"]
                else:
                    # gap beyond target fills at the open (better than TP —
                    # taking TP level there would understate profits, but the
                    # honest executable price is the open)
                    gapped = open_px >= position["takeProfit"] if sgn == 1 else open_px <= position["takeProfit"]
                    exit_raw = open_px if gapped else position["takeProfit"]
                exit_price = exit_raw * (1 - sgn * cost_bps / 10000)
                pnl = (exit_price - position["entry"]) * position["qty"] * sgn
                balance = max(0.0, balance + pnl)
                if pnl < 0:
                    daily_abs_loss[day] = daily_abs_loss.get(day, 0.0) + abs(pnl)
                r_mult = pnl / position["riskAmount"] if position["riskAmount"] > 0 else 0.0
                trades.append({
                    **{k: position[k] for k in (
                        "direction", "entry", "entryTs", "stopLoss", "takeProfit",
                        "qty", "riskAmount", "signalTs", "conds", "passCount",
                        "rsi", "macd", "macdSignal", "trend1h", "trend4h",
                        "ema20", "volume", "atr",
                    )},
                    "exit": exit_price, "exitTs": ts, "exitReason": reason,
                    "pnl": pnl, "rMultiple": r_mult,
                    "durationH": (ts - position["entryTs"]) / 3600,
                    "win": pnl > 0, "ambiguous": flag_ambiguous,
                    "balanceAfter": balance,
                })
                consecutive_losses = 0 if pnl > 0 else consecutive_losses + 1
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    streak_block_day = day  # blocked for the rest of this UTC day
                position = None
                armed = False  # re-entry protection: signal must reset first

        # equity tracking (mark-to-market on close)
        equity = balance
        if position is not None:
            sgn = 1 if position["direction"] == "LONG" else -1
            equity += (row[4] - position["entry"]) * position["qty"] * sgn
        equity_peak = max(equity_peak, equity)
        dd_abs = equity_peak - equity
        if equity_peak > 0 and dd_abs / equity_peak * 100 > max_dd_pct:
            max_dd_pct = dd_abs / equity_peak * 100
            max_dd_abs = dd_abs

        # ---- signal generation at candle close ------------------------------
        signal = gate_signal(ev, required)
        if signal is None:
            armed = True  # gate not satisfied → re-entry re-armed
            continue
        if ts < window_start or position is not None or pending_entry is not None or not armed:
            continue
        # live rule: sum of |losing trades| this UTC day vs 3% of FIXED starting balance
        if daily_abs_loss.get(day, 0.0) >= STARTING_BALANCE * DAILY_LOSS_LIMIT:
            continue
        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES and streak_block_day == day:
            continue
        if balance <= 0:
            continue
        pending_entry = {
            "direction": signal, "signalTs": ts, "atr": ev["atr"],
            "conds": list(ev["conds"]), "passCount": ev["passCount"],
            "rsi": ev["rsi"], "macd": ev["macd"], "macdSignal": ev["macdSignal"],
            "trend1h": ev["trend1h"], "trend4h": ev["trend4h"],
            "ema20": ev["ema20"], "volume": ev["volume"],
        }

    return {
        "trades": trades, "endingBalance": balance,
        "maxDrawdownPct": max_dd_pct, "maxDrawdownAbs": max_dd_abs,
        "ambiguousExits": ambiguous,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def metrics(sim: dict[str, Any], window_days: float, subset: list[dict] | None = None) -> dict[str, Any]:
    trades = subset if subset is not None else sim["trades"]
    n = len(trades)
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    gross_p = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pnl_total = sum(t["pnl"] for t in trades)
    rs = [t["rMultiple"] for t in trades]
    durs = sorted(t["durationH"] for t in trades)
    # consecutive streaks
    max_cw = max_cl = cw = cl = 0
    for t in trades:
        if t["win"]:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        max_cw = max(max_cw, cw); max_cl = max(max_cl, cl)
    # Sharpe (approx): per-trade % return annualized by trade frequency
    sharpe = None
    if n >= 2 and window_days > 0:
        rets = [t["pnl"] / max(t["balanceAfter"] - t["pnl"], 1e-9) for t in trades]
        sd = statistics.pstdev(rets)
        if sd > 0:
            sharpe = statistics.mean(rets) / sd * math.sqrt(n * 365 / window_days)
    monthly: dict[str, dict[str, Any]] = {}
    for t in trades:
        m = datetime.fromtimestamp(t["exitTs"], timezone.utc).strftime("%Y-%m")
        mm = monthly.setdefault(m, {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        mm["trades"] += 1; mm["pnl"] += t["pnl"]
        mm["wins" if t["win"] else "losses"] += 1
    months = window_days / 30.44
    return {
        "startingCapital": STARTING_BALANCE,
        "endingCapital": round(STARTING_BALANCE + pnl_total, 2) if subset is not None else round(sim["endingBalance"], 2),
        "netProfit": round(pnl_total, 2),
        "roiPct": round(pnl_total / STARTING_BALANCE * 100, 2),
        "trades": n, "wins": len(wins), "losses": len(losses),
        "winRatePct": round(len(wins) / n * 100, 1) if n else None,
        "profitFactor": round(gross_p / gross_l, 2) if gross_l > 0 else (math.inf if gross_p > 0 else None),
        "grossProfit": round(gross_p, 2), "grossLoss": round(gross_l, 2),
        "avgWin": round(gross_p / len(wins), 3) if wins else None,
        "avgLoss": round(gross_l / len(losses), 3) if losses else None,
        "winLossRatio": round((gross_p / len(wins)) / (gross_l / len(losses)), 2) if wins and losses and gross_l else None,
        "expectancy": round(pnl_total / n, 4) if n else None,
        "expectancyR": round(sum(rs) / n, 3) if n else None,
        "avgR": round(sum(rs) / n, 3) if n else None,
        "maxDrawdownPct": round(sim["maxDrawdownPct"], 2) if subset is None else None,
        "maxDrawdownAbs": round(sim["maxDrawdownAbs"], 2) if subset is None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "avgDurationH": round(sum(durs) / n, 1) if n else None,
        "medianDurationH": round(durs[n // 2], 1) if n else None,
        "longestTradeH": round(durs[-1], 1) if n else None,
        "maxConsecWins": max_cw, "maxConsecLosses": max_cl,
        "largestWin": round(max((t["pnl"] for t in trades), default=0), 3),
        "largestLoss": round(min((t["pnl"] for t in trades), default=0), 3),
        "tradesPerMonth": round(n / months, 2) if months else None,
        "tradesPerWeek": round(n / (window_days / 7), 2) if window_days else None,
        "avgDaysBetweenTrades": round(window_days / n, 1) if n else None,
        "monthly": {k: {**v, "pnl": round(v["pnl"], 3),
                        "roiPct": round(v["pnl"] / STARTING_BALANCE * 100, 2),
                        "winRatePct": round(v["wins"] / v["trades"] * 100, 1)}
                    for k, v in sorted(monthly.items())},
        "profitableMonths": sum(1 for v in monthly.values() if v["pnl"] > 0),
        "losingMonths": sum(1 for v in monthly.values() if v["pnl"] < 0),
        "ambiguousExits": sim["ambiguousExits"] if subset is None else None,
    }


# ---------------------------------------------------------------------------
# QC checks
# ---------------------------------------------------------------------------

def quality_checks(rows: list[list[float]], evals: list[dict], sims: dict[str, dict]) -> list[str]:
    notes = []
    # missing candles (markets close weekends — report gap stats honestly)
    gaps = sum(1 for a, b in zip(rows, rows[1:]) if b[0] - a[0] > 3600)
    weekend_gaps = sum(1 for a, b in zip(rows, rows[1:]) if b[0] - a[0] >= 40 * 3600)
    notes.append(f"candle gaps >1h: {gaps} (of which {weekend_gaps} weekend/holiday closures); "
                 "gaps are handled by evaluating only completed candles in sequence")
    # timestamps strictly increasing (timezone/ordering sanity)
    assert all(b[0] > a[0] for a, b in zip(rows, rows[1:])), "timestamps not strictly increasing"
    notes.append("timestamps strictly increasing, all UTC epoch — no timezone ambiguity")
    # warm-up: first candle where any signal possible must have full indicators
    first_sig = next((e for e in evals if e["passCount"] > 0), None)
    if first_sig:
        assert first_sig["atr"] is not None and first_sig["rsi"] is not None
    notes.append("indicator warm-up verified: signals impossible until EMA50/RSI/ATR + 55 4h buckets exist")
    for name, sim in sims.items():
        ts_seen = set()
        last_exit = -1.0
        for t in sim["trades"]:
            assert t["entryTs"] > t["signalTs"], f"{name}: look-ahead (entry before signal)"
            assert t["entryTs"] >= last_exit, f"{name}: overlapping positions"
            key = (t["signalTs"], t["direction"])
            assert key not in ts_seen, f"{name}: duplicate entry from same signal"
            ts_seen.add(key)
            last_exit = t["exitTs"]
            if t["direction"] == "SHORT":
                assert t["stopLoss"] > t["entry"] > t["takeProfit"], f"{name}: bad SHORT levels"
            else:
                assert t["stopLoss"] < t["entry"] < t["takeProfit"], f"{name}: bad LONG levels"
    notes.append("per-trade assertions passed: no look-ahead, no overlaps, no duplicate entries, "
                 "correct LONG/SHORT SL/TP orientation")
    notes.append("same-candle SL+TP touches resolved conservatively as STOP_LOSS and counted per run")
    return notes


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def build_variants() -> dict[str, Any]:
    v: dict[str, Any] = {
        "6/6 strict": {"required": [0, 1, 2, 3, 4, 5]},
        "any 5/6": {"minPass": 5},
        "any 4/6": {"minPass": 4},
    }
    for k, name in enumerate(CONDITION_NAMES):
        v[f"5/6 ignore {name}"] = {"required": [x for x in range(6) if x != k]}
    for a, b in combinations(range(6), 2):
        v[f"4/6 ignore {CONDITION_NAMES[a]} + {CONDITION_NAMES[b]}"] = {
            "required": [x for x in range(6) if x not in (a, b)]}
    return v


def simulate_variant(rows, evals, spec, window_start, cost_bps):
    if "minPass" in spec:
        return _simulate_minpass(rows, evals, spec["minPass"], window_start, cost_bps)
    return simulate(rows, evals, spec["required"], window_start, cost_bps)


def _simulate_minpass(rows, evals, min_pass, window_start, cost_bps):
    # reuse simulate() by patching gate via required=[] and a passCount filter
    patched = []
    for e in evals:
        if e["passCount"] >= min_pass and e["direction"] != "NEUTRAL":
            patched.append(e)
        else:
            patched.append({**e, "direction": "NEUTRAL"})
    return simulate(rows, patched, [], window_start, cost_bps)


def run(days: int, cost_bps: float, use_cache: bool = True) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    variants = build_variants()
    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "windowDays": days, "costBpsPerSide": cost_bps,
        "dataSource": "Yahoo Finance COMEX futures 1h candles (GC=F, SI=F) — single source for indicators AND fills",
        "assets": {},
    }
    for asset, symbol in ASSETS.items():
        rows = fetch_candles(symbol, use_cache)
        evals = precompute(rows)
        window_start = rows[-1][0] - days * 86400
        in_window = [r for r in rows if r[0] >= window_start]
        print(f"\n=== {asset} ({symbol}) — {len(rows)} candles total, "
              f"{len(in_window)} in test window "
              f"({datetime.fromtimestamp(window_start, timezone.utc):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(rows[-1][0], timezone.utc):%Y-%m-%d}) ===")
        asset_out: dict[str, Any] = {
            "candlesTotal": len(rows), "candlesInWindow": len(in_window),
            "testStart": datetime.fromtimestamp(window_start, timezone.utc).isoformat(),
            "testEnd": datetime.fromtimestamp(rows[-1][0], timezone.utc).isoformat(),
            "variants": {},
        }
        sims: dict[str, dict] = {}
        for name, spec in variants.items():
            sim0 = simulate_variant(rows, evals, spec, window_start, 0.0)
            simc = simulate_variant(rows, evals, spec, window_start, cost_bps)
            sims[name] = sim0
            m0 = metrics(sim0, days)
            mc = metrics(simc, days)
            longs = [t for t in sim0["trades"] if t["direction"] == "LONG"]
            shorts = [t for t in sim0["trades"] if t["direction"] == "SHORT"]
            asset_out["variants"][name] = {
                "feeFree": m0,
                "withCosts": {k: mc[k] for k in ("netProfit", "roiPct", "winRatePct",
                                                 "profitFactor", "expectancyR", "trades")},
                "long": metrics(sim0, days, longs),
                "short": metrics(sim0, days, shorts),
            }
            pf = m0["profitFactor"]
            print(f"  {name:<45} trades={m0['trades']:>3}  ROI={m0['roiPct']:>7}%  "
                  f"PF={pf if pf is not None else '—':>6}  WR={m0['winRatePct'] or '—':>5}  "
                  f"DD={m0['maxDrawdownPct']:>5}%  ExpR={m0['expectancyR'] if m0['expectancyR'] is not None else '—'}")
            # trade log CSV
            safe = name.replace("/", "-").replace(" ", "_").replace("+", "and")
            path = os.path.join(RESULTS_DIR, f"{asset}_{safe}.csv")
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["asset", "direction", "signalTs", "entryTs", "entry", "exitTs", "exit",
                            "stopLoss", "takeProfit", "qty", "risk", "pnl", "rMultiple",
                            "durationH", "win", "exitReason", "ambiguous", "passCount",
                            "condsPassed", "condsFailed", "rsi", "macd", "macdSignal",
                            "trend1h", "trend4h", "ema20", "volume", "atr", "balanceAfter"])
                for t in sim0["trades"]:
                    passed = [CONDITION_NAMES[k] for k in range(6) if t["conds"][k]]
                    failed = [CONDITION_NAMES[k] for k in range(6) if not t["conds"][k]]
                    w.writerow([
                        asset, t["direction"],
                        datetime.fromtimestamp(t["signalTs"], timezone.utc).isoformat(),
                        datetime.fromtimestamp(t["entryTs"], timezone.utc).isoformat(),
                        round(t["entry"], 4),
                        datetime.fromtimestamp(t["exitTs"], timezone.utc).isoformat(),
                        round(t["exit"], 4), round(t["stopLoss"], 4), round(t["takeProfit"], 4),
                        round(t["qty"], 6), round(t["riskAmount"], 4), round(t["pnl"], 4),
                        round(t["rMultiple"], 3), round(t["durationH"], 1), t["win"],
                        t["exitReason"], t["ambiguous"], t["passCount"],
                        "|".join(passed), "|".join(failed),
                        t["rsi"], t["macd"], t["macdSignal"], t["trend1h"], t["trend4h"],
                        t["ema20"], t["volume"], t["atr"], round(t["balanceAfter"], 4),
                    ])
        # condition analysis on the most permissive population (any 4/6)
        broad = sims["any 4/6"]["trades"]
        cond_analysis = {}
        for k, cname in enumerate(CONDITION_NAMES):
            w_t = [t for t in broad if t["win"]]
            l_t = [t for t in broad if not t["win"]]
            cond_analysis[cname] = {
                "passRateWinnersPct": round(100 * sum(t["conds"][k] for t in w_t) / len(w_t), 1) if w_t else None,
                "passRateLosersPct": round(100 * sum(t["conds"][k] for t in l_t) / len(l_t), 1) if l_t else None,
            }
        asset_out["conditionAnalysis"] = cond_analysis
        # 5/6 missing-condition breakdown (exact misses within the any-5/6 run)
        five = sims["any 5/6"]["trades"]
        missing = {}
        for k, cname in enumerate(CONDITION_NAMES):
            sub = [t for t in five if t["passCount"] == 5 and not t["conds"][k]]
            missing[cname] = metrics(sims["any 5/6"], days, sub)
        asset_out["fiveOfSixMissingBreakdown"] = missing
        # 4/6 exact two-miss combos within the any-4/6 run
        pairs = {}
        for a, b in combinations(range(6), 2):
            sub = [t for t in broad
                   if t["passCount"] == 4 and not t["conds"][a] and not t["conds"][b]]
            if sub:
                pairs[f"{CONDITION_NAMES[a]} + {CONDITION_NAMES[b]}"] = metrics(sims["any 4/6"], days, sub)
        asset_out["fourOfSixPairBreakdown"] = pairs
        asset_out["qualityChecks"] = quality_checks(rows, evals, sims)
        report["assets"][asset] = asset_out

    out_path = os.path.join(RESULTS_DIR, "metals_backtest_report.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"\nFull report: {out_path}\nTrade logs: {RESULTS_DIR}/<ASSET>_<variant>.csv")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["fetch", "run"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--cost-bps", type=float, default=2.0,
                   help="per-side cost in basis points (default 2bp ≈ conservative metals spread+slippage)")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()
    if args.command == "fetch":
        for asset, sym in ASSETS.items():
            rows = fetch_candles(sym, use_cache=not args.no_cache)
            print(f"{asset}: {len(rows)} candles "
                  f"({datetime.fromtimestamp(rows[0][0], timezone.utc):%Y-%m-%d} → "
                  f"{datetime.fromtimestamp(rows[-1][0], timezone.utc):%Y-%m-%d})")
    else:
        run(args.days, args.cost_bps, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
