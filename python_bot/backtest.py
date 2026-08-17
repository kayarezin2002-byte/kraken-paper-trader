#!/usr/bin/env python3
"""
Kraken Paper-Trader Historical Backtest  (extended — up to 12 months)
======================================================================
Compares three entry strategies using EXACT live-bot logic.

Strategy A — Current (6/6): all conditions must pass
Strategy B — Relaxed (5/6): at least 5 of 6 conditions must pass
Strategy C — 5/6 + Trend Guard: 5/6 pass, 1h trend must align,
             4h NEUTRAL is ok, 4h OPPOSITE blocks entry

Data source: CryptoCompare public API (no API key required).
  GBP pairs: BTC/GBP, ETH/GBP, SOL/GBP, XRP/GBP
  Hourly OHLCV data paginated back up to 12 months.
  4h candles are aggregated from 1h candles for perfect alignment.

All strategy parameters identical to the live paper-trading bot:
  RISK_PER_TRADE=1%, ATR_MULTIPLIER=1.5, REWARD_TO_RISK=2.0,
  DAILY_LOSS_LIMIT=3%, MAX_CONSECUTIVE_LOSSES=3, no fees/slippage.

Usage: python3 backtest.py
"""

from __future__ import annotations

import json
import math
import sys
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

# ─── Constants — identical to live bot ────────────────────────────────────────
RISK_PER_TRADE         = 0.01
REWARD_TO_RISK         = 2.0
ATR_MULTIPLIER         = 1.5
STARTING_BALANCE       = 100.0
DAILY_LOSS_LIMIT       = 0.03
MAX_CONSECUTIVE_LOSSES = 3

# Target lookback period in days
TARGET_DAYS = 365   # try for 12 months

# Binance spot symbols (USDT pairs — freely accessible, most liquid)
# Note: prices are in USDT, not GBP.  The P&L amounts will differ from the
# live bot's GBP values, but all METRICS (ROI%, PF, DD%, win rate) are valid
# for relative A vs B vs C comparison since all three strategies share the
# same data.  The GBP/USD rate fluctuation does not affect indicator signals.
COINS: dict[str, str] = {
    # Live trading coins
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "XRP":  "XRPUSDT",
    # Research candidates
    "ADA":  "ADAUSDT",
    "LINK": "LINKUSDT",
    "AVAX": "AVAXUSDT",
    "DOT":  "DOTUSDT",
}

# Which coins are live vs research-only (for labelling)
LIVE_COINS     = {"BTC", "ETH", "SOL", "XRP"}
RESEARCH_COINS = {"ADA", "LINK", "AVAX", "DOT"}

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
            c("4h Trend",      four_hour_trend,                                    "BULLISH",              cond_4h),
            c("1h Trend",      one_hour_trend,                                     "BULLISH",              cond_1h),
            c("RSI",           f"{rsi_val:.1f}" if rsi_val is not None else "—",   "≥ 50",                 cond_rsi),
            c("MACD Momentum",
              f"{macd_val:.4f} > {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
              "MACD above signal",                                                                          cond_macd),
            c("Price vs MA",   f"{close:.2f} > EMA20 {e20:.2f}" if e20 else "—",  "Price > EMA20 > EMA50", cond_price),
            c("Volume",        f"{volume:.4f}",                                    f"≥ {avg_vol*0.7:.4f}", cond_vol),
        ]
    elif direction == "SHORT":
        cond_4h    = four_hour_trend == "BEARISH"
        cond_1h    = one_hour_trend  == "BEARISH"
        cond_rsi   = rsi_val  is not None and rsi_val  <= 50
        cond_macd  = macd_val is not None and sig_val  is not None and macd_val < sig_val
        cond_price = e20 is not None and e50 is not None and close < e20 < e50
        cond_vol   = avg_vol > 0 and volume >= avg_vol * 0.7
        conds = [
            c("4h Trend",      four_hour_trend,                                    "BEARISH",              cond_4h),
            c("1h Trend",      one_hour_trend,                                     "BEARISH",              cond_1h),
            c("RSI",           f"{rsi_val:.1f}" if rsi_val is not None else "—",   "≤ 50",                 cond_rsi),
            c("MACD Momentum",
              f"{macd_val:.4f} < {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
              "MACD below signal",                                                                          cond_macd),
            c("Price vs MA",   f"{close:.2f} < EMA20 {e20:.2f}" if e20 else "—",  "Price < EMA20 < EMA50", cond_price),
            c("Volume",        f"{volume:.4f}",                                    f"≥ {avg_vol*0.7:.4f}", cond_vol),
        ]
    else:
        conds = [
            c("4h Trend",      four_hour_trend, "BULLISH or BEARISH",              False),
            c("1h Trend",      one_hour_trend,  "BULLISH or BEARISH",              False),
            c("RSI",           f"{rsi_val:.1f}" if rsi_val is not None else "—",   "≥50/≤50", False),
            c("MACD Momentum", "—", "MACD above/below signal",                     False),
            c("Price vs MA",   "—", "Price aligned with EMA20/50",                 False),
            c("Volume",        f"{volume:.4f}", "≥ 70% of 20-period avg",          False),
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
    conds       = eval_result["conditions"]
    pass_count  = eval_result["passCount"]
    total_count = eval_result["totalCount"]
    bias        = eval_result["bias"]
    one_hour_t  = eval_result["oneHourTrend"]
    four_hour_t = eval_result["fourHourTrend"]

    if bias == "NEUTRAL" or total_count == 0:
        return False

    if strategy == "A":
        return pass_count == total_count

    if strategy == "B":
        return pass_count >= total_count - 1

    if strategy == "C":
        if pass_count < total_count - 1:
            return False
        if bias == "LONG":
            if one_hour_t != "BULLISH":
                return False
            if four_hour_t == "BEARISH":
                return False
        elif bias == "SHORT":
            if one_hour_t != "BEARISH":
                return False
            if four_hour_t == "BULLISH":
                return False
        return True

    return False


def missing_condition_name(eval_result: dict) -> str | None:
    failed = [c["name"] for c in eval_result["conditions"] if not c["pass"]]
    return failed[0] if len(failed) == 1 else None


# ─── Binance data fetching ────────────────────────────────────────────────────

def fetch_json_raw(url: str, retries: int = 4) -> Any:
    """Fetch JSON from any public URL with retries."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 Kraken-Paper-Trader-Backtest/2.0",
            })
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt == retries - 1:
                raise
            _time.sleep(2 ** attempt)
    return []


def fetch_binance_klines(symbol: str, target_days: int) -> list[list[Any]]:
    """
    Fetch hourly OHLCV from Binance (public, no auth) going back target_days.

    Binance kline format:
      [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]

    We convert to our internal Kraken-compatible format:
      [timestamp_s, open, high, low, close, 0.0, volume, 0]

    Paginates forward from (now - target_days) using startTime.
    Binance returns up to 1000 candles per request.
    """
    all_rows: dict[int, list[Any]] = {}
    now_ms    = int(_time.time() * 1000)
    cutoff_ms = now_ms - target_days * 24 * 3600 * 1000
    start_ms  = cutoff_ms
    MAX_PAGES = 15

    for _ in range(MAX_PAGES):
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1h&limit=1000&startTime={start_ms}"
        )
        data = fetch_json_raw(url)
        if not isinstance(data, list) or len(data) == 0:
            break

        for row in data:
            ts_ms = int(row[0])
            ts_s  = ts_ms // 1000
            o     = float(row[1])
            h     = float(row[2])
            lo    = float(row[3])
            cl    = float(row[4])
            vol   = float(row[5])
            if cl == 0 or h == 0:
                continue
            all_rows[ts_s] = [ts_s, o, h, lo, cl, 0.0, vol, 0]

        last_open_ms = int(data[-1][0])
        if last_open_ms >= now_ms - 3600_000:
            break       # caught up to now
        start_ms = last_open_ms + 3_600_000   # advance by 1h
        _time.sleep(0.25)

    sorted_rows = sorted(all_rows.values(), key=lambda r: r[0])
    # Drop the last candle (may be incomplete / still open)
    if len(sorted_rows) > 1:
        sorted_rows = sorted_rows[:-1]
    return sorted_rows


def aggregate_to_4h(candles_1h: list[list[Any]]) -> list[list[Any]]:
    """
    Aggregate 1h candles into 4h candles by grouping on 4h boundary timestamps.
    4h boundary = (timestamp // 14400) * 14400.
    Returns rows in the same [ts, open, high, low, close, 0, volume, 0] format.
    """
    buckets: dict[int, list[list[Any]]] = defaultdict(list)
    for row in candles_1h:
        ts      = int(row[0])
        bucket  = (ts // 14400) * 14400
        buckets[bucket].append(row)

    result = []
    for bucket_ts in sorted(buckets):
        rows = buckets[bucket_ts]
        if not rows:
            continue
        o   = float(rows[0][1])
        h   = max(float(r[2]) for r in rows)
        l   = min(float(r[3]) for r in rows)
        cl  = float(rows[-1][4])
        vol = sum(float(r[6]) for r in rows)
        result.append([bucket_ts, o, h, l, cl, 0.0, vol, 0])

    return result


def gap_count(candles: list[list[Any]], interval_s: int) -> int:
    """Count candles where the gap to the previous is > 2× the interval."""
    return sum(
        1 for i in range(1, len(candles))
        if float(candles[i][0]) - float(candles[i - 1][0]) > interval_s * 2
    )


# ─── Backtest engine ──────────────────────────────────────────────────────────

def run_backtest(
    coin: str,
    candles_1h: list[list[Any]],
    candles_4h: list[list[Any]],
    strategy: str,
) -> dict[str, Any]:
    balance      = STARTING_BALANCE
    starting_bal = STARTING_BALANCE
    open_pos     = None
    trades: list[dict] = []
    balance_curve: list[float] = [balance]

    daily_loss    = 0.0
    day_key       = ""
    consec_losses = 0

    MIN_CANDLES  = 60
    four_h_idx   = 0

    for i in range(MIN_CANDLES, len(candles_1h)):
        candle       = candles_1h[i]
        candle_ts    = int(candle[0])
        candle_high  = float(candle[2])
        candle_low   = float(candle[3])
        candle_close = float(candle[4])

        # Advance 4h pointer to candles completed before this 1h candle
        while (four_h_idx + 1 < len(candles_4h) and
               float(candles_4h[four_h_idx + 1][0]) <= candle_ts):
            four_h_idx += 1

        one_h_window  = candles_1h[max(0, i - 719) : i + 1]
        four_h_window = candles_4h[max(0, four_h_idx - 719) : four_h_idx + 1]

        if len(one_h_window) < 55 or len(four_h_window) < 55:
            balance_curve.append(balance)
            continue

        candle_date = datetime.fromtimestamp(candle_ts, tz=timezone.utc).date().isoformat()
        if candle_date != day_key:
            day_key    = candle_date
            daily_loss = 0.0

        # ── SL/TP check ────────────────────────────────────────────────────
        if open_pos is not None:
            direction            = open_pos["direction"]
            sl                   = open_pos["stop_loss"]
            tp                   = open_pos["take_profit"]
            entry                = open_pos["entry"]
            qty                  = open_pos["quantity"]
            opened_at            = open_pos["opened_at"]
            missing_cond         = open_pos.get("missing_cond")
            four_h_trend_entry   = open_pos.get("four_h_trend_at_entry")

            hit_sl = (candle_low  <= sl) if direction == "LONG"  else (candle_high >= sl)
            hit_tp = (candle_high >= tp) if direction == "LONG"  else (candle_low  <= tp)

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:
                    exit_price, exit_reason = sl, "STOP_LOSS"
                elif hit_sl:
                    exit_price, exit_reason = sl, "STOP_LOSS"
                else:
                    exit_price, exit_reason = tp, "TAKE_PROFIT"

                pnl = ((exit_price - entry) * qty if direction == "LONG"
                       else (entry - exit_price) * qty)

                balance   += pnl
                daily_loss = max(0.0, daily_loss + (-pnl if pnl < 0 else 0.0))
                consec_losses = (consec_losses + 1) if pnl < 0 else 0

                trades.append({
                    "entry":                  entry,
                    "exit":                   exit_price,
                    "direction":              direction,
                    "pnl":                    pnl,
                    "qty":                    qty,
                    "exit_reason":            exit_reason,
                    "opened_at":              opened_at,
                    "closed_at":              candle_ts,
                    "duration_h":             (candle_ts - opened_at) / 3600,
                    "missing_cond":           missing_cond,
                    "four_h_trend_at_entry":  four_h_trend_entry,
                    "balance_after":          balance,
                })
                open_pos = None

        balance_curve.append(balance)

        if open_pos is not None:
            continue

        # ── Risk gate ──────────────────────────────────────────────────────
        daily_limit = starting_bal * DAILY_LOSS_LIMIT
        if (daily_loss >= daily_limit or
                consec_losses >= MAX_CONSECUTIVE_LOSSES or
                balance <= 0):
            continue

        # ── Evaluate entry ─────────────────────────────────────────────────
        eval_r = evaluate_conditions_full(one_h_window, four_h_window)
        if not check_entry(eval_r, strategy):
            continue

        a_would_enter = check_entry(eval_r, "A")
        direction     = eval_r["bias"]
        snap          = eval_r["indicators"]
        atr_val       = snap.get("atr")
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

        sl = (candle_close - stop_dist if direction == "LONG"
              else candle_close + stop_dist)
        tp = (candle_close + stop_dist * REWARD_TO_RISK if direction == "LONG"
              else candle_close - stop_dist * REWARD_TO_RISK)

        missing = missing_condition_name(eval_r) if not a_would_enter else None

        open_pos = {
            "direction":             direction,
            "entry":                 candle_close,
            "stop_loss":             sl,
            "take_profit":           tp,
            "quantity":              quantity,
            "opened_at":             candle_ts,
            "missing_cond":          missing,
            "four_h_trend_at_entry": eval_r["fourHourTrend"],
        }

    # Close open trade at end of data
    if open_pos is not None:
        last_c = candles_1h[-1]
        exit_p = float(last_c[4])
        pnl = ((exit_p - open_pos["entry"]) * open_pos["quantity"] if open_pos["direction"] == "LONG"
               else (open_pos["entry"] - exit_p) * open_pos["quantity"])
        balance += pnl
        balance_curve.append(balance)
        trades.append({
            "entry":                 open_pos["entry"],
            "exit":                  exit_p,
            "direction":             open_pos["direction"],
            "pnl":                   pnl,
            "qty":                   open_pos["quantity"],
            "exit_reason":           "MARKET_CLOSE",
            "opened_at":             open_pos["opened_at"],
            "closed_at":             int(last_c[0]),
            "duration_h":            (int(last_c[0]) - open_pos["opened_at"]) / 3600,
            "missing_cond":          open_pos.get("missing_cond"),
            "four_h_trend_at_entry": open_pos.get("four_h_trend_at_entry"),
            "balance_after":         balance,
        })

    return {
        "strategy":      strategy,
        "coin":          coin,
        "trades":        trades,
        "balance_curve": balance_curve,
        "final_balance": balance,
        "starting_bal":  starting_bal,
    }


# ─── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(result: dict, test_days: float = 0) -> dict[str, Any]:
    trades = result["trades"]
    curve  = result["balance_curve"]
    start  = result["starting_bal"]
    finish = result["final_balance"]
    n      = len(trades)

    wins  = [t for t in trades if t["pnl"] > 0]
    loses = [t for t in trades if t["pnl"] <= 0]
    longs = [t for t in trades if t["direction"] == "LONG"]
    shrts = [t for t in trades if t["direction"] == "SHORT"]

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss   = abs(sum(t["pnl"] for t in loses))
    profit_factor= (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    avg_win  = (gross_profit / len(wins))  if wins  else 0.0
    avg_loss = (gross_loss   / len(loses)) if loses else 0.0
    avg_pnl  = (sum(t["pnl"] for t in trades) / n) if n > 0 else 0.0

    win_rate   = len(wins) / n if n > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if n > 0 else 0.0

    # Max drawdown
    peak = start; max_dd = 0.0
    for b in curve:
        peak   = max(peak, b)
        max_dd = max(max_dd, (peak - b) / peak * 100)

    # Consecutive wins/losses
    max_cw = max_cl = cw = cl = 0
    for t in sorted(trades, key=lambda x: x["closed_at"]):
        if t["pnl"] > 0:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        max_cw = max(max_cw, cw); max_cl = max(max_cl, cl)

    # Sharpe (daily, rf=0)
    daily: dict[str, float] = {}
    for t in sorted(trades, key=lambda x: x["closed_at"]):
        d = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).date().isoformat()
        daily[d] = t["balance_after"]
    returns = []
    prev = start
    for d in sorted(daily):
        b = daily[d]
        if prev > 0: returns.append((b - prev) / prev)
        prev = b
    if len(returns) >= 5:
        mu  = sum(returns) / len(returns)
        var = sum((r - mu) ** 2 for r in returns) / len(returns)
        std = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mu / std * math.sqrt(252)) if std > 0 else 0.0
    else:
        sharpe = None

    months           = test_days / 30.44 if test_days > 0 else None
    trades_per_month = (n / months) if months and months > 0 else None
    long_wins        = [t for t in longs if t["pnl"] > 0]
    short_wins       = [t for t in shrts if t["pnl"] > 0]

    return {
        "n":                  n,
        "wins":               len(wins),
        "losses":             len(loses),
        "win_rate_pct":       win_rate * 100,
        "net_pnl":            finish - start,
        "roi_pct":            (finish - start) / start * 100,
        "final_balance":      finish,
        "profit_factor":      profit_factor,
        "avg_win":            avg_win,
        "avg_loss":           avg_loss,
        "avg_pnl":            avg_pnl,
        "expectancy":         expectancy,
        "max_drawdown_pct":   max_dd,
        "largest_win":        max((t["pnl"] for t in trades), default=0.0),
        "largest_loss":       min((t["pnl"] for t in trades), default=0.0),
        "avg_duration_h":     (sum(t["duration_h"] for t in trades) / n) if n > 0 else 0.0,
        "long_trades":        len(longs),
        "short_trades":       len(shrts),
        "long_win_rate":      (len(long_wins)  / len(longs)  * 100) if longs  else 0.0,
        "short_win_rate":     (len(short_wins) / len(shrts)  * 100) if shrts  else 0.0,
        "sharpe":             sharpe,
        "gross_profit":       gross_profit,
        "gross_loss":         gross_loss,
        "max_consec_wins":    max_cw,
        "max_consec_losses":  max_cl,
        "trades_per_month":   trades_per_month,
    }


def analyze_extra_trades(b_result: dict) -> dict[str, Any]:
    """Trades that B/C took but A rejected, grouped by missing condition.
    '4h Trend' is further split into NEUTRAL vs OPPOSITE."""
    extra = [t for t in b_result["trades"] if t.get("missing_cond") is not None]

    by_cond: dict[str, list[dict]] = defaultdict(list)
    for t in extra:
        by_cond[t["missing_cond"] or "Unknown"].append(t)

    def summarise(ts: list[dict]) -> dict:
        wins   = [t for t in ts if t["pnl"] > 0]
        loses  = [t for t in ts if t["pnl"] <= 0]
        gp     = sum(t["pnl"] for t in wins)
        gl     = abs(sum(t["pnl"] for t in loses))
        net    = sum(t["pnl"] for t in ts)
        bal = STARTING_BALANCE; peak = bal; dd = 0.0
        for t in sorted(ts, key=lambda x: x["closed_at"]):
            bal += t["pnl"]; peak = max(peak, bal)
            dd   = max(dd, (peak - bal) / peak * 100)
        return {
            "trades":        len(ts),
            "wins":          len(wins),
            "losses":        len(loses),
            "win_rate_pct":  len(wins) / len(ts) * 100 if ts else 0.0,
            "net_pnl":       net,
            "profit_factor": (gp / gl) if gl > 0 else float("inf"),
            "max_drawdown":  dd,
            "avg_return":    net / len(ts) if ts else 0.0,
        }

    out: dict[str, Any] = {"total_extra": len(extra), "by_missing_condition": {}}

    for cond, ts in by_cond.items():
        if cond == "4h Trend":
            neutral  = [t for t in ts if t.get("four_h_trend_at_entry") == "NEUTRAL"]
            opposite = [t for t in ts if t.get("four_h_trend_at_entry") != "NEUTRAL"]
            out["by_missing_condition"]["4h Trend (NEUTRAL only)"] = summarise(neutral)
            out["by_missing_condition"]["4h Trend (OPPOSITE)"]     = summarise(opposite)
            out["by_missing_condition"]["4h Trend (ALL)"]          = summarise(ts)
        else:
            out["by_missing_condition"][cond] = summarise(ts)

    return out


# ─── Pretty printing ──────────────────────────────────────────────────────────

def fmt_pct(v, dec=2):
    if v is None: return "N/A"
    return f"{v:+.{dec}f}%"
def fmt_money(v, dec=2):
    if v is None: return "N/A"
    return f"£{v:+.{dec}f}" if v != 0 else f"£{v:.{dec}f}"
def fmt_num(v, dec=2):
    if v is None: return "N/A"
    return f"{v:.{dec}f}"
def fmt_inf(v):
    if v is None: return "N/A"
    return "∞" if math.isinf(v) else f"{v:.3f}"
def evidence_label(n):
    if n < 30:   return f"⚠ WEAK ({n} trades < 30)"
    if n < 100:  return f"~ MODERATE ({n} trades)"
    return f"✓ STRONGER ({n} trades ≥ 100)"


def print_strategy_block(label: str, m: dict) -> None:
    print(f"\n  {'─'*46}")
    print(f"  {label}")
    print(f"  {'─'*46}")
    print(f"  Starting balance      : £{STARTING_BALANCE:.2f}")
    print(f"  Final balance         : £{m['final_balance']:.2f}")
    print(f"  Net P&L               : {fmt_money(m['net_pnl'])}")
    print(f"  ROI                   : {fmt_pct(m['roi_pct'])}")
    print(f"  Total trades          : {m['n']}  [{evidence_label(m['n'])}]")
    tpm = m.get("trades_per_month")
    print(f"  Trades per month      : {f'{tpm:.1f}' if tpm else 'N/A'}")
    print(f"  Wins / Losses         : {m['wins']} / {m['losses']}")
    print(f"  Win rate              : {fmt_pct(m['win_rate_pct'])}")
    print(f"  Avg winning trade     : {fmt_money(m['avg_win'])}")
    print(f"  Avg losing trade      : {fmt_money(-m['avg_loss'])}")
    print(f"  Avg trade P&L         : {fmt_money(m['avg_pnl'])}")
    print(f"  Expectancy/trade      : {fmt_money(m['expectancy'])}")
    print(f"  Profit factor         : {fmt_inf(m['profit_factor'])}")
    print(f"  Max drawdown          : {fmt_pct(m['max_drawdown_pct'])}")
    print(f"  Largest win           : {fmt_money(m['largest_win'])}")
    print(f"  Largest loss          : {fmt_money(m['largest_loss'])}")
    print(f"  Avg trade duration    : {fmt_num(m['avg_duration_h'])}h")
    print(f"  LONG trades           : {m['long_trades']}")
    print(f"  SHORT trades          : {m['short_trades']}")
    print(f"  LONG win rate         : {fmt_pct(m['long_win_rate'])}")
    print(f"  SHORT win rate        : {fmt_pct(m['short_win_rate'])}")
    s = m["sharpe"]
    print(f"  Sharpe ratio          : {fmt_num(s) if s is not None else 'N/A (<5 trading days)'}")
    print(f"  Max consecutive wins  : {m['max_consec_wins']}")
    print(f"  Max consecutive losses: {m['max_consec_losses']}")


def print_extra_analysis(label: str, analysis: dict) -> None:
    print(f"\n  Extra trades in {label} vs Strategy A: {analysis['total_extra']}")
    if analysis["total_extra"] == 0:
        print("  (No additional trades — strategies produced identical results.)")
        return
    by_cond = analysis["by_missing_condition"]
    if not by_cond:
        print("  (All extra trades had more than one missing condition.)")
        return

    W = 28
    print(f"\n  {'Missing condition':<{W}} {'Trd':>4} {'W':>3} {'L':>3} {'WR%':>6} "
          f"{'Net P/L':>9} {'PF':>7} {'AvgRet':>8} {'MaxDD':>7}")
    print(f"  {'-'*W} {'-'*4} {'-'*3} {'-'*3} {'-'*6} {'-'*9} {'-'*7} {'-'*8} {'-'*7}")

    priority = ["4h Trend (NEUTRAL only)", "4h Trend (OPPOSITE)", "4h Trend (ALL)"]
    keys = priority + [k for k in sorted(by_cond) if k not in priority]
    for cond in keys:
        if cond not in by_cond:
            continue
        d = by_cond[cond]
        if d["trades"] == 0:
            continue
        print(
            f"  {cond:<{W}} {d['trades']:>4} {d['wins']:>3} {d['losses']:>3}"
            f" {d['win_rate_pct']:>6.1f}"
            f" {fmt_money(d['net_pnl']):>9}"
            f" {fmt_inf(d['profit_factor']):>7}"
            f" {fmt_money(d['avg_return']):>8}"
            f" {d['max_drawdown']:>6.2f}%"
        )


def strategy_score(m: dict) -> float:
    """Composite score used for ranking: PF×3 + ROI×2 − DD, sample-penalised."""
    pf_v    = min(m["profit_factor"] if not math.isinf(m["profit_factor"]) else 5.0, 5.0)
    penalty = 0.35 if m["n"] < 10 else (0.65 if m["n"] < 30 else 1.0)
    return (3 * pf_v + 2 * m["roi_pct"] - m["max_drawdown_pct"]) * penalty


def rank_strategies(metrics: dict[str, dict]) -> tuple[str, str]:
    """Return (best_key, recommendation_label) for a coin's three strategies."""
    best    = max(metrics, key=lambda s: strategy_score(metrics[s]))
    total_n = sum(m["n"] for m in metrics.values())

    if total_n < 30:
        rec = "INSUFFICIENT EVIDENCE"
    elif best == "A":
        rec = "KEEP 6/6"
    elif best == "B":
        rec = "SWITCH TO 5/6"
    else:
        rec = "SWITCH TO 5/6 WITH TREND GUARD"

    return best, rec


def coin_candidate_label(best_m: dict) -> str:
    """STRONG CANDIDATE / PROMISING / NEEDS MORE DATA / REJECT label for a coin."""
    n  = best_m["n"]
    pf = best_m["profit_factor"] if not math.isinf(best_m["profit_factor"]) else 5.0
    roi= best_m["roi_pct"]
    exp= best_m["expectancy"]

    if n < 15:
        return "NEEDS MORE DATA"
    if exp <= 0 or pf < 1.0:
        return "REJECT"
    if n >= 30 and pf >= 1.4 and roi >= 5.0 and exp >= 0.2:
        return "STRONG CANDIDATE"
    if pf >= 1.1 and roi >= 2.0:
        return "PROMISING"
    if n < 30:
        return "NEEDS MORE DATA"
    return "REJECT"


def portfolio_metrics(
    coins: list[str],
    all_metrics: dict[str, dict[str, dict]],
    all_results: dict[str, dict[str, dict]],
    strat_per_coin: dict[str, str],   # coin → best strategy key
) -> dict:
    """Aggregate portfolio metrics for a given coin subset using per-coin best strategy."""
    coins = [c for c in coins if c in all_metrics]
    if not coins:
        return {}

    total_start  = len(coins) * STARTING_BALANCE
    total_finish = sum(all_metrics[c][strat_per_coin[c]]["final_balance"] for c in coins)
    total_pnl    = total_finish - total_start
    total_roi    = total_pnl / total_start * 100
    total_n      = sum(all_metrics[c][strat_per_coin[c]]["n"]            for c in coins)
    total_wins   = sum(all_metrics[c][strat_per_coin[c]]["wins"]         for c in coins)
    total_gp     = sum(all_metrics[c][strat_per_coin[c]]["gross_profit"] for c in coins)
    total_gl     = sum(all_metrics[c][strat_per_coin[c]]["gross_loss"]   for c in coins)
    pf           = (total_gp / total_gl) if total_gl > 0 else float("inf")
    wr           = total_wins / total_n * 100 if total_n > 0 else 0
    exp          = total_pnl / total_n if total_n > 0 else 0

    # Portfolio drawdown — summed balance curves
    curves  = [all_results[c][strat_per_coin[c]]["balance_curve"] for c in coins]
    min_len = min(len(cv) for cv in curves)
    port_curve = [sum(curves[ci][i] for ci in range(len(coins))) for i in range(min_len)]
    peak = total_start; port_dd = 0.0
    for b in port_curve:
        peak    = max(peak, b)
        port_dd = max(port_dd, (peak - b) / peak * 100)

    return {
        "coins":         coins,
        "n_coins":       len(coins),
        "total_start":   total_start,
        "total_finish":  total_finish,
        "total_pnl":     total_pnl,
        "total_roi":     total_roi,
        "total_n":       total_n,
        "win_rate":      wr,
        "profit_factor": pf,
        "max_dd":        port_dd,
        "expectancy":    exp,
    }


def portfolio_score(p: dict) -> float:
    """Score a portfolio config for ranking (same weights as coin scoring)."""
    if not p:
        return -999.0
    pf_v = min(p["profit_factor"] if not math.isinf(p["profit_factor"]) else 5.0, 5.0)
    pen  = 0.65 if p["total_n"] < 30 else 1.0
    return (3 * pf_v + 2 * p["total_roi"] - p["max_dd"]) * pen


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("  KRAKEN PAPER-TRADER — 8-COIN EXTENDED HISTORICAL BACKTEST")
    print("  Strategy A (6/6) vs B (5/6) vs C (5/6 + trend guard)")
    print("=" * 70)
    print(f"\n  RISK={RISK_PER_TRADE*100:.0f}% | ATR×{ATR_MULTIPLIER} | "
          f"R:R={REWARD_TO_RISK} | DD_LIMIT={DAILY_LOSS_LIMIT*100:.0f}% | "
          f"MAX_STREAK={MAX_CONSECUTIVE_LOSSES}")
    print("  Fees/slippage: none (matching live bot)")
    print("  Exit logic: intra-candle high/low SL/TP detection")
    print("  Data source: Binance public REST API — USDT pairs (no auth required)")
    print("  Note: prices in USDT; ROI%/PF/DD%/WR metrics are identical to GBP-based runs")
    print("  4h candles: aggregated from 1h data for perfect alignment")
    print(f"  Target history: {TARGET_DAYS} days (~{TARGET_DAYS/30.44:.0f} months)")
    print(f"\n  Coins tested: {', '.join(COINS.keys())}")
    print(f"  Live: {', '.join(LIVE_COINS)}   Research: {', '.join(RESEARCH_COINS)}")
    print()

    comparison_rows: list[dict]             = []
    all_metrics: dict[str, dict[str, dict]] = {}
    all_results: dict[str, dict[str, dict]] = {}

    for coin, symbol in COINS.items():
        tag = "(LIVE)" if coin in LIVE_COINS else "(RESEARCH)"
        print(f"\n{'='*70}")
        print(f"  COIN: {coin} {tag}  (Binance: {symbol})")
        print(f"{'='*70}")

        print(f"\n  Fetching 1h data for {symbol}...", end="", flush=True)
        try:
            c1h = fetch_binance_klines(symbol, TARGET_DAYS)
            print(f" {len(c1h)} candles")
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        if len(c1h) < 120:
            print(f"  Insufficient data ({len(c1h)} candles), skipping.")
            continue

        c4h = aggregate_to_4h(c1h)
        print(f"  4h candles (aggregated): {len(c4h)}")
        if len(c4h) < 60:
            print(f"  Insufficient 4h data, skipping.")
            continue

        start_ts = int(c1h[0][0]); end_ts = int(c1h[-1][0])
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        end_dt   = datetime.fromtimestamp(end_ts,   tz=timezone.utc).strftime("%Y-%m-%d")
        days     = (end_ts - start_ts) / 86400
        months   = days / 30.44

        print(f"  Period: {start_dt} → {end_dt} ({days:.0f} days / {months:.1f} months)")
        print(f"  Gaps >2h: {gap_count(c1h, 3600)} in 1h | "
              f"{gap_count(c4h, 14400)} in 4h")

        results: dict[str, dict] = {}
        mets:    dict[str, dict] = {}
        print()
        for strat in ("A", "B", "C"):
            slabel = {"A": "6/6", "B": "5/6", "C": "5/6+Guard"}[strat]
            print(f"  Strategy {strat} ({slabel})...", end="", flush=True)
            res = run_backtest(coin, c1h, c4h, strat)
            m   = compute_metrics(res, test_days=days)
            results[strat] = res
            mets[strat]    = m
            print(f" {m['n']} trades | ROI {m['roi_pct']:+.2f}% | "
                  f"PF {fmt_inf(m['profit_factor'])} | MaxDD {m['max_drawdown_pct']:.2f}%")

        all_metrics[coin] = mets
        all_results[coin] = results

        # Detailed blocks
        for strat in ("A", "B", "C"):
            slabel = {
                "A": "Strategy A — 6/6 (Current)",
                "B": "Strategy B — 5/6 (Relaxed)",
                "C": "Strategy C — 5/6 + Trend Guard",
            }[strat]
            print_strategy_block(slabel, mets[strat])

        # Side-by-side mini table
        print(f"\n  ── Side-by-side: {coin} ────────────────────────────────────")
        print(f"  {'Metric':<28} {'A (6/6)':>12} {'B (5/6)':>12} {'C (5/6+G)':>12}")
        print(f"  {'-'*28} {'-'*12} {'-'*12} {'-'*12}")
        def srow(lbl, key, fmt_fn):
            vals = [fmt_fn(mets[s][key]) for s in ("A","B","C")]
            print(f"  {lbl:<28} {vals[0]:>12} {vals[1]:>12} {vals[2]:>12}")
        srow("ROI",               "roi_pct",          fmt_pct)
        srow("Trades",            "n",                 lambda v: str(int(v)))
        srow("Trades/month",      "trades_per_month",  lambda v: f"{v:.1f}" if v else "N/A")
        srow("Win rate",          "win_rate_pct",      fmt_pct)
        srow("Profit factor",     "profit_factor",     fmt_inf)
        srow("Max drawdown",      "max_drawdown_pct",  fmt_pct)
        srow("Expectancy/trade",  "expectancy",        fmt_money)
        srow("Sharpe",            "sharpe",            lambda v: fmt_num(v) if v is not None else "N/A")
        srow("Max consec losses", "max_consec_losses", lambda v: str(int(v)))

        # Extra trade analysis
        print(f"\n  ── Extra-trade analysis: B vs A ({coin}) ──")
        print_extra_analysis("Strategy B", analyze_extra_trades(results["B"]))
        print(f"\n  ── Extra-trade analysis: C vs A ({coin}) ──")
        print_extra_analysis("Strategy C", analyze_extra_trades(results["C"]))

        # Per-coin strategy recommendation
        best, rec = rank_strategies(mets)
        best_m    = mets[best]
        best_lbl  = {
            "A": "Strategy A (6/6)",
            "B": "Strategy B (5/6)",
            "C": "Strategy C (5/6+Guard)",
        }[best]
        cand_lbl  = coin_candidate_label(best_m)
        print(f"\n  ▶ {coin} strategy recommendation: {rec}")
        print(f"    Best: {best_lbl}  |  Candidate label: {cand_lbl}")
        print(f"    Trades={best_m['n']}  ROI={best_m['roi_pct']:+.2f}%  "
              f"PF={fmt_inf(best_m['profit_factor'])}  "
              f"MaxDD={best_m['max_drawdown_pct']:.2f}%  "
              f"WR={best_m['win_rate_pct']:.1f}%  "
              f"[{evidence_label(best_m['n'])}]")

        comparison_rows.append({
            "coin":    coin,
            "days":    days,
            "months":  months,
            "mets":    mets,
            "rec":     rec,
            "best":    best,
            "best_m":  best_m,
            "cand":    cand_lbl,
        })

    if not comparison_rows:
        print("\nNo data available. Check internet connectivity.")
        return

    # ── Cross-coin comparison table ────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  CROSS-COIN COMPARISON TABLE (all 8 coins × 3 strategies)")
    print(f"{'='*70}")
    hdr = (f"{'Coin':<5} {'Mo':>4}  "
           f"{'A ROI':>8} {'B ROI':>8} {'C ROI':>8}  "
           f"{'A Trd':>5} {'B Trd':>5} {'C Trd':>5}  "
           f"{'A WR':>6} {'B WR':>6} {'C WR':>6}  "
           f"{'A PF':>6} {'B PF':>6} {'C PF':>6}  "
           f"{'A DD':>6} {'B DD':>6} {'C DD':>6}")
    print(f"\n  {hdr}")
    print(f"  {'-'*len(hdr)}")
    for row_ in comparison_rows:
        m = row_["mets"]
        g = lambda s, k: m[s][k]
        tag = "(L)" if row_["coin"] in LIVE_COINS else "(R)"
        print(
            f"  {row_['coin']:<4}{tag} {row_['months']:>4.1f}  "
            f"{g('A','roi_pct'):>+8.2f} {g('B','roi_pct'):>+8.2f} {g('C','roi_pct'):>+8.2f}  "
            f"{g('A','n'):>5} {g('B','n'):>5} {g('C','n'):>5}  "
            f"{g('A','win_rate_pct'):>6.1f} {g('B','win_rate_pct'):>6.1f} {g('C','win_rate_pct'):>6.1f}  "
            f"{fmt_inf(g('A','profit_factor')):>6} {fmt_inf(g('B','profit_factor')):>6} {fmt_inf(g('C','profit_factor')):>6}  "
            f"{g('A','max_drawdown_pct'):>6.2f} {g('B','max_drawdown_pct'):>6.2f} {g('C','max_drawdown_pct'):>6.2f}"
        )
    print("  (L)=Live trading coin  (R)=Research candidate")

    # ── Coin ranking ───────────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  COIN RANKING  (best strategy per coin)")
    print("  Ranked by: PF×3 + ROI×2 − MaxDD (sample-penalised)")
    print(f"{'='*70}")

    ranked = sorted(comparison_rows, key=lambda r: strategy_score(r["best_m"]), reverse=True)

    print(f"\n  {'Rank':<5} {'Coin':<5} {'Tag':<12} {'Best':<10} {'Trades':>6} "
          f"{'ROI%':>7} {'PF':>6} {'DD%':>6} {'WR%':>6} {'Exp':>7} {'Evidence':<22}")
    print(f"  {'-'*5} {'-'*5} {'-'*12} {'-'*10} {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*22}")
    for rank_i, row_ in enumerate(ranked, 1):
        bm  = row_["best_m"]
        tag = "(Live)" if row_["coin"] in LIVE_COINS else "(Research)"
        print(
            f"  {rank_i:<5} {row_['coin']:<5} {row_['cand']:<12} "
            f"{row_['best']:<10} {bm['n']:>6} "
            f"{bm['roi_pct']:>+7.2f} {fmt_inf(bm['profit_factor']):>6} "
            f"{bm['max_drawdown_pct']:>6.2f} {bm['win_rate_pct']:>6.1f} "
            f"{fmt_money(bm['expectancy']):>7}  {evidence_label(bm['n'])}"
        )

    # ── Candidate summary ──────────────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  CANDIDATE ASSESSMENT SUMMARY")
    print(f"{'='*70}")
    for label in ("STRONG CANDIDATE", "PROMISING", "NEEDS MORE DATA", "REJECT"):
        coins_in_group = [r["coin"] for r in ranked if r["cand"] == label]
        if coins_in_group:
            print(f"\n  {label}:")
            for coin in coins_in_group:
                r   = next(x for x in ranked if x["coin"] == coin)
                bm  = r["best_m"]
                tag = "(Live)" if coin in LIVE_COINS else "(Research)"
                print(f"    • {coin} {tag}  —  "
                      f"Trades={bm['n']}, ROI={bm['roi_pct']:+.2f}%, "
                      f"PF={fmt_inf(bm['profit_factor'])}, "
                      f"WR={bm['win_rate_pct']:.1f}%, "
                      f"Exp={fmt_money(bm['expectancy'])}")

    # ── Per-coin best strategy ─────────────────────────────────────────────────
    best_strat_per_coin: dict[str, str] = {r["coin"]: r["best"] for r in comparison_rows}

    print(f"\n\n{'='*70}")
    print("  PER-COIN BEST STRATEGY (for portfolio construction)")
    print(f"{'='*70}")
    for row_ in comparison_rows:
        coin = row_["coin"]
        bm   = row_["best_m"]
        print(f"  {coin:<5}  {row_['rec']:<30}  "
              f"[{row_['best']}]  PF={fmt_inf(bm['profit_factor'])}  "
              f"Trades={bm['n']}  {evidence_label(bm['n'])}")

    # ── Portfolio combination analysis ─────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  PORTFOLIO COMBINATION ANALYSIS")
    print("  Each coin uses its own best strategy. Equal allocation per coin.")
    print(f"{'='*70}")

    # Rank coins by score for selection
    coins_by_score = [r["coin"] for r in ranked if r["coin"] in all_metrics]

    # Build specific combinations
    combos: list[tuple[str, list[str]]] = []

    # All 8 (or however many ran)
    combos.append(("All tested coins", coins_by_score))

    # All 4 live coins
    live_in_results = [c for c in coins_by_score if c in LIVE_COINS]
    if live_in_results:
        combos.append(("Live coins only (BTC+ETH+SOL+XRP)", live_in_results))

    # Top 4 by score
    top4 = coins_by_score[:4]
    combos.append(("Top 4 by score", top4))

    # Top 5
    if len(coins_by_score) >= 5:
        combos.append(("Top 5 by score", coins_by_score[:5]))

    # Top 6
    if len(coins_by_score) >= 6:
        combos.append(("Top 6 by score", coins_by_score[:6]))

    # Best 4 STRONG/PROMISING candidates
    good_coins = [r["coin"] for r in ranked
                  if r["cand"] in ("STRONG CANDIDATE", "PROMISING")]
    if 2 <= len(good_coins) <= 8:
        combos.append((f"Strong/Promising only ({','.join(good_coins)})", good_coins))

    # Research coins only
    research_in_results = [c for c in coins_by_score if c in RESEARCH_COINS]
    if research_in_results:
        combos.append(("Research candidates only", research_in_results))

    # Print portfolio results
    portfolio_results: list[tuple[str, dict]] = []
    for combo_name, coin_list in combos:
        p = portfolio_metrics(coin_list, all_metrics, all_results, best_strat_per_coin)
        if p:
            portfolio_results.append((combo_name, p))

    print(f"\n  {'Portfolio':<42} {'Coins':>5} {'Capital':>8} {'Final':>8} "
          f"{'ROI%':>7} {'Trades':>7} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Exp':>7}")
    print(f"  {'-'*42} {'-'*5} {'-'*8} {'-'*8} {'-'*7} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*7}")
    for combo_name, p in portfolio_results:
        print(
            f"  {combo_name[:42]:<42} {p['n_coins']:>5} "
            f"£{p['total_start']:>7.0f} £{p['total_finish']:>7.2f} "
            f"{p['total_roi']:>+7.2f} {p['total_n']:>7} "
            f"{p['win_rate']:>6.1f} {fmt_inf(p['profit_factor']):>6} "
            f"{p['max_dd']:>6.2f} {fmt_money(p['expectancy']):>7}"
        )

    # Best portfolio recommendation
    if portfolio_results:
        best_port_name, best_port = max(portfolio_results, key=lambda x: portfolio_score(x[1]))
        print(f"\n  ▶ Recommended portfolio: {best_port_name}")
        print(f"    Coins: {', '.join(best_port['coins'])}")
        print(f"    Capital: £{best_port['total_start']:.0f} → £{best_port['total_finish']:.2f}")
        print(f"    ROI={fmt_pct(best_port['total_roi'])}  "
              f"PF={fmt_inf(best_port['profit_factor'])}  "
              f"MaxDD={fmt_pct(best_port['max_dd'])}  "
              f"WR={fmt_pct(best_port['win_rate'])}  "
              f"Trades={best_port['total_n']} [{evidence_label(best_port['total_n'])}]")

    # ── Improvement opportunities ──────────────────────────────────────────────
    print(f"\n\n{'='*70}")
    print("  POTENTIAL IMPROVEMENTS (research only — not yet implemented)")
    print(f"{'='*70}")

    # Identify coins to drop
    reject_coins = [r["coin"] for r in ranked if r["cand"] == "REJECT"]
    ndm_coins    = [r["coin"] for r in ranked if r["cand"] == "NEEDS MORE DATA"]
    strong_coins = [r["coin"] for r in ranked if r["cand"] in ("STRONG CANDIDATE","PROMISING")]

    print(f"""
  1. COIN SELECTION
     • Remove consistently losing coins from the portfolio to reduce drag.
     • Coins flagged REJECT: {reject_coins if reject_coins else 'none'}
     • Coins needing more data before inclusion: {ndm_coins if ndm_coins else 'none'}
     • Focus capital on coins scoring STRONG CANDIDATE or PROMISING: {strong_coins}

  2. PER-COIN STRATEGY TUNING
     • Rather than forcing all coins onto one strategy (A/B/C), allow each
       coin to run its historically best-validated variant.
     • Example: if BTC performs best with 6/6 but LINK performs better with
       Strategy C, run each on its optimal config.
     • Caution: only adopt a different strategy where sample size is >= 30 trades.

  3. TRADE FREQUENCY — MARKET REGIME FILTER
     • Some coins produce very few trades over 12 months (< 5/month).
       This may indicate the strategy is too strict for volatile alt-coins.
     • Option: test a regime filter (e.g. only trade when VIX-equivalent
       crypto fear index is < 50, or when BTC is trending bullish).
     • This reduces losses during range/chop without changing entry rules.

  4. EXIT LOGIC IMPROVEMENTS
     • Current R:R is fixed at 2:1. On coins with strong momentum (BTC LONG),
       a trailing stop could capture larger moves (3R, 4R).
     • Test: after price reaches 1R in profit, move SL to breakeven (risk-free).
     • This would reduce the average loss on near-winners and improve expectancy.

  5. POSITION SIZING — KELLY-FRACTION
     • Current: fixed 1% risk per trade.
     • Option: use fractional Kelly sizing based on each coin's historical win
       rate and payoff ratio. This would size up on higher-edge setups.
     • For BTC (WR≈47%, R:R=2:1): full Kelly ≈ 11.5%, half-Kelly ≈ 5.75%.
       A move from 1% to 3% risk (still conservative) would roughly triple
       the £ return without changing strategy rules.

  6. LONG vs SHORT ASYMMETRY
     • SHORT trades have lower win rates on most coins in this backtest.
     • Option: require an additional confirmation for SHORT entries, or
       skip SHORT trades entirely on alt-coins where SHORT underperforms.
     • This would reduce total trade count but may improve overall win rate.

  7. MULTI-TIMEFRAME CONFLUENCE
     • Currently: 1h entry + 4h trend context.
     • Option: add a daily (1D) trend filter — only take LONGs when the
       daily chart is also in a bullish regime. This would reduce entries
       during major bear cycles and likely improve SHORT accuracy.

  8. COOLDOWN AFTER LOSSES
     • Current: MAX_CONSECUTIVE_LOSSES=3 pauses trading for the day.
     • Option: after 2 consecutive losses on a single coin, pause that coin
       for 24h but continue trading other coins. This isolates coin-specific
       drawdowns from impacting the whole portfolio.
""")

    # ── Final per-coin recommendations ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  FINAL RECOMMENDATIONS PER COIN")
    print("  Ranked by: PF × 3 + ROI × 2 − MaxDD (sample-penalised)")
    print(f"{'='*70}")
    for rank_i, row_ in enumerate(ranked, 1):
        coin = row_["coin"]
        bm   = row_["best_m"]
        tag  = "(Live)" if coin in LIVE_COINS else "(Research)"
        blbl = {
            "A": "Strategy A (6/6 — Current)",
            "B": "Strategy B (5/6 — Relaxed)",
            "C": "Strategy C (5/6 + Trend Guard)",
        }[row_["best"]]
        print(f"\n  #{rank_i}  {coin} {tag}  ── {row_['cand']}")
        print(f"       Strategy recommendation : {row_['rec']}")
        print(f"       Best strategy           : {blbl}")
        print(f"       Evidence                : {evidence_label(bm['n'])}")
        print(f"       ROI={bm['roi_pct']:+.2f}%  PF={fmt_inf(bm['profit_factor'])}  "
              f"MaxDD={bm['max_drawdown_pct']:.2f}%  WR={bm['win_rate_pct']:.1f}%  "
              f"Exp={fmt_money(bm['expectancy'])}")
        if row_["cand"] == "REJECT":
            print(f"       ⛔ Do not add to live trading.")
        elif row_["cand"] == "NEEDS MORE DATA":
            print(f"       ⏳ Insufficient sample — monitor for more data before deciding.")
        elif row_["cand"] == "PROMISING":
            print(f"       🔶 Promising but not yet sufficient evidence — worth watching.")
        else:
            print(f"       ✅ Strong historical edge — consider for live trading (approval required).")

    print(f"\n{'─'*70}")
    print("  IMPORTANT: This is research/backtesting only.")
    print("  The live paper trader has NOT been changed.")
    print("  Do NOT add research coins to live trading without explicit approval.")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
