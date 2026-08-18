#!/usr/bin/env python3
"""
hybrid_research.py
==================
NEW HYBRID CRYPTO ALGORITHM — BACKTEST / RESEARCH ONLY.
The live paper_trader.py is NOT touched. No real-money execution.

╔═══════════════════════════════════════════════════════════════════════════╗
║ PREDETERMINED RULES — documented BEFORE running, never tuned to results  ║
╚═══════════════════════════════════════════════════════════════════════════╝

MARKETS   BTC ETH SOL XRP LINK (Binance USDT, ~3 years where available)
TIMEFRAME CONFIGS (only two, per spec):
  A = 30m entry + 1h structure + 4h context
  B = 15m entry + 1h structure + 4h context

STEP 1 — MARKET REGIME ENGINE (evaluated on the 4h context timeframe):
  TREND_UP   : close > EMA50 > EMA200, EMA50 rising (vs 3 bars ago),
               MACD ≥ signal
  TREND_DOWN : mirror image
  DANGER     : any of — entry-TF ATR > 3× its 20-period mean;
               current entry candle range > 3× ATR mean (flash move);
               |close − EMA50(4h)| > 4× ATR(4h) (extremely extended);
               required indicators unavailable.
               In DANGER: NO NEW TRADES (open positions still managed).
  RANGE      : everything else (no strong 4h direction).

STEP 2 — TREND STRATEGY (regime TREND_UP → LONG pullbacks; mirror for DOWN):
  1. 4h regime trending, 1h trend not opposing (not BEARISH for longs)
  2. price pulled back to within 1.0× ATR of EMA20 or EMA50 (entry TF)
  3. RSI in pullback zone (LONG: 35–55) and turning up (rsi[i] > rsi[i-1])
  4. MACD improving (macd rising vs previous bar, or fresh cross)
  5. confirmation: close back above EMA20 (LONG)
  6. not chasing: candle range ≤ 2.5× ATR and close ≤ 1.5×ATR above EMA20
  Weighted score must also pass threshold (see STEP 5).
  Stop: 1.5× ATR.  TP tested: 1.5R and 2R (both reported, no other values).

STEP 3 — RANGE / MEAN-REVERSION (regime RANGE only):
  LONG : close ≤ lower Bollinger (20, 2σ) × 1.002; RSI < 35 and rising;
         4h trend NOT bearish; MACD histogram improving.
  SHORT: mirror (upper band, RSI > 65 falling, 4h not bullish).
  Stop: 1.2× ATR (single predetermined value in the 1.0–1.5 spec window).
  TP tested: (a) Bollinger midline (SMA20), (b) 1R.  Reported separately.
  Range entries use this checklist INSTEAD of the weighted score because a
  neutral 4h+1h regime caps the score at 4/8 by construction — documented
  design decision, made before running.

STEP 4 — BREAKOUT:
  Compression: prior 20-bar range width < 3× the 20-period ATR mean.
  LONG : CLOSE breaks above prior 20-bar high (close beyond, not wick);
         volume ≥ 1.3× 20-bar mean OR ATR ≥ 1.1× its mean (expansion);
         1h trend not opposing; candle range ≤ 2.5× ATR (no oversized bar).
  SHORT: mirror.  Score must also pass threshold.
  Stop: 1.5× ATR.  TP: same 1.5R / 2R variants as TREND.

STEP 5 — WEIGHTED SCORE (TREND & BREAKOUT entries):
  4h context = 2 | entry-TF structure = 2 | RSI = 1 | MACD = 1
  | EMA/price = 1 | volume = 1  → max 8.  Thresholds tested: 6 and 7 ONLY.

HARD RULES (always override): no LONG vs clearly bearish 4h, no SHORT vs
clearly bullish 4h, no trades in DANGER, no trade without indicators.

RE-ENTRY PROTECTION: after any close → ≥ 4 entry candles AND signal reset
(score below threshold / setup gone) before re-entry; force re-arm after 12
candles.  Never re-enter on the closing candle itself.

RISK: 1% per trade, one position per instrument, no martingale/averaging.
PORTFOLIO CORRELATION RULE (predetermined): max 2 concurrent open crypto
positions across the portfolio; additional signals are SKIPPED (not scaled).

COSTS (main results): Kraken taker 0.26%/fill + 0.05% slippage/spread per
fill = 0.62% round trip.  GROSS vs COSTS vs NET reported.

VALIDATION: per-coin 70% IS / 30% OOS by time.  Rules frozen before OOS.
"""
from __future__ import annotations

import json
import math
import os
import time as _time
from collections import deque
from typing import Any

from opportunity_research import (
    ema_series, rsi_series, fetch_binance, aggregate, build_pre,
    compute_metrics, run_original_66_bt, _trend_at, _compute_score_fast,
    STARTING_BALANCE, RISK_PER_TRADE, DAILY_LOSS_LIMIT,
    MAX_CONSECUTIVE_LOSSES, CRYPTO_FEE_PER_FILL,
)

MARKETS   = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
             "XRP": "XRPUSDT", "LINK": "LINKUSDT"}
DAYS      = 3 * 365
CACHE_DIR = "/tmp/hybrid_data"
THRESHOLDS = [6, 7]
TREND_RRS  = [1.5, 2.0]
ATR_STOP_TREND = 1.5
ATR_STOP_RANGE = 1.2
COOLDOWN_MIN   = 4
COOLDOWN_FORCE = 12
MAX_CONCURRENT = 2   # portfolio correlation cap


# ─── Extended precompute (adds what build_pre lacks) ─────────────────────────

def extend_pre(pre: dict) -> dict:
    closes, n = pre["closes"], pre["n"]
    pre["e200"] = ema_series(closes, 200)
    # SMA20 / STD20 via running sums (O(n))
    sma20: list = [None] * n
    std20: list = [None] * n
    s = s2 = 0.0
    for i in range(n):
        s += closes[i]; s2 += closes[i] * closes[i]
        if i >= 20:
            s -= closes[i - 20]; s2 -= closes[i - 20] * closes[i - 20]
        if i >= 19:
            m = s / 20
            var = max(0.0, s2 / 20 - m * m)
            sma20[i] = m
            std20[i] = math.sqrt(var)
    pre["sma20"], pre["std20"] = sma20, std20
    return pre


# ─── Regime engine (predetermined; see module docstring) ─────────────────────

def regime_at(pre4: dict, i4: int, pre_e: dict, ei: int) -> str:
    e50, e200 = pre4["e50"][i4], pre4["e200"][i4]
    macd, sig = pre4["macd"][i4], pre4["macd_sig"][i4]
    cl4       = pre4["closes"][i4]
    atr_e     = pre_e["atr"][ei]
    atr_ma    = pre_e["atr_ma20"][ei]
    atr4      = pre4["atr"][i4]

    if None in (e50, e200, macd, sig, atr_e, atr4) or atr_ma is None or atr_ma <= 0:
        return "DANGER"
    if atr_e > 3.0 * atr_ma:
        return "DANGER"
    rng = pre_e["highs"][ei] - pre_e["lows"][ei]
    if rng > 3.0 * atr_ma:
        return "DANGER"
    if abs(cl4 - e50) > 4.0 * atr4:
        return "DANGER"

    e50_prev = pre4["e50"][i4 - 3] if i4 >= 3 else None
    rising  = e50_prev is not None and e50 > e50_prev
    falling = e50_prev is not None and e50 < e50_prev
    if cl4 > e50 > e200 and rising and macd >= sig:
        return "UP"
    if cl4 < e50 < e200 and falling and macd <= sig:
        return "DOWN"
    return "RANGE"


# ─── Strategy detectors ──────────────────────────────────────────────────────

def detect_trend_pullback(pre_e: dict, ei: int, pre1: dict, i1: int,
                          regime: str) -> str | None:
    if regime not in ("UP", "DOWN") or ei < 2:
        return None
    cl   = pre_e["closes"][ei]
    e20  = pre_e["e20"][ei]
    e50  = pre_e["e50"][ei]
    rsi  = pre_e["rsi"][ei]
    rsi_p = pre_e["rsi"][ei - 1]
    macd  = pre_e["macd"][ei]
    macd_p = pre_e["macd"][ei - 1]
    atr  = pre_e["atr"][ei]
    if None in (e20, e50, rsi, rsi_p, macd, macd_p, atr) or atr <= 0:
        return None
    rng = pre_e["highs"][ei] - pre_e["lows"][ei]
    if rng > 2.5 * atr:                       # do not chase extended candles
        return None
    lo, hi = pre_e["lows"][ei], pre_e["highs"][ei]

    if regime == "UP":
        if pre1["trend"][i1] == "R":          # 1h opposing
            return None
        touched = (lo <= e20 + atr) or (lo <= e50 + atr)
        if not touched:                       # pulled back toward EMA20/50
            return None
        if not (35 <= rsi <= 55 and rsi > rsi_p):
            return None
        if not (macd > macd_p or pre_e["cross_up"][ei]):
            return None
        if not (cl > e20):                    # confirmation close
            return None
        if cl - e20 > 1.5 * atr:              # already too far from the EMA
            return None
        return "LONG"
    else:
        if pre1["trend"][i1] == "B":
            return None
        touched = (hi >= e20 - atr) or (hi >= e50 - atr)
        if not touched:
            return None
        if not (45 <= rsi <= 65 and rsi < rsi_p):
            return None
        if not (macd < macd_p or pre_e["cross_dn"][ei]):
            return None
        if not (cl < e20):
            return None
        if e20 - cl > 1.5 * atr:
            return None
        return "SHORT"


def detect_range_mr(pre_e: dict, ei: int, pre4: dict, i4: int) -> str | None:
    if ei < 2:
        return None
    cl    = pre_e["closes"][ei]
    mid   = pre_e["sma20"][ei]
    std   = pre_e["std20"][ei]
    rsi   = pre_e["rsi"][ei]
    rsi_p = pre_e["rsi"][ei - 1]
    macd  = pre_e["macd"][ei]
    macd_p = pre_e["macd"][ei - 1]
    if None in (mid, std, rsi, rsi_p, macd, macd_p) or std <= 0:
        return None
    lower, upper = mid - 2 * std, mid + 2 * std
    htf = pre4["trend"][i4]
    if cl <= lower * 1.002 and rsi < 35 and rsi > rsi_p and macd > macd_p and htf != "R":
        return "LONG"
    if cl >= upper * 0.998 and rsi > 65 and rsi < rsi_p and macd < macd_p and htf != "B":
        return "SHORT"
    return None


def detect_breakout(pre_e: dict, ei: int, pre1: dict, i1: int) -> str | None:
    if ei < 21:
        return None
    cl   = pre_e["closes"][ei]
    vol  = pre_e["vols"][ei]
    atr  = pre_e["atr"][ei]
    avgv = pre_e["vol_ma"][ei]
    atr_ma = pre_e["atr_ma20"][ei]
    hi20, lo20 = pre_e["high20"][ei], pre_e["low20"][ei]
    if None in (atr, avgv, atr_ma) or avgv <= 0 or atr_ma <= 0 or hi20 <= 0:
        return None
    rng = pre_e["highs"][ei] - pre_e["lows"][ei]
    if rng > 2.5 * atr:                        # oversized candle — skip
        return None
    if (hi20 - lo20) > 3.0 * atr_ma * 20 ** 0.5:  # not compressed enough
        pass  # width check below is the binding one
    if (hi20 - lo20) > 6.0 * atr_ma:           # compression filter
        return None
    expansion = (vol >= avgv * 1.3) or (atr >= atr_ma * 1.1)
    if not expansion:
        return None
    if cl > hi20 and pre1["trend"][i1] != "R":
        return "LONG"
    if cl < lo20 and pre1["trend"][i1] != "B":
        return "SHORT"
    return None


# ─── Hybrid backtest ─────────────────────────────────────────────────────────

def shift_ts(pre: dict, offset: int) -> dict:
    """Return a shallow copy of a precomputed dict whose timestamps are moved
    by `offset` seconds.  Used to make higher-TF bars usable only once they
    have CLOSED relative to the entry bar's close (no look-ahead):
    a HTF bar starting at t (duration D) is knowable at t+D; the entry bar
    (duration d) decision happens at cts+d.  Shifting HTF ts by D-d makes the
    plain `shifted_ts <= cts` comparison equivalent to `t+D <= cts+d`."""
    out = dict(pre)
    out["ts"] = [t + offset for t in pre["ts"]]
    return out


def run_hybrid_bt(label: str, pre_e: dict, pre1: dict, pre4: dict,
                  threshold: int, trend_rr: float, range_tp: str,
                  fee: float, split_ts: int) -> dict:
    balance = STARTING_BALANCE
    open_pos = None
    trades: list[dict] = []
    curve = [balance]
    daily_loss, day_key, consec = 0.0, -1, 0
    i1 = i4 = 0
    ts1, ts4 = pre1["ts"], pre4["ts"]
    cooldown, armed, last_dir = COOLDOWN_FORCE, True, None
    regime_counts = {"UP": 0, "DOWN": 0, "RANGE": 0, "DANGER": 0}

    for i in range(220, pre_e["n"]):
        cts = pre_e["ts"][i]
        ch, clo, ccl = pre_e["highs"][i], pre_e["lows"][i], pre_e["closes"][i]
        while i1 + 1 < len(ts1) and ts1[i1 + 1] <= cts: i1 += 1
        while i4 + 1 < len(ts4) and ts4[i4 + 1] <= cts: i4 += 1

        if pre_e["atr"][i] is None or pre4["e200"][i4] is None:
            curve.append(balance); continue

        dn = pre_e["day_num"][i]
        if dn != day_key:
            day_key, daily_loss, consec = dn, 0.0, 0

        # manage open position
        if open_pos is not None:
            d, sl, tp = open_pos["direction"], open_pos["stop_loss"], open_pos["take_profit"]
            ent, qty = open_pos["entry"], open_pos["quantity"]
            hit_sl = (clo <= sl) if d == "LONG" else (ch >= sl)
            hit_tp = (ch >= tp) if d == "LONG" else (clo <= tp)
            if hit_sl or hit_tp:
                ep = sl if hit_sl else tp
                er = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
                exit_fee = ep * qty * fee
                pnl = ((ep - ent) * qty if d == "LONG" else (ent - ep) * qty) - exit_fee
                balance += pnl
                net = pnl - open_pos["entry_fee"]
                daily_loss += -net if net < 0 else 0.0
                consec = consec + 1 if net < 0 else 0
                trades.append({
                    "direction": d, "entry": ent, "exit": ep, "pnl": net,
                    "qty": qty, "exit_reason": er,
                    "opened_at": open_pos["opened_at"], "closed_at": cts,
                    "duration_h": (cts - open_pos["opened_at"]) / 3600,
                    "balance_after": balance,
                    "balance_before": balance - net,
                    "fees_paid": open_pos["entry_fee"] + exit_fee,
                    "strategy_type": open_pos["strategy_type"],
                    "risk_amount": open_pos["risk_amount"],
                    # period assigned by OPEN time: the entry decision (and its
                    # fee) belongs to the period in which it was made
                    "period": "OOS" if split_ts and open_pos["opened_at"] >= split_ts else "IS",
                })
                open_pos = None
                cooldown, armed, last_dir = 0, False, d

        curve.append(balance)
        if open_pos is not None:
            continue

        cooldown += 1
        regime = regime_at(pre4, i4, pre_e, i)
        regime_counts[regime] += 1

        # re-arm: new candle(s) + setup gone (score below thr in both dirs
        # AND no active range/breakout in the last-traded direction)
        if not armed:
            s1, _ = _compute_score_fast(last_dir, pre_e, i, pre4, i4)
            opp_d = "SHORT" if last_dir == "LONG" else "LONG"
            s2, _ = _compute_score_fast(opp_d, pre_e, i, pre4, i4)
            same_range = detect_range_mr(pre_e, i, pre4, i4) == last_dir
            if (s1 < threshold and s2 < threshold and not same_range) or cooldown >= COOLDOWN_FORCE:
                armed = True
        if cooldown < COOLDOWN_MIN or not armed:
            continue
        if (daily_loss >= STARTING_BALANCE * DAILY_LOSS_LIMIT or
                consec >= MAX_CONSECUTIVE_LOSSES or balance <= 0):
            continue
        if regime == "DANGER":
            continue

        # ── signal generation by regime ──
        direction: str | None = None
        stype = ""
        stop_mult, rr = ATR_STOP_TREND, trend_rr
        tp_override = None

        if regime in ("UP", "DOWN"):
            direction = detect_trend_pullback(pre_e, i, pre1, i1, regime)
            stype = "TREND"
            if direction is None:
                direction = detect_breakout(pre_e, i, pre1, i1)
                stype = "BREAKOUT"
        else:  # RANGE
            direction = detect_range_mr(pre_e, i, pre4, i4)
            stype = "RANGE"
            stop_mult, rr = ATR_STOP_RANGE, 1.0
            if direction is None:
                direction = detect_breakout(pre_e, i, pre1, i1)
                stype = "BREAKOUT"
                stop_mult, rr = ATR_STOP_TREND, trend_rr

        if direction is None:
            continue

        # hard direction rules vs 4h
        htf = _trend_at(pre4, i4)
        if direction == "LONG" and htf == "BEARISH":  continue
        if direction == "SHORT" and htf == "BULLISH": continue

        # weighted score gate for TREND & BREAKOUT (RANGE uses its checklist)
        if stype in ("TREND", "BREAKOUT"):
            sc, _ = _compute_score_fast(direction, pre_e, i, pre4, i4)
            if sc < threshold:
                continue

        atr = pre_e["atr"][i]
        stop_dist = atr * stop_mult
        risk_amt = balance * RISK_PER_TRADE
        qty = min(risk_amt / stop_dist, balance / ccl if ccl > 0 else 0)
        if qty <= 0:
            continue
        sl0 = ccl - stop_dist if direction == "LONG" else ccl + stop_dist
        if stype == "RANGE" and range_tp == "mid":
            mid = pre_e["sma20"][i]
            tp_override = mid
            if (direction == "LONG" and mid <= ccl) or (direction == "SHORT" and mid >= ccl):
                continue  # target on wrong side — no trade
        tp0 = tp_override if tp_override is not None else (
            ccl + stop_dist * rr if direction == "LONG" else ccl - stop_dist * rr)
        fee_cost = ccl * qty * fee
        balance -= fee_cost
        open_pos = {"direction": direction, "entry": ccl, "stop_loss": sl0,
                    "take_profit": tp0, "quantity": qty, "opened_at": cts,
                    "entry_fee": fee_cost, "strategy_type": stype,
                    "risk_amount": risk_amt}

    if open_pos is not None:
        ep = pre_e["closes"][-1]; d = open_pos["direction"]
        exit_fee = ep * open_pos["quantity"] * fee
        pnl = ((ep - open_pos["entry"]) * open_pos["quantity"] if d == "LONG"
               else (open_pos["entry"] - ep) * open_pos["quantity"]) - exit_fee
        balance += pnl
        net = pnl - open_pos["entry_fee"]
        last_ts = pre_e["ts"][-1]
        trades.append({"direction": d, "entry": open_pos["entry"], "exit": ep,
                       "pnl": net, "qty": open_pos["quantity"],
                       "exit_reason": "END_OF_DATA",
                       "opened_at": open_pos["opened_at"], "closed_at": last_ts,
                       "duration_h": (last_ts - open_pos["opened_at"]) / 3600,
                       "balance_after": balance,
                       "balance_before": balance - net,
                       "fees_paid": open_pos["entry_fee"] + exit_fee,
                       "strategy_type": open_pos["strategy_type"],
                       "risk_amount": open_pos["risk_amount"],
                       "period": "OOS" if split_ts and open_pos["opened_at"] >= split_ts else "IS"})
    return {"label": label, "trades": trades, "balance_curve": curve,
            "final_balance": balance, "starting_bal": STARTING_BALANCE,
            "split_ts": split_ts, "regime_counts": regime_counts}


# ─── Extra metrics required by the brief ─────────────────────────────────────

def extra_metrics(trades: list[dict], start_bal: float) -> dict:
    if not trades:
        return {"avg_hold_h": None, "pct_prof_months": None,
                "long": (0, 0.0), "short": (0, 0.0), "avg_r": None,
                "gross": 0.0, "costs": 0.0, "net": 0.0}
    hold = sum(t["duration_h"] for t in trades) / len(trades)
    months: dict[str, float] = {}
    for t in trades:
        mk = _time.strftime("%Y-%m", _time.gmtime(t["closed_at"]))
        months[mk] = months.get(mk, 0.0) + t["pnl"]
    prof = sum(1 for v in months.values() if v > 0) / len(months) * 100
    longs  = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]
    costs = sum(t["fees_paid"] for t in trades)
    net = sum(t["pnl"] for t in trades)
    rs = [t["pnl"] / t["risk_amount"] for t in trades if t.get("risk_amount")]
    return {"avg_hold_h": hold, "pct_prof_months": prof,
            "long": (len(longs), sum(t["pnl"] for t in longs)),
            "short": (len(shorts), sum(t["pnl"] for t in shorts)),
            "avg_r": sum(rs) / len(rs) if rs else None,
            "gross": net + costs, "costs": costs, "net": net}


def by_strategy(trades: list[dict]) -> dict:
    out: dict[str, list] = {}
    for t in trades:
        out.setdefault(t["strategy_type"], []).append(t)
    return out


# ─── Portfolio replay with correlation cap ───────────────────────────────────

def portfolio_replay(selected: dict[str, list[dict]], cap: int) -> dict:
    """Chronological replay of per-coin trades. Each coin has a £100 sleeve.
    A trade is admitted only if < cap positions are open at its open time;
    P&L is applied as R × 1% × current sleeve balance (documented approx.)."""
    events = []
    for coin, trades in selected.items():
        for t in trades:
            r = t["pnl"] / t["risk_amount"] if t.get("risk_amount") else 0.0
            events.append((t["opened_at"], t["closed_at"], coin, r))
    events.sort()
    sleeves = {c: STARTING_BALANCE for c in selected}
    open_until: list[tuple[int, str]] = []
    admitted, skipped, overlap_samples = [], 0, []
    curve_ts, curve_val = [], []
    for o, c, coin, r in events:
        open_until = [(cc, cn) for cc, cn in open_until if cc > o]
        overlap_samples.append(len(open_until))
        if len(open_until) >= cap:
            skipped += 1
            continue
        pnl = r * RISK_PER_TRADE * sleeves[coin]
        sleeves[coin] = max(0.0, sleeves[coin] + pnl)
        open_until.append((c, coin))
        admitted.append({"coin": coin, "opened_at": o, "closed_at": c,
                         "pnl": pnl, "r": r})
        curve_ts.append(c); curve_val.append(sum(sleeves.values()))
    return {"sleeves": sleeves, "admitted": admitted, "skipped": skipped,
            "overlaps": overlap_samples, "curve_ts": curve_ts,
            "curve_val": curve_val,
            "start": STARTING_BALANCE * len(selected)}


# ─── Data ────────────────────────────────────────────────────────────────────

def load_series(coin: str) -> dict[str, list]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = f"{CACHE_DIR}/{coin}.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    sym = MARKETS[coin]
    print(f"  fetching {coin} 15m (~3y)...", flush=True)
    c15 = fetch_binance(sym, DAYS, "15m")
    print(f"    {len(c15)} x 15m candles", flush=True)
    data = {
        "c15": c15,
        "c30": aggregate(c15, 1800),
        "c1h": aggregate(c15, 3600),
        "c4h": aggregate(c15, 14400),
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return data


# ─── Report helpers ──────────────────────────────────────────────────────────

def m_row(m: dict, x: dict) -> str:
    pf = m.get("profit_factor")
    pf_s = "∞" if isinstance(pf, float) and math.isinf(pf) else (f"{pf:.2f}" if pf else "0.00")
    return (f"n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  roi={m['roi']:>+8.1f}%  "
            f"pf={pf_s:>5}  dd={m['max_dd']:>5.1f}%  exp=£{m['expectancy']:>+6.3f}  "
            f"tpw={m['tpw'] if m['tpw'] is not None else 0:>5.2f}  "
            f"avgR={x['avg_r'] if x['avg_r'] is not None else 0:>+5.2f}  "
            f"hold={x['avg_hold_h'] or 0:>5.1f}h")


def period_metrics(res: dict, days_is: float, days_oos: float):
    trades = res["trades"]
    is_t  = [t for t in trades if t["period"] == "IS"]
    oos_t = [t for t in trades if t["period"] == "OOS"]
    is_end = is_t[-1]["balance_after"] if is_t else STARTING_BALANCE
    m_is  = compute_metrics(is_t, STARTING_BALANCE, days_is)
    m_oos = compute_metrics(oos_t, is_end, days_oos)
    m_full = compute_metrics(trades, STARTING_BALANCE, days_is + days_oos)
    return (m_is, extra_metrics(is_t, STARTING_BALANCE)), \
           (m_oos, extra_metrics(oos_t, is_end)), \
           (m_full, extra_metrics(trades, STARTING_BALANCE))


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = _time.time()
    print("=" * 100)
    print("HYBRID ALGORITHM RESEARCH — regime engine + trend/range/breakout — BACKTEST ONLY")
    print("=" * 100, flush=True)

    all_results: dict[str, dict] = {}       # coin → {config_label: result}
    ref_66: dict[str, dict] = {}
    spans: dict[str, tuple[float, float, int]] = {}

    for coin in MARKETS:
        print(f"\n[{coin}] loading data...", flush=True)
        data = load_series(coin)
        c15, c30, c1h, c4h = data["c15"], data["c30"], data["c1h"], data["c4h"]
        if len(c15) < 5000:
            print(f"  !! insufficient data ({len(c15)} candles) — skipping"); continue

        split_ts = c15[int(len(c15) * 0.7)][0]
        first, last = c15[0][0], c15[-1][0]
        days_is  = (split_ts - first) / 86400
        days_oos = (last - split_ts) / 86400
        spans[coin] = (days_is, days_oos, split_ts)
        print(f"  span {(last-first)/86400:.0f}d | IS {days_is:.0f}d | OOS {days_oos:.0f}d", flush=True)

        pre15 = extend_pre(build_pre(c15))
        pre30 = extend_pre(build_pre(c30))
        pre1h = extend_pre(build_pre(c1h))
        pre4h = extend_pre(build_pre(c4h))
        print(f"  precompute done ({_time.time()-t0:.0f}s)", flush=True)

        # No-look-ahead HTF sync: shift HTF timestamps by (HTF duration −
        # entry duration) so a HTF bar only becomes visible once it has
        # closed relative to the entry bar's close.
        pre1_30, pre4_30 = shift_ts(pre1h, 3600 - 1800), shift_ts(pre4h, 14400 - 1800)
        pre1_15, pre4_15 = shift_ts(pre1h, 3600 - 900),  shift_ts(pre4h, 14400 - 900)

        results: dict[str, dict] = {}
        for tf_label, pre_e, p1, p4 in (("A30m", pre30, pre1_30, pre4_30),
                                        ("B15m", pre15, pre1_15, pre4_15)):
            for thr in THRESHOLDS:
                for rr in TREND_RRS:
                    lbl = f"{tf_label}-{thr}of8-{rr}R-mid"
                    results[lbl] = run_hybrid_bt(lbl, pre_e, p1, p4,
                                                 thr, rr, "mid",
                                                 CRYPTO_FEE_PER_FILL, split_ts)
        # range-TP comparison on one predetermined config (A30m, 6/8, 2R)
        results["A30m-6of8-2.0R-1R"] = run_hybrid_bt(
            "A30m-6of8-2.0R-1R", pre30, pre1_30, pre4_30, 6, 2.0, "1r",
            CRYPTO_FEE_PER_FILL, split_ts)

        # current live 6/6 reference (1h entry + 4h confirm, as live bot);
        # same no-look-ahead shift for the 4h confirm series
        ref_66[coin] = run_original_66_bt(f"{coin}-66", pre1h,
                                          shift_ts(pre4h, 14400 - 3600),
                                          CRYPTO_FEE_PER_FILL, split_ts)
        all_results[coin] = results
        print(f"  {len(results)} hybrid configs + 6/6 ref done ({_time.time()-t0:.0f}s)", flush=True)

    # ══ REPORTS ══════════════════════════════════════════════════════════════
    for coin, results in all_results.items():
        days_is, days_oos, _ = spans[coin]
        print(f"\n{'='*100}\n{coin} — per-configuration results (£100 start, fees+slippage included)\n{'='*100}")
        for lbl, res in results.items():
            (mi, xi), (mo, xo), (mf, xf) = period_metrics(res, days_is, days_oos)
            print(f"\n  {lbl}   regimes {res['regime_counts']}")
            print(f"    IS  : {m_row(mi, xi)}")
            print(f"    OOS : {m_row(mo, xo)}")
            print(f"    FULL: {m_row(mf, xf)}  gross=£{xf['gross']:+.2f} costs=£{xf['costs']:.2f} net=£{xf['net']:+.2f}")
            print(f"          L/S: {xf['long'][0]}L £{xf['long'][1]:+.2f} / {xf['short'][0]}S £{xf['short'][1]:+.2f}"
                  f"  | prof.months {xf['pct_prof_months'] or 0:.0f}%")
            for st, ts_ in sorted(by_strategy(res["trades"]).items()):
                stm = compute_metrics(ts_, STARTING_BALANCE, days_is + days_oos)
                pf = stm.get("profit_factor")
                pf_s = "∞" if isinstance(pf, float) and math.isinf(pf) else f"{pf:.2f}"
                print(f"          {st:<9} n={stm['n']:>4} wr={stm['win_rate']:>5.1f}% pf={pf_s} netP&L=£{sum(t['pnl'] for t in ts_):+.2f}")

        r66 = ref_66[coin]
        (mi, xi), (mo, xo), (mf, xf) = period_metrics(r66, days_is, days_oos)
        print(f"\n  CURRENT-6/6 (1h entry)")
        print(f"    IS  : {m_row(mi, xi)}")
        print(f"    OOS : {m_row(mo, xo)}")
        print(f"    FULL: {m_row(mf, xf)}")

    # 6/8 vs 7/8 summary (best RR per threshold on A30m, OOS)
    print(f"\n{'='*100}\n6/8 vs 7/8 COMPARISON (A30m configs, OOS period)\n{'='*100}")
    print(f"{'coin':<6}{'thr':<5}{'RR':<5}{'n/mo':<7}{'ROI':<10}{'PF':<7}{'MaxDD':<8}{'Exp £':<9}")
    for coin, results in all_results.items():
        days_is, days_oos, _ = spans[coin]
        for thr in THRESHOLDS:
            for rr in TREND_RRS:
                res = results[f"A30m-{thr}of8-{rr}R-mid"]
                (_, _), (mo, xo), _ = period_metrics(res, days_is, days_oos)
                pf = mo.get("profit_factor") or 0
                pf_s = "∞" if isinstance(pf, float) and math.isinf(pf) else f"{pf:.2f}"
                print(f"{coin:<6}{thr:<5}{rr:<5}{(mo['tpm'] or 0):<7.1f}{mo['roi']:<+10.1f}{pf_s:<7}{mo['max_dd']:<8.1f}{mo['expectancy']:<+9.3f}")

    # Portfolio: select configs on IS expectancy ONLY, then replay their
    # untouched OOS trades (no post-selection on OOS results)
    print(f"\n{'='*100}\nPORTFOLIO (configs selected on IS expectancy only; OOS replay; cap {MAX_CONCURRENT} concurrent)\n{'='*100}")
    selected: dict[str, list[dict]] = {}
    for coin, results in all_results.items():
        days_is, days_oos, _ = spans[coin]
        best_lbl, best_exp = None, 0.0
        for lbl, res in results.items():
            (mi, _), (_, _), _ = period_metrics(res, days_is, days_oos)
            if mi["n"] >= 30 and mi["expectancy"] > best_exp:
                best_lbl, best_exp = lbl, mi["expectancy"]
        if best_lbl:
            oos_trades = [t for t in results[best_lbl]["trades"] if t["period"] == "OOS"]
            selected[coin] = oos_trades
            print(f"  {coin}: {best_lbl} (IS exp £{best_exp:+.3f}, OOS n={len(oos_trades)})")
    if selected:
        port = portfolio_replay(selected, MAX_CONCURRENT)
        total = sum(port["sleeves"].values())
        ov = port["overlaps"]
        print(f"\n  start £{port['start']:.2f} → final £{total:.2f} "
              f"(ROI {((total/port['start'])-1)*100:+.1f}%)")
        print(f"  admitted {len(port['admitted'])} trades, skipped {port['skipped']} (correlation cap)")
        if ov:
            from collections import Counter
            cnt = Counter(ov)
            print(f"  concurrent-positions at signal time: " +
                  ", ".join(f"{k}:{v}" for k, v in sorted(cnt.items())))
        months: dict[str, float] = {}
        for t in port["admitted"]:
            mk = _time.strftime("%Y-%m", _time.gmtime(t["closed_at"]))
            months[mk] = months.get(mk, 0.0) + t["pnl"]
        if months:
            worst = min(months, key=months.get); best = max(months, key=months.get)
            prof = sum(1 for v in months.values() if v > 0) / len(months) * 100
            print(f"  worst month {worst} £{months[worst]:+.2f} | best {best} £{months[best]:+.2f} | profitable months {prof:.0f}%")
    else:
        print("  NO coin/config had positive OOS expectancy — no portfolio formed.")

    print(f"\nDone in {_time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
