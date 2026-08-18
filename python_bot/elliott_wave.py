"""ELLIOTT WAVE + FIBONACCI INTELLIGENCE MODULE — experimental analytics only.

PAPER TRADING ONLY. This module never places orders and never vetoes trades.
It classifies market structure (impulse 1-5 / ABC correction) from swing
pivots, scores a confidence value, computes Fibonacci retracements and
extensions, and flags POTENTIAL WAVE 3 / WAVE 5 EXHAUSTION setups.

Design constraints (Aug 2026 spec):
- Never force a count — return UNCERTAIN when structure is unclear.
- Enforce core Elliott rules (W2 retrace, W3 not shortest, W4 overlap) and
  cap confidence when rules are only diagonal-tolerable.
- Influence flags exist but default OFF (elliott_influence, wave5_veto).
"""
from __future__ import annotations

from typing import Any

# Reuse indicator helpers from the main bot (no duplicate math).
from paper_trader import ema_series, rsi_series, safe_float

FIB_RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXTENSIONS = (1.0, 1.272, 1.618, 2.0, 2.618)

# ZigZag reversal threshold, in ATR multiples.
_ZZ_ATR_MULT = 1.6
_MAX_PIVOTS = 40  # keep only recent structure


# ---------------------------------------------------------------------------
# Swing pivots (ZigZag on completed candles)
# ---------------------------------------------------------------------------
def _atr_value(rows: list[list[Any]]) -> float | None:
    trs: list[float] = []
    for i, row in enumerate(rows):
        high, low = float(row[2]), float(row[3])
        prev_close = float(rows[i - 1][4]) if i else float(row[4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    series = ema_series(trs, 14)
    return series[-1] if series else None


def detect_pivots(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """ZigZag swing highs/lows. Each pivot: {i, time, price, kind:'H'|'L'}."""
    if len(rows) < 20:
        return []
    atr = _atr_value(rows)
    if not atr or atr <= 0:
        return []
    threshold = atr * _ZZ_ATR_MULT
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    pivots: list[dict[str, Any]] = []
    # seed direction from first meaningful move
    trend = None  # 'up' means we are tracking a rising leg (next pivot = H)
    ext_i, ext_hi, ext_lo = 0, highs[0], lows[0]
    hi_i = lo_i = 0  # indices of the extremes seen during the undecided phase
    for i in range(1, len(rows)):
        if trend is None:
            if highs[i] >= ext_lo + threshold:
                # rising leg confirmed — keep the true high seen so far
                if highs[i] >= ext_hi:
                    ext_hi, hi_i = highs[i], i
                trend, ext_i = "up", hi_i
            elif lows[i] <= ext_hi - threshold:
                if lows[i] <= ext_lo:
                    ext_lo, lo_i = lows[i], i
                trend, ext_i = "down", lo_i
            if trend is not None:
                continue
            if highs[i] > ext_hi:
                ext_hi, hi_i = highs[i], i
            if lows[i] < ext_lo:
                ext_lo, lo_i = lows[i], i
            continue
        if trend == "up":
            if highs[i] > ext_hi:
                ext_i, ext_hi = i, highs[i]
            elif lows[i] <= ext_hi - threshold:
                pivots.append({"i": ext_i, "time": int(rows[ext_i][0]), "price": ext_hi, "kind": "H"})
                trend, ext_i, ext_lo = "down", i, lows[i]
        else:
            if lows[i] < ext_lo:
                ext_i, ext_lo = i, lows[i]
            elif highs[i] >= ext_lo + threshold:
                pivots.append({"i": ext_i, "time": int(rows[ext_i][0]), "price": ext_lo, "kind": "L"})
                trend, ext_i, ext_hi = "up", i, highs[i]
    # current developing extreme as a provisional pivot
    if trend == "up":
        pivots.append({"i": ext_i, "time": int(rows[ext_i][0]), "price": ext_hi, "kind": "H", "provisional": True})
    elif trend == "down":
        pivots.append({"i": ext_i, "time": int(rows[ext_i][0]), "price": ext_lo, "kind": "L", "provisional": True})
    return pivots[-_MAX_PIVOTS:]


# ---------------------------------------------------------------------------
# Impulse / correction classification
# ---------------------------------------------------------------------------
def _legs(pivots: list[dict[str, Any]]) -> list[float]:
    return [abs(pivots[k + 1]["price"] - pivots[k]["price"]) for k in range(len(pivots) - 1)]


def _score_impulse(p: list[dict[str, Any]], bullish: bool) -> tuple[float, list[str]]:
    """Score pivots p0..p5 (6 pivots = waves 1..5) against Elliott rules.
    Returns (confidence 0-1, notes). 0 = impossible."""
    notes: list[str] = []
    sign = 1 if bullish else -1
    # direction pattern: motive legs move with sign, corrective against
    for k in range(5):
        d = (p[k + 1]["price"] - p[k]["price"]) * sign
        if k % 2 == 0 and d <= 0:
            return 0.0, ["wave direction wrong"]
        if k % 2 == 1 and d >= 0:
            return 0.0, ["correction direction wrong"]
    w = _legs(p)  # w[0]=W1 .. w[4]=W5
    # Rule: W2 must not retrace beyond W1 origin
    if (p[2]["price"] - p[0]["price"]) * sign <= 0:
        return 0.0, ["W2 retraced beyond W1 origin"]
    # Rule: W3 not the shortest of 1,3,5
    if w[2] <= w[0] and w[2] <= w[4]:
        return 0.0, ["W3 is the shortest"]
    conf = 0.45
    # W4 overlap of W1 territory → diagonal only (tolerated, capped)
    overlap = (p[4]["price"] - p[1]["price"]) * sign < 0
    if overlap:
        conf -= 0.12
        notes.append("W4 overlaps W1 (possible diagonal)")
    else:
        conf += 0.10
    # Guideline bonuses
    w2_r = w[1] / w[0] if w[0] else 0
    if 0.4 <= w2_r <= 0.8:
        conf += 0.10
        notes.append("W2 retrace in 50-78.6% zone")
    if w[2] >= w[0] * 1.4:
        conf += 0.12
        notes.append("W3 extended (>1.4x W1)")
    w4_r = w[3] / w[2] if w[2] else 0
    if 0.2 <= w4_r <= 0.5:
        conf += 0.08
        notes.append("W4 shallow (23.6-50%)")
    ext = w[4] / w[0] if w[0] else 0
    if 0.6 <= ext <= 1.8:
        conf += 0.05
    return min(conf, 0.92), notes


def _score_abc(p: list[dict[str, Any]], bearish_correction: bool) -> tuple[float, list[str]]:
    """Score pivots p0..p3 (4 pivots = waves A,B,C). bearish_correction means
    the correction moves down (i.e. corrects a bullish move)."""
    notes: list[str] = []
    sign = -1 if bearish_correction else 1
    for k in range(3):
        d = (p[k + 1]["price"] - p[k]["price"]) * sign
        if k % 2 == 0 and d <= 0:
            return 0.0, ["leg direction wrong"]
        if k % 2 == 1 and d >= 0:
            return 0.0, ["B direction wrong"]
    a, b, c = _legs(p)
    if a <= 0:
        return 0.0, ["degenerate A"]
    b_r = b / a
    if b_r > 1.05:
        return 0.0, ["B retraced beyond A start"]
    conf = 0.40
    if 0.3 <= b_r <= 0.85:
        conf += 0.12
        notes.append("B retrace typical (38-78.6%)")
    c_r = c / a
    if 0.8 <= c_r <= 1.3:
        conf += 0.14
        notes.append("C ≈ A (equality guideline)")
    elif 1.3 < c_r <= 1.8:
        conf += 0.08
        notes.append("C extended toward 161.8% of A")
    return min(conf, 0.85), notes


def _label(conf: float) -> str:
    if conf >= 0.70:
        return "HIGH"
    if conf >= 0.50:
        return "MEDIUM"
    return "LOW"


def _fib_levels(lo: float, hi: float, bullish: bool) -> dict[str, Any]:
    rng = hi - lo
    retr = {f"{int(r * 1000) / 10:g}%": round(hi - rng * r, 8) if bullish else round(lo + rng * r, 8)
            for r in FIB_RETRACEMENTS}
    ext = {f"{int(e * 1000) / 10:g}%": round(lo + rng * e, 8) if bullish else round(hi - rng * e, 8)
           for e in FIB_EXTENSIONS}
    return {"swingLow": round(lo, 8), "swingHigh": round(hi, 8),
            "retracements": retr, "extensions": ext}


def _fib_location(close: float, lo: float, hi: float, bullish: bool) -> str | None:
    if hi <= lo:
        return None
    pos = (hi - close) / (hi - lo) if bullish else (close - lo) / (hi - lo)
    if pos < 0:
        return "beyond swing extreme"
    marks = [(0.236, "23.6%"), (0.382, "38.2%"), (0.5, "50%"), (0.618, "61.8%"), (0.786, "78.6%"), (1.0, "100%")]
    prev = "0%"
    for lim, name in marks:
        if pos <= lim:
            return f"between {prev} and {name} retracement"
        prev = name
    return "beyond 100% retracement"


# ---------------------------------------------------------------------------
# Per-timeframe analysis
# ---------------------------------------------------------------------------
def analyze_timeframe(rows: list[list[Any]], timeframe: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "timeframe": timeframe, "structure": "UNCERTAIN", "wave": None,
        "direction": "NEUTRAL", "confidence": 31, "confidenceLabel": "LOW",
        "notes": [], "fib": None, "fibLocation": None,
        "wave3Candidate": False, "wave5Exhaustion": False, "abcCandidate": False,
        "pivots": [],
    }
    pivots = detect_pivots(rows)
    if len(pivots) < 4 or len(rows) < 30:
        return base
    close = float(rows[-1][4])
    closes = [float(r[4]) for r in rows]
    volumes = [float(r[6]) for r in rows]
    rsi = rsi_series(closes)
    ema12, ema26 = ema_series(closes, 12), ema_series(closes, 26)
    macd = [(f - s) if f is not None and s is not None else None for f, s in zip(ema12, ema26)]

    candidates: list[dict[str, Any]] = []

    # -- impulse candidates: try the last 6 pivots, and 6 ending one earlier
    for end in (len(pivots), len(pivots) - 1):
        if end < 6:
            continue
        p = pivots[end - 6:end]
        for bullish in (True, False):
            conf, notes = _score_impulse(p, bullish)
            if conf <= 0:
                continue
            complete = end == len(pivots) and not p[-1].get("provisional")
            # which wave is price in NOW? if the 5th wave pivot is provisional
            # we are inside wave 5; if the count ended a pivot ago, we are in
            # the correction after it.
            if end == len(pivots) - 1:
                wave, in_wave_dir = "A", ("BEARISH" if bullish else "BULLISH")
            elif p[-1].get("provisional"):
                wave, in_wave_dir = "5", ("BULLISH" if bullish else "BEARISH")
            else:
                wave, in_wave_dir = "5", ("BULLISH" if bullish else "BEARISH")
            candidates.append({
                "type": "IMPULSE", "bullish": bullish, "pivots": p, "conf": conf,
                "notes": notes, "wave": wave, "direction": in_wave_dir,
                "complete": complete,
            })

    # -- partial impulse: 4 pivots = W1+W2 (+ developing W3) → wave 3 setup
    if len(pivots) >= 3:
        p = pivots[-3:]
        for bullish in (True, False):
            sign = 1 if bullish else -1
            w1 = (p[1]["price"] - p[0]["price"]) * sign
            w2 = (p[1]["price"] - p[2]["price"]) * sign
            if w1 <= 0 or w2 <= 0:
                continue
            r = w2 / w1
            if r >= 1.0:  # W2 beyond W1 origin — invalid
                continue
            conf = 0.42
            notes = ["W1 complete", f"W2 retraced {r * 100:.0f}%"]
            if 0.4 <= r <= 0.8:
                conf += 0.12
                notes.append("W2 in golden-zone retrace")
            progressed = (close - p[2]["price"]) * sign
            broke_w1 = (close - p[1]["price"]) * sign > 0
            if broke_w1:
                conf += 0.14
                notes.append("price broke beyond W1 extreme")
            elif progressed > 0:
                conf += 0.06
                notes.append("price turning from W2 low" if bullish else "price turning from W2 high")
            else:
                conf -= 0.10
            if macd[-1] is not None and macd[-5] is not None and len(macd) >= 5:
                if (macd[-1] - macd[-5]) * sign > 0:
                    conf += 0.08
                    notes.append("MACD momentum rising with count")
            if volumes and len(volumes) >= 20:
                if volumes[-1] > (sum(volumes[-20:]) / 20) * 1.1:
                    conf += 0.05
                    notes.append("above-average volume")
            if conf >= 0.42:
                candidates.append({
                    "type": "WAVE3_SETUP", "bullish": bullish, "pivots": p,
                    "conf": min(conf, 0.90), "notes": notes, "wave": "3",
                    "direction": "BULLISH" if bullish else "BEARISH", "complete": False,
                })

    # -- ABC candidates on last 4 pivots
    if len(pivots) >= 4:
        p = pivots[-4:]
        for bearish_corr in (True, False):
            conf, notes = _score_abc(p, bearish_corr)
            if conf <= 0:
                continue
            candidates.append({
                "type": "ABC", "bullish": not bearish_corr, "pivots": p,
                "conf": conf, "notes": notes, "wave": "C",
                "direction": "BEARISH" if bearish_corr else "BULLISH",
                "complete": not p[-1].get("provisional"),
            })

    if not candidates:
        return base

    best = max(candidates, key=lambda c: c["conf"])
    conf_pct = int(round(best["conf"] * 100))
    p = best["pivots"]
    prices = [pv["price"] for pv in p]
    lo, hi = min(prices), max(prices)
    bullish = best["bullish"]

    # Wave 5 exhaustion evidence (only for impulse counts in/after wave 5)
    wave5_exh = False
    exh_notes: list[str] = []
    if best["type"] == "IMPULSE" and best["wave"] in ("5", "A"):
        sign = 1 if bullish else -1
        w = _legs(p)
        # fib extension reached? W5 ≥ 61.8% of W1-W3 distance
        if w[0] and w[4] >= w[0] * 0.618:
            exh_notes.append("W5 reached typical extension of W1")
        # RSI divergence between W3 and W5 extremes
        try:
            i3, i5 = p[3]["i"], p[5]["i"]
            if rsi[i3] is not None and rsi[i5] is not None:
                if (prices[5] - prices[3]) * sign > 0 and (rsi[i5] - rsi[i3]) * sign < 0:
                    exh_notes.append("RSI divergence W3→W5")
            if macd[i3] is not None and macd[i5] is not None:
                if (prices[5] - prices[3]) * sign > 0 and (macd[i5] - macd[i3]) * sign < 0:
                    exh_notes.append("MACD divergence W3→W5")
        except (IndexError, TypeError):
            pass
        if volumes and len(volumes) >= 20 and volumes[-1] < (sum(volumes[-20:]) / 20) * 0.9:
            exh_notes.append("fading volume")
        wave5_exh = len(exh_notes) >= 2

    wave_labels = (["1", "2", "3", "4", "5"] if best["type"] == "IMPULSE"
                   else ["A", "B", "C"] if best["type"] == "ABC"
                   else ["1", "2", "3"])
    chart_pivots = [
        {"time": pv["time"], "price": round(pv["price"], 8),
         "label": wave_labels[k] if k < len(wave_labels) else "?"}
        for k, pv in enumerate(p[1:])
    ]

    structure = ("IMPULSE" if best["type"] in ("IMPULSE", "WAVE3_SETUP") else "ABC CORRECTION")
    return {
        "timeframe": timeframe,
        "structure": structure,
        "wave": best["wave"],
        "direction": best["direction"],
        "confidence": conf_pct,
        "confidenceLabel": _label(best["conf"]),
        "notes": best["notes"] + exh_notes,
        "fib": _fib_levels(lo, hi, bullish),
        "fibLocation": _fib_location(close, lo, hi, bullish),
        "wave3Candidate": best["type"] == "WAVE3_SETUP" and best["conf"] >= 0.55,
        "wave5Exhaustion": wave5_exh,
        "abcCandidate": best["type"] == "ABC" and best["conf"] >= 0.55,
        "pivots": chart_pivots,
    }


# ---------------------------------------------------------------------------
# Multi-timeframe wrapper
# ---------------------------------------------------------------------------
def _aggregate_rows(rows: list[list[Any]], factor: int) -> list[list[Any]]:
    out: list[list[Any]] = []
    start = len(rows) % factor
    for i in range(start, len(rows) - factor + 1, factor):
        chunk = rows[i:i + factor]
        out.append([chunk[0][0], float(chunk[0][1]),
                    max(float(r[2]) for r in chunk), min(float(r[3]) for r in chunk),
                    float(chunk[-1][4]), 0.0, sum(float(r[6]) for r in chunk)])
    return out


def analyze_elliott(fifteen: list[list[Any]]) -> dict[str, Any]:
    """Full multi-timeframe Elliott analysis from cached 15m candles."""
    tf15 = analyze_timeframe(fifteen, "15m")
    tf1h = analyze_timeframe(_aggregate_rows(fifteen, 4), "1h")
    tf4h = analyze_timeframe(_aggregate_rows(fifteen, 16), "4h")
    frames = [tf15, tf1h, tf4h]

    bulls = sum(1 for t in frames if t["direction"] == "BULLISH" and t["structure"] != "UNCERTAIN")
    bears = sum(1 for t in frames if t["direction"] == "BEARISH" and t["structure"] != "UNCERTAIN")
    if bulls >= 2 and bears == 0:
        alignment = "STRONG BULLISH ALIGNMENT"
    elif bears >= 2 and bulls == 0:
        alignment = "STRONG BEARISH ALIGNMENT"
    elif bulls == 0 and bears == 0:
        alignment = "UNCERTAIN"
    else:
        alignment = "MIXED"

    # Headline = most confident non-uncertain frame, preferring 1h on ties.
    ranked = sorted(frames, key=lambda t: (t["structure"] != "UNCERTAIN", t["confidence"],
                                           t["timeframe"] == "1h"), reverse=True)
    head = ranked[0]
    return {
        "structure": head["structure"],
        "wave": head["wave"],
        "direction": head["direction"] if head["structure"] != "UNCERTAIN" else "NEUTRAL",
        "confidence": head["confidence"],
        "confidenceLabel": head["confidenceLabel"],
        "headlineTimeframe": head["timeframe"],
        "alignment": alignment,
        "wave3Candidate": any(t["wave3Candidate"] for t in frames),
        "wave5Exhaustion": any(t["wave5Exhaustion"] for t in frames),
        "abcCandidate": any(t["abcCandidate"] for t in frames),
        "fibLocation": head["fibLocation"],
        "timeframes": {
            "15m": {k: v for k, v in tf15.items() if k not in ("pivots", "fib")},
            "1h": {k: v for k, v in tf1h.items() if k not in ("pivots", "fib")},
            "4h": {k: v for k, v in tf4h.items() if k not in ("pivots", "fib")},
        },
    }


def elliott_entry_record(elliott: dict[str, Any] | None, trade_direction: str) -> dict[str, Any]:
    """Compact snapshot stored on every ACTIVE/SCANNER entry (observation mode)."""
    if not elliott:
        return {"available": False, "aligned": "UNKNOWN"}
    tf = elliott.get("timeframes") or {}
    direction = elliott.get("direction") or "NEUTRAL"
    if elliott.get("structure") == "UNCERTAIN" or direction == "NEUTRAL":
        aligned = "MIXED"
    elif (direction == "BULLISH") == (trade_direction == "LONG"):
        aligned = "YES"
    else:
        aligned = "NO"
    high_conf_exhaustion = bool(elliott.get("wave5Exhaustion")) and (elliott.get("confidence") or 0) >= 70
    return {
        "available": True,
        "direction": direction,
        "wave": elliott.get("wave"),
        "confidence": elliott.get("confidence"),
        "confidenceLabel": elliott.get("confidenceLabel"),
        "alignment": elliott.get("alignment"),
        "wave15m": (tf.get("15m") or {}).get("wave"),
        "wave1h": (tf.get("1h") or {}).get("wave"),
        "wave4h": (tf.get("4h") or {}).get("wave"),
        "fibLocation": elliott.get("fibLocation"),
        "wave3Candidate": bool(elliott.get("wave3Candidate")),
        "wave5Exhaustion": bool(elliott.get("wave5Exhaustion")),
        "abcCandidate": bool(elliott.get("abcCandidate")),
        "aligned": aligned,
        # Counterfactual: would the (currently OFF) wave-5 veto have blocked this?
        "wave5VetoWouldBlock": high_conf_exhaustion,
    }
