#!/usr/bin/env python3
"""
opportunity_research.py
=======================
NEW STRATEGY RESEARCH: Opportunity-Based System

Objective
─────────
Move from "wait for perfect 6/6 alignment" to "capture more quality
opportunities while maintaining positive expectancy and controlled risk."

What is tested
──────────────
Scoring:        Weighted 8-point system; thresholds 6/8 and 7/8
Timeframes:     A (30m/4h)  B (30m/2h)  C (15m/1h)  D (15m/4h)
Strategy types: 1. Trend Continuation  2. Pullback  3. Breakout
Markets:        BTC ETH SOL XRP LINK (Binance/USDT)
                Gold Silver (Yahoo Finance / GC=F, SI=F)
Validation:     70 % in-sample development / 30 % out-of-sample test
Costs:          Kraken 0.62 % round-trip for crypto; 0.30 % for metals
Risk:           1 % per trade | ATR×1.5 stop | 2R target (unchanged)

Hard safety conditions enforced
────────────────────────────────
• No LONG if higher-timeframe trend is clearly BEARISH
• No SHORT if higher-timeframe trend is clearly BULLISH
• No trade if ATR is abnormally extreme (>3× 20-period average)
• Cooldown after every close: score must reset + ≥4 entry-TF candles elapsed

The existing live paper_trader.py is NOT changed.
Usage: python3 opportunity_research.py
"""

from __future__ import annotations

import json
import math
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

# ─── Constants ─────────────────────────────────────────────────────────────────

RISK_PER_TRADE         = 0.01
REWARD_TO_RISK         = 2.0
ATR_MULTIPLIER         = 1.5
STARTING_BALANCE       = 100.0
DAILY_LOSS_LIMIT       = 0.03
MAX_CONSECUTIVE_LOSSES = 3

# Fees
CRYPTO_FEE_PER_FILL = 0.0026 + 0.0005   # Kraken taker 0.26% + slippage 0.05%
METALS_FEE_PER_FILL = 0.0010 + 0.0005   # Spread 0.10% + slippage 0.05%

# IS / OOS split timestamps (UTC midnight)
# Crypto: ~70% of Aug 2023 → Aug 2026 ≈ Oct 2025
# Metals: ~70% of ~2yr Yahoo data ≈ Nov 2025
IS_SPLIT_CRYPTO = 1759276800  # 2025-10-01 00:00 UTC
IS_SPLIT_METALS = 1761955200  # 2025-11-01 00:00 UTC

# Window size for indicator computation (candles of entry TF)
WIN = 720

# Thresholds to test
THRESHOLDS = [6, 7]

# Cooldown: minimum candles after a close before re-entry is allowed
COOLDOWN_MIN_CANDLES = 4
COOLDOWN_FORCE_AFTER = 12   # allow re-entry regardless of score after this many candles


# ─── Indicators (identical to live paper_trader.py) ────────────────────────────

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


# ─── MACD and ATR history helpers ──────────────────────────────────────────────

def _macd_signal_series(rows: list[list[Any]]) -> tuple[list, list]:
    """Return full MACD and Signal series aligned to rows."""
    closes = [float(r[4]) for r in rows]
    e12 = ema_series(closes, 12); e26 = ema_series(closes, 26)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(e12, e26)]
    mv = [v for v in macd if v is not None]
    sv = ema_series(mv, 9)
    ms = [None] * len(macd); si = 0
    for idx, v in enumerate(macd):
        if v is not None: ms[idx] = sv[si]; si += 1
    return macd, ms


def macd_crossed_up_recently(rows: list[list[Any]], within: int = 5) -> bool:
    """True if MACD crossed above Signal in the last `within` candles."""
    macd, ms = _macd_signal_series(rows)
    L = len(macd) - 1
    for j in range(max(1, L - within + 1), L + 1):
        m0, s0, m1, s1 = macd[j-1], ms[j-1], macd[j], ms[j]
        if None not in (m0, s0, m1, s1) and m0 < s0 and m1 >= s1:
            return True
    return False


def macd_crossed_down_recently(rows: list[list[Any]], within: int = 5) -> bool:
    """True if MACD crossed below Signal in the last `within` candles."""
    macd, ms = _macd_signal_series(rows)
    L = len(macd) - 1
    for j in range(max(1, L - within + 1), L + 1):
        m0, s0, m1, s1 = macd[j-1], ms[j-1], macd[j], ms[j]
        if None not in (m0, s0, m1, s1) and m0 > s0 and m1 <= s1:
            return True
    return False


def atr_recent_vals(rows: list[list[Any]], n: int = 20) -> list[float]:
    """Return last n ATR(14) values."""
    closes = [float(r[4]) for r in rows]
    highs  = [float(r[2]) for r in rows]
    lows   = [float(r[3]) for r in rows]
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - (closes[i-1] if i else closes[i])),
               abs(lows[i]  - (closes[i-1] if i else closes[i])))
           for i in range(len(rows))]
    valid = [v for v in ema_series(trs, 14) if v is not None]
    return valid[-n:]


# ─── Pre-computed indicator arrays (fast path for backtesting) ─────────────────
#
#  Instead of recomputing all indicators from a sliding window on every candle,
#  we compute the full series once (O(n)) and look up values by index (O(1)).
#  This gives ~50× speed-up over the window-based approach.

def build_pre(candles: list) -> dict:
    """Pre-compute ALL indicator series and derived lookups for a candle array.

    Everything that would be a loop inside the backtest hot-path is computed
    here once (O(n)) so the backtest itself does only O(1) lookups per candle.
    Call once per unique candle series and reuse across all configs.
    """
    n      = len(candles)
    ts     = [int(c[0])   for c in candles]
    closes = [float(c[4]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    vols   = [float(c[6]) for c in candles]

    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)

    macd_l = [f - s if f is not None and s is not None else None
               for f, s in zip(e12, e26)]
    mv = [v for v in macd_l if v is not None]
    sv = ema_series(mv, 9)
    ms: list = [None] * n; si = 0
    for idx, v in enumerate(macd_l):
        if v is not None: ms[idx] = sv[si]; si += 1

    trs = [0.0]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i-1]),
                       abs(lows[i]  - closes[i-1])))
    atr = ema_series(trs, 14)
    rsi = rsi_series(closes)

    # Volume MA20 — list comprehension (C-speed max/sum on slices)
    vol_ma: list = [None] * n
    for i in range(19, n):
        vol_ma[i] = sum(vols[i-19:i+1]) / 20

    # ── Trend ("B"=BULLISH, "R"=BEARISH, "N"=NEUTRAL) ──────────────────────────
    trend = ["N"] * n   # plain list of single chars — bytearray comparison is broken
    for i in range(n):
        e2, e5, ml, sl_ = e20[i], e50[i], macd_l[i], ms[i]
        if None in (e2, e5, ml, sl_): continue
        cl = closes[i]
        if cl > e2 > e5 and ml > sl_: trend[i] = "B"
        elif cl < e2 < e5 and ml < sl_: trend[i] = "R"

    # ── Rolling 20-period range high/low — O(n) deque sliding window ────────────
    # high20[i] = max(highs[max(0, i-20):i])  (prior 20, excluding candle i)
    # low20[i]  = min(lows[max(0,  i-20):i])
    from collections import deque as _deque
    high20 = [0.0] * n
    low20  = [0.0] * n
    dq_h: deque = _deque()   # indices of max candidates (descending value order)
    dq_l: deque = _deque()   # indices of min candidates (ascending value order)
    for i in range(n):
        # Evict indices that have left the window [i-20, i)
        while dq_h and dq_h[0] < i - 20: dq_h.popleft()
        while dq_l and dq_l[0] < i - 20: dq_l.popleft()
        # Record max/min of the PRIOR window before adding candle i
        high20[i] = highs[dq_h[0]] if dq_h else 0.0
        low20[i]  = lows[dq_l[0]]  if dq_l else 0.0
        # Add candle i as a candidate for future candles
        while dq_h and highs[dq_h[-1]] <= highs[i]: dq_h.pop()
        dq_h.append(i)
        while dq_l and lows[dq_l[-1]] >= lows[i]: dq_l.pop()
        dq_l.append(i)

    # ── Rolling 20-period ATR mean — O(n) running accumulator ───────────────────
    # atr_ma20[i] = mean(atr[max(0, i-20):i])  (prior 20, excluding candle i)
    atr_ma20: list = [None] * n
    _win: deque = _deque()
    _win_sum = 0.0
    for i in range(n):
        # Compute from current window (which covers [max(0,i-20):i]) BEFORE adding i
        if _win:
            atr_ma20[i] = _win_sum / len(_win)
        if atr[i] is not None:
            _win.append(atr[i])
            _win_sum += atr[i]
            if len(_win) > 20:
                _win_sum -= _win.popleft()

    # ── MACD cross within-5 flags: find all crossings once, then fan out ────────
    cross_up = bytearray(n)   # 0/1 bytes
    cross_dn = bytearray(n)
    for k in range(1, n):
        m0, s0, m1, s1 = macd_l[k-1], ms[k-1], macd_l[k], ms[k]
        if None not in (m0, s0, m1, s1):
            if m0 < s0 and m1 >= s1:
                for j in range(k, min(k + 5, n)): cross_up[j] = 1
            if m0 > s0 and m1 <= s1:
                for j in range(k, min(k + 5, n)): cross_dn[j] = 1

    # ── Day number (avoids datetime.fromtimestamp in hot loop) ──────────────────
    day_num = [t // 86400 for t in ts]

    return {
        "ts": ts, "closes": closes, "highs": highs, "lows": lows, "vols": vols,
        "e20": e20, "e50": e50,
        "macd": macd_l, "macd_sig": ms,
        "atr": atr, "rsi": rsi, "vol_ma": vol_ma, "n": n,
        "trend": trend,
        "high20": high20, "low20": low20, "atr_ma20": atr_ma20,
        "cross_up": cross_up, "cross_dn": cross_dn,
        "day_num": day_num,
    }


def _trend_at(pre: dict, idx: int) -> str:
    """O(1) trend lookup from pre-computed trend array."""
    t = pre["trend"][idx]
    if t == "B": return "BULLISH"
    if t == "R": return "BEARISH"
    return "NEUTRAL"


def _compute_score_fast(direction: str,
                         pre_e: dict, ei: int,
                         pre_c: dict, ci: int) -> tuple:
    """O(1) scoring using pre-computed arrays.  Logic identical to compute_score."""
    htf   = _trend_at(pre_c, ci)
    entry = _trend_at(pre_e, ei)

    if direction == "LONG"  and htf == "BEARISH": return -1, {}
    if direction == "SHORT" and htf == "BULLISH": return -1, {}

    cl   = pre_e["closes"][ei]
    rsi  = pre_e["rsi"][ei]
    macd = pre_e["macd"][ei]
    sig  = pre_e["macd_sig"][ei]
    e20  = pre_e["e20"][ei]
    e50  = pre_e["e50"][ei]
    vol  = pre_e["vols"][ei]
    avgv = pre_e["vol_ma"][ei]

    if direction == "LONG":
        htf_pts  = 2 if htf   == "BULLISH" else 0
        ent_pts  = 2 if entry == "BULLISH" else 0
        macd_pts = 1 if (macd is not None and sig is not None and macd > sig) else 0
        rsi_pts  = 1 if (rsi  is not None and rsi >= 50) else 0
        ema_pts  = 1 if (e20  is not None and e50 is not None and cl > e20 > e50) else 0
        vol_pts  = 1 if (avgv is not None and avgv > 0 and vol >= avgv * 0.8) else 0
    else:
        htf_pts  = 2 if htf   == "BEARISH" else 0
        ent_pts  = 2 if entry == "BEARISH" else 0
        macd_pts = 1 if (macd is not None and sig is not None and macd < sig) else 0
        rsi_pts  = 1 if (rsi  is not None and rsi <= 50) else 0
        ema_pts  = 1 if (e20  is not None and e50 is not None and cl < e20 < e50) else 0
        vol_pts  = 1 if (avgv is not None and avgv > 0 and vol >= avgv * 0.8) else 0

    return htf_pts + ent_pts + macd_pts + rsi_pts + ema_pts + vol_pts, {}


def _detect_pullback_fast(pre_e: dict, ei: int,
                           pre_c: dict, ci: int) -> str | None:
    """O(1) pullback detection using pre-computed arrays."""
    htf_t = pre_c["trend"][ci]
    if htf_t == "N": return None   # must have clear HTF trend

    cl   = pre_e["closes"][ei]
    e50  = pre_e["e50"][ei]
    rsi  = pre_e["rsi"][ei]
    atr  = pre_e["atr"][ei]
    vol  = pre_e["vols"][ei]
    avgv = pre_e["vol_ma"][ei]

    if None in (e50, rsi, atr, avgv) or avgv <= 0 or atr <= 0: return None

    # ATR spike safety (pre-computed 20-period ATR mean)
    avg_atr = pre_e["atr_ma20"][ei]
    if avg_atr is not None and avg_atr > 0 and atr > avg_atr * 3.0: return None

    if htf_t == "B":   # BULLISH → look for LONG pullback
        if cl <= e50:             return None
        if not (35 <= rsi <= 58): return None
        if not pre_e["cross_up"][ei]: return None
        if vol < avgv * 0.50:     return None
        return "LONG"
    else:              # BEARISH → look for SHORT pullback
        if cl >= e50:             return None
        if not (42 <= rsi <= 65): return None
        if not pre_e["cross_dn"][ei]: return None
        if vol < avgv * 0.50:     return None
        return "SHORT"


def _detect_breakout_fast(pre_e: dict, ei: int,
                           pre_c: dict, ci: int) -> str | None:
    """O(1) breakout detection using pre-computed arrays."""
    if ei < 21: return None
    cl   = pre_e["closes"][ei]
    vol  = pre_e["vols"][ei]
    atr  = pre_e["atr"][ei]
    avgv = pre_e["vol_ma"][ei]
    if None in (atr, avgv) or avgv <= 0: return None

    # All pre-computed — just index lookups
    avg_atr  = pre_e["atr_ma20"][ei]
    range_hi = pre_e["high20"][ei]
    range_lo = pre_e["low20"][ei]

    if avg_atr is None or avg_atr <= 0: return None
    if atr > avg_atr * 3.0:             return None

    htf_t  = pre_c["trend"][ci]
    vol_ok = (vol >= avgv * 1.30)
    atr_ok = (atr >= avg_atr * 1.10)
    if not (vol_ok and atr_ok): return None

    if cl >= range_hi and htf_t != "R":
        sc, _ = _compute_score_fast("LONG", pre_e, ei, pre_c, ci)
        if sc >= 3: return "LONG"
    if cl <= range_lo and htf_t != "B":
        sc, _ = _compute_score_fast("SHORT", pre_e, ei, pre_c, ci)
        if sc >= 3: return "SHORT"
    return None


def _get_signal_fast(pre_e: dict, ei: int, pre_c: dict, ci: int,
                      threshold: int = 6, direction_filter: str = "both") -> tuple:
    """Fast signal generation.  Priority: TREND → PULLBACK → BREAKOUT."""
    lng_sc, _ = _compute_score_fast("LONG",  pre_e, ei, pre_c, ci)
    sht_sc, _ = _compute_score_fast("SHORT", pre_e, ei, pre_c, ci)

    if lng_sc >= threshold and direction_filter in ("both", "long_only"):
        return "LONG",  "TREND"
    if sht_sc >= threshold and direction_filter in ("both", "short_only"):
        return "SHORT", "TREND"

    pb = _detect_pullback_fast(pre_e, ei, pre_c, ci)
    if pb == "LONG"  and direction_filter in ("both", "long_only"):  return "LONG",  "PULLBACK"
    if pb == "SHORT" and direction_filter in ("both", "short_only"): return "SHORT", "PULLBACK"

    bo = _detect_breakout_fast(pre_e, ei, pre_c, ci)
    if bo == "LONG"  and direction_filter in ("both", "long_only"):  return "LONG",  "BREAKOUT"
    if bo == "SHORT" and direction_filter in ("both", "short_only"): return "SHORT", "BREAKOUT"

    return None, "NO_TRADE"


def _evaluate_66_fast(pre_e: dict, ei: int, pre_c: dict, ci: int) -> str | None:
    """Fast 6/6 evaluation.  Logic identical to evaluate_66."""
    cl   = pre_e["closes"][ei]
    avgv = pre_e["vol_ma"][ei] or 0
    htf  = _trend_at(pre_c, ci)
    ent  = _trend_at(pre_e, ei)
    rsi  = pre_e["rsi"][ei]
    macd = pre_e["macd"][ei]
    sig  = pre_e["macd_sig"][ei]
    e20  = pre_e["e20"][ei]
    e50  = pre_e["e50"][ei]
    vol  = pre_e["vols"][ei]

    if   htf == "BULLISH" or ent == "BULLISH": bias = "LONG"
    elif htf == "BEARISH" or ent == "BEARISH": bias = "SHORT"
    else: return None

    if bias == "LONG":
        passes = [htf == "BULLISH", ent == "BULLISH",
                  rsi  is not None and rsi >= 50,
                  macd is not None and sig is not None and macd > sig,
                  e20 and e50 and cl > e20 > e50,
                  avgv > 0 and vol and vol >= avgv * 0.7]
    else:
        passes = [htf == "BEARISH", ent == "BEARISH",
                  rsi  is not None and rsi <= 50,
                  macd is not None and sig is not None and macd < sig,
                  e20 and e50 and cl < e20 < e50,
                  avgv > 0 and vol and vol >= avgv * 0.7]
    return bias if all(passes) else None


def _open_position_fast(balance: float, ccl: float, atr_val: float,
                         direction: str, fee_per_fill: float, cts: int) -> tuple:
    """Open a new position using a pre-looked-up ATR value."""
    if atr_val is None or atr_val <= 0: return None, balance
    stop_dist   = atr_val * ATR_MULTIPLIER
    risk_amount = balance * RISK_PER_TRADE
    quantity    = min(risk_amount / stop_dist,
                      balance / ccl if ccl > 0 else 0)
    if quantity <= 0: return None, balance
    sl0 = ccl - stop_dist if direction == "LONG" else ccl + stop_dist
    tp0 = (ccl + stop_dist * REWARD_TO_RISK if direction == "LONG"
           else ccl - stop_dist * REWARD_TO_RISK)
    notional = ccl * quantity
    fee_cost = notional * fee_per_fill
    balance -= fee_cost
    pos = {"direction": direction, "entry": ccl,
           "stop_loss": sl0, "take_profit": tp0,
           "quantity": quantity, "opened_at": cts, "entry_fee": fee_cost}
    return pos, balance


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
        except Exception:
            if attempt == retries - 1: raise
            _time.sleep(2 ** attempt)


def fetch_binance(symbol: str, days: int, interval: str = "1h") -> list[list[Any]]:
    """Fetch OHLCV from Binance (any interval: 15m, 30m, 1h, 2h, 4h)."""
    iv_s = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}[interval]
    all_rows: dict[int, list] = {}
    now_ms   = int(_time.time() * 1000)
    start_ms = now_ms - days * 24 * 3600 * 1000
    for _ in range(250):
        url  = (f"https://api.binance.com/api/v3/klines"
                f"?symbol={symbol}&interval={interval}&limit=1000&startTime={start_ms}")
        data = _fetch(url)
        if not isinstance(data, list) or not data: break
        for row in data:
            ts = int(row[0]) // 1000
            cl = float(row[4])
            if cl > 0:
                all_rows[ts] = [ts, float(row[1]), float(row[2]),
                                 float(row[3]), cl, 0.0, float(row[5]), 0]
        last_ms = int(data[-1][0])
        if last_ms >= now_ms - iv_s * 1000: break
        start_ms = last_ms + iv_s * 1000
        _time.sleep(0.12)
    rows = sorted(all_rows.values(), key=lambda r: r[0])
    return rows[:-1] if len(rows) > 1 else rows


def fetch_yahoo(symbol: str, days: int) -> list[list[Any]]:
    """Fetch 1h OHLCV from Yahoo Finance (max ~730 days).
    Tries multiple endpoints/hosts; returns [] on failure.
    """
    import urllib.error
    end_ts   = int(_time.time())
    start_ts = end_ts - days * 86400

    urls = [
        (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
         f"?interval=1h&period1={start_ts}&period2={end_ts}&events=history"),
        (f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
         f"?interval=1h&period1={start_ts}&period2={end_ts}&events=history"),
    ]
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Referer": "https://finance.yahoo.com/",
    }

    data = None
    for url in urls:
        for attempt in range(2):
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=10) as r:
                    data = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 422, 429):
                    _time.sleep(2 * (attempt + 1))
                    continue
                break
            except Exception:
                _time.sleep(1)
        if data:
            break

    if not data:
        return []

    res = data.get("chart", {}).get("result", [])
    if not res: return []
    ch    = res[0]
    tss   = ch.get("timestamp", [])
    q     = ch.get("indicators", {}).get("quote", [{}])[0]
    opens = q.get("open",   [None] * len(tss))
    highs = q.get("high",   [None] * len(tss))
    lows  = q.get("low",    [None] * len(tss))
    closes= q.get("close",  [None] * len(tss))
    vols  = q.get("volume", [None] * len(tss))
    rows  = []
    for i, ts in enumerate(tss):
        o, h, lo, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        if c is None or h is None or lo is None: continue
        rows.append([int(ts), float(o or c), float(h), float(lo),
                     float(c), 0.0, float(v or 0), 0])
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


# ─── Weighted scoring (0–8 per direction) ──────────────────────────────────────
#
#  HTF trend    : 2 pts   (BULLISH/BEARISH for LONG/SHORT respectively)
#  Entry trend  : 2 pts   (same logic)
#  MACD momentum: 1 pt
#  RSI          : 1 pt    (≥50 for LONG, ≤50 for SHORT)
#  EMA structure: 1 pt    (cl>E20>E50 for LONG; cl<E20<E50 for SHORT)
#  Volume       : 1 pt    (≥80% of 20-period average)
#
#  Hard safety  : LONG blocked if HTF=BEARISH | SHORT blocked if HTF=BULLISH

def compute_score(direction: str,
                  entry_rows: list[list[Any]],
                  confirm_rows: list[list[Any]]) -> tuple[int, dict]:
    """
    Return (score_0_to_8, indicator_snap).
    Includes hard safety: returns -1 if the HTF strongly opposes direction.
    """
    if len(entry_rows) < 55 or len(confirm_rows) < 55:
        return 0, {}
    snap  = indicator_snapshot(entry_rows)
    htf   = trend_for(confirm_rows)
    entry = trend_for(entry_rows)
    cl    = float(entry_rows[-1][4])
    rsi   = snap.get("rsi")
    macd  = snap.get("macd")
    sig   = snap.get("macdSignal")
    e20   = snap.get("ema20")
    e50   = snap.get("ema50")
    vol   = snap.get("volume")
    avgv  = snap.get("_avg_volume") or 0.0

    if direction == "LONG":
        if htf == "BEARISH": return -1, snap          # hard safety
        htf_pts  = 2 if htf   == "BULLISH" else 0
        ent_pts  = 2 if entry == "BULLISH" else 0
        macd_pts = 1 if (macd and sig and macd > sig) else 0
        rsi_pts  = 1 if (rsi is not None and rsi >= 50) else 0
        ema_pts  = 1 if (e20 and e50 and cl > e20 > e50) else 0
        vol_pts  = 1 if (avgv > 0 and vol and vol >= avgv * 0.8) else 0
    else:  # SHORT
        if htf == "BULLISH": return -1, snap          # hard safety
        htf_pts  = 2 if htf   == "BEARISH" else 0
        ent_pts  = 2 if entry == "BEARISH" else 0
        macd_pts = 1 if (macd and sig and macd < sig) else 0
        rsi_pts  = 1 if (rsi is not None and rsi <= 50) else 0
        ema_pts  = 1 if (e20 and e50 and cl < e20 < e50) else 0
        vol_pts  = 1 if (avgv > 0 and vol and vol >= avgv * 0.8) else 0

    return htf_pts + ent_pts + macd_pts + rsi_pts + ema_pts + vol_pts, snap


# ─── Strategy type detectors ───────────────────────────────────────────────────

def detect_pullback(entry_rows: list[list[Any]],
                    confirm_rows: list[list[Any]]) -> str | None:
    """
    PULLBACK entry: retrace within an established HTF trend, with momentum
    beginning to return in the trend direction.

    LONG pullback requires:
      • HTF trend BULLISH (mandatory — no pullback in neutral/bearish HTF)
      • Close > EMA50 (price still above longer-term average — not too deep)
      • RSI 35–58  (pulled back from overbought, not yet deeply oversold)
      • MACD crossed above Signal in last 5 candles (momentum returning)
      • Volume ≥ 50% of average (lower volume acceptable during pullback)
      • No extreme ATR spike

    SHORT pullback is the mirror.
    Returns "LONG", "SHORT", or None.
    """
    if len(entry_rows) < 55 or len(confirm_rows) < 55:
        return None
    htf = trend_for(confirm_rows)
    if htf not in ("BULLISH", "BEARISH"):
        return None
    snap = indicator_snapshot(entry_rows)
    cl   = float(entry_rows[-1][4])
    e50  = snap.get("ema50")
    rsi  = snap.get("rsi")
    atr  = snap.get("atr")
    vol  = snap.get("volume")
    avgv = snap.get("_avg_volume") or 0.0
    if None in (e50, rsi, atr, vol) or avgv <= 0 or atr <= 0:
        return None
    # ATR safety: no extreme volatility spike
    atr_hist = atr_recent_vals(entry_rows, 20)
    if len(atr_hist) >= 5:
        avg_atr = sum(atr_hist[:-1]) / max(1, len(atr_hist) - 1)
        if avg_atr > 0 and atr > avg_atr * 3.0:
            return None

    if htf == "BULLISH":
        if cl <= e50: return None
        if not (35 <= rsi <= 58): return None
        if not macd_crossed_up_recently(entry_rows, within=5): return None
        if vol < avgv * 0.50: return None
        return "LONG"
    else:
        if cl >= e50: return None
        if not (42 <= rsi <= 65): return None
        if not macd_crossed_down_recently(entry_rows, within=5): return None
        if vol < avgv * 0.50: return None
        return "SHORT"


def detect_breakout(entry_rows: list[list[Any]],
                    confirm_rows: list[list[Any]]) -> str | None:
    """
    BREAKOUT entry: price breaks above/below a 20-candle range with
    expanding ATR and above-average volume. HTF must not strongly oppose.

    Conditions:
      • Close ≥ highest high of last 20 candles (LONG)  /  ≤ lowest low (SHORT)
      • Current ATR ≥ 1.10 × average ATR of last 20 periods (expanding volatility)
      • Volume ≥ 1.30 × 20-period average volume
      • HTF trend not BEARISH for LONG / not BULLISH for SHORT
      • Score ≥ 3 from weighted system (minimal directional alignment)
    Returns "LONG", "SHORT", or None.
    """
    if len(entry_rows) < 25 or len(confirm_rows) < 55:
        return None
    recent = entry_rows[-21:-1]
    if len(recent) < 20: return None

    snap = indicator_snapshot(entry_rows)
    cl   = float(entry_rows[-1][4])
    vol  = snap.get("volume")
    avgv = snap.get("_avg_volume") or 0.0
    if vol is None or avgv <= 0: return None

    atr_hist = atr_recent_vals(entry_rows, 21)
    if len(atr_hist) < 5: return None
    current_atr = atr_hist[-1]
    avg_atr     = sum(atr_hist[:-1]) / max(1, len(atr_hist) - 1)
    if avg_atr <= 0: return None

    # ATR safety
    if current_atr > avg_atr * 3.0: return None

    htf = trend_for(confirm_rows)
    vol_ok  = (vol >= avgv * 1.30)
    atr_ok  = (current_atr >= avg_atr * 1.10)
    if not (vol_ok and atr_ok): return None

    highs_20 = [float(r[2]) for r in recent]
    lows_20  = [float(r[3]) for r in recent]

    if cl >= max(highs_20):
        if htf != "BEARISH":
            lng_sc, _ = compute_score("LONG", entry_rows, confirm_rows)
            if lng_sc >= 3: return "LONG"
    if cl <= min(lows_20):
        if htf != "BULLISH":
            shrt_sc, _ = compute_score("SHORT", entry_rows, confirm_rows)
            if shrt_sc >= 3: return "SHORT"
    return None


def get_signal(entry_rows: list[list[Any]],
               confirm_rows: list[list[Any]],
               threshold: int = 6,
               direction_filter: str = "both") -> tuple[str | None, str]:
    """
    Unified signal generation.  Priority: TREND → PULLBACK → BREAKOUT.
    Returns (direction_or_None, strategy_type_label).
    """
    lng_sc, lng_snap = compute_score("LONG",  entry_rows, confirm_rows)
    sht_sc, sht_snap = compute_score("SHORT", entry_rows, confirm_rows)

    # TREND signal
    if lng_sc >= threshold and (direction_filter in ("both", "long_only")):
        return "LONG", "TREND"
    if sht_sc >= threshold and (direction_filter in ("both", "short_only")):
        return "SHORT", "TREND"

    # PULLBACK signal
    pb = detect_pullback(entry_rows, confirm_rows)
    if pb == "LONG" and direction_filter in ("both", "long_only"):
        return "LONG", "PULLBACK"
    if pb == "SHORT" and direction_filter in ("both", "short_only"):
        return "SHORT", "PULLBACK"

    # BREAKOUT signal
    bo = detect_breakout(entry_rows, confirm_rows)
    if bo == "LONG" and direction_filter in ("both", "long_only"):
        return "LONG", "BREAKOUT"
    if bo == "SHORT" and direction_filter in ("both", "short_only"):
        return "SHORT", "BREAKOUT"

    return None, "NO_TRADE"


# ─── Original 6/6 signal (reference comparison) ────────────────────────────────

def evaluate_66(entry_rows: list[list[Any]],
                confirm_rows: list[list[Any]]) -> str | None:
    """Original strict 6/6 condition evaluation.  Returns LONG/SHORT/None."""
    if len(entry_rows) < 55 or len(confirm_rows) < 55: return None
    snap  = indicator_snapshot(entry_rows)
    cl    = float(entry_rows[-1][4])
    avgv  = snap.get("_avg_volume") or 0.0
    t_ent = trend_for(entry_rows)
    t_cnf = trend_for(confirm_rows)
    rsi, macd, sig = snap["rsi"], snap["macd"], snap["macdSignal"]
    e20, e50, vol  = snap["ema20"], snap["ema50"], snap["volume"]
    if t_ent == "BULLISH" or t_cnf == "BULLISH":
        bias = "LONG"
    elif t_ent == "BEARISH" or t_cnf == "BEARISH":
        bias = "SHORT"
    else:
        return None
    if bias == "LONG":
        passes = [t_cnf == "BULLISH", t_ent == "BULLISH",
                  rsi is not None and rsi >= 50,
                  macd is not None and sig is not None and macd > sig,
                  e20 and e50 and cl > e20 > e50,
                  avgv > 0 and vol and vol >= avgv * 0.7]
    else:
        passes = [t_cnf == "BEARISH", t_ent == "BEARISH",
                  rsi is not None and rsi <= 50,
                  macd is not None and sig is not None and macd < sig,
                  e20 and e50 and cl < e20 < e50,
                  avgv > 0 and vol and vol >= avgv * 0.7]
    return bias if all(passes) else None


# ─── Backtest engine ───────────────────────────────────────────────────────────

def run_opportunity_bt(
    label:           str,
    pre_e:           dict,
    pre_c:           dict,
    threshold:       int   = 6,
    fee_per_fill:    float = 0.0,
    split_ts:        int   = 0,
    direction_filter: str  = "both",
) -> dict:
    """
    Run the opportunity-based backtest.  Accepts pre-computed indicator dicts
    (build_pre output) so the caller can reuse the same dict across all configs
    that share the same candle series — eliminating redundant computation.

    Cooldown rule (prevents signal recycling):
      After any close, entry is blocked until BOTH:
        (a) ≥ COOLDOWN_MIN_CANDLES entry-TF candles have elapsed, AND
        (b) score for the previously triggered direction has dropped below threshold.
      Exception: if COOLDOWN_FORCE_AFTER candles have elapsed, allow re-entry
      regardless of (b) — prevents over-restriction in strong trending markets.

    consec_losses resets every calendar day (matches live Replit restart behaviour).
    Returns dict with full trade list, balance curve, and IS/OOS split.
    """
    balance       = STARTING_BALANCE
    open_pos      = None
    trades        = []
    balance_curve = [balance]
    daily_loss    = 0.0
    day_key       = -1
    consec_losses = 0
    confirm_idx   = 0
    MIN_C         = 60

    cooldown_count = COOLDOWN_FORCE_AFTER
    cooldown_reset = True
    last_direction = None

    pre_c_ts = pre_c["ts"]   # pre-extracted for hot-loop confirm tracking

    for i in range(MIN_C, pre_e["n"]):
        cts = pre_e["ts"][i]
        ch  = pre_e["highs"][i]
        clo = pre_e["lows"][i]
        ccl = pre_e["closes"][i]

        while (confirm_idx + 1 < len(pre_c_ts) and
               pre_c_ts[confirm_idx + 1] <= cts):
            confirm_idx += 1
        ci = confirm_idx

        if (pre_e["atr"][i] is None or pre_e["e50"][i] is None or
                pre_c["e50"][ci] is None):
            balance_curve.append(balance); continue

        # Fast daily reset using pre-computed integer day numbers (no datetime parsing)
        dn = pre_e["day_num"][i]
        if dn != day_key:
            day_key       = dn
            daily_loss    = 0.0
            consec_losses = 0   # daily reset — simulates Replit restart

        if open_pos is not None:
            d, sl, tp, ent, qty = (open_pos["direction"], open_pos["stop_loss"],
                                   open_pos["take_profit"], open_pos["entry"],
                                   open_pos["quantity"])
            hit_sl = (clo <= sl) if d == "LONG" else (ch  >= sl)
            hit_tp = (ch  >= tp) if d == "LONG" else (clo <= tp)
            if hit_sl or hit_tp:
                if hit_sl and hit_tp: ep, er = sl, "STOP_LOSS"
                elif hit_sl:          ep, er = sl, "STOP_LOSS"
                else:                 ep, er = tp, "TAKE_PROFIT"
                exit_fee = ep * qty * fee_per_fill
                pnl = ((ep - ent) * qty if d == "LONG" else (ent - ep) * qty) - exit_fee
                balance       += pnl
                net_pnl        = pnl - open_pos["entry_fee"]   # true P&L inc. all fees
                daily_loss     = max(0.0, daily_loss + (-net_pnl if net_pnl < 0 else 0.0))
                consec_losses  = (consec_losses + 1) if net_pnl < 0 else 0
                trades.append({
                    "direction": d, "entry": ent, "exit": ep, "pnl": net_pnl,
                    "qty": qty, "exit_reason": er,
                    "opened_at": open_pos["opened_at"], "closed_at": cts,
                    "duration_h": (cts - open_pos["opened_at"]) / 3600,
                    "balance_after": balance,
                    "fees_paid": open_pos["entry_fee"] + exit_fee,
                    "strategy_type": open_pos.get("strategy_type", "TREND"),
                    "period": "OOS" if split_ts and cts >= split_ts else "IS",
                })
                open_pos = None
                cooldown_count = 0; cooldown_reset = False; last_direction = d

        balance_curve.append(balance)
        if open_pos is not None: continue

        cooldown_count += 1
        if not cooldown_reset and last_direction:
            sc,  _ = _compute_score_fast(last_direction, pre_e, i, pre_c, ci)
            opp    = "SHORT" if last_direction == "LONG" else "LONG"
            sc2, _ = _compute_score_fast(opp, pre_e, i, pre_c, ci)
            if sc < threshold and sc2 < threshold:
                cooldown_reset = True

        cooldown_ok = (cooldown_count >= COOLDOWN_MIN_CANDLES and
                       (cooldown_reset or cooldown_count >= COOLDOWN_FORCE_AFTER))

        if (daily_loss >= STARTING_BALANCE * DAILY_LOSS_LIMIT or
                consec_losses >= MAX_CONSECUTIVE_LOSSES or balance <= 0):
            continue
        if not cooldown_ok: continue

        direction, stype = _get_signal_fast(pre_e, i, pre_c, ci, threshold, direction_filter)
        if direction is None: continue

        pos, balance = _open_position_fast(balance, ccl, pre_e["atr"][i],
                                            direction, fee_per_fill, cts)
        if pos is None: continue
        pos["strategy_type"] = stype
        open_pos = pos

    if open_pos is not None:
        ep = pre_e["closes"][-1]; d = open_pos["direction"]
        exit_fee = ep * open_pos["quantity"] * fee_per_fill
        pnl = ((ep - open_pos["entry"]) * open_pos["quantity"] if d == "LONG"
               else (open_pos["entry"] - ep) * open_pos["quantity"]) - exit_fee
        balance += pnl
        net_pnl  = pnl - open_pos["entry_fee"]
        last_ts  = pre_e["ts"][-1]
        trades.append({
            "direction": d, "entry": open_pos["entry"], "exit": ep, "pnl": net_pnl,
            "qty": open_pos["quantity"], "exit_reason": "END_OF_DATA",
            "opened_at": open_pos["opened_at"], "closed_at": last_ts,
            "duration_h": (last_ts - open_pos["opened_at"]) / 3600,
            "balance_after": balance,
            "fees_paid": open_pos["entry_fee"] + exit_fee,
            "strategy_type": open_pos.get("strategy_type", "TREND"),
            "period": "OOS" if split_ts and last_ts >= split_ts else "IS",
        })
    return {"label": label, "trades": trades,
            "balance_curve": balance_curve, "final_balance": balance,
            "starting_bal": STARTING_BALANCE, "split_ts": split_ts}


def run_original_66_bt(
    label:        str,
    pre_e:        dict,
    pre_c:        dict,
    fee_per_fill: float = 0.0,
    split_ts:     int   = 0,
) -> dict:
    """Original strict 6/6 backtest for reference comparison.
    Accepts pre-built dicts from build_pre() — no redundant indicator recomputation.
    """
    balance       = STARTING_BALANCE
    open_pos      = None
    trades        = []
    balance_curve = [balance]
    daily_loss    = 0.0
    day_key       = -1
    consec_losses = 0
    confirm_idx   = 0
    MIN_C         = 60

    pre_c_ts = pre_c["ts"]

    for i in range(MIN_C, pre_e["n"]):
        cts = pre_e["ts"][i]
        ch  = pre_e["highs"][i]
        clo = pre_e["lows"][i]
        ccl = pre_e["closes"][i]

        while (confirm_idx + 1 < len(pre_c_ts) and
               pre_c_ts[confirm_idx + 1] <= cts):
            confirm_idx += 1
        ci = confirm_idx

        if (pre_e["atr"][i] is None or pre_e["e50"][i] is None or
                pre_c["e50"][ci] is None):
            balance_curve.append(balance); continue

        dn = pre_e["day_num"][i]
        if dn != day_key:
            day_key       = dn
            daily_loss    = 0.0
            consec_losses = 0   # daily reset — simulates Replit restart

        if open_pos is not None:
            d, sl, tp, ent, qty = (open_pos["direction"], open_pos["stop_loss"],
                                   open_pos["take_profit"], open_pos["entry"],
                                   open_pos["quantity"])
            hit_sl = (clo <= sl) if d == "LONG" else (ch  >= sl)
            hit_tp = (ch  >= tp) if d == "LONG" else (clo <= tp)
            if hit_sl or hit_tp:
                ep = sl if hit_sl else tp
                er = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
                if hit_sl and hit_tp: ep, er = sl, "STOP_LOSS"
                exit_fee = ep * qty * fee_per_fill
                pnl = ((ep - ent) * qty if d == "LONG" else (ent - ep) * qty) - exit_fee
                balance       += pnl
                net_pnl        = pnl - open_pos.get("entry_fee", 0)
                daily_loss     = max(0.0, daily_loss + (-net_pnl if net_pnl < 0 else 0.0))
                consec_losses  = (consec_losses + 1) if net_pnl < 0 else 0
                trades.append({
                    "direction": d, "entry": ent, "exit": ep, "pnl": net_pnl,
                    "qty": qty, "exit_reason": er,
                    "opened_at": open_pos["opened_at"], "closed_at": cts,
                    "duration_h": (cts - open_pos["opened_at"]) / 3600,
                    "balance_after": balance,
                    "fees_paid": open_pos.get("entry_fee", 0) + exit_fee,
                    "strategy_type": "TREND_66",
                    "period": "OOS" if split_ts and cts >= split_ts else "IS",
                })
                open_pos = None

        balance_curve.append(balance)
        if open_pos is not None: continue

        if (daily_loss >= STARTING_BALANCE * DAILY_LOSS_LIMIT or
                consec_losses >= MAX_CONSECUTIVE_LOSSES or balance <= 0):
            continue

        direction = _evaluate_66_fast(pre_e, i, pre_c, ci)
        if direction is None: continue

        pos, balance = _open_position_fast(balance, ccl, pre_e["atr"][i],
                                            direction, fee_per_fill, cts)
        if pos is None: continue
        open_pos = pos

    if open_pos is not None:
        ep = pre_e["closes"][-1]; d = open_pos["direction"]
        exit_fee = ep * open_pos["quantity"] * fee_per_fill
        pnl = ((ep - open_pos["entry"]) * open_pos["quantity"] if d == "LONG"
               else (open_pos["entry"] - ep) * open_pos["quantity"]) - exit_fee
        balance += pnl
        net_pnl  = pnl - open_pos.get("entry_fee", 0)
        last_ts  = pre_e["ts"][-1]
        trades.append({
            "direction": d, "entry": open_pos["entry"], "exit": ep, "pnl": net_pnl,
            "qty": open_pos["quantity"], "exit_reason": "END_OF_DATA",
            "opened_at": open_pos["opened_at"], "closed_at": last_ts,
            "duration_h": (last_ts - open_pos["opened_at"]) / 3600,
            "balance_after": balance,
            "fees_paid": open_pos.get("entry_fee", 0) + exit_fee,
            "strategy_type": "TREND_66",
            "period": "OOS" if split_ts and last_ts >= split_ts else "IS",
        })
    return {"label": label, "trades": trades,
            "balance_curve": balance_curve, "final_balance": balance,
            "starting_bal": STARTING_BALANCE, "split_ts": split_ts}


# ─── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics(trades: list[dict], start_bal: float,
                    test_days: float) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "win_rate": 0, "roi": 0,
                "ann_roi": None, "profit_factor": 0, "expectancy": 0,
                "max_dd": 0, "sharpe": None, "tpm": None, "tpw": None,
                "max_cl": 0, "max_cw": 0, "long_n": 0, "short_n": 0,
                "long_wr": 0, "short_wr": 0, "long_pf": 0, "short_pf": 0,
                "long_pnl": 0, "short_pnl": 0, "total_fees": 0,
                "final_balance": start_bal, "net_pnl": 0, "avg_duration": 0,
                "type_stats": {}}
    wins  = [t for t in trades if t["pnl"] > 0]
    loses = [t for t in trades if t["pnl"] <= 0]
    longs = [t for t in trades if t["direction"] == "LONG"]
    shrts = [t for t in trades if t["direction"] == "SHORT"]
    lw = [t for t in longs if t["pnl"] > 0]; ll = [t for t in longs if t["pnl"] <= 0]
    sw = [t for t in shrts if t["pnl"] > 0]; sl = [t for t in shrts if t["pnl"] <= 0]
    gp  = sum(t["pnl"] for t in wins);  gl = abs(sum(t["pnl"] for t in loses))
    pf  = (gp / gl) if gl > 0 else float("inf")
    wr  = len(wins) / n
    aw  = gp / len(wins) if wins else 0
    aloss = gl / len(loses) if loses else 0
    exp = wr * aw - (1 - wr) * aloss
    lgp = sum(t["pnl"] for t in lw); lgl = abs(sum(t["pnl"] for t in ll))
    sgp = sum(t["pnl"] for t in sw); sgl = abs(sum(t["pnl"] for t in sl))
    lpf = (lgp / lgl) if lgl > 0 else float("inf")
    spf = (sgp / sgl) if sgl > 0 else float("inf")

    # Balance curve from trades
    sorted_t = sorted(trades, key=lambda x: x["closed_at"])
    curve = [start_bal]
    for t in sorted_t: curve.append(t["balance_after"])
    finish = curve[-1]
    peak = start_bal; max_dd = 0.0
    for b in curve:
        peak = max(peak, b)
        max_dd = max(max_dd, (peak - b) / peak * 100)

    mx_cw = mx_cl = cw = cl_ = 0
    for t in sorted_t:
        if t["pnl"] > 0: cw += 1; cl_ = 0
        else:            cl_ += 1; cw = 0
        mx_cw = max(mx_cw, cw); mx_cl = max(mx_cl, cl_)

    # Sharpe (daily returns)
    daily: dict[str, float] = {}
    for t in sorted_t:
        dk = datetime.fromtimestamp(t["closed_at"], tz=timezone.utc).date().isoformat()
        daily[dk] = t["balance_after"]
    rets: list[float] = []; prev = start_bal
    for dk in sorted(daily):
        b = daily[dk]
        if prev > 0: rets.append((b - prev) / prev)
        prev = b
    sharpe = None
    if len(rets) >= 5:
        mu  = sum(rets) / len(rets)
        std = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        if std > 0: sharpe = mu / std * math.sqrt(252)

    months = test_days / 30.44 if test_days > 0 else 1
    weeks  = test_days / 7     if test_days > 0 else 1
    ann    = ((finish / start_bal) ** (365 / test_days) - 1) * 100 if test_days > 30 else None

    # Strategy type breakdown
    types = {"TREND": [], "PULLBACK": [], "BREAKOUT": [], "TREND_66": []}
    for t in trades:
        stype = t.get("strategy_type", "TREND")
        if stype not in types: types[stype] = []
        types[stype].append(t)
    type_stats = {}
    for stype, tlist in types.items():
        if not tlist: continue
        tw = [t for t in tlist if t["pnl"] > 0]
        tl = [t for t in tlist if t["pnl"] <= 0]
        tgp = sum(t["pnl"] for t in tw); tgl = abs(sum(t["pnl"] for t in tl))
        type_stats[stype] = {
            "n": len(tlist),
            "wins": len(tw), "losses": len(tl),
            "wr": len(tw) / len(tlist) * 100,
            "pf": (tgp / tgl) if tgl > 0 else float("inf"),
            "pnl": tgp - tgl,
        }

    return {
        "n": n, "wins": len(wins), "losses": len(loses),
        "win_rate": wr * 100, "net_pnl": finish - start_bal,
        "roi": (finish - start_bal) / start_bal * 100,
        "ann_roi": ann, "final_balance": finish, "profit_factor": pf,
        "expectancy": exp, "max_dd": max_dd, "sharpe": sharpe,
        "tpm": n / months, "tpw": n / weeks, "tpd": n / test_days if test_days else 0,
        "max_cw": mx_cw, "max_cl": mx_cl,
        "long_n": len(longs), "short_n": len(shrts),
        "long_wr": (len(lw)/len(longs)*100) if longs else 0,
        "short_wr": (len(sw)/len(shrts)*100) if shrts else 0,
        "long_pf": lpf, "short_pf": spf,
        "long_pnl": lgp - lgl, "short_pnl": sgp - sgl,
        "total_fees": sum(t.get("fees_paid", 0) for t in trades),
        "avg_duration": sum(t["duration_h"] for t in trades) / n,
        "type_stats": type_stats,
    }


def split_by_period(result: dict) -> tuple[list, list]:
    """Split trades into IS and OOS lists."""
    is_t  = [t for t in result["trades"] if t.get("period") != "OOS"]
    oos_t = [t for t in result["trades"] if t.get("period") == "OOS"]
    return is_t, oos_t


def period_days(trades: list, start_bal: float) -> tuple[float, float]:
    """Return (test_days, starting_balance_for_period)."""
    if not trades: return 1.0, start_bal
    ts_start = trades[0]["opened_at"]
    ts_end   = trades[-1]["closed_at"]
    days     = max((ts_end - ts_start) / 86400, 1.0)
    return days, start_bal


# ─── Formatting helpers ─────────────────────────────────────────────────────────

def fp(v, d=2): return "N/A" if v is None else f"{v:+.{d}f}%"
def fm(v, d=2):
    if v is None: return "N/A"
    return f"+£{v:.{d}f}" if v >= 0 else f"-£{abs(v):.{d}f}"
def fi(v): return "N/A" if v is None else ("∞" if isinstance(v, float) and math.isinf(v) else f"{v:.3f}")
def fn(v, d=2): return "N/A" if v is None else f"{v:.{d}f}"

def candidate_score(m: dict) -> float:
    """Composite IS score for config selection.  Requires PF > 1.0 to qualify."""
    if m["n"] < 10: return -999
    pf_raw = m["profit_factor"]
    if isinstance(pf_raw, float) and math.isinf(pf_raw): pf_raw = 3.0
    if pf_raw <= 1.0: return -998   # reject unprofitable IS configs
    pf  = min(pf_raw, 3.0)
    roi = m["roi"] or 0
    pen = 0.5 if m["n"] < 20 else (0.75 if m["n"] < 40 else 1.0)
    return (4 * pf + 2 * roi - m["max_dd"] + m["tpw"] * 0.5) * pen


def print_row(label, m, prefix=""):
    tpw = f"{m['tpw']:.2f}" if m.get('tpw') is not None else "N/A"
    tpm = f"{m['tpm']:.1f}" if m.get('tpm') is not None else "N/A"
    pf  = fi(m['profit_factor'])
    sh  = fn(m['sharpe']) if m.get('sharpe') is not None else "N/A"
    print(f"  {prefix}{label:<20} {m['n']:>4}  {tpw:>5}  {tpm:>5}"
          f"  {fp(m['roi']):>8}  {fp(m['ann_roi']):>8}"
          f"  {pf:>6}  {fp(m['win_rate']):>7}  {fp(m['max_dd']):>7}"
          f"  {fm(m['expectancy']):>7}  {sh:>6}  {m['max_cl']:>4}")


def print_header():
    print(f"  {'Config':<20} {'Trd':>4}  {'Trd/w':>5}  {'Trd/m':>5}"
          f"  {'ROI%':>8}  {'CAGR%':>8}"
          f"  {'PF':>6}  {'WR%':>7}  {'MaxDD%':>7}"
          f"  {'Exp':>7}  {'Sharpe':>6}  {'MaxCL':>4}")
    print("  " + "─" * 110)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    SEP  = "═" * 72
    sep2 = "─" * 72

    print(SEP)
    print("  OPPORTUNITY RESEARCH — NEW STRATEGY SYSTEM")
    print("  Weighted scoring (6/8 & 7/8) | Trend · Pullback · Breakout")
    print("  7 markets | 4 TF configs | 70/30 IS/OOS | Kraken fees")
    print(SEP)

    dt_is  = datetime.fromtimestamp(IS_SPLIT_CRYPTO, tz=timezone.utc).strftime("%Y-%m-%d")
    dt_is_m= datetime.fromtimestamp(IS_SPLIT_METALS, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"\n  IS/OOS split — Crypto: {dt_is} | Metals: {dt_is_m}")
    print(f"  Cooldown: ≥{COOLDOWN_MIN_CANDLES} entry-TF candles after close + score reset "
          f"(or ≥{COOLDOWN_FORCE_AFTER} candles elapsed)")
    print(f"  Fees: Crypto {CRYPTO_FEE_PER_FILL*2*100:.2f}% RT | "
          f"Metals {METALS_FEE_PER_FILL*2*100:.2f}% RT")
    print(f"\n  Fetching data (Binance + Yahoo Finance)...\n")

    # ── Crypto data (15m base; derive all coarser TFs by aggregation) ─────────
    CRYPTO = [
        ("BTCUSDT",  "BTC/USDT"),
        ("ETHUSDT",  "ETH/USDT"),
        ("SOLUSDT",  "SOL/USDT"),
        ("XRPUSDT",  "XRP/USDT"),
        ("LINKUSDT", "LINK/USDT"),
    ]

    crypto_data: dict[str, dict] = {}
    for sym, label in CRYPTO:
        print(f"  {label} 15m (3yr)...", end="", flush=True)
        c15 = fetch_binance(sym, 3 * 365, "15m")
        c30 = aggregate(c15, 1800)
        c1h = aggregate(c15, 3600)
        c2h = aggregate(c15, 7200)
        c4h = aggregate(c15, 14400)
        ts0 = datetime.fromtimestamp(int(c15[0][0]),  tz=timezone.utc).strftime("%Y-%m-%d")
        ts1 = datetime.fromtimestamp(int(c15[-1][0]), tz=timezone.utc).strftime("%Y-%m-%d")
        days= (int(c15[-1][0]) - int(c15[0][0])) / 86400
        print(f" {len(c15)} candles  ({ts0}→{ts1})")
        crypto_data[sym] = {
            "label": label, "c15": c15, "c30": c30,
            "c1h": c1h, "c2h": c2h, "c4h": c4h,
            "ts0": int(c15[0][0]), "ts1": int(c15[-1][0]), "days": days,
        }

    # ── Metals data (1h from Yahoo Finance; max ~2 years) ────────────────────
    METALS = [("GC=F", "Gold/USD"), ("SI=F", "Silver/USD")]
    metals_data: dict[str, dict] = {}
    for sym, label in METALS:
        print(f"  {label} 1h (2yr)...", end="", flush=True)
        c1h = fetch_yahoo(sym, 2 * 365)
        if not c1h:
            print(" NO DATA — skipping")
            continue
        c4h = aggregate(c1h, 14400)
        c2h = aggregate(c1h, 7200)
        ts0 = datetime.fromtimestamp(int(c1h[0][0]),  tz=timezone.utc).strftime("%Y-%m-%d")
        ts1 = datetime.fromtimestamp(int(c1h[-1][0]), tz=timezone.utc).strftime("%Y-%m-%d")
        days= (int(c1h[-1][0]) - int(c1h[0][0])) / 86400
        print(f" {len(c1h)} candles  ({ts0}→{ts1})")
        metals_data[sym] = {
            "label": label, "c1h": c1h, "c4h": c4h, "c2h": c2h,
            "ts0": int(c1h[0][0]), "ts1": int(c1h[-1][0]), "days": days,
        }

    print()

    # ── Configurations ────────────────────────────────────────────────────────
    # Each config: (name, entry_key, confirm_key, entry_secs_for_info)
    CONFIGS = [
        ("A 30m/4h", "c30", "c4h", 1800),
        ("B 30m/2h", "c30", "c2h", 1800),
        ("C 15m/1h", "c15", "c1h",  900),
        ("D 15m/4h", "c15", "c4h",  900),
    ]
    METALS_CONFIGS = [
        ("M 1h/4h",  "c1h", "c4h", 3600),
        ("N 1h/2h",  "c1h", "c2h", 3600),
    ]

    # ── Results store ─────────────────────────────────────────────────────────
    all_best:       dict[str, dict] = {}   # best IS config per market
    all_oos_m:      dict[str, dict] = {}   # OOS metrics for best config
    all_orig_oos:   dict[str, dict] = {}   # original 6/6 OOS metrics
    all_orig_full:  dict[str, dict] = {}   # original 6/6 full metrics

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1 — CRYPTO IS DEVELOPMENT
    # ══════════════════════════════════════════════════════════════════════════
    print(SEP)
    print("  PHASE 1 — IN-SAMPLE DEVELOPMENT (CRYPTO)")
    dt_crypto_is_end = datetime.fromtimestamp(IS_SPLIT_CRYPTO, tz=timezone.utc)
    print(f"  IS period ends: {dt_crypto_is_end.strftime('%Y-%m-%d')} (≈70% of data)")
    print(SEP)

    for sym, mkt_label in CRYPTO:
        d = crypto_data[sym]
        dt_start = datetime.fromtimestamp(d["ts0"], tz=timezone.utc).strftime("%Y-%m-%d")
        dt_end   = datetime.fromtimestamp(d["ts1"], tz=timezone.utc).strftime("%Y-%m-%d")

        print(f"\n  ┌─── {mkt_label} ──────────────────────────────────────────────────────")
        print(f"  │  Full period: {dt_start} → {dt_end}  ({d['days']:.0f} days)")
        print(f"  │  IS window  : {dt_start} → {dt_crypto_is_end.strftime('%Y-%m-%d')}")
        print()
        print_header()

        best_score = -999
        best_run   = None

        # Compute actual IS and OOS period lengths from timestamps, not trade spans
        crypto_is_days  = max((IS_SPLIT_CRYPTO - d["ts0"]) / 86400, 1)
        crypto_oos_days = max((d["ts1"] - IS_SPLIT_CRYPTO) / 86400, 1)

        # Build indicator arrays ONCE per unique series — reused across all configs
        pre = {k: build_pre(d[k]) for k in ("c15", "c30", "c1h", "c2h", "c4h")}

        for cfg_name, ek, ck, _ in CONFIGS:
            for th in THRESHOLDS:
                label = f"{cfg_name} th{th}"
                res = run_opportunity_bt(
                    label, pre[ek], pre[ck], threshold=th,
                    fee_per_fill=CRYPTO_FEE_PER_FILL,
                    split_ts=IS_SPLIT_CRYPTO,
                )
                is_trades, _ = split_by_period(res)
                if not is_trades:
                    print(f"  {label:<20} {'0':>4}  {'—':>5}  {'—':>5}"
                          f"  {'—':>8}  {'—':>8}  {'—':>6}  {'—':>7}  {'—':>7}"
                          f"  {'—':>7}  {'—':>6}  {'—':>4}")
                    continue
                m = compute_metrics(is_trades, STARTING_BALANCE, crypto_is_days)
                sc = candidate_score(m)
                print_row(f"{cfg_name} {th}/8", m)
                if sc > best_score:
                    best_score = sc
                    best_run   = {"cfg": cfg_name, "ek": ek, "ck": ck,
                                  "th": th, "res": res, "is_m": m,
                                  "oos_days": crypto_oos_days,
                                  "is_end_bal": is_trades[-1]["balance_after"]}

        # Original 6/6 on 1h/4h for comparison (reuses pre-built series)
        res66 = run_original_66_bt(
            "Orig 6/6 1h/4h", pre["c1h"], pre["c4h"],
            fee_per_fill=CRYPTO_FEE_PER_FILL,
            split_ts=IS_SPLIT_CRYPTO,
        )
        is66, oos66 = split_by_period(res66)
        if is66:
            m66_is = compute_metrics(is66, STARTING_BALANCE, crypto_is_days)
            print_row("Orig 6/6 1h/4h", m66_is, prefix="[REF] ")
        else:
            m66_is = compute_metrics([], STARTING_BALANCE, 1)
        if oos66:
            m66_oos = compute_metrics(oos66, STARTING_BALANCE, crypto_oos_days)
        else:
            m66_oos = compute_metrics([], STARTING_BALANCE, 1)
        all_orig_oos[sym]  = m66_oos
        all_orig_full[sym] = m66_is

        if best_run:
            print(f"\n  │  ✓ Best IS config: {best_run['cfg']} {best_run['th']}/8"
                  f"  (score {best_score:.1f}  |  PF {best_run['is_m']['profit_factor']:.3f}"
                  f"  trades {best_run['is_m']['n']}  CAGR {fp(best_run['is_m']['ann_roi'])})")
        else:
            print(f"\n  │  ✗ No config produced ≥10 IS trades — market skipped.")

        all_best[sym] = best_run
        print(f"  └{'─' * 68}")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — METALS IS DEVELOPMENT
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  PHASE 2 — IN-SAMPLE DEVELOPMENT (METALS)")
    dt_metals_is_end = datetime.fromtimestamp(IS_SPLIT_METALS, tz=timezone.utc)
    print(f"  IS period ends: {dt_metals_is_end.strftime('%Y-%m-%d')}")
    print(f"  Note: Yahoo Finance 1h data (≤2yr); entry TF = 1h for all metal configs")
    print(SEP)

    for sym, mkt_label in METALS:
        if sym not in metals_data:
            print(f"  {mkt_label}: no data — skipped")
            continue
        d = metals_data[sym]
        dt_start = datetime.fromtimestamp(d["ts0"], tz=timezone.utc).strftime("%Y-%m-%d")
        dt_end   = datetime.fromtimestamp(d["ts1"], tz=timezone.utc).strftime("%Y-%m-%d")

        print(f"\n  ┌─── {mkt_label} ─────────────────────────────────────────────────────")
        print(f"  │  Full period: {dt_start} → {dt_end}  ({d['days']:.0f} days)")
        print()
        print_header()

        best_score = -999
        best_run   = None

        metals_is_days  = max((IS_SPLIT_METALS - d["ts0"]) / 86400, 1)
        metals_oos_days = max((d["ts1"] - IS_SPLIT_METALS) / 86400, 1)

        # Build indicator arrays ONCE per unique series
        pre = {k: build_pre(d[k]) for k in ("c1h", "c2h", "c4h")}

        for cfg_name, ek, ck, _ in METALS_CONFIGS:
            for th in THRESHOLDS:
                label = f"{cfg_name} th{th}"
                res = run_opportunity_bt(
                    label, pre[ek], pre[ck], threshold=th,
                    fee_per_fill=METALS_FEE_PER_FILL,
                    split_ts=IS_SPLIT_METALS,
                )
                is_trades, _ = split_by_period(res)
                if not is_trades:
                    print(f"  {label:<20} {'0':>4}  {'—':>5}  {'—':>5}"
                          f"  {'—':>8}  {'—':>8}  {'—':>6}  {'—':>7}  {'—':>7}"
                          f"  {'—':>7}  {'—':>6}  {'—':>4}")
                    continue
                m = compute_metrics(is_trades, STARTING_BALANCE, metals_is_days)
                sc = candidate_score(m)
                print_row(f"{cfg_name} {th}/8", m)
                if sc > best_score:
                    best_score = sc
                    best_run   = {"cfg": cfg_name, "ek": ek, "ck": ck,
                                  "th": th, "res": res, "is_m": m,
                                  "oos_days": metals_oos_days,
                                  "is_end_bal": is_trades[-1]["balance_after"]}

        # Original 6/6 on 1h/4h for metals comparison (reuses pre-built series)
        res66 = run_original_66_bt(
            "Orig 6/6 1h/4h", pre["c1h"], pre["c4h"],
            fee_per_fill=METALS_FEE_PER_FILL,
            split_ts=IS_SPLIT_METALS,
        )
        is66, oos66 = split_by_period(res66)
        if is66:
            m66_is = compute_metrics(is66, STARTING_BALANCE, metals_is_days)
            print_row("Orig 6/6 1h/4h", m66_is, prefix="[REF] ")
        if oos66:
            all_orig_oos[sym] = compute_metrics(oos66, STARTING_BALANCE, metals_oos_days)
        else:
            all_orig_oos[sym] = compute_metrics([], STARTING_BALANCE, 1)

        if best_run:
            print(f"\n  │  ✓ Best IS config: {best_run['cfg']} {best_run['th']}/8"
                  f"  (score {best_score:.1f}  |  PF {best_run['is_m']['profit_factor']:.3f}"
                  f"  trades {best_run['is_m']['n']})")
        else:
            print(f"\n  │  ✗ No config produced ≥10 IS trades.")

        all_best[sym] = best_run
        metals_data[sym]["mkt_label"] = mkt_label
        print(f"  └{'─' * 68}")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 3 — OUT-OF-SAMPLE VALIDATION
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  PHASE 3 — OUT-OF-SAMPLE VALIDATION")
    print("  Configs selected on IS data only.  OOS data NEVER seen during selection.")
    print(SEP)

    all_markets_ordered = (
        [(sym, crypto_data[sym]["label"]) for sym, _ in CRYPTO] +
        [(sym, metals_data[sym]["mkt_label"]) for sym, _ in METALS if sym in metals_data]
    )

    for sym, mkt_label in all_markets_ordered:
        best = all_best.get(sym)
        orig_oos = all_orig_oos.get(sym, {})
        split_ts = IS_SPLIT_CRYPTO if sym in crypto_data else IS_SPLIT_METALS

        if not best:
            print(f"\n  {mkt_label}: skipped (no qualifying IS config)")
            continue

        _, oos_t = split_by_period(best["res"])
        if not oos_t:
            print(f"\n  {mkt_label}: 0 OOS trades with selected config")
            continue

        # Use actual OOS window length and IS-ending balance for correct OOS metrics
        oos_days    = best.get("oos_days",
                      (oos_t[-1]["closed_at"] - oos_t[0]["opened_at"]) / 86400)
        oos_start_b = best.get("is_end_bal", STARTING_BALANCE)
        oos_m       = compute_metrics(oos_t, oos_start_b, max(oos_days, 1))
        all_oos_m[sym] = oos_m

        oos_dt0 = datetime.fromtimestamp(oos_t[0]["opened_at"],  tz=timezone.utc).strftime("%Y-%m-%d")
        oos_dt1 = datetime.fromtimestamp(oos_t[-1]["closed_at"], tz=timezone.utc).strftime("%Y-%m-%d")

        print(f"\n  ┌─── {mkt_label} OOS — config: {best['cfg']} {best['th']}/8 ──────────────")
        print(f"  │  Period: {oos_dt0} → {oos_dt1}  ({oos_days:.0f} days)")
        print(f"  │  (Selected on IS; OOS is untouched validation)")
        print()

        # OOS metrics
        print(f"  {'Metric':<22}  {'New Opp-Based':>16}  {'Orig 6/6 1h/4h':>16}  {'Δ':>10}")
        print(f"  {'─'*22}  {'─'*16}  {'─'*16}  {'─'*10}")

        def cmp(k, fmt, oo=None, oo2=None, invert=False):
            nv = oo.get(k) if oo else oos_m.get(k)
            ov = oo2.get(k) if oo2 else orig_oos.get(k)
            ns = fmt(nv); os_ = fmt(ov)
            if nv is not None and ov is not None and isinstance(nv, (int,float)):
                d = (nv - ov) * (-1 if invert else 1)
                ds = f"{'+'if d>=0 else ''}{d:.2f}"
            else:
                ds = "—"
            return ns, os_, ds

        rows_to_print = [
            ("Trades",          lambda v: f"{v:.0f}" if v is not None else "—", "n"),
            ("Trades/week",     lambda v: f"{v:.2f}" if v is not None else "—", "tpw"),
            ("Trades/month",    lambda v: f"{v:.1f}" if v is not None else "—", "tpm"),
            ("ROI%",            lambda v: fp(v) if v is not None else "—", "roi"),
            ("CAGR%",           lambda v: fp(v) if v is not None else "—", "ann_roi"),
            ("Profit factor",   lambda v: fi(v) if v is not None else "—", "profit_factor"),
            ("Win rate%",       lambda v: fp(v) if v is not None else "—", "win_rate"),
            ("Max drawdown%",   lambda v: fp(v) if v is not None else "—", "max_dd"),
            ("Expectancy/trd",  lambda v: fm(v) if v is not None else "—", "expectancy"),
            ("Sharpe",          lambda v: fn(v) if v is not None else "—", "sharpe"),
            ("Max consec loss", lambda v: f"{v:.0f}" if v is not None else "—", "max_cl"),
            ("Total fees",      lambda v: fm(v) if v is not None else "—", "total_fees"),
        ]
        for row_label, fmt, key in rows_to_print:
            nv = oos_m.get(key); ov = orig_oos.get(key)
            ns = fmt(nv); os_ = fmt(ov)
            if key in ("max_dd", "max_cl", "total_fees"):
                # lower is better: delta inverted
                ds = ("—" if nv is None or ov is None or not isinstance(nv,(int,float))
                      else f"{'+'if(nv-ov)<=0 else ''}{nv-ov:.2f}")
            else:
                ds = ("—" if nv is None or ov is None or not isinstance(nv,(int,float))
                      else f"{'+'if(nv-ov)>=0 else ''}{nv-ov:.2f}")
            print(f"  {row_label:<22}  {ns:>16}  {os_:>16}  {ds:>10}")

        # LONG / SHORT OOS
        print(f"\n  {'LONG':>8}: {oos_m['long_n']} trades  WR {fp(oos_m['long_wr'])}  "
              f"PF {fi(oos_m['long_pf'])}  PnL {fm(oos_m['long_pnl'])}")
        print(f"  {'SHORT':>8}: {oos_m['short_n']} trades  WR {fp(oos_m['short_wr'])}  "
              f"PF {fi(oos_m['short_pf'])}  PnL {fm(oos_m['short_pnl'])}")

        # Strategy type breakdown OOS
        ts = oos_m.get("type_stats", {})
        if ts:
            print(f"\n  Strategy type breakdown (OOS):")
            print(f"  {'Type':<12}  {'Trd':>4}  {'WR%':>7}  {'PF':>6}  {'PnL':>8}")
            print(f"  {'─'*12}  {'─'*4}  {'─'*7}  {'─'*6}  {'─'*8}")
            for stype in ("TREND", "PULLBACK", "BREAKOUT"):
                st = ts.get(stype)
                if st:
                    print(f"  {stype:<12}  {st['n']:>4}  {fp(st['wr']):>7}"
                          f"  {fi(st['pf']):>6}  {fm(st['pnl']):>8}")

        # Additional trade analysis
        n_new = oos_m["n"]; n_orig = orig_oos.get("n", 0)
        n_add = n_new - n_orig
        if n_add > 0:
            add_wins   = oos_m["wins"] - orig_oos.get("wins", 0)
            add_losses = oos_m["losses"] - orig_oos.get("losses", 0)
            add_be     = n_add - max(0, add_wins) - max(0, add_losses)
            print(f"\n  Additional trades vs 6/6: +{n_add}")
            print(f"  Of additional trades: ~{max(0,add_wins)} winners  "
                  f"~{max(0,add_losses)} losers  ~{max(0,add_be)} breakeven")
        if oos_m["n"] > 0 and oos_m["max_dd"] > 0:
            tpdd_new  = oos_m["n"] / oos_m["max_dd"]
            tpdd_orig = (orig_oos.get("n",0) / orig_oos.get("max_dd",1)
                         if orig_oos.get("max_dd",0) > 0 else 0)
            print(f"  Trades per 1% MaxDD:  New={tpdd_new:.1f}  Orig={tpdd_orig:.1f}")

        print(f"  └{'─' * 68}")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 4 — PORTFOLIO SUMMARY
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  PHASE 4 — PORTFOLIO SUMMARY (OOS PERIOD)")
    print("  How much does each market contribute across the out-of-sample window?")
    print(SEP)

    print()
    print(f"  {'Market':<12}  {'Config':>12}  {'Trd':>4}  {'Trd/w':>5}  "
          f"{'ROI%':>8}  {'PF':>6}  {'WR%':>7}  {'MaxDD%':>7}  {'Exp':>7}  {'Status':>12}")
    print(f"  {'─'*12}  {'─'*12}  {'─'*4}  {'─'*5}  "
          f"{'─'*8}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*12}")

    total_oos_trades    = 0
    contributing_mkts   = 0
    total_weekly_trades = 0.0

    for sym, mkt_label in all_markets_ordered:
        best = all_best.get(sym)
        oos_m = all_oos_m.get(sym)
        if not best or not oos_m or oos_m["n"] == 0:
            cfg_name = best["cfg"] + f" {best['th']}/8" if best else "N/A"
            print(f"  {mkt_label:<12}  {cfg_name if best else 'N/A':>12}  "
                  f"{'0':>4}  {'—':>5}  {'—':>8}  {'—':>6}  {'—':>7}  {'—':>7}  {'—':>7}  "
                  f"{'NO TRADES':>12}")
            continue
        cfg_name = best["cfg"] + f" {best['th']}/8"
        status = "✓ POSITIVE" if oos_m["roi"] > 0 else "✗ NEGATIVE"
        total_oos_trades    += oos_m["n"]
        total_weekly_trades += oos_m.get("tpw", 0)
        contributing_mkts   += 1
        print(f"  {mkt_label:<12}  {cfg_name:>12}  {oos_m['n']:>4}  "
              f"{fn(oos_m.get('tpw'),2):>5}  {fp(oos_m['roi']):>8}  "
              f"{fi(oos_m['profit_factor']):>6}  {fp(oos_m['win_rate']):>7}  "
              f"{fp(oos_m['max_dd']):>7}  {fm(oos_m['expectancy']):>7}  {status:>12}")

    print(f"\n  PORTFOLIO TOTALS (combined across all markets, OOS):")
    print(f"  Total OOS trades:         {total_oos_trades}")
    print(f"  Total trades/week:        {total_weekly_trades:.2f}")
    print(f"  Target range:             3–10 trades/week")
    print(f"  Markets with OOS trades:  {contributing_mkts} of {len(all_markets_ordered)}")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 5 — FINAL COMPARISON vs ORIGINAL 6/6
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  PHASE 5 — FINAL COMPARISON: NEW vs ORIGINAL 6/6 SYSTEM")
    print("  Metrics shown are OOS (out-of-sample) period only")
    print(SEP)

    print()
    print(f"  {'Metric':<22}  {'ORIGINAL 6/6':>14}  {'NEW OPP-BASED':>14}  {'Δ':>10}")
    print(f"  {'─'*22}  {'─'*14}  {'─'*14}  {'─'*10}")

    # Aggregate across all markets
    def agg(metrics_dict, key):
        vals = [m.get(key, 0) for m in metrics_dict.values() if m]
        return sum(v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v)))

    orig_total_n    = agg(all_orig_oos, "n")
    new_total_n     = total_oos_trades
    orig_total_fees = agg(all_orig_oos, "total_fees")
    new_total_fees  = agg(all_oos_m, "total_fees")
    orig_tpw        = agg(all_orig_oos, "tpw")
    orig_tpm        = agg(all_orig_oos, "tpm")

    print(f"  {'Total trades':<22}  {orig_total_n:>14}  {new_total_n:>14}  "
          f"  {'+' if new_total_n>=orig_total_n else ''}{new_total_n-orig_total_n:>8}")
    print(f"  {'Trades/week (sum)':<22}  {orig_tpw:>14.2f}  {total_weekly_trades:>14.2f}  "
          f"  {'+' if total_weekly_trades>=orig_tpw else ''}{total_weekly_trades-orig_tpw:>8.2f}")
    print(f"  {'Total fees paid':<22}  {fm(orig_total_fees):>14}  {fm(new_total_fees):>14}  "
          f"  {fm(new_total_fees - orig_total_fees):>10}")

    # Per-market ROI, PF comparisons
    print(f"\n  Per-market OOS comparison:")
    print(f"  {'Market':<12}  {'Orig Trd':>8}  {'New Trd':>8}  "
          f"{'Orig PF':>8}  {'New PF':>8}  {'Orig ROI':>9}  {'New ROI':>9}")
    print(f"  {'─'*12}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*9}  {'─'*9}")
    for sym, mkt_label in all_markets_ordered:
        o = all_orig_oos.get(sym, {}); n = all_oos_m.get(sym, {})
        print(f"  {mkt_label:<12}  {o.get('n',0):>8}  {n.get('n',0):>8}  "
              f"  {fi(o.get('profit_factor',0)):>7}  {fi(n.get('profit_factor',0)):>7}"
              f"  {fp(o.get('roi')):>9}  {fp(n.get('roi')):>9}")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 6 — FINAL RECOMMENDATION
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{SEP}")
    print("  PHASE 6 — FINAL RECOMMENDATION")
    print(SEP)

    print("""
  Selection criteria applied:
  1. Positive OOS profit factor (PF > 1.0 after fees)
  2. PF ≥ 1.20 preferred (indicates genuine edge, not noise)
  3. Positive OOS ROI
  4. Adequate OOS trade count (≥ 10 trades)
  5. Drawdown ≤ 20%
  6. IS→OOS consistency (large IS/OOS divergence flags overfitting)
  7. Meaningful increase in trade frequency vs original 6/6
""")

    # Evaluate each market's best config
    candidates = []
    skipped    = []

    for sym, mkt_label in all_markets_ordered:
        best  = all_best.get(sym)
        oos_m = all_oos_m.get(sym)
        is_m  = best["is_m"] if best else None

        if not best or not oos_m or oos_m["n"] < 10:
            skipped.append((mkt_label, "< 10 OOS trades"))
            continue
        if oos_m["roi"] <= 0:
            skipped.append((mkt_label, f"Negative OOS ROI ({fp(oos_m['roi'])})"))
            continue
        pf = oos_m.get("profit_factor", 0)
        if not isinstance(pf, float) or math.isinf(pf): pf = 3.0
        if pf <= 1.0:
            skipped.append((mkt_label, f"OOS PF ≤ 1.0 ({fi(oos_m['profit_factor'])})"))
            continue
        if oos_m.get("max_dd", 100) > 20:
            skipped.append((mkt_label, f"OOS MaxDD > 20% ({fp(oos_m['max_dd'])})"))
            continue

        # IS/OOS consistency check
        is_pf  = is_m.get("profit_factor", 0) if is_m else 0
        if isinstance(is_pf, float) and math.isinf(is_pf): is_pf = 3.0
        oos_pf = pf
        divergence = abs(is_pf - oos_pf) / max(is_pf, 0.01)
        consistency = "GOOD" if divergence < 0.5 else ("MODERATE" if divergence < 1.0 else "POOR")

        cfg_name = best["cfg"] + f" {best['th']}/8"
        candidates.append({
            "sym": sym, "label": mkt_label, "cfg": cfg_name,
            "oos_m": oos_m, "is_m": is_m,
            "consistency": consistency, "divergence": divergence,
        })

    if candidates:
        print("  ┌─ CANDIDATES FOR PAPER TESTING ─────────────────────────────────────")
        for c in candidates:
            m  = c["oos_m"]
            pf = m.get("profit_factor",0)
            pf_str = fi(pf) if not (isinstance(pf,float) and math.isinf(pf)) else "∞"
            grade = ("★★★" if (isinstance(pf,(int,float)) and not math.isinf(pf) and pf >= 1.30
                               and m["roi"] > 5 and c["consistency"] == "GOOD")
                     else "★★ " if (isinstance(pf,(int,float)) and not math.isinf(pf) and pf >= 1.15)
                     else "★  ")
            print(f"  │")
            print(f"  │  {grade} {c['label']} — {c['cfg']}")
            print(f"  │       OOS: {m['n']} trades  {fn(m.get('tpw'),2)}/wk  "
                  f"ROI {fp(m['roi'])}  PF {pf_str}  WR {fp(m['win_rate'])}  "
                  f"DD {fp(m['max_dd'])}  Exp {fm(m['expectancy'])}")
            print(f"  │       IS/OOS consistency: {c['consistency']}  (divergence {c['divergence']:.0%})")
            print(f"  │       Max consec losses: {m['max_cl']}  Sharpe: {fn(m.get('sharpe'))}")
        print("  └─────────────────────────────────────────────────────────────────────")
    else:
        print("  No market cleared all criteria with the new system.")
        print("  The original 6/6 system remains the reference until further research.")

    if skipped:
        print(f"\n  Markets not qualifying:")
        for mkt, reason in skipped:
            print(f"   ✗ {mkt}: {reason}")

    print(f"""
  SUMMARY
  ───────
  The new opportunity-based system (weighted scoring + Pullback + Breakout)
  targets substantially more trades than the original strict 6/6 approach.

  Key differences from 6/6:
  • Score threshold 6/8 or 7/8 replaces binary "all must pass"
  • Neutral higher-timeframe trend does NOT block trades at 6/8 threshold
    (requires all other 6 points → strong entry confirmation still needed)
  • PULLBACK entries capture retracements in established trends
  • BREAKOUT entries capture range expansions with volume confirmation
  • Cooldown prevents signal recycling: score must reset + ≥{COOLDOWN_MIN_CANDLES} candles elapsed

  Action required to proceed:
  • Review OOS results above for each market
  • Approve preferred market/config(s) for paper testing
  • The existing live paper_trader.py remains UNCHANGED until you approve
""")
    print(SEP)


if __name__ == "__main__":
    main()
