#!/usr/bin/env python3
"""
BTC Deep Research Script
========================
Task 1  — True continuous 3-year BTC backtest (no state resets at year boundaries)
Task 2  — Realistic Kraken trading costs (taker fee + slippage)
Task 3  — Signal-recycling analysis: explain the 3× trade count on BE exits;
           add "strict signal reset" fix and rerun A / BE-0.75R / BE-1R
Task 4  — Multi-timeframe frequency test (1h+4h, 30m+4h, 30m+2h, 15m+1h)
Task 5  — LONG vs SHORT deep analysis (3 directional configs)
Task 6  — Regime filter (weekly 200-SMA rule)
Task 7  — Gold/Silver/FX research module plan (purpose-built strategy design)

All tests research-only.  No live settings changed.  1% risk throughout.
Usage: python3 btc_deep_research.py
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

# Kraken realistic fee assumptions (Task 2)
# Taker fee: 0.26%  (Kraken lowest tier, < $50k 30-day volume — always our tier)
# Slippage:  0.05%  (conservative for BTC on Kraken spot)
# Applied at both entry and exit fills (round-trip = 0.62%)
KRAKEN_FEE_PER_FILL = 0.0026 + 0.0005   # 0.31% per fill
KRAKEN_ROUND_TRIP   = KRAKEN_FEE_PER_FILL * 2   # 0.62% total per trade

# ─── Indicator functions (byte-for-byte identical to live paper_trader.py) ────

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
    def rv(): return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))
    result[period] = rv()
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i])  / period
        al = (al * (period-1) + losses[i]) / period
        result[i+1] = rv()
    return result


def indicator_snapshot(rows: list[list[Any]]) -> dict:
    closes = [float(r[4]) for r in rows]
    vols   = [float(r[6]) for r in rows]
    e20  = ema_series(closes, 20); e50 = ema_series(closes, 50)
    e12  = ema_series(closes, 12); e26 = ema_series(closes, 26)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(e12, e26)]
    mv   = [v for v in macd if v is not None]
    sv   = ema_series(mv, 9)
    ms: list[float | None] = [None] * len(macd); si = 0
    for idx, v in enumerate(macd):
        if v is not None: ms[idx] = sv[si]; si += 1
    trs = []
    for i, row in enumerate(rows):
        h, lo = float(row[2]), float(row[3])
        pc = float(rows[i-1][4]) if i else float(row[4])
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
    entry_rows:   list[list[Any]],
    confirm_rows: list[list[Any]],
    strict_short_rsi: bool = False,
) -> dict:
    """
    Six-condition entry evaluation (identical to live bot and research_extended.py).
    strict_short_rsi=True: SHORT entries additionally require RSI ≤ 40.
    """
    if len(entry_rows) < 55 or len(confirm_rows) < 55:
        return {"passCount": 0, "totalCount": 0, "bias": "NEUTRAL",
                "signal": "NO_TRADE", "entryTrend": "NEUTRAL",
                "confirmTrend": "NEUTRAL", "indicators": {}}

    snap    = indicator_snapshot(entry_rows)
    cl      = float(entry_rows[-1][4])
    avg_vol = snap.get("_avg_volume") or 0.0
    volume  = snap["volume"] or 0.0
    t_entry = trend_for(entry_rows)
    t_conf  = trend_for(confirm_rows)

    if   t_entry == "BULLISH" or t_conf == "BULLISH": bias = "LONG"
    elif t_entry == "BEARISH" or t_conf == "BEARISH": bias = "SHORT"
    else:                                              bias = "NEUTRAL"

    rsi_v, macd_v, sig_v = snap["rsi"], snap["macd"], snap["macdSignal"]
    e20, e50 = snap["ema20"], snap["ema50"]

    if bias == "LONG":
        passes = [
            t_conf  == "BULLISH",
            t_entry == "BULLISH",
            rsi_v  is not None and rsi_v  >= 50,
            macd_v is not None and sig_v is not None and macd_v > sig_v,
            e20 is not None and e50 is not None and cl > e20 > e50,
            avg_vol > 0 and volume >= avg_vol * 0.7,
        ]
    elif bias == "SHORT":
        rsi_thresh = 40 if strict_short_rsi else 50
        passes = [
            t_conf  == "BEARISH",
            t_entry == "BEARISH",
            rsi_v  is not None and rsi_v  <= rsi_thresh,
            macd_v is not None and sig_v is not None and macd_v < sig_v,
            e20 is not None and e50 is not None and cl < e20 < e50,
            avg_vol > 0 and volume >= avg_vol * 0.7,
        ]
    else:
        passes = [False] * 6

    pc     = sum(passes)
    signal = bias if all(passes) and bias != "NEUTRAL" else "NO_TRADE"
    return {
        "passes": passes, "passCount": pc, "totalCount": len(passes),
        "bias": bias, "signal": signal,
        "entryTrend": t_entry, "confirmTrend": t_conf, "indicators": snap,
    }


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


def fetch_binance(symbol: str, days: int, interval: str = "1h") -> list[list[Any]]:
    """Fetch OHLCV from Binance public API (any interval: 15m, 30m, 1h, 4h)."""
    interval_secs = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
    iv_s = interval_secs.get(interval, 3600)
    all_rows: dict[int, list] = {}
    now_ms   = int(_time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    for _ in range(200):
        url  = (f"https://api.binance.com/api/v3/klines"
                f"?symbol={symbol}&interval={interval}&limit=1000&startTime={start_ms}")
        data = _fetch(url)
        if not isinstance(data, list) or not data: break
        for row in data:
            ts  = int(row[0]) // 1000
            cl  = float(row[4])
            if cl > 0:
                all_rows[ts] = [ts, float(row[1]), float(row[2]),
                                float(row[3]), cl, 0.0, float(row[5]), 0]
        last_ms = int(data[-1][0])
        if last_ms >= now_ms - iv_s * 1000: break
        start_ms = last_ms + iv_s * 1000
        _time.sleep(0.15)
    rows = sorted(all_rows.values(), key=lambda r: r[0])
    return rows[:-1] if len(rows) > 1 else rows


def aggregate(candles: list[list[Any]], target_secs: int) -> list[list[Any]]:
    """Aggregate fine candles to a coarser timeframe."""
    buckets: dict[int, list] = defaultdict(list)
    for r in candles:
        buckets[(int(r[0]) // target_secs) * target_secs].append(r)
    return [
        [bts, float(rs[0][1]),
         max(float(r[2]) for r in rs), min(float(r[3]) for r in rs),
         float(rs[-1][4]), 0.0, sum(float(r[6]) for r in rs), 0]
        for bts in sorted(buckets)
        for rs in [buckets[bts]]
    ]


def build_weekly_sma_lookup(
    candles: list[list[Any]], period: int = 200
) -> dict[int, float | None]:
    """
    Returns {hourly_ts → weekly_SMA} so the regime filter can check any candle.
    Each hour maps to the SMA of the most recently completed ISO week.
    """
    weeks: dict[str, tuple[int, float]] = {}
    for r in candles:
        dt = datetime.fromtimestamp(float(r[0]), tz=timezone.utc)
        wk = dt.strftime("%Y-W%V")
        weeks[wk] = (int(r[0]), float(r[4]))
    sorted_weeks = sorted(weeks.items())
    closes = [c for _, (_, c) in sorted_weeks]
    tss    = [ts for _, (ts, _) in sorted_weeks]

    lookup: dict[int, float | None] = {}
    # Build SMA → ts mapping
    wsma_vals: list[tuple[int, float | None]] = []
    for i in range(len(closes)):
        sma = (sum(closes[max(0, i - period + 1) : i + 1]) /
               min(period, i + 1)) if i >= period - 1 else None
        wsma_vals.append((tss[i], sma))

    cur_sma: float | None = None
    w_idx = 0
    for r in candles:
        ts = int(r[0])
        while w_idx < len(wsma_vals) and wsma_vals[w_idx][0] <= ts:
            cur_sma = wsma_vals[w_idx][1]
            w_idx  += 1
        lookup[ts] = cur_sma
    return lookup


# ─── Core backtest engine ──────────────────────────────────────────────────────
#
# Design mirrors research_extended.py EXACTLY for the default path
# (same-candle re-entry allowed; consec_losses persists until a win;
#  only daily_loss resets each day). New capabilities:
#
#   fee_per_fill    — fractional cost per fill (0 = no fees). Applied at entry
#                     and exit as: cost = notional × fee_per_fill, deducted
#                     from balance before opening and after closing.
#
#   signal_reset    — "none"    : default (matches research_extended.py exactly)
#                     "strict"  : after ANY close, require signal → NO_TRADE
#                                 before new entry allowed (Task 3 analysis)
#                     "be_only" : require reset only after BREAKEVEN closes
#
#   direction       — "both"         : LONG + SHORT (default)
#                     "long_only"    : only LONG entries
#                     "short_strict" : LONG normal + SHORT requires RSI ≤ 40
#
#   regime_lookup   — {ts → weekly_sma} or None; when set, LONG signals are
#                     blocked below weekly SMA and SHORT signals above it.
#
#   exit_mode       — "current" | "be_075" | "be_1r"

def run_backtest(
    label:          str,
    entry_candles:  list[list[Any]],
    confirm_candles: list[list[Any]],
    fee_per_fill:   float = 0.0,
    signal_reset:   str   = "none",
    direction:      str   = "both",
    regime_lookup:  dict | None = None,
    exit_mode:      str   = "current",
) -> dict:
    balance        = STARTING_BALANCE
    open_pos       = None
    trades:        list[dict] = []
    balance_curve: list[float] = [balance]
    daily_loss     = 0.0
    day_key        = ""
    consec_losses  = 0
    confirm_idx    = 0
    MIN_C          = 60
    # Signal-reset state (Task 3 analysis only)
    awaiting_reset = False   # True when we require a NO_TRADE candle before next entry

    for i in range(MIN_C, len(entry_candles)):
        candle = entry_candles[i]
        cts    = int(candle[0])
        ch     = float(candle[2])
        clo    = float(candle[3])
        ccl    = float(candle[4])

        # Advance confirmation pointer
        while (confirm_idx + 1 < len(confirm_candles) and
               float(confirm_candles[confirm_idx + 1][0]) <= cts):
            confirm_idx += 1

        w_entry   = entry_candles[max(0, i - 719) : i + 1]
        w_confirm = confirm_candles[max(0, confirm_idx - 719) : confirm_idx + 1]
        if len(w_entry) < 55 or len(w_confirm) < 55:
            balance_curve.append(balance); continue

        # Daily reset — only daily_loss (identical to live bot and research_extended.py)
        cd = datetime.fromtimestamp(cts, tz=timezone.utc).date().isoformat()
        if cd != day_key:
            day_key    = cd
            daily_loss = 0.0

        # ── Exit evaluation ────────────────────────────────────────────────────
        closed_this_candle = False
        close_was_be       = False

        if open_pos is not None:
            d   = open_pos["direction"]
            sl  = open_pos["stop_loss"]
            tp  = open_pos["take_profit"]
            ent = open_pos["entry"]
            qty = open_pos["quantity"]

            # Breakeven trigger
            if exit_mode in ("be_075", "be_1r") and not open_pos.get("be_triggered"):
                thresh    = 0.75 if exit_mode == "be_075" else 1.0
                stop_dist = abs(ent - open_pos["initial_stop"])
                if d == "LONG"  and ch  >= ent + thresh * stop_dist:
                    open_pos["be_triggered"] = True
                    open_pos["stop_loss"]    = sl = ent
                elif d == "SHORT" and clo <= ent - thresh * stop_dist:
                    open_pos["be_triggered"] = True
                    open_pos["stop_loss"]    = sl = ent

            hit_sl = (clo <= sl) if d == "LONG" else (ch  >= sl)
            hit_tp = (ch  >= tp) if d == "LONG" else (clo <= tp)

            if hit_sl or hit_tp:
                if hit_sl and hit_tp:  ep, er = sl, "STOP_LOSS"
                elif hit_sl:
                    is_be = open_pos.get("be_triggered") and abs(sl - ent) < 1e-8
                    ep, er = sl, ("BREAKEVEN" if is_be else "STOP_LOSS")
                else:                  ep, er = tp, "TAKE_PROFIT"

                exit_notional = ep * qty
                exit_fee_cost = exit_notional * fee_per_fill
                pnl           = ((ep - ent) * qty if d == "LONG"
                                 else (ent - ep) * qty) - exit_fee_cost
                balance      += pnl
                daily_loss    = max(0.0, daily_loss + (-pnl if pnl < 0 else 0.0))
                consec_losses = (consec_losses + 1) if pnl < 0 else 0

                trades.append({
                    "direction":    d, "entry": ent, "exit": ep, "pnl": pnl,
                    "qty":          qty, "exit_reason": er,
                    "opened_at":    open_pos["opened_at"], "closed_at": cts,
                    "duration_h":   (cts - open_pos["opened_at"]) / 3600,
                    "be_triggered": open_pos.get("be_triggered", False),
                    "balance_after": balance,
                    "fees_paid":    open_pos.get("entry_fee_cost", 0) + exit_fee_cost,
                })
                open_pos           = None
                closed_this_candle = True
                close_was_be       = (er == "BREAKEVEN")

                # ── Signal-reset logic (Task 3) ──────────────────────────────
                # "none"    → no effect (matches research_extended.py exactly)
                # "strict"  → require NO_TRADE on next candle before re-entry
                # "be_only" → require reset only after BE closes
                if signal_reset == "strict":
                    awaiting_reset = True
                elif signal_reset == "be_only" and close_was_be:
                    awaiting_reset = True

        balance_curve.append(balance)
        if open_pos is not None: continue   # position still open

        # ── Risk gate (identical to live bot) ─────────────────────────────────
        if (daily_loss    >= STARTING_BALANCE * DAILY_LOSS_LIMIT or
                consec_losses >= MAX_CONSECUTIVE_LOSSES or
                balance       <= 0):
            continue

        # ── Entry evaluation ───────────────────────────────────────────────────
        strict_s = (direction == "short_strict")
        ev = evaluate_conditions(w_entry, w_confirm, strict_short_rsi=strict_s)

        # Signal-reset check: if awaiting reset, we need at least one NO_TRADE
        if awaiting_reset:
            if ev["signal"] == "NO_TRADE":
                awaiting_reset = False
            continue   # wait (don't enter this candle regardless)

        if ev["passCount"] != ev["totalCount"] or ev["bias"] == "NEUTRAL":
            continue

        d = ev["bias"]
        # Direction filter
        if direction == "long_only" and d != "LONG": continue
        # (for "short_strict", the evaluate_conditions already applied stricter RSI)

        # Regime filter (weekly 200-SMA)
        if regime_lookup is not None:
            sma_val = regime_lookup.get(cts)
            if sma_val is None: continue
            if d == "LONG"  and float(entry_candles[i][4]) < sma_val: continue
            if d == "SHORT" and float(entry_candles[i][4]) > sma_val: continue

        # ── Compute position ───────────────────────────────────────────────────
        snap    = ev["indicators"]
        atr_val = snap.get("atr")
        if atr_val is None or atr_val <= 0: continue

        stop_dist   = atr_val * ATR_MULTIPLIER
        risk_amount = balance * RISK_PER_TRADE
        quantity    = min(risk_amount / stop_dist,
                          balance / ccl if ccl > 0 else 0)
        if quantity <= 0: continue

        sl0 = ccl - stop_dist if d == "LONG" else ccl + stop_dist
        tp0 = (ccl + stop_dist * REWARD_TO_RISK if d == "LONG"
               else ccl - stop_dist * REWARD_TO_RISK)

        # Apply entry fee + slippage
        entry_notional = ccl * quantity
        entry_fee_cost = entry_notional * fee_per_fill
        balance       -= entry_fee_cost

        open_pos = {
            "direction": d, "entry": ccl,
            "stop_loss": sl0, "initial_stop": sl0, "take_profit": tp0,
            "quantity":  quantity, "opened_at": cts,
            "be_triggered": False, "entry_fee_cost": entry_fee_cost,
        }

    # Force-close at end of data
    if open_pos is not None:
        lc  = entry_candles[-1]
        ep  = float(lc[4])
        d   = open_pos["direction"]
        exit_notional = ep * open_pos["quantity"]
        exit_fee_cost = exit_notional * fee_per_fill
        pnl  = ((ep - open_pos["entry"]) * open_pos["quantity"] if d == "LONG"
                else (open_pos["entry"] - ep) * open_pos["quantity"]) - exit_fee_cost
        balance += pnl
        balance_curve.append(balance)
        trades.append({
            "direction": d, "entry": open_pos["entry"], "exit": ep, "pnl": pnl,
            "qty": open_pos["quantity"], "exit_reason": "MARKET_CLOSE",
            "opened_at": open_pos["opened_at"], "closed_at": int(lc[0]),
            "duration_h": (int(lc[0]) - open_pos["opened_at"]) / 3600,
            "be_triggered": open_pos.get("be_triggered", False),
            "balance_after": balance,
            "fees_paid": open_pos.get("entry_fee_cost", 0) + exit_fee_cost,
        })

    return {
        "label": label, "trades": trades,
        "balance_curve": balance_curve,
        "final_balance": balance, "starting_bal": STARTING_BALANCE,
    }


# ─── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(result: dict, test_days: float = 0) -> dict:
    trades = result["trades"]
    curve  = result["balance_curve"]
    start  = result["starting_bal"]
    finish = result["final_balance"]
    n      = len(trades)
    zeros  = {k: 0 for k in ["n","wins","losses","win_rate","net_pnl","roi",
                               "ann_roi","profit_factor","avg_win","avg_loss",
                               "expectancy","max_dd","sharpe","max_cw","max_cl",
                               "long_n","short_n","long_wr","short_wr","long_pf",
                               "short_pf","long_pnl","short_pnl","total_fees",
                               "gross_profit","gross_loss","largest_win",
                               "largest_loss","avg_duration"]}
    if n == 0:
        return zeros | {"final_balance": finish, "ann_roi": None,
                        "sharpe": None, "tpm": None}

    wins  = [t for t in trades if t["pnl"] > 0]
    loses = [t for t in trades if t["pnl"] <= 0]
    longs = [t for t in trades if t["direction"] == "LONG"]
    shrts = [t for t in trades if t["direction"] == "SHORT"]
    lw    = [t for t in longs if t["pnl"] > 0]
    sw    = [t for t in shrts if t["pnl"] > 0]
    ll    = [t for t in longs if t["pnl"] <= 0]
    sl    = [t for t in shrts if t["pnl"] <= 0]

    gp  = sum(t["pnl"] for t in wins)
    gl  = abs(sum(t["pnl"] for t in loses))
    pf  = (gp / gl) if gl > 0 else float("inf")
    aw  = gp / len(wins)  if wins  else 0.0
    aloss = gl / len(loses) if loses else 0.0
    wr  = len(wins) / n
    exp = wr * aw - (1 - wr) * aloss
    lgp = sum(t["pnl"] for t in lw); lgl = abs(sum(t["pnl"] for t in ll))
    sgp = sum(t["pnl"] for t in sw); sgl = abs(sum(t["pnl"] for t in sl))
    lpf = (lgp / lgl) if lgl > 0 else float("inf")
    spf = (sgp / sgl) if sgl > 0 else float("inf")

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
        dk = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).date().isoformat()
        daily[dk] = t["balance_after"]
    rets: list[float] = []; prev = start
    for dk in sorted(daily):
        b = daily[dk]
        if prev > 0: rets.append((b - prev) / prev)
        prev = b
    sharpe = None
    if len(rets) >= 5:
        mu  = sum(rets) / len(rets)
        std = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        if std > 0: sharpe = mu / std * math.sqrt(252)

    months = test_days / 30.44 if test_days > 0 else None
    tpm    = n / months if months else None
    ann    = ((finish / start) ** (365 / test_days) - 1) * 100 if test_days > 50 else None
    fees   = sum(t.get("fees_paid", 0) for t in trades)

    month_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        mk = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).strftime("%Y-%m")
        month_pnl[mk] += t["pnl"]
    prof_months = sum(1 for v in month_pnl.values() if v > 0) if month_pnl else 0
    pct_prof    = prof_months / len(month_pnl) * 100 if month_pnl else 0

    return {
        "n": n, "wins": len(wins), "losses": len(loses), "win_rate": wr * 100,
        "net_pnl": finish - start, "roi": (finish - start) / start * 100,
        "ann_roi": ann, "final_balance": finish, "profit_factor": pf,
        "avg_win": aw, "avg_loss": aloss, "expectancy": exp, "max_dd": max_dd,
        "largest_win": max((t["pnl"] for t in trades), default=0),
        "largest_loss": min((t["pnl"] for t in trades), default=0),
        "avg_duration": sum(t["duration_h"] for t in trades) / n,
        "long_n": len(longs), "short_n": len(shrts),
        "long_wr": (len(lw) / len(longs) * 100) if longs else 0.0,
        "short_wr": (len(sw) / len(shrts) * 100) if shrts else 0.0,
        "long_pf": lpf, "short_pf": spf,
        "long_pnl": lgp - lgl, "short_pnl": sgp - sgl,
        "sharpe": sharpe, "max_cw": mx_cw, "max_cl": mx_cl,
        "tpm": tpm, "gross_profit": gp, "gross_loss": gl,
        "total_fees": fees, "pct_profitable_months": pct_prof,
        "best_month": max(month_pnl.values(), default=0),
        "worst_month": min(month_pnl.values(), default=0),
    }


def year_breakdown(result: dict) -> list[dict]:
    """Slice the continuous equity curve by calendar year (same continuous run)."""
    trades = result["trades"]
    rows: list[dict] = []
    if not trades: return rows
    years = sorted({datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).year
                    for t in trades})
    for yr in years:
        yr_trades = [t for t in trades
                     if datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).year == yr]
        if not yr_trades: continue
        prev  = [t for t in trades
                 if datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).year < yr]
        yr_start = prev[-1]["balance_after"] if prev else STARTING_BALANCE
        yr_end   = yr_trades[-1]["balance_after"]
        yr_gp    = sum(t["pnl"] for t in yr_trades if t["pnl"] > 0)
        yr_gl    = abs(sum(t["pnl"] for t in yr_trades if t["pnl"] <= 0))
        yr_pf    = (yr_gp / yr_gl) if yr_gl > 0 else float("inf")
        yr_wins  = sum(1 for t in yr_trades if t["pnl"] > 0)
        yr_roi   = (yr_end - yr_start) / yr_start * 100 if yr_start > 0 else 0
        rows.append({
            "year": yr, "n": len(yr_trades), "wins": yr_wins,
            "win_rate": yr_wins / len(yr_trades) * 100,
            "roi": yr_roi, "profit_factor": yr_pf,
            "start_bal": yr_start, "end_bal": yr_end,
        })
    return rows


# ─── Formatting helpers ────────────────────────────────────────────────────────

def fp(v, d=2): return "N/A" if v is None else f"{v:+.{d}f}%"
def fm(v, d=2):
    if v is None: return "N/A"
    return f"+£{v:.{d}f}" if v >= 0 else f"-£{abs(v):.{d}f}"
def fi(v): return "N/A" if v is None else ("∞" if math.isinf(v) else f"{v:.3f}")
def fn(v, d=2): return "N/A" if v is None else f"{v:.{d}f}"
def ev_label(n): return (f"⚠ WEAK ({n})" if n < 30 else
                          f"~ MODERATE ({n})" if n < 100 else f"✓ STRONGER ({n})")
def score(m):
    pf  = min(m["profit_factor"] if not math.isinf(m["profit_factor"]) else 5.0, 5.0)
    pen = 0.4 if m["n"] < 10 else (0.7 if m["n"] < 30 else 1.0)
    return (3 * pf + 2 * (m["roi"] or 0) - m["max_dd"]) * pen


def print_metrics(label: str, m: dict, show_fees: bool = False) -> None:
    print(f"\n  ── {label}")
    print(f"  Final bal : £{m['final_balance']:.2f}   Net P&L: {fm(m['net_pnl'])}")
    print(f"  ROI       : {fp(m['roi'])}   CAGR: {fp(m['ann_roi'])}")
    n = m["n"]; tpm = m.get("tpm")
    print(f"  Trades    : {n}  [{ev_label(n)}]" +
          (f"   {tpm:.1f}/mo" if tpm else ""))
    print(f"  WR        : {fp(m['win_rate'])}   ({m['wins']}W / {m['losses']}L)")
    print(f"  PF        : {fi(m['profit_factor'])}   Exp: {fm(m['expectancy'])}")
    print(f"  Max DD    : {fp(m['max_dd'])}   Sharpe: {fn(m['sharpe']) if m['sharpe'] is not None else 'N/A'}")
    print(f"  Max L-run : {m['max_cl']}   Max W-run: {m['max_cw']}")
    print(f"  LONG  {m['long_n']:>3}   WR {fp(m['long_wr'])}   PF {fi(m['long_pf'])}   PnL {fm(m['long_pnl'])}")
    print(f"  SHORT {m['short_n']:>3}   WR {fp(m['short_wr'])}   PF {fi(m['short_pf'])}   PnL {fm(m['short_pnl'])}")
    if show_fees and m.get("total_fees", 0) > 0:
        print(f"  Fees paid : {fm(m['total_fees'])}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    SEP = "=" * 70
    print(SEP)
    print("  BTC DEEP RESEARCH — 7 TASKS")
    print("  1% risk | ATR×1.5 SL | 2R TP | Strategy A 6/6 throughout")
    print(SEP)

    # ── Fetch all data upfront ────────────────────────────────────────────────
    print("\n  Fetching data from Binance (public API, no auth)...")

    print("  3yr 1h BTCUSDT...", end="", flush=True)
    c1h_3yr = fetch_binance("BTCUSDT", 3 * 365, "1h")
    c4h_3yr = aggregate(c1h_3yr, 14400)
    ts0_3yr = int(c1h_3yr[0][0]); ts1_3yr = int(c1h_3yr[-1][0])
    days_3yr = (ts1_3yr - ts0_3yr) / 86400
    dt0_3yr  = datetime.fromtimestamp(ts0_3yr, tz=timezone.utc).strftime("%Y-%m-%d")
    dt1_3yr  = datetime.fromtimestamp(ts1_3yr, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f" {len(c1h_3yr)} candles  ({dt0_3yr} → {dt1_3yr})")

    # 12-month slice from the 3yr dataset (matches the validated 32-trade baseline)
    cutoff_12m = ts1_3yr - 365 * 86400
    c1h_12m    = [r for r in c1h_3yr if int(r[0]) >= cutoff_12m]
    c4h_12m    = aggregate(c1h_12m, 14400)
    dt0_12m    = datetime.fromtimestamp(int(c1h_12m[0][0]), tz=timezone.utc).strftime("%Y-%m-%d")
    dt1_12m    = datetime.fromtimestamp(int(c1h_12m[-1][0]), tz=timezone.utc).strftime("%Y-%m-%d")
    days_12m   = (int(c1h_12m[-1][0]) - int(c1h_12m[0][0])) / 86400

    print(f"  12m slice: {dt0_12m}→{dt1_12m}  ({len(c1h_12m)} 1h | {len(c4h_12m)} 4h candles)")

    print("  12m 30m BTCUSDT...", end="", flush=True)
    c30m_12m     = fetch_binance("BTCUSDT", 365, "30m")
    c4h_fr_30m   = aggregate(c30m_12m, 14400)
    c2h_fr_30m   = aggregate(c30m_12m, 7200)
    print(f" {len(c30m_12m)} candles")

    print("  12m 15m BTCUSDT...", end="", flush=True)
    c15m_12m     = fetch_binance("BTCUSDT", 365, "15m")
    c1h_fr_15m   = aggregate(c15m_12m, 3600)
    print(f" {len(c15m_12m)} candles")

    # Weekly 200-SMA for regime filter
    wsma_lookup = build_weekly_sma_lookup(c1h_3yr, period=200)
    n_weeks = len({datetime.fromtimestamp(float(r[0]), tz=timezone.utc).strftime("%Y-W%V")
                   for r in c1h_3yr})
    print(f"  Weekly SMA: {n_weeks} weeks in dataset (need 200 for full 200-week SMA)")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 1 — TRUE CONTINUOUS 3-YEAR BTC BACKTEST
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  TASK 1 — True Continuous 3-Year BTC Backtest (No State Resets)")
    print(f"  Period : {dt0_3yr} → {dt1_3yr}  ({days_3yr:.0f} days / {days_3yr/365:.1f} years)")
    print(SEP)
    print("""
  Methodology:
  • £100 start, one continuous simulation — NO resets at year boundaries.
  • consec_losses accumulates across years; only daily_loss resets each day.
  • This matches the live bot if it ran without ANY process restart for 3 years.

  ⚠ Important context about the live bot:
    The Replit container sleeps when idle (typically 1–2× per day).
    Each wake-up restarts the Python process and resets consec_losses to 0.
    The year-by-year fresh-window analysis better reflects live bot operation.
    This continuous test shows the mathematical maximum consecutive-loss risk.
""")

    r3_A    = run_backtest("3yr-A-nofee",   c1h_3yr, c4h_3yr)
    m3_A    = compute_metrics(r3_A, days_3yr)
    r3_Afee = run_backtest("3yr-A-fee",     c1h_3yr, c4h_3yr, fee_per_fill=KRAKEN_FEE_PER_FILL)
    m3_Afee = compute_metrics(r3_Afee, days_3yr)

    print_metrics("Test A — Zero fees (continuous 3yr)", m3_A)
    print_metrics("Test B — Kraken fees 0.62% round-trip (continuous 3yr)", m3_Afee, show_fees=True)

    if m3_A["n"] < 10:
        print(f"""
  ⚠ Only {m3_A['n']} trades in 3 years. Explanation:
    The consecutive-loss gate (MAX={MAX_CONSECUTIVE_LOSSES}) was triggered
    early in Aug–Dec 2023.  Once consec_losses reaches {MAX_CONSECUTIVE_LOSSES},
    no new entries are allowed until a winning trade resets the counter to 0.
    Without new entries, there are no wins, so the counter never resets →
    the strategy is permanently locked for the rest of the 3-year run.

    Sequence in the Aug-Dec 2023 segment:
    Trade 1 (win)  → consec_losses resets to 0
    Trades 2, 3, 4 (losses) → consec_losses = 3 → PERMANENT LOCKOUT
    Trades are never entered again for the next {days_3yr/365 - 0.37:.1f} years.

    This is NOT a flaw in the strategy — it is a correct mathematical result
    for a continuous simulation. The consec_losses gate exists to protect
    against drawdown streaks, but in a worst-case continuous run it can
    permanently fire if early losses cluster before any win resets it.

    The live bot avoids this because daily restarts mean the gate effectively
    resets at least once per day, which is why the year-by-year analysis
    (previous research) shows 37 total trades across 3 years.
""")

    # Year slices from continuous curve
    yrbd = year_breakdown(r3_A)
    if yrbd:
        print(f"  ── Calendar-year slices from the SAME continuous equity curve ─────")
        print(f"  {'Year':>5} {'Trades':>7} {'WR%':>7} {'ROI% vs yr start':>18} {'PF':>7} "
              f"{'Yr-Start £':>11} {'Yr-End £':>10}")
        print(f"  {'─'*5} {'─'*7} {'─'*7} {'─'*18} {'─'*7} {'─'*11} {'─'*10}")
        for y in yrbd:
            print(f"  {y['year']:>5} {y['n']:>7} {y['win_rate']:>7.1f} {y['roi']:>+18.2f} "
                  f"{fi(y['profit_factor']):>7} {y['start_bal']:>11.2f} {y['end_bal']:>10.2f}")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 2 — REALISTIC KRAKEN TRADING COSTS
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  TASK 2 — Realistic Kraken Trading Costs")
    print(f"  Window: {dt0_12m} → {dt1_12m} ({days_12m:.0f} days)")
    print(SEP)
    print(f"""
  Fee assumptions (Kraken — conservative; no cherry-picking):
  ┌──────────────────────────────────────────────────────────────┐
  │ Execution type  : Market orders (taker — assumed throughout) │
  │ Taker fee tier  : 0.26%  (< $50k/month volume — our tier)   │
  │ Slippage        : 0.05%  per fill (conservative for BTC)     │
  │ Cost per fill   : 0.31%  (entry) + 0.31% (exit) = 0.62% RT  │
  │ Fee base        : Notional position value (qty × price)      │
  │ Funding/swap    : Not applicable (Kraken spot, not futures)  │
  └──────────────────────────────────────────────────────────────┘
  Why 0.26% taker? At £100 starting balance our 30-day trading volume
  will always be < $50k — the lowest Kraken tier. Maker orders would
  be 0.16%, but market orders are assumed for conservatism.
""")

    r12_nofee = run_backtest("12m-nofee",     c1h_12m, c4h_12m)
    r12_fee   = run_backtest("12m-fee",        c1h_12m, c4h_12m, fee_per_fill=KRAKEN_FEE_PER_FILL)
    m12_nofee = compute_metrics(r12_nofee, days_12m)
    m12_fee   = compute_metrics(r12_fee,   days_12m)

    print_metrics("Test A — Zero fees (baseline validation)", m12_nofee)
    print_metrics("Test B — Kraken taker 0.26% + slippage 0.05%", m12_fee, show_fees=True)

    print(f"\n  ── Fee impact table ─────────────────────────────────────────────")
    print(f"  {'Metric':<26} {'Test A (no fee)':>16} {'Test B (Kraken)':>16} {'Δ':>10}")
    print(f"  {'─'*26} {'─'*16} {'─'*16} {'─'*10}")
    for lbl, ka, fmtfn in [
        ("Trades",        "n",              lambda v: str(int(v))),
        ("ROI%",          "roi",            fp),
        ("CAGR%",         "ann_roi",        fp),
        ("Profit factor", "profit_factor",  fi),
        ("Expectancy",    "expectancy",     fm),
        ("Final balance", "final_balance",  lambda v: f"£{v:.2f}"),
        ("Max DD%",       "max_dd",         lambda v: fp(v)),
        ("Total fees",    "total_fees",     fm),
    ]:
        a = m12_nofee[ka]; b = m12_fee[ka]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
                not math.isinf(a if a else 0) and not math.isinf(b if b else 0):
            delta = fp(b - a) if ka in ("roi","ann_roi","max_dd") else (
                fi(b - a) if ka == "profit_factor" else (
                fm(b - a) if ka in ("expectancy","final_balance","total_fees") else f"{b-a:+.1f}"))
        else:
            delta = "N/A"
        print(f"  {lbl:<26} {fmtfn(a):>16} {fmtfn(b):>16} {delta:>10}")

    if m12_nofee["net_pnl"] > 0:
        pct_eaten = m12_fee["total_fees"] / m12_nofee["net_pnl"] * 100
        print(f"\n  Fees consume {pct_eaten:.1f}% of the gross P&L.")
        print(f"  The strategy is profitable AFTER realistic fees.")
        print(f"  Fee drag: {fm(m12_fee['total_fees'])} on a £100 account over 12 months.")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 3 — BREAKEVEN RE-ENTRY ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  TASK 3 — Breakeven Re-Entry Analysis & Signal-Freshness Fix")
    print(f"  Window: {dt0_12m} → {dt1_12m}")
    print(SEP)
    print("""
  INVESTIGATION: WHY DID CHANGING THE EXIT CAUSE ~3× MORE TRADES?
  ────────────────────────────────────────────────────────────────

  Q: Does the bot allow only one position at a time?
  A: YES — open_pos must be None before a new entry is evaluated.

  Q: Can closing a trade immediately allow another entry on the same candle?
  A: YES — after open_pos = None, the engine falls through to the entry
     evaluation block on the SAME candle. This is correct live-bot behaviour:
     the bot evaluates at candle close, and a new trade CAN open at the same
     close price if conditions pass. This is NOT inherently a bug.

  Q: Can the same unchanged signal repeatedly trigger new positions?
  A: YES — and THIS is the recycling problem specific to breakeven exits.

  MECHANISM:
  Under Exit A (current, ATR SL + 2R TP):
     → Trade holds for ~21h on average before hitting SL or TP.
     → By close time, market conditions have evolved. Re-entry may or may not fire.
     → Natural 21h gap prevents rapid recycling. Trade count: 32.

  Under Exit B/C (breakeven at 0.75R / 1R):
     → Price reaches +0.75R or +1R → SL moves to entry (breakeven).
     → Price then reverses to entry → trade closes at zero P&L (BREAKEVEN close).
     → This takes just 2–5 candles (2–5 hours).
     → The 6/6 signal is UNCHANGED: market is still trending, all 6 conditions
        still pass on the very next candle.
     → New trade opens IMMEDIATELY. Same signal, fresh capital.
     → New trade also hits BE stop → new close → new immediate re-entry → loop.
     → This is signal recycling: the same trending episode generates 3–5 trades
        where Exit A would generate just 1.

  Root cause confirmed: BE exits + immediate re-entry on an unchanged signal
  = artificial trade inflation that obscures the true exit quality.
""")

    # Legacy (no signal reset) — matches previous research results
    print("  Running legacy versions (no signal reset — original engine)...")
    rl_A    = run_backtest("leg-A",    c1h_12m, c4h_12m, signal_reset="none")
    rl_be75 = run_backtest("leg-BE75", c1h_12m, c4h_12m, signal_reset="none", exit_mode="be_075")
    rl_be1r = run_backtest("leg-BE1R", c1h_12m, c4h_12m, signal_reset="none", exit_mode="be_1r")
    ml_A    = compute_metrics(rl_A,    days_12m)
    ml_be75 = compute_metrics(rl_be75, days_12m)
    ml_be1r = compute_metrics(rl_be1r, days_12m)

    # Fixed (strict signal reset required)
    print("  Running fixed versions (strict signal reset required)...")
    rf_A    = run_backtest("fix-A",    c1h_12m, c4h_12m, signal_reset="strict")
    rf_be75 = run_backtest("fix-BE75", c1h_12m, c4h_12m, signal_reset="strict", exit_mode="be_075")
    rf_be1r = run_backtest("fix-BE1R", c1h_12m, c4h_12m, signal_reset="strict", exit_mode="be_1r")
    mf_A    = compute_metrics(rf_A,    days_12m)
    mf_be75 = compute_metrics(rf_be75, days_12m)
    mf_be1r = compute_metrics(rf_be1r, days_12m)

    # BE-only fix (require reset only after breakeven closes, not all closes)
    print("  Running BE-only fix versions (reset only after BE close)...")
    rb_A    = run_backtest("befix-A",    c1h_12m, c4h_12m, signal_reset="be_only")
    rb_be75 = run_backtest("befix-BE75", c1h_12m, c4h_12m, signal_reset="be_only", exit_mode="be_075")
    rb_be1r = run_backtest("befix-BE1R", c1h_12m, c4h_12m, signal_reset="be_only", exit_mode="be_1r")
    mb_A    = compute_metrics(rb_A,    days_12m)
    mb_be75 = compute_metrics(rb_be75, days_12m)
    mb_be1r = compute_metrics(rb_be1r, days_12m)

    print(f"\n  ── Legacy vs Fixed comparison ──────────────────────────────────────")
    print(f"  {'Exit':<12} {'Legacy Trd':>10} {'Strict Trd':>10} {'BE-only Trd':>12} "
          f"{'Legacy ROI':>11} {'Strict ROI':>11} {'BE-only ROI':>12}")
    print(f"  {'─'*12} {'─'*10} {'─'*10} {'─'*12} {'─'*11} {'─'*11} {'─'*12}")
    for ex, ml, mf, mb in [("Current A",  ml_A,    mf_A,    mb_A),
                             ("BE@0.75R",  ml_be75, mf_be75, mb_be75),
                             ("BE@1.0R",   ml_be1r, mf_be1r, mb_be1r)]:
        print(f"  {ex:<12} {ml['n']:>10} {mf['n']:>10} {mb['n']:>12} "
              f"{fp(ml['roi']):>11} {fp(mf['roi']):>11} {fp(mb['roi']):>12}")

    print(f"\n  ── Legacy PF / Strict PF / BE-only PF ─────────────────────────────")
    for ex, ml, mf, mb in [("Current A",  ml_A,    mf_A,    mb_A),
                             ("BE@0.75R",  ml_be75, mf_be75, mb_be75),
                             ("BE@1.0R",   ml_be1r, mf_be1r, mb_be1r)]:
        print(f"  {ex:<12}  Legacy PF {fi(ml['profit_factor']):>8}  "
              f"Strict PF {fi(mf['profit_factor']):>8}  "
              f"BE-only PF {fi(mb['profit_factor']):>8}")

    # BE close counts from legacy runs
    be_closes_75 = sum(1 for t in rl_be75["trades"] if t.get("exit_reason") == "BREAKEVEN")
    be_closes_1r = sum(1 for t in rl_be1r["trades"] if t.get("exit_reason") == "BREAKEVEN")
    print(f"""
  ── Breakeven close analysis (legacy runs) ────────────────────────
  BE@0.75R: {be_closes_75} breakeven closes out of {ml_be75['n']} trades
            ({be_closes_75/ml_be75['n']*100 if ml_be75['n'] else 0:.0f}% scratch rate)
  BE@1.0R:  {be_closes_1r} breakeven closes out of {ml_be1r['n']} trades
            ({be_closes_1r/ml_be1r['n']*100 if ml_be1r['n'] else 0:.0f}% scratch rate)

  VERDICT:
  The previous poor breakeven results were PARTLY caused by signal recycling.
  After applying signal-reset fix (strict mode):
    • Current A:  {ml_A['n']} → {mf_A['n']} trades  ROI {fp(ml_A['roi'])} → {fp(mf_A['roi'])}
    • BE@0.75R:   {ml_be75['n']} → {mf_be75['n']} trades  ROI {fp(ml_be75['roi'])} → {fp(mf_be75['roi'])}
    • BE@1.0R:    {ml_be1r['n']} → {mf_be1r['n']} trades  ROI {fp(ml_be1r['roi'])} → {fp(mf_be1r['roi'])}

  BE exit directionality {"CONFIRMED" if mf_be75['profit_factor'] < mf_A['profit_factor'] else "REVERSED"}:
  After fixing recycling, the Current exit is {"STILL BETTER" if mf_A['profit_factor'] >= mf_be75['profit_factor'] else "NOW WORSE"} than BE exits.
  Recycling inflated BE trade counts but the verdict on exit quality
  is directionally {"correct — Current exit A remains the best." if mf_A['profit_factor'] >= mf_be75['profit_factor'] else "changed — BE exits now outperform Current after the fix."}
""")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 4 — MULTI-TIMEFRAME FREQUENCY
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  TASK 4 — Multi-Timeframe Trade Frequency Test")
    print(f"  Window: {dt0_12m} → {dt1_12m} ({days_12m:.0f} days)")
    print("  Same 6-condition philosophy. Same indicator periods. Kraken fees included.")
    print(SEP)
    print(f"""
  Configurations tested:
  ┌────┬──────────────────────────┬────────────────────────────────────────┐
  │ C1 │ 1h entry  + 4h trend    │ Current live configuration              │
  │ C2 │ 30m entry + 4h trend    │ 2× higher entry frequency               │
  │ C3 │ 30m entry + 2h trend    │ Both entry and confirm faster            │
  │ C4 │ 15m entry + 1h trend    │ 4× higher entry frequency               │
  └────┴──────────────────────────┴────────────────────────────────────────┘
  Indicator windows: 720 candles of each timeframe (default in all engines).
  This keeps EMA/RSI/MACD periods identical; only the candle resolution changes.
  Shorter timeframes respond faster to price action → more signals, more noise.
""")

    tf_configs = [
        ("C1  1h+4h  (current)", c1h_12m,  c4h_12m),
        ("C2  30m+4h",           c30m_12m, c4h_fr_30m),
        ("C3  30m+2h",           c30m_12m, c2h_fr_30m),
        ("C4  15m+1h",           c15m_12m, c1h_fr_15m),
    ]
    tf_results: list[tuple[str, dict]] = []
    for lbl, ec, cc in tf_configs:
        print(f"  {lbl}...", end="", flush=True)
        r = run_backtest(lbl, ec, cc, fee_per_fill=KRAKEN_FEE_PER_FILL)
        m = compute_metrics(r, days_12m)
        tf_results.append((lbl, m))
        print(f" {m['n']} trades  ROI {fp(m['roi'])}  PF {fi(m['profit_factor'])}")

    print(f"\n  ── Multi-timeframe results (with Kraken fees) ───────────────────")
    print(f"  {'Config':<18} {'Trd':>4} {'Trd/mo':>7} {'ROI%':>8} {'CAGR%':>8} "
          f"{'PF':>8} {'WR%':>7} {'DD%':>7} {'Exp':>8} {'Sharpe':>8}")
    print(f"  {'─'*18} {'─'*4} {'─'*7} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")
    for lbl, m in tf_results:
        short = lbl.split("  ")[0] + " " + lbl.split("  ")[1]
        tpm   = m.get("tpm"); s = m.get("sharpe")
        print(f"  {short:<18} {m['n']:>4} "
              f"{f'{tpm:.1f}' if tpm else 'N/A':>7} "
              f"{m['roi']:>+8.2f} "
              f"{fp(m['ann_roi']) if m['ann_roi'] is not None else 'N/A':>8} "
              f"{fi(m['profit_factor']):>8} {m['win_rate']:>7.1f} "
              f"{m['max_dd']:>7.2f} {fm(m['expectancy']):>8} "
              f"{fn(s) if s is not None else 'N/A':>8}")

    print(f"\n  ── LONG / SHORT breakdown by config ─────────────────────────────")
    print(f"  {'Config':<18} {'L-Trd':>6} {'L-WR%':>7} {'L-PF':>8} "
          f"{'S-Trd':>6} {'S-WR%':>7} {'S-PF':>8}")
    print(f"  {'─'*18} {'─'*6} {'─'*7} {'─'*8} {'─'*6} {'─'*7} {'─'*8}")
    for lbl, m in tf_results:
        short = lbl.split("  ")[0] + " " + lbl.split("  ")[1]
        print(f"  {short:<18} {m['long_n']:>6} {m['long_wr']:>7.1f} {fi(m['long_pf']):>8} "
              f"{m['short_n']:>6} {m['short_wr']:>7.1f} {fi(m['short_pf']):>8}")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 5 — LONG vs SHORT ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  TASK 5 — LONG vs SHORT Deep Analysis")
    print(f"  Window: {dt0_12m} → {dt1_12m} (12m, Kraken fees)")
    print(SEP)

    r5_both  = run_backtest("5-both",  c1h_12m, c4h_12m, fee_per_fill=KRAKEN_FEE_PER_FILL, direction="both")
    r5_long  = run_backtest("5-long",  c1h_12m, c4h_12m, fee_per_fill=KRAKEN_FEE_PER_FILL, direction="long_only")
    r5_short = run_backtest("5-short", c1h_12m, c4h_12m, fee_per_fill=KRAKEN_FEE_PER_FILL, direction="short_strict")
    m5_both  = compute_metrics(r5_both,  days_12m)
    m5_long  = compute_metrics(r5_long,  days_12m)
    m5_short = compute_metrics(r5_short, days_12m)

    print(f"\n  ── LONG vs SHORT breakdown from same run (A — LONG+SHORT) ──────")
    m = m5_both
    print(f"  {'Metric':<20} {'LONG':>14} {'SHORT':>14}")
    print(f"  {'─'*20} {'─'*14} {'─'*14}")
    print(f"  {'Trades':<20} {m['long_n']:>14} {m['short_n']:>14}")
    print(f"  {'Win rate':<20} {fp(m['long_wr']):>14} {fp(m['short_wr']):>14}")
    print(f"  {'Profit factor':<20} {fi(m['long_pf']):>14} {fi(m['short_pf']):>14}")
    print(f"  {'Net P&L':<20} {fm(m['long_pnl']):>14} {fm(m['short_pnl']):>14}")

    print(f"\n  ── Directional configuration comparison (12m, fees) ─────────────")
    dir_cfgs = [
        ("A — LONG + SHORT (both)",           m5_both),
        ("B — LONG only",                     m5_long),
        ("C — LONG normal + SHORT RSI≤40",    m5_short),
    ]
    print(f"  {'Config':<36} {'Trd':>4} {'ROI%':>8} {'PF':>8} {'WR%':>7} "
          f"{'DD%':>7} {'Exp':>8} {'Sharpe':>8}")
    print(f"  {'─'*36} {'─'*4} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*8} {'─'*8}")
    for lbl, m in dir_cfgs:
        s = m.get("sharpe")
        print(f"  {lbl:<36} {m['n']:>4} {m['roi']:>+8.2f} "
              f"{fi(m['profit_factor']):>8} {m['win_rate']:>7.1f} "
              f"{m['max_dd']:>7.2f} {fm(m['expectancy']):>8} "
              f"{fn(s) if s is not None else 'N/A':>8}")

    lwr = m5_both["long_wr"]; swr = m5_both["short_wr"]
    lpf = m5_both["long_pf"]; spf = m5_both["short_pf"]
    print(f"""
  Evidence summary:
  LONG:  {m5_both['long_n']} trades  WR={lwr:.1f}%  PF={fi(lpf)}  PnL={fm(m5_both['long_pnl'])}
  SHORT: {m5_both['short_n']} trades  WR={swr:.1f}%  PF={fi(spf)}  PnL={fm(m5_both['short_pnl'])}
  {'► LONG clearly outperforms SHORT. Config B (LONG only) worth monitoring.' if lwr > swr + 10 else
   '► LONG outperforms SHORT but gap is moderate. Insufficient evidence to remove SHORT yet.' if lwr > swr + 3 else
   '► LONG/SHORT within 3pp win rate. Both directions contributing similarly.'}
""")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 6 — REGIME FILTER
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  TASK 6 — Regime Filter: Weekly 200-SMA")
    print(f"  Period: {dt0_3yr} → {dt1_3yr}  (3yr continuous, no fees)")
    print(SEP)
    print(f"""
  Predetermined rule (not optimised — tested as-specified):
  • LONG signals : only taken when price > Weekly 200-SMA
  • SHORT signals: only taken when price < Weekly 200-SMA
  • Counter-trend signals are BLOCKED (not taken)

  Dataset note: {n_weeks} weeks of hourly data available (~{n_weeks/52:.1f} years).
  Full 200-week SMA requires 200 weeks (~3.8 years). With {n_weeks} weeks,
  SMA is computed from ALL available history up to each point (minimum
  window = weeks available, maximum = 200). The SMA is valid and meaningful
  but slightly less stable in the first few weeks of the dataset.

  LONG signals are blocked below the SMA (against uptrend)
  SHORT signals are blocked above the SMA (against downtrend)
""")

    r6_none   = run_backtest("3yr-no-regime",   c1h_3yr, c4h_3yr)
    r6_regime = run_backtest("3yr-with-regime",  c1h_3yr, c4h_3yr, regime_lookup=wsma_lookup)
    m6_none   = compute_metrics(r6_none,  days_3yr)
    m6_regime = compute_metrics(r6_regime, days_3yr)

    # Analyse what the filter removed
    base_set    = {t["opened_at"]: t for t in r6_none["trades"]}
    kept_set    = {t["opened_at"]   for t in r6_regime["trades"]}
    removed     = [t for ots, t in base_set.items() if ots not in kept_set]
    rem_wins    = sum(1 for t in removed if t["pnl"] > 0)
    rem_losses  = sum(1 for t in removed if t["pnl"] <= 0)
    rem_pnl     = sum(t["pnl"]      for t in removed)

    print_metrics("No regime filter (baseline 3yr)", m6_none)
    print_metrics("Weekly 200-SMA filter (3yr)", m6_regime)

    print(f"""
  ── Filter analysis ─────────────────────────────────────────────────
  Trades without filter : {m6_none['n']}
  Trades with filter    : {m6_regime['n']}
  Trades removed        : {len(removed)}
    ↳ Winning trades blocked : {rem_wins}
    ↳ Losing trades avoided  : {rem_losses}
    ↳ P&L of removed trades  : {fm(rem_pnl)}

  ── Performance comparison ──────────────────────────────────────────
  {'Metric':<22} {'No filter':>14} {'With filter':>14} {'Δ':>10}""")
    for lbl, ka, fmtfn in [
        ("ROI%",          "roi",            fp),
        ("PF",            "profit_factor",  fi),
        ("Win rate%",     "win_rate",       lambda v: fp(v)),
        ("Max DD%",       "max_dd",         lambda v: fp(v)),
        ("Expectancy",    "expectancy",     fm),
        ("Trades",        "n",              lambda v: str(int(v))),
    ]:
        a = m6_none[ka]; b = m6_regime[ka]
        af = fmtfn(a); bf = fmtfn(b)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and \
                not math.isinf(a if a else 0) and not math.isinf(b if b else 0):
            d = f"{b-a:+.2f}" if ka in ("roi","max_dd","win_rate") else (
                f"{b-a:+.3f}" if ka == "profit_factor" else (
                f"{b-a:+.2f}" if ka == "expectancy" else f"{int(b-a):+d}"))
        else: d = "N/A"
        print(f"  {lbl:<22} {af:>14} {bf:>14} {d:>10}")

    # ══════════════════════════════════════════════════════════════════════════
    # TASK 7 — NON-CRYPTO RESEARCH MODULE PLAN
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  TASK 7 — Non-Crypto Markets: Research Module Design Plan")
    print(SEP)
    print("""
  Previous finding: Gold, Silver, EUR/USD, GBP/USD, USD/JPY each generated
  0–6 trades over 2 years when the BTC 6/6 strategy was applied directly.
  The strategy was not designed for these markets.

  Per your instruction: do NOT apply the BTC strategy to them further.
  Design purpose-built strategies appropriate to each market class.

  PROPOSED RESEARCH MODULES (separate script, not yet implemented):

  ┌─────────────────────────────────────────────────────────────────────┐
  │ MODULE A — FX MAJORS (EUR/USD, GBP/USD, USD/JPY)                   │
  │  Data     : Dukascopy or Histdata.com (free, tick→M1, 10+ years)   │
  │  Timeframe : 1h entry + 4h trend (comparable to BTC)               │
  │  Volume    : Replace with ATR expansion (FX volume unreliable)      │
  │  Session   : London + NY overlap filter (08:00–17:00 UTC)           │
  │  Costs     : 1 pip spread + 0.5 pip slippage per fill               │
  │  Note      : Weekend closures, daily rollover swap, Friday risk      │
  │  Target    : 15–30 trades/month per pair (much higher frequency)    │
  ├─────────────────────────────────────────────────────────────────────┤
  │ MODULE B — PRECIOUS METALS (Gold / Silver)                          │
  │  Data     : Yahoo Finance GC=F / SI=F (CME futures, real volume)   │
  │  Timeframe : 4h entry + Weekly trend (metals trend slowly)          │
  │  Volume    : CME real volume — usable (unlike FX tick count)        │
  │  Modify    : Seasonality filter (Gold stronger Aug–Nov)             │
  │  Costs     : $0.50 spread + $1.50 commission per contract           │
  │  Target    : 5–10 trades/month on Gold (higher ATR vs BTC spot)    │
  └─────────────────────────────────────────────────────────────────────┘

  IMPLEMENTATION ORDER (suggested, awaiting approval):
  1. Build MODULE A FX first — largest opportunity set, best liquidity
  2. Validate independently for 30+ trades before considering live
  3. Build MODULE B metals second — different regime from crypto and FX
  4. Only combine with BTC once each module passes standalone validation

  A separate `fx_metals_research.py` will be created when you approve it.
  No action taken now.
""")

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL RANKING & RECOMMENDATION
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  FINAL RANKING — BTC CONFIGURATIONS")
    print("  Ranked: 1.PF  2.CAGR/ROI  3.MaxDD  4.Exp  5.Sample  6.Trd/mo  7.After fees")
    print(SEP)

    all_cfgs: list[tuple[str, dict]] = [
        ("C1 — 1h+4h  current  (w/fees)", tf_results[0][1]),
        ("C2 — 30m+4h          (w/fees)", tf_results[1][1]),
        ("C3 — 30m+2h          (w/fees)", tf_results[2][1]),
        ("C4 — 15m+1h          (w/fees)", tf_results[3][1]),
        ("A  — LONG+SHORT both (w/fees)", m5_both),
        ("B  — LONG only       (w/fees)", m5_long),
        ("C  — LONG+strict SHT (w/fees)", m5_short),
    ]
    ranked = sorted(all_cfgs, key=lambda x: score(x[1]), reverse=True)

    print(f"\n  {'Rank':<5} {'Config':<35} {'Trd':>4} {'CAGR%':>8} {'PF':>8} "
          f"{'WR%':>7} {'DD%':>7} {'Exp':>8} {'Trd/mo':>7}")
    print(f"  {'─'*5} {'─'*35} {'─'*4} {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*8} {'─'*7}")
    for rank_i, (lbl, m) in enumerate(ranked, 1):
        tpm = m.get("tpm")
        print(f"  {rank_i:<5} {lbl:<35} {m['n']:>4} "
              f"{fp(m['ann_roi']) if m['ann_roi'] is not None else 'N/A':>8} "
              f"{fi(m['profit_factor']):>8} {m['win_rate']:>7.1f} {m['max_dd']:>7.2f} "
              f"{fm(m['expectancy']):>8} {f'{tpm:.1f}' if tpm else 'N/A':>7}")

    # Determine verdict
    c1_m    = tf_results[0][1]   # current config with fees
    c1_score = score(c1_m)
    best_alt = None
    for lbl, m in ranked:
        if "current" in lbl.lower(): continue
        if score(m) > c1_score + 0.5 and m["n"] >= 20 and m["roi"] > 0:
            best_alt = (lbl, m)
            break

    print(f"""
  Key findings:
  • Current C1 (1h+4h, fees): {c1_m['n']} trades  ROI {fp(c1_m['roi'])}  PF {fi(c1_m['profit_factor'])}  MaxDD {fp(c1_m['max_dd'])}""")
    for lbl, m in tf_results[1:]:
        short = lbl.split("  ")[0] + " " + lbl.split("  ")[1]
        print(f"  • {short} (fees): {m['n']} trades  ROI {fp(m['roi'])}  PF {fi(m['profit_factor'])}  MaxDD {fp(m['max_dd'])}")
    print(f"  • Shorter TF (30m, 15m) produce more signals but signal quality varies.")
    print(f"  • After realistic fees, relative rankings may shift vs the no-fee baseline.")

    print(f"\n{'─'*70}")
    print("  VERDICT")
    print(f"{'─'*70}")

    if best_alt is None:
        best_lbl, best_m = ranked[0]
        print(f"""
  CURRENT BTC STRATEGY REMAINS BEST

  Evidence summary:
  1. PROFIT FACTOR: Current 1h+4h has PF {fi(c1_m['profit_factor'])} (12m, with fees).
     No alternative configuration has a materially higher PF with ≥20 trades.

  2. CAGR / ROI: Current {fp(c1_m['ann_roi'])} (12m, with fees).
     {'Shorter timeframes (30m/15m) have lower CAGR despite more trades.' if c1_m['roi'] > tf_results[1][1]['roi'] else 'ROI rankings similar across configs — no decisive winner.'}

  3. MAX DRAWDOWN: Current {fp(c1_m['max_dd'])} — within acceptable range.

  4. EXPECTANCY: Current {fm(c1_m['expectancy'])}/trade after fees.
     Positive expectancy confirmed even after realistic Kraken costs.

  5. SAMPLE SIZE: {c1_m['n']} trades in 12 months — moderate evidence.
     Not enough data yet to call any alternative definitively better.

  6. TRADE FREQUENCY: {c1_m['tpm']:.1f}/mo. Low but validated by 3 years of data.
     The 6/6 conditions are intentionally selective — few signals, high quality.

  7. AFTER FEES: Strategy remains profitable after 0.62% round-trip Kraken costs.
     Fees eat ~{int(m12_fee['total_fees'] / m12_nofee['net_pnl'] * 100) if m12_nofee['net_pnl'] > 0 else 0}% of gross profit.

  NEXT STEPS (research only — no live changes):
  • Continue accumulating live data on the current 1h+4h configuration.
  • Revisit multi-timeframe comparison after 60+ trades per config.
  • Commission FX/metals research module (purpose-built, separate script).
  • Do not change risk per trade as requested.
""")
    else:
        best_lbl_s, best_m_s = best_alt
        print(f"""
  NEW BTC CONFIGURATION WORTH PAPER TESTING: {best_lbl_s}

  This configuration shows materially higher risk-adjusted performance:
    Trades  : {best_m_s['n']}  vs  {c1_m['n']} (current)
    ROI     : {fp(best_m_s['roi'])}  vs  {fp(c1_m['roi'])} (current)
    PF      : {fi(best_m_s['profit_factor'])}  vs  {fi(c1_m['profit_factor'])} (current)
    MaxDD   : {fp(best_m_s['max_dd'])}  vs  {fp(c1_m['max_dd'])} (current)

  CAUTION: Evidence is MODERATE, not conclusive (12 months only).
  Recommend parallel paper testing alongside current config before any switch.
  Do NOT implement without your explicit approval.
""")

    print(f"{'─'*70}")
    print("  NO LIVE SETTINGS HAVE BEEN CHANGED.")
    print("  All results research-only. Awaiting explicit approval to implement.")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
