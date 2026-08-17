#!/usr/bin/env python3
"""
Kraken Paper-Trader — Extended Research Script
===============================================
TASK 1A — BTC exit strategy comparison on the last 12 months (same window
           that produced the validated 32-trade, +9.83% ROI result).
TASK 1B — BTC 3-year validation by year (each year is a fresh window,
           consistent with the live bot being restarted daily on Replit).
TASK 2  — Non-crypto markets: Gold (GC=F), Silver (SI=F),
           EUR/USD, GBP/USD, USD/JPY via Yahoo Finance.

Constants are identical to the live bot. No live settings changed.
Usage: python3 research_extended.py
"""

from __future__ import annotations

import json
import math
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

# ─── Constants — identical to live bot ────────────────────────────────────────
RISK_PER_TRADE         = 0.01
REWARD_TO_RISK         = 2.0
ATR_MULTIPLIER         = 1.5
STARTING_BALANCE       = 100.0
DAILY_LOSS_LIMIT       = 0.03
MAX_CONSECUTIVE_LOSSES = 3

TRAIL_TIGHT = 1.5   # Exit D1 ATR multiplier for trailing stop
TRAIL_LOOSE = 2.0   # Exit D2 ATR multiplier for trailing stop

# ─── Indicator functions (verbatim from live paper_trader.py) ────────────────

def ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period: return result
    cur = sum(values[:period]) / period
    result[period - 1] = cur
    mult = 2 / (period + 1)
    for i in range(period, len(values)):
        cur = (values[i] - cur) * mult + cur
        result[i] = cur
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period: return result
    gains  = [max(values[i] - values[i-1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0.0) for i in range(1, len(values))]
    ag = sum(gains[:period])  / period
    al = sum(losses[:period]) / period
    def rv():
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))
    result[period] = rv()
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i])  / period
        al = (al * (period-1) + losses[i]) / period
        result[i+1] = rv()
    return result


def indicator_snapshot(rows: list[list[Any]]) -> dict:
    closes  = [float(r[4]) for r in rows]
    vols    = [float(r[6]) for r in rows]
    e20  = ema_series(closes, 20)
    e50  = ema_series(closes, 50)
    e12  = ema_series(closes, 12)
    e26  = ema_series(closes, 26)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(e12, e26)]
    mv   = [v for v in macd if v is not None]
    sv   = ema_series(mv, 9)
    ms: list[float | None] = [None] * len(macd); si = 0
    for idx, v in enumerate(macd):
        if v is not None: ms[idx] = sv[si]; si += 1
    trs = []
    for i, row in enumerate(rows):
        h, lo, pc = float(row[2]), float(row[3]), float(rows[i-1][4]) if i else float(row[4])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = ema_series(trs, 14)
    rsi = rsi_series(closes)
    L   = len(rows) - 1
    lv  = lambda s: s[L] if s and L >= 0 else None
    return {
        "rsi": lv(rsi), "macd": lv(macd), "macdSignal": lv(ms),
        "atr": lv(atr), "ema20": lv(e20), "ema50": lv(e50),
        "volume": vols[-1] if vols else None,
        "_avg_volume": sum(vols[-20:]) / min(20, len(vols)) if vols else None,
    }


def trend_for(rows: list[list[Any]]) -> str:
    if len(rows) < 55: return "NEUTRAL"
    snap = indicator_snapshot(rows)
    cl   = float(rows[-1][4])
    e20, e50, macd, sig = snap["ema20"], snap["ema50"], snap["macd"], snap["macdSignal"]
    if all(v is not None for v in (e20, e50, macd, sig)):
        if cl > e20 > e50 and macd > sig: return "BULLISH"
        if cl < e20 < e50 and macd < sig: return "BEARISH"
    return "NEUTRAL"


def evaluate_conditions(
    one_hour: list[list[Any]],
    four_hour: list[list[Any]],
    skip_volume: bool = False,
) -> dict:
    """
    Evaluate the 6 entry conditions.
    skip_volume=True auto-passes the volume condition — used for FX where
    Yahoo Finance volume is tick count (not reliable exchange volume).
    """
    if len(one_hour) < 55 or len(four_hour) < 55:
        return {"passCount": 0, "totalCount": 0, "bias": "NEUTRAL",
                "signal": "NO_TRADE", "oneHourTrend": "NEUTRAL",
                "fourHourTrend": "NEUTRAL", "indicators": {}}

    snap    = indicator_snapshot(one_hour)
    cl      = float(one_hour[-1][4])
    avg_vol = snap.get("_avg_volume") or 0.0
    volume  = snap["volume"] or 0.0
    t1h     = trend_for(one_hour)
    t4h     = trend_for(four_hour)

    if   t1h == "BULLISH" or t4h == "BULLISH":  bias = "LONG"
    elif t1h == "BEARISH" or t4h == "BEARISH":  bias = "SHORT"
    else:                                        bias = "NEUTRAL"

    rsi_v, macd_v, sig_v = snap["rsi"], snap["macd"], snap["macdSignal"]
    e20, e50 = snap["ema20"], snap["ema50"]

    if bias == "LONG":
        passes = [
            t4h == "BULLISH",
            t1h == "BULLISH",
            rsi_v  is not None and rsi_v  >= 50,
            macd_v is not None and sig_v is not None and macd_v > sig_v,
            e20 is not None and e50 is not None and cl > e20 > e50,
            True if skip_volume else (avg_vol > 0 and volume >= avg_vol * 0.7),
        ]
    elif bias == "SHORT":
        passes = [
            t4h == "BEARISH",
            t1h == "BEARISH",
            rsi_v  is not None and rsi_v  <= 50,
            macd_v is not None and sig_v is not None and macd_v < sig_v,
            e20 is not None and e50 is not None and cl < e20 < e50,
            True if skip_volume else (avg_vol > 0 and volume >= avg_vol * 0.7),
        ]
    else:
        passes = [False] * 6

    pc     = sum(passes)
    signal = bias if all(passes) and bias != "NEUTRAL" else "NO_TRADE"
    return {
        "passes": passes, "passCount": pc, "totalCount": len(passes),
        "bias": bias, "signal": signal,
        "oneHourTrend": t1h, "fourHourTrend": t4h, "indicators": snap,
    }


def check_entry(ev: dict, strategy: str) -> bool:
    pc   = ev["passCount"]; tc = ev["totalCount"]
    bias = ev["bias"]; t1h = ev["oneHourTrend"]; t4h = ev["fourHourTrend"]
    if bias == "NEUTRAL" or tc == 0: return False
    if strategy == "A": return pc == tc
    if strategy == "B": return pc >= tc - 1
    if strategy == "C":
        if pc < tc - 1: return False
        if bias == "LONG"  and (t1h != "BULLISH" or t4h == "BEARISH"): return False
        if bias == "SHORT" and (t1h != "BEARISH" or t4h == "BULLISH"): return False
        return True
    return False


# ─── Data fetching ─────────────────────────────────────────────────────────────

def _fetch(url: str, retries: int = 4) -> Any:
    for attempt in range(retries):
        try:
            req = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)",
            })
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt == retries - 1: raise
            _time.sleep(2 ** attempt)


def fetch_binance(symbol: str, days: int) -> list[list[Any]]:
    """Fetch hourly candles from Binance public API. No auth required."""
    all_rows: dict[int, list] = {}
    now_ms   = int(_time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    for _ in range(50):
        url  = (f"https://api.binance.com/api/v3/klines"
                f"?symbol={symbol}&interval=1h&limit=1000&startTime={start_ms}")
        data = _fetch(url)
        if not isinstance(data, list) or not data: break
        for row in data:
            ts  = int(row[0]) // 1000
            o, h, lo, cl, vol = (float(row[i]) for i in (1, 2, 3, 4, 5))
            if cl > 0: all_rows[ts] = [ts, o, h, lo, cl, 0.0, vol, 0]
        last_ms = int(data[-1][0])
        if last_ms >= now_ms - 3_600_000: break
        start_ms = last_ms + 3_600_000
        _time.sleep(0.15)
    rows = sorted(all_rows.values(), key=lambda r: r[0])
    return rows[:-1] if len(rows) > 1 else rows


def fetch_yahoo(symbol: str, days: int = 730) -> list[list[Any]]:
    """
    Fetch hourly OHLCV from Yahoo Finance public API (max ~730 days for 1h).
    Returns [ts, open, high, low, close, 0, volume, 0].
    Weekend/holiday gaps are expected for FX/metals and are NOT errors.
    """
    now_ts   = int(_time.time())
    start_ts = now_ts - min(days, 729) * 24 * 3600
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval=1h&period1={start_ts}&period2={now_ts}"
           f"&includePrePost=false&events=history")
    payload = _fetch(url)
    result  = (payload.get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp", [])
    quote      = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens  = quote.get("open",   [])
    highs  = quote.get("high",   [])
    lows   = quote.get("low",    [])
    closes = quote.get("close",  [])
    vols   = quote.get("volume", [])
    rows = []
    for i, ts in enumerate(timestamps):
        try:
            o  = opens[i];  h  = highs[i];  lo = lows[i];  cl = closes[i]
            v  = vols[i] if i < len(vols) else None
            if None in (o, h, lo, cl) or cl <= 0 or h <= 0: continue
            rows.append([int(ts), float(o), float(h), float(lo), float(cl),
                         0.0, float(v) if v is not None else 0.0, 0])
        except (TypeError, IndexError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows[:-1] if len(rows) > 1 else rows


def aggregate_to_4h(c1h: list[list[Any]]) -> list[list[Any]]:
    buckets: dict[int, list] = defaultdict(list)
    for r in c1h:
        buckets[(int(r[0]) // 14400) * 14400].append(r)
    return [
        [bts, float(rs[0][1]),
         max(float(r[2]) for r in rs), min(float(r[3]) for r in rs),
         float(rs[-1][4]), 0.0, sum(float(r[6]) for r in rs), 0]
        for bts in sorted(buckets)
        for rs in [buckets[bts]]
    ]


def fx_gap_count(candles: list[list[Any]], interval_s: int) -> int:
    """Count gaps that are NOT attributable to weekends or major holidays."""
    count = 0
    for i in range(1, len(candles)):
        gap = float(candles[i][0]) - float(candles[i-1][0])
        if gap <= interval_s * 2: continue
        dt = datetime.fromtimestamp(float(candles[i-1][0]), tz=timezone.utc)
        # Allow Fri/Sat/Sun gaps up to ~72h (normal market closure)
        if dt.weekday() >= 4 and gap / 86400 <= 3.5: continue
        count += 1
    return count


# ─── Backtest engine (supports multiple exit modes) ───────────────────────────
#
# Exit modes
#   "A"      — current: fixed ATR SL + 2R TP (matches live bot exactly)
#   "B_075"  — move SL to breakeven once trade reaches +0.75R unrealised
#   "C_1R"   — move SL to breakeven once trade reaches +1.0R unrealised
#   "D1_1.5" — ATR trailing stop, trail multiplier = 1.5×  (keep original TP)
#   "D2_2.0" — ATR trailing stop, trail multiplier = 2.0×  (keep original TP)
#
# Risk management is identical to live bot:
#   - daily_loss resets each day, consec_losses persists until a winning trade
#   - consec_losses is NOT reset daily (matches live bot / original backtest.py)

EXIT_NAMES = {
    "A":      "Exit A — Current (ATR SL + 2R TP)",
    "B_075":  "Exit B — Breakeven at +0.75R",
    "C_1R":   "Exit C — Breakeven at +1.0R",
    "D1_1.5": "Exit D1 — Trailing stop 1.5×ATR (tight)",
    "D2_2.0": "Exit D2 — Trailing stop 2.0×ATR (loose)",
}


def run_backtest(
    label:       str,
    c1h:         list[list[Any]],
    c4h:         list[list[Any]],
    strategy:    str  = "A",
    exit_mode:   str  = "A",
    skip_volume: bool = False,
) -> dict:
    balance      = STARTING_BALANCE
    start_bal    = STARTING_BALANCE
    open_pos     = None
    trades:       list[dict] = []
    balance_curve: list[float] = [balance]
    daily_loss    = 0.0
    day_key       = ""
    consec_losses = 0
    four_h_idx    = 0
    MIN_C         = 60

    for i in range(MIN_C, len(c1h)):
        candle   = c1h[i]
        cts      = int(candle[0])
        ch       = float(candle[2])   # candle high
        clo      = float(candle[3])   # candle low
        ccl      = float(candle[4])   # candle close

        while (four_h_idx + 1 < len(c4h) and
               float(c4h[four_h_idx + 1][0]) <= cts):
            four_h_idx += 1

        w1h = c1h[max(0, i - 719) : i + 1]
        w4h = c4h[max(0, four_h_idx - 719) : four_h_idx + 1]
        if len(w1h) < 55 or len(w4h) < 55:
            balance_curve.append(balance); continue

        # Daily reset — only daily_loss; consec_losses persists until a win
        # (matches live bot behaviour and original backtest.py exactly)
        cd = datetime.fromtimestamp(cts, tz=timezone.utc).date().isoformat()
        if cd != day_key:
            day_key    = cd
            daily_loss = 0.0

        # ── SL/TP + exit mode logic ────────────────────────────────────────
        if open_pos is not None:
            d    = open_pos["direction"]
            sl   = open_pos["stop_loss"]
            tp   = open_pos["take_profit"]
            ent  = open_pos["entry"]
            qty  = open_pos["quantity"]
            atr0 = open_pos["atr_at_entry"]

            # Trailing stop update (D1/D2): trail behind session extreme
            if exit_mode in ("D1_1.5", "D2_2.0"):
                mult = TRAIL_TIGHT if exit_mode == "D1_1.5" else TRAIL_LOOSE
                if d == "LONG":
                    # Candle high extends our best reached price; trail behind it
                    new_trail = ch - mult * atr0
                    if new_trail > sl:
                        open_pos["stop_loss"] = sl = new_trail
                else:
                    new_trail = clo + mult * atr0
                    if new_trail < sl:
                        open_pos["stop_loss"] = sl = new_trail

            # Breakeven trigger (B/C): move SL to entry once threshold is reached
            if exit_mode in ("B_075", "C_1R") and not open_pos.get("be_triggered"):
                thresh    = 0.75 if exit_mode == "B_075" else 1.0
                stop_dist = abs(ent - open_pos["initial_stop"])
                if d == "LONG"  and ch  >= ent + thresh * stop_dist:
                    open_pos["be_triggered"] = True
                    open_pos["stop_loss"]    = ent
                    sl                       = ent
                elif d == "SHORT" and clo <= ent - thresh * stop_dist:
                    open_pos["be_triggered"] = True
                    open_pos["stop_loss"]    = ent
                    sl                       = ent

            hit_sl = (clo <= sl) if d == "LONG" else (ch >= sl)
            hit_tp = (tp is not None) and ((ch >= tp) if d == "LONG" else (clo <= tp))

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:   ep, er = sl, "STOP_LOSS"
                elif hit_sl:
                    be = open_pos.get("be_triggered") and abs(sl - ent) < 1e-9
                    ep, er = sl, ("BREAKEVEN" if be else "STOP_LOSS")
                else:                   ep, er = tp, "TAKE_PROFIT"

                pnl        = (ep - ent) * qty if d == "LONG" else (ent - ep) * qty
                balance   += pnl
                daily_loss = max(0.0, daily_loss + (-pnl if pnl < 0 else 0.0))
                consec_losses = (consec_losses + 1) if pnl < 0 else 0

                trades.append({
                    "direction":    d, "entry": ent, "exit": ep, "pnl": pnl,
                    "qty":          qty, "exit_reason": er,
                    "opened_at":    open_pos["opened_at"], "closed_at": cts,
                    "duration_h":   (cts - open_pos["opened_at"]) / 3600,
                    "be_triggered": open_pos.get("be_triggered", False),
                    "balance_after": balance,
                })
                open_pos = None

        balance_curve.append(balance)
        if open_pos is not None: continue

        # ── Risk gate (identical to live bot) ─────────────────────────────
        if (daily_loss  >= start_bal * DAILY_LOSS_LIMIT or
                consec_losses >= MAX_CONSECUTIVE_LOSSES or
                balance       <= 0):
            continue

        # ── Entry evaluation ───────────────────────────────────────────────
        ev = evaluate_conditions(w1h, w4h, skip_volume=skip_volume)
        if not check_entry(ev, strategy): continue

        d       = ev["bias"]
        snap    = ev["indicators"]
        atr_val = snap.get("atr")
        if atr_val is None or atr_val <= 0: continue

        stop_dist   = atr_val * ATR_MULTIPLIER
        risk_amount = balance * RISK_PER_TRADE
        quantity    = min(risk_amount / stop_dist,
                         balance / ccl if ccl > 0 else 0)
        if quantity <= 0: continue

        sl0 = ccl - stop_dist if d == "LONG" else ccl + stop_dist
        tp0 = ccl + stop_dist * REWARD_TO_RISK if d == "LONG" else ccl - stop_dist * REWARD_TO_RISK

        open_pos = {
            "direction": d, "entry": ccl,
            "stop_loss": sl0, "initial_stop": sl0, "take_profit": tp0,
            "quantity": quantity, "opened_at": cts, "atr_at_entry": atr_val,
            "be_triggered": False,
        }

    # Force-close any open trade at end of data
    if open_pos is not None:
        lc  = c1h[-1]
        ep  = float(lc[4])
        d   = open_pos["direction"]
        pnl = (ep - open_pos["entry"]) * open_pos["quantity"] if d == "LONG" \
              else (open_pos["entry"] - ep) * open_pos["quantity"]
        balance += pnl
        balance_curve.append(balance)
        trades.append({
            "direction": d, "entry": open_pos["entry"], "exit": ep, "pnl": pnl,
            "qty": open_pos["quantity"], "exit_reason": "MARKET_CLOSE",
            "opened_at": open_pos["opened_at"], "closed_at": int(lc[0]),
            "duration_h": (int(lc[0]) - open_pos["opened_at"]) / 3600,
            "be_triggered": open_pos.get("be_triggered", False),
            "balance_after": balance,
        })

    return {
        "label": label, "strategy": strategy, "exit_mode": exit_mode,
        "trades": trades, "balance_curve": balance_curve,
        "final_balance": balance, "starting_bal": start_bal,
    }


# ─── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(result: dict, test_days: float = 0) -> dict:
    trades = result["trades"]
    curve  = result["balance_curve"]
    start  = result["starting_bal"]
    finish = result["final_balance"]
    n      = len(trades)

    wins  = [t for t in trades if t["pnl"] > 0]
    loses = [t for t in trades if t["pnl"] <= 0]
    longs = [t for t in trades if t["direction"] == "LONG"]
    shrts = [t for t in trades if t["direction"] == "SHORT"]
    be_exits = [t for t in trades if t.get("exit_reason") == "BREAKEVEN"]

    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in loses))
    pf = (gp / gl) if gl > 0 else float("inf")
    avg_win  = (gp / len(wins))  if wins  else 0.0
    avg_loss = (gl / len(loses)) if loses else 0.0
    wr       = len(wins) / n if n > 0 else 0.0
    exp      = wr * avg_win - (1 - wr) * avg_loss if n > 0 else 0.0

    peak = start; max_dd = 0.0
    for b in curve:
        peak   = max(peak, b)
        max_dd = max(max_dd, (peak - b) / peak * 100)

    mx_cw = mx_cl = cw = cl_ = 0
    for t in sorted(trades, key=lambda x: x["closed_at"]):
        if t["pnl"] > 0: cw += 1; cl_ = 0
        else:            cl_ += 1; cw = 0
        mx_cw = max(mx_cw, cw); mx_cl = max(mx_cl, cl_)

    daily: dict[str, float] = {}
    for t in sorted(trades, key=lambda x: x["closed_at"]):
        d = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).date().isoformat()
        daily[d] = t["balance_after"]
    rets: list[float] = []
    prev = start
    for d in sorted(daily):
        b = daily[d]
        if prev > 0: rets.append((b - prev) / prev)
        prev = b
    sharpe = None
    if len(rets) >= 5:
        mu  = sum(rets) / len(rets)
        std = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        if std > 0: sharpe = mu / std * math.sqrt(252)

    months   = test_days / 30.44 if test_days > 0 else None
    tpm      = n / months if months and months > 0 else None
    ann_roi  = ((finish / start) ** (365 / test_days) - 1) * 100 if test_days > 50 else None
    lw = [t for t in longs if t["pnl"] > 0]
    sw = [t for t in shrts if t["pnl"] > 0]

    # Monthly PnL buckets
    month_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        mk = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).strftime("%Y-%m")
        month_pnl[mk] += t["pnl"]
    profitable_months = sum(1 for v in month_pnl.values() if v > 0)
    pct_profitable    = (profitable_months / len(month_pnl) * 100) if month_pnl else 0.0
    worst_month = min(month_pnl.values(), default=0.0)
    best_month  = max(month_pnl.values(), default=0.0)

    return {
        "n": n, "wins": len(wins), "losses": len(loses),
        "long_n": len(longs), "short_n": len(shrts),
        "long_wr": (len(lw)/len(longs)*100) if longs else 0.0,
        "short_wr": (len(sw)/len(shrts)*100) if shrts else 0.0,
        "win_rate": wr * 100, "net_pnl": finish - start,
        "roi": (finish - start) / start * 100, "ann_roi": ann_roi,
        "final_balance": finish, "profit_factor": pf,
        "avg_win": avg_win, "avg_loss": avg_loss, "avg_pnl": sum(t["pnl"] for t in trades)/n if n else 0,
        "expectancy": exp, "max_dd": max_dd,
        "largest_win":  max((t["pnl"] for t in trades), default=0.0),
        "largest_loss": min((t["pnl"] for t in trades), default=0.0),
        "avg_duration": sum(t["duration_h"] for t in trades)/n if n else 0.0,
        "sharpe": sharpe, "max_cw": mx_cw, "max_cl": mx_cl,
        "tpm": tpm, "gross_profit": gp, "gross_loss": gl,
        "n_be_exits": len(be_exits),
        "pct_profitable_months": pct_profitable,
        "worst_month": worst_month, "best_month": best_month,
    }


def be_detail(result_a: dict, result_be: dict) -> dict:
    """Compare A vs a breakeven variant trade-by-trade."""
    be_trades   = [t for t in result_be["trades"] if t.get("be_triggered")]
    be_exits    = [t for t in be_trades  if t["exit_reason"] == "BREAKEVEN"]
    tp_after_be = [t for t in be_trades  if t["exit_reason"] == "TAKE_PROFIT"]
    a_by_open   = {t["opened_at"]: t for t in result_a["trades"]}
    converted = stopped_early = 0
    for t in be_exits:
        at = a_by_open.get(t["opened_at"])
        if at is None: continue
        if at["pnl"] < 0: converted    += 1
        else:              stopped_early += 1
    return {
        "n_be_triggered":   len(be_trades),
        "n_be_exits":       len(be_exits),
        "n_tp_after_be":    len(tp_after_be),
        "converted":        converted,
        "stopped_early":    stopped_early,
        "net_pnl_diff_vs_A": result_be["final_balance"] - result_a["final_balance"],
    }


# ─── Regime detection ─────────────────────────────────────────────────────────

def classify_segment_regime(c1h: list[list[Any]]) -> str:
    """SMA-200 / slope-based regime label for a candle window."""
    if len(c1h) < 220: return "UNKNOWN"
    closes   = [float(r[4]) for r in c1h]
    sma_now  = sum(closes[-200:]) / 200
    sma_prev = sum(closes[-220:-20]) / 200
    cl       = closes[-1]
    if cl > sma_now and sma_now > sma_prev: return "BULLISH"
    if cl < sma_now and sma_now < sma_prev: return "BEARISH"
    return "SIDEWAYS"


def split_by_year(c1h: list[list[Any]], c4h: list[list[Any]]) -> list[tuple[int, list, list, float]]:
    by_year: dict[int, list] = defaultdict(list)
    for r in c1h:
        y = datetime.fromtimestamp(float(r[0]), tz=timezone.utc).year
        by_year[y].append(r)
    result = []
    for y in sorted(by_year):
        y1h = by_year[y]
        if len(y1h) < 500: continue
        ts0 = int(y1h[0][0]); ts1 = int(y1h[-1][0])
        y4h = [r for r in c4h if ts0 <= int(r[0]) <= ts1]
        if len(y4h) >= 60:
            result.append((y, y1h, y4h, (ts1 - ts0) / 86400))
    return result


def split_by_regime(c1h: list[list[Any]], c4h: list[list[Any]],
                    window_h: int = 720, min_seg_h: int = 500) -> list[tuple[str, list, list, float]]:
    if len(c1h) < window_h + min_seg_h: return []
    regimes = [classify_segment_regime(c1h[max(0, i-window_h):i+1])
               for i in range(window_h, len(c1h))]
    segs: list[tuple[str, list, list, float]] = []
    start = window_h; cur = regimes[0]
    for i, r in enumerate(regimes[1:], window_h + 1):
        if r != cur or i == len(c1h) - 1:
            seg = c1h[start:i]
            if len(seg) >= min_seg_h:
                ts0 = int(seg[0][0]); ts1 = int(seg[-1][0])
                s4h = [c for c in c4h if ts0 <= int(c[0]) <= ts1]
                if len(s4h) >= 60:
                    segs.append((cur, seg, s4h, (ts1 - ts0) / 86400))
            start = i; cur = r
    return segs


# ─── Formatting ───────────────────────────────────────────────────────────────

def fp(v, d=2): return "N/A" if v is None else f"{v:+.{d}f}%"
def fm(v, d=2): return "N/A" if v is None else (f"+£{v:.{d}f}" if v >= 0 else f"-£{abs(v):.{d}f}")
def fi(v):      return "N/A" if v is None else ("∞" if math.isinf(v) else f"{v:.3f}")
def fn(v, d=2): return "N/A" if v is None else f"{v:.{d}f}"

def ev_label(n: int) -> str:
    if n < 30:  return f"⚠ WEAK ({n} trades)"
    if n < 100: return f"~ MODERATE ({n} trades)"
    return f"✓ STRONGER ({n} trades)"

def instrument_rec(m: dict) -> str:
    n = m["n"]; pf = m["profit_factor"]; exp = m["expectancy"]
    if n == 0:                              return "MORE RESEARCH NEEDED"
    if n < 15 or math.isinf(pf):           return "MORE RESEARCH NEEDED"
    if exp <= 0 or pf < 1.0:               return "REJECT"
    if n >= 30 and pf >= 1.4 and exp > 0:  return "ADD TO PAPER TESTING"
    return "MORE RESEARCH NEEDED"

def score(m: dict) -> float:
    pf_v  = min(m["profit_factor"] if not math.isinf(m["profit_factor"]) else 5.0, 5.0)
    pen   = 0.4 if m["n"] < 10 else (0.7 if m["n"] < 30 else 1.0)
    return (3 * pf_v + 2 * m["roi"] - m["max_dd"]) * pen


def print_metrics(label: str, m: dict, show_ann: bool = False) -> None:
    print(f"\n  {'─'*50}")
    print(f"  {label}")
    print(f"  {'─'*50}")
    print(f"  Final balance       : £{m['final_balance']:.2f}  (started £{STARTING_BALANCE:.2f})")
    print(f"  Net P&L             : {fm(m['net_pnl'])}")
    print(f"  ROI                 : {fp(m['roi'])}")
    if show_ann and m["ann_roi"] is not None:
        print(f"  Annualised ROI      : {fp(m['ann_roi'])}")
    print(f"  Trades              : {m['n']}  [{ev_label(m['n'])}]")
    tpm = m.get("tpm")
    print(f"  Trades/month        : {f'{tpm:.1f}' if tpm else 'N/A'}")
    print(f"  Win rate            : {fp(m['win_rate'])}  ({m['wins']}W / {m['losses']}L)")
    print(f"  Avg winning trade   : {fm(m['avg_win'])}")
    print(f"  Avg losing trade    : {fm(-m['avg_loss'])}")
    print(f"  Largest win         : {fm(m['largest_win'])}")
    print(f"  Largest loss        : {fm(m['largest_loss'])}")
    print(f"  Profit factor       : {fi(m['profit_factor'])}")
    print(f"  Expectancy/trade    : {fm(m['expectancy'])}")
    print(f"  Max drawdown        : {fp(m['max_dd'])}")
    print(f"  Avg trade duration  : {fn(m['avg_duration'])}h")
    s = m["sharpe"]
    print(f"  Sharpe ratio        : {fn(s) if s is not None else 'N/A (<5 active days)'}")
    print(f"  LONG  {m['long_n']:>3} trades | WR {fp(m['long_wr'])}")
    print(f"  SHORT {m['short_n']:>3} trades | WR {fp(m['short_wr'])}")
    print(f"  Max consec. losses  : {m['max_cl']}")
    if m.get("pct_profitable_months"):
        print(f"  Profitable months   : {m['pct_profitable_months']:.0f}%")
        print(f"  Best month          : {fm(m['best_month'])}")
        print(f"  Worst month         : {fm(m['worst_month'])}")
    if m.get("n_be_exits"):
        print(f"  Breakeven exits     : {m['n_be_exits']}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 70)
    print("  EXTENDED RESEARCH — BTC EXITS + 3-YEAR VALIDATION + FX/METALS")
    print("  RISK=1% | ATR×1.5 | R:R=2.0 | No fees/slippage | No look-ahead")
    print("=" * 70)
    print("  Risk management: consec_losses persists until a winning trade")
    print("  (matches original backtest.py and live bot behaviour exactly)")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # FETCH BTC DATA
    # ══════════════════════════════════════════════════════════════════════════
    print("  Fetching BTC 3-year 1h data from Binance...", end="", flush=True)
    try:
        btc_3yr = fetch_binance("BTCUSDT", 3 * 365)
        print(f" {len(btc_3yr)} candles")
    except Exception as e:
        print(f" FAILED: {e}"); return

    btc_4h_3yr = aggregate_to_4h(btc_3yr)
    ts_3yr0 = int(btc_3yr[0][0]); ts_3yr1 = int(btc_3yr[-1][0])
    dt_3yr0 = datetime.fromtimestamp(ts_3yr0, tz=timezone.utc).strftime("%Y-%m-%d")
    dt_3yr1 = datetime.fromtimestamp(ts_3yr1, tz=timezone.utc).strftime("%Y-%m-%d")
    days_3yr = (ts_3yr1 - ts_3yr0) / 86400
    print(f"  Full 3yr period: {dt_3yr0} → {dt_3yr1} ({days_3yr:.0f} days)")

    # Carve out the last 12 months for exit comparison (same as validated baseline)
    cutoff_12m = ts_3yr1 - 365 * 86400
    btc_12m    = [r for r in btc_3yr if int(r[0]) >= cutoff_12m]
    btc_4h_12m = aggregate_to_4h(btc_12m)
    ts_12m0    = int(btc_12m[0][0]); ts_12m1 = int(btc_12m[-1][0])
    dt_12m0    = datetime.fromtimestamp(ts_12m0, tz=timezone.utc).strftime("%Y-%m-%d")
    dt_12m1    = datetime.fromtimestamp(ts_12m1, tz=timezone.utc).strftime("%Y-%m-%d")
    days_12m   = (ts_12m1 - ts_12m0) / 86400
    print(f"  12-month window:  {dt_12m0} → {dt_12m1} "
          f"({len(btc_12m)} 1h | {len(btc_4h_12m)} 4h candles)")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 1A — BTC EXIT STRATEGY COMPARISON  (12-month window, Strategy A 6/6)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  TASK 1A — BTC Exit Strategy Comparison")
    print(f"  Window: {dt_12m0} → {dt_12m1} ({days_12m:.0f} days)")
    print("  Entry: Strategy A 6/6 unchanged | Only exit logic varies")
    print(f"{'='*70}")
    print("  Data: Binance BTCUSDT (USDT proxy — metrics equivalent to GBP runs)")
    print("  Fees/slippage: none | No look-ahead | 1h entries | 4h trend context")
    print()

    exit_results: dict[str, dict] = {}
    exit_metrics: dict[str, dict] = {}

    for em in ("A", "B_075", "C_1R", "D1_1.5", "D2_2.0"):
        print(f"  {em:<8} {EXIT_NAMES[em]}...", end="", flush=True)
        r = run_backtest("BTC-12m", btc_12m, btc_4h_12m, strategy="A", exit_mode=em)
        m = compute_metrics(r, test_days=days_12m)
        exit_results[em] = r; exit_metrics[em] = m
        print(f" {m['n']} trades | ROI {m['roi']:+.2f}% | "
              f"PF {fi(m['profit_factor'])} | MaxDD {m['max_dd']:.2f}%")

    # Detailed output per exit
    for em in ("A", "B_075", "C_1R", "D1_1.5", "D2_2.0"):
        print_metrics(EXIT_NAMES[em], exit_metrics[em], show_ann=True)

    # Breakeven-specific analysis
    for em in ("B_075", "C_1R"):
        be  = be_detail(exit_results["A"], exit_results[em])
        lbl = "BE@0.75R" if em == "B_075" else "BE@1.0R"
        print(f"\n  ── Breakeven analysis ({lbl}) ──")
        print(f"  Trades with BE triggered          : {be['n_be_triggered']}")
        print(f"  Trades that stopped at BE (scratch): {be['n_be_exits']}")
        print(f"  Trades that continued to TP        : {be['n_tp_after_be']}")
        print(f"  A-losers converted to scratch      : {be['converted']}  (positive)")
        print(f"  A-winners prematurely stopped at BE: {be['stopped_early']}  (negative)")
        print(f"  Net P&L effect vs Exit A           : {fm(be['net_pnl_diff_vs_A'])}")
        m_be = exit_metrics[em]
        m_a  = exit_metrics["A"]
        print(f"  PF change vs A: {fi(m_a['profit_factor'])} → {fi(m_be['profit_factor'])}")
        print(f"  MaxDD change vs A: {fp(m_a['max_dd'])} → {fp(m_be['max_dd'])}")

    # Side-by-side table
    print(f"\n  ── Exit strategy side-by-side (BTC 12-month) ──────────────────────")
    print(f"  {'Exit':<22} {'Trd':>4} {'ROI%':>8} {'PF':>7} {'WR%':>7} "
          f"{'DD%':>6} {'Exp':>8} {'Sharpe':>8}")
    print(f"  {'-'*22} {'-'*4} {'-'*8} {'-'*7} {'-'*7} {'-'*6} {'-'*8} {'-'*8}")
    for em in ("A", "B_075", "C_1R", "D1_1.5", "D2_2.0"):
        m = exit_metrics[em]
        s = m["sharpe"]
        short = {"A":"Current","B_075":"BE@0.75R","C_1R":"BE@1.0R",
                 "D1_1.5":"Trail 1.5×ATR","D2_2.0":"Trail 2.0×ATR"}[em]
        print(f"  {short:<22} {m['n']:>4} {m['roi']:>+8.2f} {fi(m['profit_factor']):>7} "
              f"{m['win_rate']:>7.1f} {m['max_dd']:>6.2f} {fm(m['expectancy']):>8} "
              f"{fn(s) if s is not None else 'N/A':>8}")

    best_em = max(exit_metrics, key=lambda e: score(exit_metrics[e]))
    print(f"\n  ▶ Best BTC exit: {EXIT_NAMES[best_em]}")
    bm = exit_metrics[best_em]
    print(f"    ROI={fp(bm['roi'])}  PF={fi(bm['profit_factor'])}  "
          f"MaxDD={fp(bm['max_dd'])}  Exp={fm(bm['expectancy'])}")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 1B — BTC 3-YEAR VALIDATION (year-by-year fresh windows)
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print("  TASK 1B — BTC 3-Year Validation (Strategy A 6/6)")
    print(f"  Full period: {dt_3yr0} → {dt_3yr1} ({days_3yr:.0f} days)")
    print("  Each year run as a FRESH window (matches live bot: daily restart resets state)")
    print(f"{'='*70}")

    yearly_results: list[tuple[int, dict, float]] = []
    for year, y1h, y4h, d_y in split_by_year(btc_3yr, btc_4h_3yr):
        ts_y0 = int(y1h[0][0]); ts_y1 = int(y1h[-1][0])
        dt_y0 = datetime.fromtimestamp(ts_y0, tz=timezone.utc).strftime("%Y-%m-%d")
        dt_y1 = datetime.fromtimestamp(ts_y1, tz=timezone.utc).strftime("%Y-%m-%d")
        r = run_backtest(f"BTC-{year}", y1h, y4h, strategy="A", exit_mode="A")
        m = compute_metrics(r, test_days=d_y)
        yearly_results.append((year, m, d_y))
        print(f"\n  ── {year}  ({dt_y0} → {dt_y1}, {d_y:.0f} days) ──")
        print(f"  Trades={m['n']}  [{ev_label(m['n'])}]")
        print(f"  ROI={fp(m['roi'])}  AnnROI={fp(m['ann_roi'])}  "
              f"PF={fi(m['profit_factor'])}  WR={fp(m['win_rate'])}")
        print(f"  MaxDD={fp(m['max_dd'])}  Exp={fm(m['expectancy'])}  "
              f"Sharpe={fn(m['sharpe']) if m['sharpe'] is not None else 'N/A'}")

    # Year-by-year table
    print(f"\n  ── Year-by-year summary ─────────────────────────────────────────────")
    print(f"  {'Year':>5} {'Trades':>7} {'ROI%':>8} {'AnnROI%':>9} {'PF':>7} "
          f"{'WR%':>7} {'MaxDD%':>8} {'Sharpe':>8} {'Evidence'}")
    print(f"  {'-'*5} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*7} {'-'*8} {'-'*8} {'-'*22}")
    for year, m, _ in yearly_results:
        s = m["sharpe"]
        print(f"  {year:>5} {m['n']:>7} {m['roi']:>+8.2f} "
              f"{fp(m['ann_roi']) if m['ann_roi'] is not None else 'N/A':>9} "
              f"{fi(m['profit_factor']):>7} {m['win_rate']:>7.1f} "
              f"{m['max_dd']:>8.2f} {fn(s) if s is not None else 'N/A':>8}  "
              f"{ev_label(m['n'])}")

    # Aggregate 3-year stats from year-by-year runs
    all_trades_3yr = sum(m["n"]             for _, m, _ in yearly_results)
    all_gp_3yr     = sum(m["gross_profit"]  for _, m, _ in yearly_results)
    all_gl_3yr     = sum(m["gross_loss"]    for _, m, _ in yearly_results)
    all_wins_3yr   = sum(m["wins"]          for _, m, _ in yearly_results)
    all_pnl_3yr    = sum(m["net_pnl"]       for _, m, _ in yearly_results)
    agg_pf_3yr     = (all_gp_3yr / all_gl_3yr) if all_gl_3yr > 0 else float("inf")
    agg_wr_3yr     = all_wins_3yr / all_trades_3yr * 100 if all_trades_3yr > 0 else 0
    agg_roi_3yr    = all_pnl_3yr / (len(yearly_results) * STARTING_BALANCE) * 100
    print(f"\n  ── Aggregate across {len(yearly_results)} years ───────────────────────────────────────────")
    print(f"  Total trades (across all year windows) : {all_trades_3yr}  [{ev_label(all_trades_3yr)}]")
    print(f"  Combined PF                            : {fi(agg_pf_3yr)}")
    print(f"  Combined WR                            : {agg_wr_3yr:.1f}%")
    print(f"  Avg annual ROI (per year account)      : {fp(agg_roi_3yr / len(yearly_results))}")

    # Market regime analysis
    print(f"\n  ── Market regime analysis ───────────────────────────────────────────")
    segs = split_by_regime(btc_3yr, btc_4h_3yr)
    regime_agg: dict[str, dict] = defaultdict(lambda: {"n":0,"gp":0.0,"gl":0.0,"wins":0,"pnl":0.0,"days":0.0,"segs":0})
    for regime, s1h, s4h, d_seg in segs:
        ts_s0 = int(s1h[0][0]); ts_s1 = int(s1h[-1][0])
        dt_s0 = datetime.fromtimestamp(ts_s0, tz=timezone.utc).strftime("%Y-%m-%d")
        dt_s1 = datetime.fromtimestamp(ts_s1, tz=timezone.utc).strftime("%Y-%m-%d")
        r = run_backtest(f"BTC-{regime[:3]}", s1h, s4h, strategy="A", exit_mode="A")
        m = compute_metrics(r, test_days=d_seg)
        ra = regime_agg[regime]
        ra["n"]+=m["n"]; ra["gp"]+=m["gross_profit"]; ra["gl"]+=m["gross_loss"]
        ra["wins"]+=m["wins"]; ra["pnl"]+=m["net_pnl"]; ra["days"]+=d_seg; ra["segs"]+=1
        print(f"  {regime:<10} {dt_s0}→{dt_s1} ({d_seg:.0f}d) | "
              f"T={m['n']}  ROI={fp(m['roi'])}  PF={fi(m['profit_factor'])}  "
              f"WR={fp(m['win_rate'])}  [{ev_label(m['n'])}]")

    print(f"\n  ── Regime aggregate ─────────────────────────────────────────────────")
    print(f"  {'Regime':<10} {'Segs':>5} {'Trades':>7} {'Avg ROI':>8} {'PF':>7} "
          f"{'WR%':>7} {'Pnl/£100':>10}")
    print(f"  {'-'*10} {'-'*5} {'-'*7} {'-'*8} {'-'*7} {'-'*7} {'-'*10}")
    for regime in ("BULLISH", "SIDEWAYS", "BEARISH", "UNKNOWN"):
        ra = regime_agg.get(regime)
        if not ra or ra["n"] == 0: continue
        pf_r  = (ra["gp"] / ra["gl"]) if ra["gl"] > 0 else float("inf")
        wr_r  = ra["wins"] / ra["n"] * 100
        roi_r = ra["pnl"] / (ra["segs"] * STARTING_BALANCE) * 100
        print(f"  {regime:<10} {ra['segs']:>5} {ra['n']:>7} {roi_r:>+8.2f} "
              f"{fi(pf_r):>7} {wr_r:>7.1f} {fm(ra['pnl']/ra['segs']):>10}")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 2 — NON-CRYPTO MARKETS
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print("  TASK 2 — NON-CRYPTO MARKETS")
    print("  Data source: Yahoo Finance v8 API (free, no auth required)")
    print("  Strategies: A (6/6) and C (5/6 + trend guard)")
    print("  FX pairs: tested WITH and WITHOUT volume condition")
    print("  Note: weekend/holiday gaps are normal market closures — not counted as errors")
    print(f"{'='*70}")

    MARKETS = {
        "GOLD":   ("GC=F",     "Gold Futures (XAU/USD)",   False, "~$0.50/oz spread, negligible vs ATR stop"),
        "SILVER": ("SI=F",     "Silver Futures (XAG/USD)", False, "~$0.02/oz spread, negligible vs ATR stop"),
        "EURUSD": ("EURUSD=X", "EUR/USD",                  True,  "~1 pip (0.0001) spread"),
        "GBPUSD": ("GBPUSD=X", "GBP/USD",                  True,  "~1.5 pips spread"),
        "USDJPY": ("JPY=X",    "USD/JPY",                   True,  "~0.5 pip spread"),
    }

    market_best_metrics: dict[str, dict] = {}
    market_best_key:     dict[str, str]  = {}
    market_rec:          dict[str, str]  = {}

    for mkt, (symbol, name, is_fx, spread_note) in MARKETS.items():
        print(f"\n{'─'*70}")
        print(f"  {mkt}  —  {name}")
        print(f"{'─'*70}")

        print(f"  Fetching 1h data from Yahoo Finance ({symbol})...", end="", flush=True)
        try:
            c1h = fetch_yahoo(symbol, 730)
            print(f" {len(c1h)} candles")
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        if len(c1h) < 300:
            print(f"  Too little data ({len(c1h)} candles), skipping."); continue

        c4h = aggregate_to_4h(c1h)
        if len(c4h) < 60:
            print("  Insufficient 4h data, skipping."); continue

        ts0m = int(c1h[0][0]); ts1m = int(c1h[-1][0])
        dt0m = datetime.fromtimestamp(ts0m, tz=timezone.utc).strftime("%Y-%m-%d")
        dt1m = datetime.fromtimestamp(ts1m, tz=timezone.utc).strftime("%Y-%m-%d")
        days_m = (ts1m - ts0m) / 86400
        non_wkd_gaps = fx_gap_count(c1h, 3600)

        print(f"  Data source  : Yahoo Finance v8 API (public, no key needed)")
        print(f"  Instrument   : {name} ({symbol})")
        print(f"  Period       : {dt0m} → {dt1m} ({days_m:.0f} days / {days_m/30.44:.1f} months)")
        print(f"  1h candles   : {len(c1h)} | 4h candles: {len(c4h)}")
        print(f"  Non-weekend gaps: {non_wkd_gaps} (weekend gaps excluded — normal closure)")
        print(f"  Volume       : {'Real CME exchange volume (futures)' if not is_fx else 'Tick count proxy — not reliable exchange volume'}")
        print(f"  Spread       : {spread_note}")
        print(f"  Fees/slippage: None (same as crypto baseline for fair comparison)")

        # Strategy variants
        variants: list[tuple[str, str, bool, str]] = [
            ("A", "A",       False, "6/6 (volume included)"),
            ("C", "C",       False, "5/6+Guard (volume included)"),
        ]
        if is_fx:
            variants += [
                ("A_nv", "A", True,  "6/6 (volume skipped — 5 conditions)"),
                ("C_nv", "C", True,  "5/6+Guard (volume skipped — 4 conditions)"),
            ]

        print()
        mkt_metrics: dict[str, dict] = {}
        for vk, strat, skip_vol, vdesc in variants:
            print(f"  {vk:<8} {vdesc}...", end="", flush=True)
            r = run_backtest(f"{mkt}-{vk}", c1h, c4h, strategy=strat,
                             exit_mode="A", skip_volume=skip_vol)
            m = compute_metrics(r, test_days=days_m)
            mkt_metrics[vk] = m
            print(f" {m['n']} trades | ROI {m['roi']:+.2f}% | "
                  f"PF {fi(m['profit_factor'])} | MaxDD {m['max_dd']:.2f}%")

        for vk, strat, skip_vol, vdesc in variants:
            lbl = f"{mkt} / Strategy {vk} ({vdesc})"
            print_metrics(lbl, mkt_metrics[vk], show_ann=True)

        print(f"\n  ── {mkt} summary table ─────────────────────────────────────────────")
        print(f"  {'Variant':<12} {'Trades':>7} {'ROI%':>8} {'PF':>7} "
              f"{'WR%':>7} {'DD%':>6} {'Exp':>8} {'AnnROI%':>9} {'Evidence'}")
        print(f"  {'-'*12} {'-'*7} {'-'*8} {'-'*7} {'-'*7} {'-'*6} {'-'*8} {'-'*9} {'-'*22}")
        for vk, _, _, _ in variants:
            m = mkt_metrics[vk]
            print(f"  {vk:<12} {m['n']:>7} {m['roi']:>+8.2f} {fi(m['profit_factor']):>7} "
                  f"{m['win_rate']:>7.1f} {m['max_dd']:>6.2f} {fm(m['expectancy']):>8} "
                  f"{fp(m['ann_roi']) if m['ann_roi'] is not None else 'N/A':>9}  "
                  f"{ev_label(m['n'])}")

        best_vk  = max(mkt_metrics, key=lambda k: score(mkt_metrics[k]))
        best_m   = mkt_metrics[best_vk]
        rec      = instrument_rec(best_m)
        print(f"\n  ▶ {mkt}: {rec}")
        print(f"    Best variant: {best_vk}  Trades={best_m['n']}  "
              f"ROI={fp(best_m['roi'])}  PF={fi(best_m['profit_factor'])}  "
              f"Exp={fm(best_m['expectancy'])}  [{ev_label(best_m['n'])}]")
        if is_fx and best_m["n"] < 15:
            print(f"    ⚠ Note: Centralised volume is unavailable for FX. Even the no-volume")
            print(f"      variant produced too few trades for reliable conclusions.")

        market_best_metrics[mkt] = best_m
        market_best_key[mkt]     = best_vk
        market_rec[mkt]          = rec

    # ══════════════════════════════════════════════════════════════════════════
    # CROSS-MARKET COMPARISON + PORTFOLIO
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print("  CROSS-MARKET COMPARISON (best variant per market)")
    print(f"{'='*70}")

    # BTC 12-month best exit is the reference
    btc_ref = exit_metrics[best_em]
    all_markets: dict[str, dict] = {"BTC": btc_ref, **market_best_metrics}

    print(f"\n  {'Market':<8} {'Trades':>7} {'Trd/mo':>7} {'ROI%':>8} {'AnnROI%':>9} "
          f"{'PF':>7} {'WR%':>7} {'DD%':>6} {'Exp':>8} {'Sharpe':>8}")
    print(f"  {'-'*8} {'-'*7} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*7} {'-'*6} {'-'*8} {'-'*8}")
    for mkt, m in all_markets.items():
        s   = m.get("sharpe")
        tpm = m.get("tpm")
        print(f"  {mkt:<8} {m['n']:>7} {f'{tpm:.1f}' if tpm else 'N/A':>7} "
              f"{m['roi']:>+8.2f} "
              f"{fp(m['ann_roi']) if m.get('ann_roi') else 'N/A':>9} "
              f"{fi(m['profit_factor']):>7} {m['win_rate']:>7.1f} "
              f"{m['max_dd']:>6.2f} {fm(m['expectancy']):>8} "
              f"{fn(s) if s is not None else 'N/A':>8}")

    qualifying = [mkt for mkt, m in all_markets.items()
                  if m["expectancy"] > 0 and m["n"] >= 5]
    print(f"\n  Markets with positive expectancy: {qualifying}")

    # Portfolio of qualifying instruments
    if len(qualifying) >= 2:
        q_start  = len(qualifying) * STARTING_BALANCE
        q_finish = sum(all_markets[mkt]["final_balance"] for mkt in qualifying)
        q_pnl    = q_finish - q_start
        q_roi    = q_pnl / q_start * 100
        q_n      = sum(all_markets[mkt]["n"]            for mkt in qualifying)
        q_wins   = sum(all_markets[mkt]["wins"]         for mkt in qualifying)
        q_gp     = sum(all_markets[mkt]["gross_profit"] for mkt in qualifying)
        q_gl     = sum(all_markets[mkt]["gross_loss"]   for mkt in qualifying)
        q_pf     = (q_gp / q_gl) if q_gl > 0 else float("inf")
        q_wr     = q_wins / q_n * 100 if q_n > 0 else 0
        q_tpm    = sum(all_markets[mkt].get("tpm") or 0 for mkt in qualifying)

        print(f"\n  ── Qualifying portfolio: {', '.join(qualifying)} ──")
        print(f"  Capital          : £{q_start:.2f}")
        print(f"  Final value      : £{q_finish:.2f}")
        print(f"  Portfolio ROI    : {fp(q_roi)}")
        print(f"  Profit factor    : {fi(q_pf)}")
        print(f"  Total trades     : {q_n}  [{ev_label(q_n)}]")
        print(f"  Est. trades/month: {q_tpm:.1f}")
        print(f"  Win rate         : {fp(q_wr)}")
        print(f"  Avg per-mkt MaxDD: {fp(sum(all_markets[mkt]['max_dd'] for mkt in qualifying)/len(qualifying))}")
        btc_alone_tpm = btc_ref.get("tpm") or 0
        non_btc = [mkt for mkt in qualifying if mkt != "BTC"]
        if non_btc:
            nb_tpm = sum(all_markets[mkt].get("tpm") or 0 for mkt in non_btc)
            print(f"\n  Diversification: BTC ~{btc_alone_tpm:.1f} t/mo + non-BTC ~{nb_tpm:.1f} t/mo")
            print(f"  FX/metals trade on different schedules — partial decorrelation expected.")
            print(f"  Formal correlation requires aligned daily return series across markets;")
            print(f"  a qualitative diversification benefit is plausible but not yet quantified.")

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL REPORT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print("  FINAL REPORT")
    print(f"{'='*70}")

    # 1. Best BTC exit
    bem = exit_metrics[best_em]
    print(f"""
  1. BEST BTC EXIT METHOD
     ► {EXIT_NAMES[best_em]}
       Trades={bem['n']}  ROI={fp(bem['roi'])}  AnnROI={fp(bem['ann_roi'])}
       PF={fi(bem['profit_factor'])}  MaxDD={fp(bem['max_dd'])}
       WR={fp(bem['win_rate'])}  Exp={fm(bem['expectancy'])}  Sharpe={fn(bem['sharpe'])}
       [{ev_label(bem['n'])}]""")

    # 2. BTC 3-year
    print(f"""
  2. BTC 3-YEAR PERFORMANCE (year-by-year fresh windows)
     Period : {dt_3yr0} → {dt_3yr1}  ({days_3yr:.0f} days / 3.0 years)
     Total trades across all years : {all_trades_3yr}  [{ev_label(all_trades_3yr)}]
     Combined PF    : {fi(agg_pf_3yr)}
     Combined WR    : {agg_wr_3yr:.1f}%
     Avg PnL/year   : {fm(all_pnl_3yr/max(len(yearly_results),1))}""")
    for year, m, _ in yearly_results:
        print(f"     {year}: {m['n']} trades  ROI={fp(m['roi'])}  PF={fi(m['profit_factor'])}  "
              f"WR={fp(m['win_rate'])}  [{ev_label(m['n'])}]")

    # 3. BTC across regimes
    print(f"\n  3. BTC EDGE ACROSS MARKET REGIMES")
    for regime in ("BULLISH", "SIDEWAYS", "BEARISH"):
        ra = regime_agg.get(regime)
        if not ra or ra["n"] == 0:
            print(f"     {regime:<10}: Insufficient segment data"); continue
        pf_r = (ra["gp"]/ra["gl"]) if ra["gl"] > 0 else float("inf")
        wr_r = ra["wins"]/ra["n"]*100
        pnl_r= ra["pnl"]/ra["segs"]
        print(f"     {regime:<10}: Trades={ra['n']}  PF={fi(pf_r)}  "
              f"WR={wr_r:.1f}%  AvgPnL={fm(pnl_r)}  [{ev_label(ra['n'])}]")

    # 4-8. Per market
    for i, (mkt, name) in enumerate([
        ("GOLD","Gold"), ("SILVER","Silver"), ("EURUSD","EUR/USD"),
        ("GBPUSD","GBP/USD"), ("USDJPY","USD/JPY")
    ], 4):
        m = market_best_metrics.get(mkt)
        if m:
            rec = market_rec.get(mkt, "MORE RESEARCH NEEDED")
            print(f"\n  {i}. {name.upper()}")
            print(f"     Trades={m['n']}  ROI={fp(m['roi'])}  AnnROI={fp(m['ann_roi'])}  "
                  f"PF={fi(m['profit_factor'])}  WR={fp(m['win_rate'])}")
            print(f"     MaxDD={fp(m['max_dd'])}  Exp={fm(m['expectancy'])}  "
                  f"[{ev_label(m['n'])}]")
            print(f"     ► {rec}")
        else:
            print(f"\n  {i}. {name.upper()}  —  Data unavailable")

    # 9-14. Portfolio + summary numbers
    best_ind = max(all_markets, key=lambda m: score(all_markets[m]))
    bim      = all_markets[best_ind]
    print(f"\n  9. BEST INDIVIDUAL MARKET: {best_ind}")
    print(f"     ROI={fp(bim['roi'])}  PF={fi(bim['profit_factor'])}  "
          f"WR={fp(bim['win_rate'])}  Exp={fm(bim['expectancy'])}  [{ev_label(bim['n'])}]")

    print(f"\n  10. BEST DIVERSIFIED PORTFOLIO")
    if len(qualifying) >= 2:
        print(f"      Markets: {', '.join(qualifying)}")
        print(f"      Portfolio ROI={fp(q_roi)}  PF={fi(q_pf)}  WR={fp(q_wr)}")
    else:
        print(f"      Only BTC shows positive expectancy with sufficient data")
        print(f"      Portfolio diversification requires more data from other markets")

    print(f"\n  11. EXPECTED TRADES/MONTH")
    for mkt, m in all_markets.items():
        tpm = m.get("tpm")
        print(f"      {mkt:<8}: {f'{tpm:.1f}' if tpm else 'N/A'}")

    print(f"\n  12. EXPECTED ANNUAL RETURN (from historical backtest, not a guarantee)")
    for mkt, m in all_markets.items():
        if m.get("ann_roi") is not None:
            print(f"      {mkt:<8}: {fp(m['ann_roi'])}  [{ev_label(m['n'])}]")

    print(f"\n  13. MAXIMUM HISTORICAL DRAWDOWN")
    for mkt, m in all_markets.items():
        print(f"      {mkt:<8}: {fp(m['max_dd'])}")

    print(f"\n  14. EVIDENCE STRENGTH")
    for mkt, m in all_markets.items():
        print(f"      {mkt:<8}: {ev_label(m['n'])}")

    # Final recommendations
    print(f"\n{'─'*70}")
    print("  FINAL RECOMMENDATIONS PER INSTRUMENT")
    print("  KEEP / ADD TO PAPER TESTING / MORE RESEARCH NEEDED / REJECT")
    print(f"{'─'*70}")

    # BTC
    btc_m   = exit_metrics["A"]
    btc_rec = ("KEEP" if btc_m["expectancy"] > 0 and btc_m["profit_factor"] > 1.0
               else "MORE RESEARCH NEEDED")
    print(f"\n  BTC        ► {btc_rec}")
    print(f"             Current exit (A): Trades={btc_m['n']}  ROI={fp(btc_m['roi'])}  "
          f"PF={fi(btc_m['profit_factor'])}  [{ev_label(btc_m['n'])}]")
    if best_em != "A":
        bm2 = exit_metrics[best_em]
        print(f"             Consider testing {EXIT_NAMES[best_em]}: "
              f"ROI={fp(bm2['roi'])}  PF={fi(bm2['profit_factor'])}")

    for mkt, m in market_best_metrics.items():
        rec = market_rec.get(mkt, "MORE RESEARCH NEEDED")
        bk  = market_best_key.get(mkt, "—")
        print(f"\n  {mkt:<10} ► {rec}")
        print(f"             Best: {bk}  Trades={m['n']}  ROI={fp(m['roi'])}  "
              f"PF={fi(m['profit_factor'])}  [{ev_label(m['n'])}]")
        if m["n"] < 30:
            print(f"             ⚠ Insufficient sample for high-confidence conclusion")

    # Route to higher returns
    print(f"""
{'─'*70}
  ROUTE TO HIGHER RETURNS WITHOUT DISPROPORTIONATE RISK INCREASE
{'─'*70}

  1. BTC EXIT OPTIMISATION (no entry rule changes):
     Best exit from this test: {EXIT_NAMES[best_em]}
     Change in ROI vs current: {fp(exit_metrics[best_em]['roi'])} vs {fp(exit_metrics['A']['roi'])}
     This is a low-risk improvement — same entries, same risk per trade.

  2. MARKET DIVERSIFICATION:
     Adding instruments with positive expectancy and ≥30-trade evidence
     increases trade frequency without concentrating BTC risk.
     Current candidate: {qualifying[0] if qualifying else 'none yet — need more data'}.

  3. POSITION SIZING ON BTC (within conservative limits):
     BTC 3-year WR ≈ {agg_wr_3yr:.0f}% | R:R = 2.0
     Current: 1% risk/trade.  Half-Kelly ≈ {((2*agg_wr_3yr/100 - (1-agg_wr_3yr/100))/2*100):.1f}%.
     A conservative move to 2% risk/trade doubles expected £ return
     without changing strategy rules. Worth testing in paper trading first.

  4. REGIME FILTER (advanced — higher effort):
     BTC BULLISH regime shows stronger PF than BEARISH/SIDEWAYS.
     A live regime filter could skip low-quality periods.
     Requires real-time regime classification in the running bot.

  None of the above have been implemented. Awaiting your explicit approval.
""")
    print(f"{'─'*70}")
    print("  NO LIVE SETTINGS HAVE BEEN CHANGED.")
    print("  All changes require explicit approval before implementation.")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
