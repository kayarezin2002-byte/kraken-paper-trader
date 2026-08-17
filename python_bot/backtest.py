#!/usr/bin/env python3
"""
Kraken Paper-Trader Historical Backtest
========================================
Compares three entry strategies using EXACT live-bot logic.

Strategy A — Current (6/6): all conditions must pass
Strategy B — Relaxed (5/6): at least 5 of 6 conditions must pass
Strategy C — 5/6 + Trend Guard: 5/6 pass, 1h trend must align,
             4h NEUTRAL is ok, 4h OPPOSITE blocks entry

All other parameters are identical to the live paper-trading bot:
  RISK_PER_TRADE=1%, ATR_MULTIPLIER=1.5, REWARD_TO_RISK=2.0,
  DAILY_LOSS_LIMIT=3%, MAX_CONSECUTIVE_LOSSES=3, no fees/slippage.

SL/TP exit logic: uses candle high/low for intra-candle hit detection
(more realistic than close-only checks).

Usage: python3 backtest.py
"""

from __future__ import annotations

import json
import math
import sys
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.request import Request, urlopen

# ─── Constants — identical to live bot ────────────────────────────────────────
RISK_PER_TRADE         = 0.01
REWARD_TO_RISK         = 2.0
ATR_MULTIPLIER         = 1.5
STARTING_BALANCE       = 100.0
DAILY_LOSS_LIMIT       = 0.03
MAX_CONSECUTIVE_LOSSES = 3

COINS: dict[str, str] = {
    "BTC": "XXBTZGBP",
    "ETH": "XETHZGBP",
    "SOL": "SOLGBP",
    "XRP": "XRPGBP",
}

# ─── Indicator functions — copied verbatim from paper_trader.py ───────────────

def ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    mult = 2 / (period + 1)
    for i in range(period, len(values)):
        current = (values[i] - current) * mult + current
        result[i] = current
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains  = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def rsi_val() -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    result[period] = rsi_val()
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        result[i + 1] = rsi_val()
    return result


def indicator_snapshot(rows: list[list[Any]]) -> dict[str, float | None]:
    closes  = [float(r[4]) for r in rows]
    volumes = [float(r[6]) for r in rows]
    ema20   = ema_series(closes, 20)
    ema50   = ema_series(closes, 50)
    ema12   = ema_series(closes, 12)
    ema26   = ema_series(closes, 26)
    macd = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema12, ema26)
    ]
    macd_vals   = [v for v in macd if v is not None]
    sig_vals    = ema_series(macd_vals, 9)
    macd_signal: list[float | None] = [None] * len(macd)
    si = 0
    for idx, v in enumerate(macd):
        if v is not None:
            macd_signal[idx] = sig_vals[si]
            si += 1
    true_ranges: list[float] = []
    for i, row in enumerate(rows):
        high = float(row[2]); low = float(row[3])
        prev_close = float(rows[i - 1][4]) if i else float(row[4])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = ema_series(true_ranges, 14)
    rsi = rsi_series(closes)
    L = len(rows) - 1

    def lv(series: list[float | None]) -> float | None:
        return series[L] if series and L >= 0 else None

    return {
        "rsi": lv(rsi), "macd": lv(macd), "macdSignal": lv(macd_signal),
        "atr": lv(atr), "ema20": lv(ema20), "ema50": lv(ema50),
        "volume": volumes[-1] if volumes else None,
        "_avg_volume": sum(volumes[-20:]) / min(20, len(volumes)) if volumes else None,
    }


def trend_for(rows: list[list[Any]]) -> str:
    if len(rows) < 55:
        return "NEUTRAL"
    snap  = indicator_snapshot(rows)
    close = float(rows[-1][4])
    e20, e50, macd, sig = snap["ema20"], snap["ema50"], snap["macd"], snap["macdSignal"]
    if e20 is not None and e50 is not None and macd is not None and sig is not None:
        if close > e20 > e50 and macd > sig:
            return "BULLISH"
        if close < e20 < e50 and macd < sig:
            return "BEARISH"
    return "NEUTRAL"


def evaluate_conditions_full(
    one_hour: list[list[Any]],
    four_hour: list[list[Any]],
) -> dict[str, Any]:
    """Returns full condition breakdown identical to live bot's evaluate_conditions()."""
    if len(one_hour) < 55 or len(four_hour) < 55:
        return {"conditions": [], "passCount": 0, "totalCount": 0,
                "bias": "NEUTRAL", "signal": "NO_TRADE",
                "oneHourTrend": "NEUTRAL", "fourHourTrend": "NEUTRAL", "indicators": {}}

    snap  = indicator_snapshot(one_hour)
    close = float(one_hour[-1][4])
    avg_vol = snap.get("_avg_volume") or 0.0
    volume  = snap["volume"] or 0.0
    one_hour_trend  = trend_for(one_hour)
    four_hour_trend = trend_for(four_hour)

    if one_hour_trend == "BULLISH" or four_hour_trend == "BULLISH":
        direction = "LONG"
    elif one_hour_trend == "BEARISH" or four_hour_trend == "BEARISH":
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    def c(name, current_val, required_val, passed):
        return {"name": name, "currentValue": current_val,
                "requiredValue": required_val, "pass": passed}

    rsi_val  = snap["rsi"]
    macd_val = snap["macd"]
    sig_val  = snap["macdSignal"]
    e20      = snap["ema20"]
    e50      = snap["ema50"]

    if direction == "LONG":
        cond_4h    = four_hour_trend == "BULLISH"
        cond_1h    = one_hour_trend  == "BULLISH"
        cond_rsi   = rsi_val  is not None and rsi_val  >= 50
        cond_macd  = macd_val is not None and sig_val  is not None and macd_val > sig_val
        cond_price = e20 is not None and e50 is not None and close > e20 > e50
        cond_vol   = avg_vol > 0 and volume >= avg_vol * 0.7
        conds = [
            c("4h Trend",    four_hour_trend,                                    "BULLISH",              cond_4h),
            c("1h Trend",    one_hour_trend,                                     "BULLISH",              cond_1h),
            c("RSI",         f"{rsi_val:.1f}"  if rsi_val  is not None else "—", "≥ 50",                 cond_rsi),
            c("MACD Momentum",
              f"{macd_val:.4f} > {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
              "MACD above signal",                                                                        cond_macd),
            c("Price vs MA", f"{close:.2f} > EMA20 {e20:.2f}" if e20 else "—",  "Price > EMA20 > EMA50", cond_price),
            c("Volume",      f"{volume:.4f}",                                    f"≥ {avg_vol*0.7:.4f}", cond_vol),
        ]
    elif direction == "SHORT":
        cond_4h    = four_hour_trend == "BEARISH"
        cond_1h    = one_hour_trend  == "BEARISH"
        cond_rsi   = rsi_val  is not None and rsi_val  <= 50
        cond_macd  = macd_val is not None and sig_val  is not None and macd_val < sig_val
        cond_price = e20 is not None and e50 is not None and close < e20 < e50
        cond_vol   = avg_vol > 0 and volume >= avg_vol * 0.7
        conds = [
            c("4h Trend",    four_hour_trend,                                    "BEARISH",              cond_4h),
            c("1h Trend",    one_hour_trend,                                     "BEARISH",              cond_1h),
            c("RSI",         f"{rsi_val:.1f}"  if rsi_val  is not None else "—", "≤ 50",                 cond_rsi),
            c("MACD Momentum",
              f"{macd_val:.4f} < {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
              "MACD below signal",                                                                        cond_macd),
            c("Price vs MA", f"{close:.2f} < EMA20 {e20:.2f}" if e20 else "—",  "Price < EMA20 < EMA50", cond_price),
            c("Volume",      f"{volume:.4f}",                                    f"≥ {avg_vol*0.7:.4f}", cond_vol),
        ]
    else:
        conds = [
            c("4h Trend",    four_hour_trend, "BULLISH or BEARISH",          False),
            c("1h Trend",    one_hour_trend,  "BULLISH or BEARISH",          False),
            c("RSI",         f"{rsi_val:.1f}" if rsi_val is not None else "—", "≥50/≤50", False),
            c("MACD Momentum", "—", "MACD above/below signal",               False),
            c("Price vs MA",   "—", "Price aligned with EMA20/50",           False),
            c("Volume",      f"{volume:.4f}", "≥ 70% of 20-period avg",      False),
        ]

    pass_count = sum(1 for cd in conds if cd["pass"])
    if all(cd["pass"] for cd in conds):
        signal = direction if direction != "NEUTRAL" else "NO_TRADE"
    else:
        signal = "NO_TRADE"

    indicators = {k: v for k, v in snap.items() if not k.startswith("_")}
    return {
        "conditions": conds, "passCount": pass_count, "totalCount": len(conds),
        "bias": direction, "signal": signal,
        "oneHourTrend": one_hour_trend, "fourHourTrend": four_hour_trend,
        "indicators": indicators,
    }


# ─── Strategy entry gate ──────────────────────────────────────────────────────

def check_entry(eval_result: dict, strategy: str) -> bool:
    """Return True if the given strategy allows entry based on the condition eval."""
    conds       = eval_result["conditions"]
    pass_count  = eval_result["passCount"]
    total_count = eval_result["totalCount"]
    bias        = eval_result["bias"]
    one_hour_t  = eval_result["oneHourTrend"]
    four_hour_t = eval_result["fourHourTrend"]

    if bias == "NEUTRAL" or total_count == 0:
        return False

    if strategy == "A":
        # 6/6 — original
        return pass_count == total_count

    if strategy == "B":
        # 5/6 — at least 5 conditions pass
        return pass_count >= total_count - 1

    if strategy == "C":
        # 5/6 + trend guard:
        #   - at least 5 conditions pass
        #   - 1h trend MUST agree with direction
        #   - 4h NEUTRAL is ok; 4h OPPOSITE blocks
        if pass_count < total_count - 1:
            return False
        if bias == "LONG":
            if one_hour_t != "BULLISH":
                return False           # 1h must be bullish
            if four_hour_t == "BEARISH":
                return False           # 4h opposite → block
        elif bias == "SHORT":
            if one_hour_t != "BEARISH":
                return False
            if four_hour_t == "BULLISH":
                return False
        return True

    return False


def missing_condition_name(eval_result: dict) -> str | None:
    """For a 5/6 pass, return the name of the one failed condition."""
    failed = [c["name"] for c in eval_result["conditions"] if not c["pass"]]
    return failed[0] if len(failed) == 1 else None


# ─── Kraken data fetching with pagination ─────────────────────────────────────

def fetch_json(url: str, retries: int = 3) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            req = Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "Replit-Kraken-Backtest/1.0"})
            with urlopen(req, timeout=20) as r:
                payload = json.loads(r.read().decode())
            errs = payload.get("error", [])
            if errs:
                raise RuntimeError("Kraken: " + ", ".join(map(str, errs)))
            return payload.get("result", {})
        except Exception as e:
            if attempt == retries - 1:
                raise
            _time.sleep(1 + attempt)
    return {}


def fetch_ohlc_all(pair: str, interval: int) -> list[list[Any]]:
    """Fetch as much OHLC history as Kraken gives us by paginating backwards."""
    all_rows: dict[int, list[Any]] = {}
    # Start from 6 months ago
    since = int((_time.time() - 60 * 24 * 3600))  # ~6 months back
    last_fetched_since = None

    while True:
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}&since={since}"
        result = fetch_json(url)
        rows = result.get(pair) or next(
            (v for k, v in result.items() if k != "last"), []
        )
        if not isinstance(rows, list) or len(rows) == 0:
            break
        # Filter out incomplete candle (last candle is in progress)
        now_ts = _time.time()
        completed = [r for r in rows if len(r) >= 8 and float(r[0]) < now_ts - interval * 60 * 0.9]
        for r in completed:
            ts = int(r[0])
            if ts not in all_rows:
                all_rows[ts] = r
        new_since = result.get("last")
        if new_since is None or new_since == last_fetched_since:
            break
        last_fetched_since = new_since
        since = int(new_since)
        _time.sleep(0.4)   # be nice to the API
        # stop if we've gone past now
        if since >= now_ts:
            break

    sorted_rows = sorted(all_rows.values(), key=lambda r: float(r[0]))
    return sorted_rows


# ─── Backtest engine ──────────────────────────────────────────────────────────

def run_backtest(
    coin: str,
    candles_1h: list[list[Any]],
    candles_4h: list[list[Any]],
    strategy: str,
) -> dict[str, Any]:
    """
    Walk candle-by-candle through 1h data, maintaining 4h context.
    Position sizing, SL/TP, risk limits identical to live bot.

    strategy: "A" | "B" | "C"
    """
    balance         = STARTING_BALANCE
    starting_bal    = STARTING_BALANCE
    open_pos        = None          # dict when in trade
    trades: list[dict] = []        # closed trade records
    balance_curve: list[float] = [balance]

    # Risk management state
    daily_loss      = 0.0
    day_key         = ""
    consec_losses   = 0

    # We need at least 55 1h candles and 55 4h candles to start
    MIN_CANDLES = 60  # a bit of headroom

    # Build a pointer into 4h candles for fast lookup
    # At each 1h candle[i], the relevant 4h window = all 4h candles with open_time < 1h[i].open_time
    four_h_idx = 0  # pointer into candles_4h

    # Track which condition was missing (for B-only trade analysis)
    extra_trades_missing: list[dict] = []  # only for strategy B & C

    for i in range(MIN_CANDLES, len(candles_1h)):
        candle      = candles_1h[i]
        candle_ts   = int(candle[0])
        candle_open = float(candle[1])
        candle_high = float(candle[2])
        candle_low  = float(candle[3])
        candle_close= float(candle[4])

        # Advance 4h pointer to include all completed 4h candles before this 1h candle
        while (four_h_idx + 1 < len(candles_4h) and
               float(candles_4h[four_h_idx + 1][0]) <= candle_ts):
            four_h_idx += 1

        # Slice of data visible at this candle
        one_h_window = candles_1h[max(0, i - 719) : i + 1]  # up to 720 candles
        four_h_window= candles_4h[max(0, four_h_idx - 719) : four_h_idx + 1]

        if len(one_h_window) < 55 or len(four_h_window) < 55:
            balance_curve.append(balance)
            continue

        # Daily loss tracking
        candle_date = datetime.fromtimestamp(candle_ts, tz=timezone.utc).date().isoformat()
        if candle_date != day_key:
            day_key    = candle_date
            daily_loss = 0.0

        # ── Position management: check SL/TP hit this candle ──────────────
        if open_pos is not None:
            direction    = open_pos["direction"]
            sl           = open_pos["stop_loss"]
            tp           = open_pos["take_profit"]
            entry        = open_pos["entry"]
            qty          = open_pos["quantity"]
            opened_at    = open_pos["opened_at"]
            missing_cond = open_pos.get("missing_cond")

            hit_sl = (candle_low  <= sl) if direction == "LONG"  else (candle_high >= sl)
            hit_tp = (candle_high >= tp) if direction == "LONG"  else (candle_low  <= tp)

            if hit_sl or hit_tp:
                # Both hit same candle → SL wins (conservative)
                if hit_sl and hit_tp:
                    exit_price   = sl
                    exit_reason  = "STOP_LOSS"
                elif hit_sl:
                    exit_price   = sl
                    exit_reason  = "STOP_LOSS"
                else:
                    exit_price   = tp
                    exit_reason  = "TAKE_PROFIT"

                if direction == "LONG":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty

                balance   += pnl
                daily_loss = max(0.0, daily_loss + (-pnl if pnl < 0 else 0.0))
                if pnl < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0

                trade_record = {
                    "entry":       entry,
                    "exit":        exit_price,
                    "direction":   direction,
                    "pnl":         pnl,
                    "qty":         qty,
                    "exit_reason": exit_reason,
                    "opened_at":   opened_at,
                    "closed_at":   candle_ts,
                    "duration_h":  (candle_ts - opened_at) / 3600,
                    "missing_cond": missing_cond,
                    "balance_after": balance,
                }
                trades.append(trade_record)
                open_pos = None

        balance_curve.append(balance)

        # ── Entry evaluation ───────────────────────────────────────────────
        if open_pos is not None:
            continue  # already in a trade

        # Risk management gate
        daily_limit  = starting_bal * DAILY_LOSS_LIMIT
        risk_paused  = (
            daily_loss       >= daily_limit           or
            consec_losses    >= MAX_CONSECUTIVE_LOSSES or
            balance          <= 0
        )
        if risk_paused:
            continue

        eval_r = evaluate_conditions_full(one_h_window, four_h_window)
        if not check_entry(eval_r, strategy):
            continue

        # Also check for A rejection (needed for extra-trade analysis)
        a_would_enter = check_entry(eval_r, "A")

        direction = eval_r["bias"]
        snap      = eval_r["indicators"]
        atr_val   = snap.get("atr")
        if atr_val is None or atr_val <= 0:
            continue

        stop_dist   = atr_val * ATR_MULTIPLIER
        risk_amount = balance * RISK_PER_TRADE
        quantity    = min(
            risk_amount / stop_dist,
            balance / candle_close if candle_close > 0 else 0,
        )
        if quantity <= 0:
            continue

        if direction == "LONG":
            sl = candle_close - stop_dist
            tp = candle_close + stop_dist * REWARD_TO_RISK
        else:
            sl = candle_close + stop_dist
            tp = candle_close - stop_dist * REWARD_TO_RISK

        # Record which condition was missing (B-only analysis)
        missing = missing_condition_name(eval_r) if not a_would_enter else None

        open_pos = {
            "direction":   direction,
            "entry":       candle_close,
            "stop_loss":   sl,
            "take_profit": tp,
            "quantity":    quantity,
            "opened_at":   candle_ts,
            "missing_cond": missing,
        }

    # Close any still-open trade at last candle close
    if open_pos is not None:
        last_c = candles_1h[-1]
        exit_p = float(last_c[4])
        if open_pos["direction"] == "LONG":
            pnl = (exit_p - open_pos["entry"]) * open_pos["quantity"]
        else:
            pnl = (open_pos["entry"] - exit_p) * open_pos["quantity"]
        balance += pnl
        balance_curve.append(balance)
        trades.append({
            "entry":         open_pos["entry"],
            "exit":          exit_p,
            "direction":     open_pos["direction"],
            "pnl":           pnl,
            "qty":           open_pos["quantity"],
            "exit_reason":   "MARKET_CLOSE",
            "opened_at":     open_pos["opened_at"],
            "closed_at":     int(last_c[0]),
            "duration_h":    (int(last_c[0]) - open_pos["opened_at"]) / 3600,
            "missing_cond":  open_pos.get("missing_cond"),
            "balance_after": balance,
        })

    return {
        "strategy":       strategy,
        "coin":           coin,
        "trades":         trades,
        "balance_curve":  balance_curve,
        "final_balance":  balance,
        "starting_bal":   starting_bal,
    }


# ─── Metrics computation ──────────────────────────────────────────────────────

def compute_metrics(result: dict) -> dict[str, Any]:
    trades  = result["trades"]
    curve   = result["balance_curve"]
    start   = result["starting_bal"]
    finish  = result["final_balance"]
    n       = len(trades)

    wins  = [t for t in trades if t["pnl"] > 0]
    loses = [t for t in trades if t["pnl"] <= 0]
    longs = [t for t in trades if t["direction"] == "LONG"]
    shrts = [t for t in trades if t["direction"] == "SHORT"]
    long_wins  = [t for t in longs if t["pnl"] > 0]
    short_wins = [t for t in shrts if t["pnl"] > 0]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss   = abs(sum(t["pnl"] for t in loses))
    profit_factor= (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win  = (gross_profit / len(wins))  if wins  else 0.0
    avg_loss = (gross_loss   / len(loses)) if loses else 0.0

    # Expectancy
    win_rate   = len(wins) / n if n > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if n > 0 else 0.0

    # Max drawdown (from peak of balance curve)
    peak = start
    max_dd = 0.0
    for b in curve:
        peak   = max(peak, b)
        max_dd = max(max_dd, (peak - b) / peak * 100)

    # Sharpe ratio (simplified: daily returns, rf=0)
    # Build daily balance from trade closing balance
    daily: dict[str, float] = {}
    bal = start
    for t in sorted(trades, key=lambda x: x["closed_at"]):
        d = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).date().isoformat()
        daily[d] = t["balance_after"]
    returns = []
    prev = start
    for d in sorted(daily):
        b = daily[d]
        if prev > 0:
            returns.append((b - prev) / prev)
        prev = b
    if len(returns) >= 5:
        mu  = sum(returns) / len(returns)
        var = sum((r - mu) ** 2 for r in returns) / len(returns)
        std = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mu / std * math.sqrt(252)) if std > 0 else 0.0
    else:
        sharpe = None

    avg_dur = (sum(t["duration_h"] for t in trades) / n) if n > 0 else 0.0

    return {
        "n":                n,
        "wins":             len(wins),
        "losses":           len(loses),
        "win_rate_pct":     win_rate * 100,
        "net_pnl":          finish - start,
        "roi_pct":          (finish - start) / start * 100,
        "final_balance":    finish,
        "profit_factor":    profit_factor,
        "avg_win":          avg_win,
        "avg_loss":         avg_loss,
        "expectancy":       expectancy,
        "max_drawdown_pct": max_dd,
        "largest_win":      max((t["pnl"] for t in trades), default=0.0),
        "largest_loss":     min((t["pnl"] for t in trades), default=0.0),
        "avg_duration_h":   avg_dur,
        "long_trades":      len(longs),
        "short_trades":     len(shrts),
        "long_win_rate":    (len(long_wins)  / len(longs)  * 100) if longs  else 0.0,
        "short_win_rate":   (len(short_wins) / len(shrts)  * 100) if shrts  else 0.0,
        "sharpe":           sharpe,
        "gross_profit":     gross_profit,
        "gross_loss":       gross_loss,
    }


def analyze_b_only_trades(
    b_result: dict,
    a_result: dict,
) -> dict[str, Any]:
    """Analyse the trades that B (or C) took but A rejected — grouped by missing condition."""
    b_trades = b_result["trades"]
    extra    = [t for t in b_trades if t.get("missing_cond") is not None]

    by_cond: dict[str, list[dict]] = {}
    for t in extra:
        cond = t["missing_cond"] or "Unknown"
        by_cond.setdefault(cond, []).append(t)

    result = {"total_extra": len(extra), "by_missing_condition": {}}
    for cond, ts in by_cond.items():
        wins  = [t for t in ts if t["pnl"] > 0]
        loses = [t for t in ts if t["pnl"] <= 0]
        gross_p = sum(t["pnl"] for t in wins)
        gross_l = abs(sum(t["pnl"] for t in loses))
        pf = (gross_p / gross_l) if gross_l > 0 else float("inf")
        wr = len(wins) / len(ts) * 100 if ts else 0.0
        roi = sum(t["pnl"] for t in ts) / STARTING_BALANCE * 100
        result["by_missing_condition"][cond] = {
            "trades":        len(ts),
            "wins":          len(wins),
            "losses":        len(loses),
            "win_rate_pct":  wr,
            "net_pnl":       sum(t["pnl"] for t in ts),
            "roi_pct":       roi,
            "profit_factor": pf,
        }
    return result


# ─── Pretty printing ──────────────────────────────────────────────────────────

def fmt_pct(v: float | None, dec: int = 2) -> str:
    if v is None: return "N/A"
    return f"{v:+.{dec}f}%"

def fmt_money(v: float | None, dec: int = 2) -> str:
    if v is None: return "N/A"
    return f"£{v:+.{dec}f}" if v != 0 else f"£{v:.{dec}f}"

def fmt_num(v: float | None, dec: int = 2) -> str:
    if v is None: return "N/A"
    return f"{v:.{dec}f}"

def fmt_inf(v: float) -> str:
    return "∞" if math.isinf(v) else f"{v:.3f}"


def print_strategy_block(label: str, m: dict) -> None:
    print(f"\n  {'─'*40}")
    print(f"  {label}")
    print(f"  {'─'*40}")
    print(f"  Starting balance    : £{STARTING_BALANCE:.2f}")
    print(f"  Final balance       : £{m['final_balance']:.2f}")
    print(f"  Net profit/loss     : {fmt_money(m['net_pnl'])}")
    print(f"  ROI                 : {fmt_pct(m['roi_pct'])}")
    print(f"  Total trades        : {m['n']}")
    print(f"  Wins / Losses       : {m['wins']} / {m['losses']}")
    print(f"  Win rate            : {fmt_pct(m['win_rate_pct'])}")
    print(f"  Avg winning trade   : {fmt_money(m['avg_win'])}")
    print(f"  Avg losing trade    : {fmt_money(-m['avg_loss'])}")
    print(f"  Profit factor       : {fmt_inf(m['profit_factor'])}")
    print(f"  Max drawdown        : {fmt_pct(m['max_drawdown_pct'])}")
    print(f"  Largest win         : {fmt_money(m['largest_win'])}")
    print(f"  Largest loss        : {fmt_money(m['largest_loss'])}")
    print(f"  Avg trade duration  : {fmt_num(m['avg_duration_h'])}h")
    print(f"  LONG trades         : {m['long_trades']}")
    print(f"  SHORT trades        : {m['short_trades']}")
    print(f"  LONG win rate       : {fmt_pct(m['long_win_rate'])}")
    print(f"  SHORT win rate      : {fmt_pct(m['short_win_rate'])}")
    print(f"  Expectancy/trade    : {fmt_money(m['expectancy'])}")
    sharpe = m['sharpe']
    print(f"  Sharpe ratio        : {fmt_num(sharpe) if sharpe is not None else 'N/A (< 5 trading days)'}")


def print_extra_analysis(label: str, analysis: dict) -> None:
    print(f"\n  Extra trades in {label} vs Strategy A: {analysis['total_extra']}")
    if analysis['total_extra'] == 0:
        print("  (No additional trades — strategies identical for this coin.)")
        return
    by_cond = analysis["by_missing_condition"]
    if not by_cond:
        print("  (All extra trades had more than one missing condition.)")
        return
    print(f"  {'Condition missing':<22} {'Trades':>6} {'Wins':>5} {'Losses':>7} {'Win%':>7} {'Net P/L':>10} {'PF':>7}")
    print(f"  {'-'*22} {'-'*6} {'-'*5} {'-'*7} {'-'*7} {'-'*10} {'-'*7}")
    for cond, d in sorted(by_cond.items(), key=lambda x: -x[1]["trades"]):
        print(
            f"  {cond:<22} {d['trades']:>6} {d['wins']:>5} {d['losses']:>7}"
            f" {d['win_rate_pct']:>6.1f}%"
            f" {fmt_money(d['net_pnl']):>10}"
            f" {fmt_inf(d['profit_factor']):>7}"
        )


def rank_strategies(metrics: dict[str, dict]) -> str:
    """Return a simple ranking sentence."""
    pf = {s: m["profit_factor"] for s, m in metrics.items()}
    roi = {s: m["roi_pct"] for s, m in metrics.items()}
    dd  = {s: m["max_drawdown_pct"] for s, m in metrics.items()}
    n   = {s: m["n"] for s, m in metrics.items()}

    # Score: weight profit factor 3x, ROI 2x, penalise DD and low sample
    scores: dict[str, float] = {}
    for s in metrics:
        pf_v  = min(pf[s], 5.0)          # cap ∞
        roi_v = roi[s]
        dd_v  = dd[s]
        n_v   = n[s]
        # Penalise if < 10 trades (unreliable)
        sample_penalty = 0.5 if n_v < 10 else (0.8 if n_v < 20 else 1.0)
        scores[s] = (3 * pf_v + 2 * roi_v - dd_v) * sample_penalty

    best = max(scores, key=lambda s: scores[s])
    return best


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("  KRAKEN PAPER-TRADER HISTORICAL BACKTEST")
    print("  Strategy A (6/6) vs B (5/6) vs C (5/6 + trend guard)")
    print("=" * 70)
    print(f"\n  Constants: RISK={RISK_PER_TRADE*100:.0f}% | ATR×{ATR_MULTIPLIER} | "
          f"R:R={REWARD_TO_RISK} | DD_LIMIT={DAILY_LOSS_LIMIT*100:.0f}%"
          f" | MAX_STREAK={MAX_CONSECUTIVE_LOSSES}")
    print("  Fees/slippage: none (matching live bot)")
    print("  Exit logic: intra-candle high/low SL/TP detection (conservative on both hits)")

    comparison_rows: list[dict] = []

    for coin, pair in COINS.items():
        print(f"\n{'='*70}")
        print(f"  COIN: {coin} ({pair})")
        print(f"{'='*70}")

        # Fetch data
        print(f"\n  Fetching 1h candles for {coin}...", end="", flush=True)
        try:
            c1h = fetch_ohlc_all(pair, 60)
            print(f" {len(c1h)} candles")
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        print(f"  Fetching 4h candles for {coin}...", end="", flush=True)
        try:
            c4h = fetch_ohlc_all(pair, 240)
            print(f" {len(c4h)} candles")
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        if len(c1h) < 120 or len(c4h) < 60:
            print(f"  Insufficient data for {coin}, skipping.")
            continue

        # Determine test period
        start_ts = int(c1h[0][0])
        end_ts   = int(c1h[-1][0])
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        end_dt   = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days     = (end_ts - start_ts) / 86400
        print(f"  Test period: {start_dt} → {end_dt} ({days:.0f} days, {len(c1h)} 1h candles)")

        # Run all three strategies
        results: dict[str, dict] = {}
        mets:    dict[str, dict] = {}
        for strat in ("A", "B", "C"):
            label = {"A": "6/6", "B": "5/6", "C": "5/6+Guard"}[strat]
            print(f"  Running Strategy {strat} ({label})...", end="", flush=True)
            res = run_backtest(coin, c1h, c4h, strat)
            m   = compute_metrics(res)
            results[strat] = res
            mets[strat]    = m
            print(f" {m['n']} trades, ROI {m['roi_pct']:+.2f}%")

        # Print detailed results
        for strat in ("A", "B", "C"):
            label = {"A": "Strategy A — 6/6 (Current)", "B": "Strategy B — 5/6 (Relaxed)", "C": "Strategy C — 5/6 + Trend Guard"}[strat]
            print_strategy_block(label, mets[strat])

        # Extra-trade analysis (B vs A)
        print("\n  ── Extra-trade analysis: Strategy B vs A ──")
        b_analysis = analyze_b_only_trades(results["B"], results["A"])
        print_extra_analysis("Strategy B", b_analysis)

        print("\n  ── Extra-trade analysis: Strategy C vs A ──")
        c_analysis = analyze_b_only_trades(results["C"], results["A"])
        print_extra_analysis("Strategy C", c_analysis)

        # Ranking for this coin
        best = rank_strategies(mets)
        best_label = {"A": "Strategy A (6/6)", "B": "Strategy B (5/6)", "C": "Strategy C (5/6+Guard)"}[best]
        print(f"\n  ▶ Best overall for {coin}: {best_label}")
        print(f"    (Scored on PF×3 + ROI×2 − MaxDD, with sample-size penalty for < 20 trades)")

        comparison_rows.append({
            "coin": coin,
            "days": days,
            "mets": mets,
        })

    # ── Comparison table ──────────────────────────────────────────────────────
    if not comparison_rows:
        print("\nNo data available for comparison table.")
        return

    print(f"\n\n{'='*70}")
    print("  CROSS-COIN COMPARISON TABLE")
    print(f"{'='*70}")
    header = (
        f"{'Coin':<5} {'Days':>5} "
        f"{'A ROI%':>8} {'B ROI%':>8} {'C ROI%':>8} "
        f"{'A Trd':>6} {'B Trd':>6} {'C Trd':>6} "
        f"{'A WR%':>6} {'B WR%':>6} {'C WR%':>6} "
        f"{'A PF':>6} {'B PF':>6} {'C PF':>6} "
        f"{'A DD%':>6} {'B DD%':>6} {'C DD%':>6}"
    )
    print(f"\n  {header}")
    print(f"  {'-'*len(header)}")
    for row in comparison_rows:
        m = row["mets"]
        def g(s, k): return m[s][k]
        print(
            f"  {row['coin']:<5} {row['days']:>5.0f} "
            f"{g('A','roi_pct'):>+8.2f} {g('B','roi_pct'):>+8.2f} {g('C','roi_pct'):>+8.2f} "
            f"{g('A','n'):>6} {g('B','n'):>6} {g('C','n'):>6} "
            f"{g('A','win_rate_pct'):>6.1f} {g('B','win_rate_pct'):>6.1f} {g('C','win_rate_pct'):>6.1f} "
            f"{fmt_inf(g('A','profit_factor')):>6} {fmt_inf(g('B','profit_factor')):>6} {fmt_inf(g('C','profit_factor')):>6} "
            f"{g('A','max_drawdown_pct'):>6.2f} {g('B','max_drawdown_pct'):>6.2f} {g('C','max_drawdown_pct'):>6.2f}"
        )

    # ── Overall recommendation ─────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  FINAL RECOMMENDATIONS (per coin)")
    print(f"{'='*70}")
    for row in comparison_rows:
        coin = row["coin"]
        mets = row["mets"]
        best = rank_strategies(mets)
        best_label = {"A": "Strategy A (6/6 — Current)", "B": "Strategy B (5/6 — Relaxed)", "C": "Strategy C (5/6 + Trend Guard)"}[best]
        m_b = mets[best]
        print(f"\n  {coin}: ► {best_label}")
        print(f"        Trades={m_b['n']}  ROI={m_b['roi_pct']:+.2f}%  "
              f"PF={fmt_inf(m_b['profit_factor'])}  MaxDD={m_b['max_drawdown_pct']:.2f}%  "
              f"WR={m_b['win_rate_pct']:.1f}%")
        # Caveats
        if m_b["n"] < 15:
            print(f"        ⚠ Sample size is very small ({m_b['n']} trades) — treat with caution.")
        if m_b["max_drawdown_pct"] > 15:
            print(f"        ⚠ Drawdown > 15% — risk of significant account impairment.")

    print("\n  ─ No live-bot settings have been changed. ─")
    print("  ─ Approve a strategy change explicitly to modify the live bot. ─\n")


if __name__ == "__main__":
    main()
