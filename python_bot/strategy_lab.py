"""STRATEGY LAB — parallel shadow-strategy simulation. PAPER / SIMULATION ONLY.

Whenever the market scanner sees a new completed 15m candle, the lab:
1. Records a SIGNAL for any direction scoring >= 3/6 (full entry context kept
   raw so experiments can be re-run later — §26).
2. Opens SHADOW TRADES for that signal — one per exit profile. These never
   touch the real paper accounts.
3. Advances open shadow trades bar-by-bar on the same cached candles.

Entry thresholds (3/6..6/6), the five risk levels, and Elliott filters are
applied mathematically at query time: a 5/6 signal counts for the 3/6, 4/6 and
5/6 experiments, and one trade result feeds every risk model. This keeps the
scan fast — one market observation feeds all experiments (§27).

HARD RULE: nothing in this module may read from or write to the real position
tables, and nothing in the main strategies reads lab results. The main ACTIVE
strategy stays at its configured gate; promotion is a human decision (§22-23).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from paper_trader import add_activity, now_iso, safe_float

# ---------------------------------------------------------------------------
# Configuration (defaults; fee/slippage overridable via bot_config)
# ---------------------------------------------------------------------------
LAB_MIN_SCORE = 3
LAB_MAX_BARS = 96          # 24h max hold on 15m candles
LAB_FEE_PCT_PER_SIDE = 0.26   # Kraken taker estimate, configurable
LAB_THRESHOLDS = (3, 4, 5, 6)

# Exit profiles (§4-5). pct values are % of entry price; atr values are ATR
# multiples captured at signal time. be = move stop to entry after the given
# favourable move (% or R). trail = trailing stop distance.
EXIT_PROFILES: dict[str, dict[str, Any]] = {
    "SCALP_A":  {"tpPct": 0.30, "slPct": 0.25},
    "SCALP_B":  {"tpPct": 0.50, "slPct": 0.30},
    "BALANCED": {"tpPct": 0.75, "slPct": 0.50, "bePct": 0.40},
    "TREND":    {"slAtr": 2.0, "trailAtr": 1.5, "trailAfterR": 1.0, "beR": 1.0},
    "DYNAMIC":  {"slAtr": 1.5, "tpAtr": 2.5, "beR": 1.0},
}

RISK_LEVELS = (0.5, 1.0, 1.5, 2.0, 2.5)

CONFIDENCE_STAGES = (
    (500, "LARGE SAMPLE"), (250, "GOOD SAMPLE"), (100, "MODERATE CONFIDENCE"),
    (30, "LOW CONFIDENCE"), (0, "VERY LOW CONFIDENCE"),
)


def sample_label(n: int) -> str:
    for floor, label in CONFIDENCE_STAGES:
        if n >= floor:
            return label
    return "VERY LOW CONFIDENCE"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init_lab_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS lab_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            candle_ts   INTEGER NOT NULL,
            price       REAL NOT NULL,
            atr         REAL,
            spread_pct  REAL,
            score       INTEGER NOT NULL,
            long_score  INTEGER,
            short_score INTEGER,
            conditions  TEXT,
            indicators  TEXT,
            elliott     TEXT,
            regime      TEXT,
            trend_15m   TEXT,
            trend_1h    TEXT,
            trend_4h    TEXT,
            main_blocked TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE (ticker, direction, candle_ts)
        );
        CREATE TABLE IF NOT EXISTS lab_trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id    INTEGER NOT NULL REFERENCES lab_signals(id),
            ticker       TEXT NOT NULL,
            direction    TEXT NOT NULL,
            profile      TEXT NOT NULL,
            entry        REAL NOT NULL,
            initial_stop REAL NOT NULL,
            stop         REAL NOT NULL,
            target       REAL,
            status       TEXT NOT NULL DEFAULT 'OPEN',
            opened_ts    INTEGER NOT NULL,
            last_ts      INTEGER NOT NULL,
            bars_held    INTEGER NOT NULL DEFAULT 0,
            best_price   REAL,
            be_armed     INTEGER NOT NULL DEFAULT 0,
            exit         REAL,
            exit_reason  TEXT,
            closed_at    TEXT,
            gross_pct    REAL,
            cost_pct     REAL,
            net_pct      REAL,
            net_r        REAL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_lab_trades_open
            ON lab_trades (ticker, status);
        CREATE INDEX IF NOT EXISTS idx_lab_trades_profile
            ON lab_trades (profile, status);
        """
    )


def _cfg_float(connection: sqlite3.Connection, key: str, default: float) -> float:
    row = connection.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Market-regime classifier (§10) — observation only
# ---------------------------------------------------------------------------
def classify_regime(rows: list[list[Any]], indicators: dict[str, Any]) -> str:
    if len(rows) < 60:
        return "RANGE"
    closes = [float(r[4]) for r in rows]
    close = closes[-1]
    e20, e50, atr = indicators.get("ema20"), indicators.get("ema50"), indicators.get("atr")
    # volatility vs recent norm: ATR as % of price vs 20-candle price stddev
    atr_pct = (atr / close * 100) if atr and close else 0.0
    rets = [abs(closes[i] / closes[i - 1] - 1) * 100 for i in range(len(closes) - 40, len(closes)) if closes[i - 1]]
    avg_move = sum(rets) / len(rets) if rets else 0.0
    if atr_pct > max(0.9, avg_move * 3.0):
        return "HIGH VOLATILITY"
    if atr_pct < min(0.12, avg_move * 0.6):
        return "LOW VOLATILITY"
    if e20 and e50:
        spread = (e20 - e50) / e50 * 100
        if close > e20 > e50:
            return "STRONG UPTREND" if spread > 0.35 else "UPTREND"
        if close < e20 < e50:
            return "STRONG DOWNTREND" if spread < -0.35 else "DOWNTREND"
    return "RANGE"


# ---------------------------------------------------------------------------
# Signal + shadow-trade recording (called from scan_market on NEW candle only)
# ---------------------------------------------------------------------------
def record_lab_observation(
    connection: sqlite3.Connection, ticker: str, dir_eval: dict[str, Any],
    fifteen: list[list[Any]], elliott: dict[str, Any] | None,
    spread_pct: float | None, main_blocked: str | None,
    trend_4h: str | None = None,
) -> None:
    """Record signals + open shadow trades for one asset's new candle.
    Never touches real positions. Failures must not break the scan."""
    candle_ts = int(fifteen[-1][0])
    price = float(fifteen[-1][4])
    indicators = dir_eval.get("indicators") or {}
    atr = safe_float(indicators.get("atr"))
    regime = classify_regime(fifteen, indicators)

    for direction in ("LONG", "SHORT"):
        score = dir_eval[direction.lower()]["score"]
        if score < LAB_MIN_SCORE:
            continue
        # One live opportunity per ticker+direction: skip while shadow trades
        # from the previous signal are still open (no pyramiding).
        open_ct = connection.execute(
            "SELECT COUNT(*) FROM lab_trades WHERE ticker=? AND direction=? AND status='OPEN'",
            (ticker, direction),
        ).fetchone()[0]
        if open_ct:
            continue
        conditions = [cd["name"] for cd in dir_eval[direction.lower()]["conditions"] if cd["pass"]]
        from elliott_wave import elliott_entry_record
        ell_record = elliott_entry_record(elliott, direction)
        cur = connection.execute(
            """INSERT OR IGNORE INTO lab_signals
               (ticker, direction, candle_ts, price, atr, spread_pct, score,
                long_score, short_score, conditions, indicators, elliott, regime,
                trend_15m, trend_1h, trend_4h, main_blocked, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, direction, candle_ts, price, atr, spread_pct, score,
             dir_eval["long"]["score"], dir_eval["short"]["score"],
             json.dumps(conditions), json.dumps(indicators),
             json.dumps(ell_record), regime,
             dir_eval.get("fifteenTrend"), dir_eval.get("oneHourTrend"), trend_4h,
             main_blocked, now_iso()),
        )
        if cur.rowcount == 0:
            continue
        signal_id = cur.lastrowid
        if not atr or atr <= 0:
            continue  # signal kept; trades need ATR for TREND/DYNAMIC sizing
        for profile, p in EXIT_PROFILES.items():
            sign = 1 if direction == "LONG" else -1
            sl_dist = (price * p["slPct"] / 100) if "slPct" in p else atr * p["slAtr"]
            stop = price - sign * sl_dist
            target = None
            if "tpPct" in p:
                target = price + sign * price * p["tpPct"] / 100
            elif "tpAtr" in p:
                target = price + sign * atr * p["tpAtr"]
            connection.execute(
                """INSERT INTO lab_trades
                   (signal_id, ticker, direction, profile, entry, initial_stop,
                    stop, target, opened_ts, last_ts, best_price, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal_id, ticker, direction, profile, price, stop, stop,
                 target, candle_ts, candle_ts, price, now_iso()),
            )


def advance_lab_trades(
    connection: sqlite3.Connection, ticker: str, fifteen: list[list[Any]],
) -> int:
    """Advance this asset's open shadow trades over any new candles.
    Conservative fill rules: on a bar hitting both SL and TP, SL wins."""
    open_rows = connection.execute(
        "SELECT * FROM lab_trades WHERE ticker=? AND status='OPEN'", (ticker,),
    ).fetchall()
    if not open_rows:
        return 0
    fee_side = _cfg_float(connection, "lab_fee_pct_per_side", LAB_FEE_PCT_PER_SIDE)
    closed = 0
    by_ts = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in fifteen]  # ts, open, high, low, close
    for t in open_rows:
        sig = connection.execute(
            "SELECT atr, spread_pct FROM lab_signals WHERE id=?", (t["signal_id"],),
        ).fetchone()
        atr = safe_float(sig["atr"]) if sig else None
        spread = (safe_float(sig["spread_pct"]) if sig else None) or 0.0
        p = EXIT_PROFILES[t["profile"]]
        sign = 1 if t["direction"] == "LONG" else -1
        entry, stop, target = float(t["entry"]), float(t["stop"]), t["target"]
        best = float(t["best_price"] or entry)
        be_armed = bool(t["be_armed"])
        bars = int(t["bars_held"])
        last_ts = int(t["last_ts"])
        exit_price = exit_reason = None
        exit_ts = last_ts

        for ts, open_, high, low, close in by_ts:
            if ts <= last_ts:
                continue
            bars += 1
            exit_ts = ts
            # stop first (conservative, gap-aware). A stop crossed at the bar
            # OPEN fills at that adverse open (the stop was never tradable);
            # a stop crossed intrabar fills at the stop.
            hit_stop = low <= stop if sign > 0 else high >= stop
            if hit_stop:
                gapped_open = (open_ <= stop) if sign > 0 else (open_ >= stop)
                fill = open_ if gapped_open else stop
                exit_price = fill
                exit_reason = ("BREAK_EVEN" if be_armed and abs(stop - entry) < abs(float(t["initial_stop"]) - entry) * 0.5
                               else "STOP_LOSS") + ("_GAP" if gapped_open else "")
                break
            if target is not None and ((high >= target) if sign > 0 else (low <= target)):
                exit_price = target
                exit_reason = "TAKE_PROFIT"
                break
            # favourable extreme + management
            fav = high if sign > 0 else low
            if (fav - best) * sign > 0:
                best = fav
            moved = (best - entry) * sign
            risk_dist = abs(entry - float(t["initial_stop"]))
            # break-even arming
            be_trigger = None
            if "bePct" in p:
                be_trigger = entry * p["bePct"] / 100
            elif "beR" in p and risk_dist > 0:
                be_trigger = risk_dist * p["beR"]
            if not be_armed and be_trigger is not None and moved >= be_trigger:
                be_armed = True
                if (entry - stop) * sign > 0:
                    stop = entry
            # trailing (TREND)
            if "trailAtr" in p and atr and risk_dist > 0 and moved >= risk_dist * p.get("trailAfterR", 1.0):
                trail = best - sign * atr * p["trailAtr"]
                if (trail - stop) * sign > 0:
                    stop = trail
            if bars >= LAB_MAX_BARS:
                exit_price = close
                exit_reason = "MAX_HOLD_TIME"
                break

        if exit_price is None:
            connection.execute(
                "UPDATE lab_trades SET stop=?, best_price=?, be_armed=?, bars_held=?, last_ts=? WHERE id=?",
                (stop, best, int(be_armed), bars, exit_ts if by_ts else last_ts, t["id"]),
            )
            continue
        gross_pct = (exit_price - entry) / entry * 100 * sign
        cost_pct = fee_side * 2 + spread / 2 * 2  # fees both sides + half spread per side
        net_pct = gross_pct - cost_pct
        risk_pct = abs(entry - float(t["initial_stop"])) / entry * 100
        net_r = (net_pct / risk_pct) if risk_pct > 0 else None
        connection.execute(
            """UPDATE lab_trades SET status='CLOSED', exit=?, exit_reason=?, closed_at=?,
               gross_pct=?, cost_pct=?, net_pct=?, net_r=?, stop=?, best_price=?,
               be_armed=?, bars_held=?, last_ts=? WHERE id=?""",
            (exit_price, exit_reason, now_iso(), round(gross_pct, 5), round(cost_pct, 5),
             round(net_pct, 5), round(net_r, 5) if net_r is not None else None,
             stop, best, int(be_armed), bars, exit_ts, t["id"]),
        )
        closed += 1
    return closed


# ---------------------------------------------------------------------------
# Statistics engine — thresholds, risk models, filters applied at query time
# ---------------------------------------------------------------------------
def _stats_from_rs(rs: list[float], risk_pct: float = 1.0, start: float = 1000.0) -> dict[str, Any]:
    n = len(rs)
    base = {"trades": n, "confidence": sample_label(n)}
    if n == 0:
        base.update({"wins": 0, "losses": 0, "winRate": None, "netR": 0.0, "roiPct": None,
                     "profitFactor": None, "expectancy": None, "maxDrawdownPct": None,
                     "sharpe": None, "avgWin": None, "avgLoss": None, "longestLossStreak": 0,
                     "insufficientData": True})
        return base
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))
    # compounded equity at the given risk level
    eq, peak, max_dd = start, start, 0.0
    streak = worst_streak = 0
    for r in rs:
        eq *= (1 + risk_pct / 100 * r)
        eq = max(eq, 0.0)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
        streak = streak + 1 if r <= 0 else 0
        worst_streak = max(worst_streak, streak)
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / n
    std = var ** 0.5
    base.update({
        "wins": len(wins), "losses": len(losses),
        "winRate": round(len(wins) / n * 100, 1),
        "netR": round(sum(rs), 2),
        "roiPct": round((eq / start - 1) * 100, 2),
        "profitFactor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "expectancy": round(mean, 3),
        "maxDrawdownPct": round(max_dd, 2),
        "sharpe": round(mean / std, 2) if std > 0 else None,
        "avgWin": round(gross_w / len(wins), 2) if wins else None,
        "avgLoss": round(-gross_l / len(losses), 2) if losses else None,
        "longestLossStreak": worst_streak,
        "insufficientData": n < 30,
    })
    return base


def _closed_trades(connection: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[sqlite3.Row]:
    return connection.execute(
        f"""SELECT t.id, t.ticker, t.direction, t.profile, t.net_r, t.net_pct,
                   t.gross_pct, t.cost_pct, t.closed_at, t.exit_reason, t.opened_ts, t.last_ts,
                   s.score, s.elliott, s.regime, s.conditions, s.main_blocked
            FROM lab_trades t JOIN lab_signals s ON s.id = t.signal_id
            WHERE t.status='CLOSED' AND t.net_r IS NOT NULL {where}
            ORDER BY t.closed_at""",
        params,
    ).fetchall()


def strategy_id(threshold: int, profile: str) -> str:
    return f"{threshold}/6 + {profile}"


def _rows_for_strategy(rows: list[sqlite3.Row], threshold: int, profile: str) -> list[sqlite3.Row]:
    return [r for r in rows if r["profile"] == profile and (r["score"] or 0) >= threshold]


def _elliott_bucket(row: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(row["elliott"]) if row["elliott"] else {}
    except (ValueError, TypeError):
        return {}


def lab_overview() -> dict[str, Any]:
    from market_scanner import SCANNER_ASSETS  # noqa: F401  (universe reference)
    from paper_trader import db, init_db
    connection = db()
    init_db(connection)
    init_lab_db(connection)
    try:
        rows = _closed_trades(connection)
        open_ct = connection.execute("SELECT COUNT(*) FROM lab_trades WHERE status='OPEN'").fetchone()[0]
        signal_ct = connection.execute("SELECT COUNT(*) FROM lab_signals").fetchone()[0]
        blocked_ct = connection.execute(
            "SELECT COUNT(*) FROM lab_signals WHERE main_blocked IS NOT NULL").fetchone()[0]
        fee_side = _cfg_float(connection, "lab_fee_pct_per_side", LAB_FEE_PCT_PER_SIDE)

        # Leaderboard: 4 thresholds × 5 profiles (§11)
        leaderboard = []
        for th in LAB_THRESHOLDS:
            for profile in EXIT_PROFILES:
                sub = _rows_for_strategy(rows, th, profile)
                st = _stats_from_rs([r["net_r"] for r in sub])
                st.update({"strategy": strategy_id(th, profile), "threshold": th, "profile": profile})
                # promotion candidate (§22): robust sample, profitable after costs, contained DD
                st["promotionCandidate"] = bool(
                    st["trades"] >= 100 and (st["profitFactor"] or 0) >= 1.3
                    and (st["expectancy"] or 0) > 0 and (st["maxDrawdownPct"] or 100) < 20
                )
                leaderboard.append(st)
        qualified = [s for s in leaderboard if s["trades"] >= 30 and s["expectancy"] is not None]
        pool = qualified or [s for s in leaderboard if s["trades"] > 0]
        best_exp = max(pool, key=lambda s: s["expectancy"] or -9e9, default=None)
        best_pf = max(pool, key=lambda s: s["profitFactor"] or -9e9, default=None)
        best_roi = max(pool, key=lambda s: s["roiPct"] or -9e9, default=None)
        low_dd = min((s for s in pool if s["trades"] > 0),
                     key=lambda s: s["maxDrawdownPct"] if s["maxDrawdownPct"] is not None else 9e9, default=None)
        best_sharpe = max(pool, key=lambda s: s["sharpe"] or -9e9, default=None)

        # LONG vs SHORT overall + per asset (§8-9), on the 4/6+DYNAMIC baseline pool
        def dir_stats(sub: list[sqlite3.Row]) -> dict[str, Any]:
            return {
                "LONG": _stats_from_rs([r["net_r"] for r in sub if r["direction"] == "LONG"]),
                "SHORT": _stats_from_rs([r["net_r"] for r in sub if r["direction"] == "SHORT"]),
            }
        base_rows = [r for r in rows if (r["score"] or 0) >= 4]
        per_asset = []
        for tk in sorted({r["ticker"] for r in base_rows}):
            sub = [r for r in base_rows if r["ticker"] == tk]
            d = dir_stats(sub)
            per_asset.append({"ticker": tk, "long": d["LONG"], "short": d["SHORT"]})
        per_asset.sort(key=lambda a: -(a["long"]["trades"] + a["short"]["trades"]))

        # Elliott buckets (§7)
        def ell_filter(fn) -> dict[str, Any]:
            return _stats_from_rs([r["net_r"] for r in base_rows if fn(_elliott_bucket(r))])
        elliott = {
            "all": _stats_from_rs([r["net_r"] for r in base_rows]),
            "aligned": ell_filter(lambda e: e.get("aligned") == "YES"),
            "notAligned": ell_filter(lambda e: e.get("aligned") == "NO"),
            "wave3": ell_filter(lambda e: bool(e.get("wave3Candidate"))),
            "wave5Exhaustion": ell_filter(lambda e: bool(e.get("wave5Exhaustion"))),
            "abc": ell_filter(lambda e: bool(e.get("abcCandidate"))),
            "uncertain": ell_filter(lambda e: not e.get("available") or e.get("wave") is None),
        }

        # Regimes (§10)
        regimes = {}
        for rg in ("STRONG UPTREND", "UPTREND", "RANGE", "DOWNTREND", "STRONG DOWNTREND",
                   "HIGH VOLATILITY", "LOW VOLATILITY"):
            regimes[rg] = _stats_from_rs([r["net_r"] for r in base_rows if r["regime"] == rg])

        # Combinations (§21)
        def has_cond(row: sqlite3.Row, name: str) -> bool:
            try:
                return name in (json.loads(row["conditions"]) or [])
            except (ValueError, TypeError):
                return False
        combos = []
        combo_defs = [
            ("RSI + MACD Momentum", lambda r: has_cond(r, "RSI") and has_cond(r, "MACD Momentum")),
            ("EMA20 + 15m Trend", lambda r: has_cond(r, "Price vs EMA20") and has_cond(r, "15m Trend")),
            ("Volume + 1h Confirmation", lambda r: has_cond(r, "Volume") and has_cond(r, "1h Confirmation")),
            ("4/6 + Elliott aligned", lambda r: (r["score"] or 0) >= 4 and _elliott_bucket(r).get("aligned") == "YES"),
            ("5/6 + Wave 3", lambda r: (r["score"] or 0) >= 5 and bool(_elliott_bucket(r).get("wave3Candidate"))),
            ("4/6 + STRONG UPTREND", lambda r: (r["score"] or 0) >= 4 and r["regime"] == "STRONG UPTREND"),
            ("4/6 + HIGH VOLATILITY", lambda r: (r["score"] or 0) >= 4 and r["regime"] == "HIGH VOLATILITY"),
        ]
        for name, fn in combo_defs:
            st = _stats_from_rs([r["net_r"] for r in rows if fn(r)])
            st["name"] = name
            combos.append(st)

        # Risk models applied to the current leader (§6)
        leader = best_exp
        risk_models = []
        if leader:
            leader_rs = [r["net_r"] for r in _rows_for_strategy(rows, leader["threshold"], leader["profile"])]
            for rl in RISK_LEVELS:
                st = _stats_from_rs(leader_rs, risk_pct=rl)
                st["riskPct"] = rl
                risk_models.append(st)
            # Drawdown-protection experiment (§16) at 1% risk
            dd_protected = _simulate_dd_protection(leader_rs, 1.0)
            dd_constant = _stats_from_rs(leader_rs, risk_pct=1.0)
        else:
            dd_protected, dd_constant = None, None

        # Correlation protection (§17): cap 2 concurrent same-direction trades
        corr = _correlation_experiment(rows)

        # Missed opportunities (§19)
        missed_rows = [r for r in base_rows if r["main_blocked"]]
        missed = {
            "signalsBlocked": blocked_ct,
            "performanceIfTaken": _stats_from_rs(
                [r["net_r"] for r in missed_rows if r["profile"] == "DYNAMIC"]),
        }

        total_closed = len(rows)
        return {
            "summary": {
                "experimentsRunning": len(leaderboard),
                "openShadowTrades": open_ct,
                "totalShadowTrades": total_closed,
                "totalSignals": signal_ct,
                "dataConfidence": sample_label(total_closed),
                "bestStrategy": best_exp["strategy"] if best_exp else None,
                "bestProfitFactor": ({"strategy": best_pf["strategy"], "value": best_pf["profitFactor"]} if best_pf else None),
                "bestExpectancy": ({"strategy": best_exp["strategy"], "value": best_exp["expectancy"]} if best_exp else None),
                "lowestDrawdown": ({"strategy": low_dd["strategy"], "value": low_dd["maxDrawdownPct"]} if low_dd else None),
                "highestNetReturn": ({"strategy": best_roi["strategy"], "value": best_roi["roiPct"]} if best_roi else None),
                "bestRiskAdjusted": ({"strategy": best_sharpe["strategy"], "value": best_sharpe["sharpe"]} if best_sharpe else None),
                "feePctPerSide": fee_side,
                "costsIncluded": True,
                "mainStrategyLocked": "ACTIVE 4/6 — Strategy Lab is observation only and cannot change it",
            },
            "leaderboard": leaderboard,
            "longShort": dir_stats(base_rows),
            "perAsset": per_asset[:30],
            "elliott": elliott,
            "regimes": regimes,
            "combinations": combos,
            "riskModels": risk_models,
            "drawdownProtection": ({"protected": dd_protected, "constant": dd_constant} if dd_protected else None),
            "correlationProtection": corr,
            "missedOpportunities": missed,
        }
    finally:
        connection.close()


def _simulate_dd_protection(rs: list[float], risk_pct: float) -> dict[str, Any] | None:
    """Dynamic risk: -25% at 5% DD, -50% at 8% DD, -75% at 10% DD; restore on
    new equity high (§16)."""
    if not rs:
        return None
    eq, peak, max_dd = 1000.0, 1000.0, 0.0
    for r in rs:
        dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
        scale = 1.0 if dd < 5 else 0.75 if dd < 8 else 0.5 if dd < 10 else 0.25
        eq *= (1 + risk_pct / 100 * scale * r)
        eq = max(eq, 0.0)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100)
    return {"trades": len(rs), "roiPct": round((eq / 1000.0 - 1) * 100, 2),
            "maxDrawdownPct": round(max_dd, 2)}


def _correlation_experiment(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Compare unrestricted vs 'max 2 concurrent same-direction' on the
    4/6 + DYNAMIC pool, using trade open/close intervals (§17)."""
    pool = sorted(
        (r for r in rows if r["profile"] == "DYNAMIC" and (r["score"] or 0) >= 4),
        key=lambda r: r["opened_ts"],
    )
    unrestricted = [r["net_r"] for r in pool]
    taken: list[float] = []
    active: list[tuple[int, str]] = []  # (exit candle ts, direction)
    for r in pool:
        opened = r["opened_ts"]
        closed = int(r["last_ts"] or 0) or opened + 3600  # last_ts = simulated exit bar
        active = [(c, d) for c, d in active if c > opened]
        same_dir = sum(1 for _, d in active if d == r["direction"])
        if same_dir < 2:
            taken.append(r["net_r"])
            active.append((closed, r["direction"]))
    return {"off": _stats_from_rs(unrestricted), "on": _stats_from_rs(taken)}


def lab_strategy_detail(strategy: str, start: float = 1000.0, risk_pct: float = 1.0) -> dict[str, Any]:
    """Equity curve + drawdown + risk table for one strategy (§6, §14, §16)."""
    from paper_trader import db, init_db
    try:
        th_part, profile = strategy.split(" + ", 1)
        threshold = int(th_part.split("/")[0])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"Unknown strategy id: {strategy}"}
    if profile not in EXIT_PROFILES or threshold not in LAB_THRESHOLDS:
        return {"ok": False, "error": f"Unknown strategy id: {strategy}"}
    start = 100.0 if start == 100 else 1000.0
    risk_pct = risk_pct if risk_pct in RISK_LEVELS else 1.0
    connection = db()
    init_db(connection)
    init_lab_db(connection)
    try:
        rows = _rows_for_strategy(_closed_trades(connection), threshold, profile)
        rs = [r["net_r"] for r in rows]
        eq, peak = start, start
        points = []
        for r in rows:
            eq *= (1 + risk_pct / 100 * r["net_r"])
            eq = max(eq, 0.0)
            peak = max(peak, eq)
            points.append({
                "ts": r["closed_at"], "balance": round(eq, 2),
                "drawdownPct": round((peak - eq) / peak * 100, 2) if peak > 0 else 0.0,
            })
        stats = _stats_from_rs(rs, risk_pct=risk_pct, start=start)
        riskTable = []
        for rl in RISK_LEVELS:
            st = _stats_from_rs(rs, risk_pct=rl, start=start)
            st["riskPct"] = rl
            riskTable.append(st)
        return {
            "ok": True, "strategy": strategy, "startBalance": start, "riskPct": risk_pct,
            "stats": stats, "equity": points, "riskTable": riskTable,
            "drawdownProtection": _simulate_dd_protection(rs, risk_pct),
        }
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Activity-log milestones (§24) — called once per full scan, never per candle
# ---------------------------------------------------------------------------
def lab_notifications(connection: sqlite3.Connection) -> None:
    try:
        rows = _closed_trades(connection)
        if not rows:
            return
        stats = []
        for th in LAB_THRESHOLDS:
            for profile in EXIT_PROFILES:
                sub = _rows_for_strategy(rows, th, profile)
                if not sub:
                    continue
                st = _stats_from_rs([r["net_r"] for r in sub])
                st["strategy"] = strategy_id(th, profile)
                stats.append(st)

        def flag(key: str) -> str | None:
            row = connection.execute("SELECT value FROM bot_config WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

        def set_flag(key: str, value: str) -> None:
            connection.execute(
                "INSERT INTO bot_config (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

        # sample milestones
        for st in stats:
            for milestone in (100, 250, 500):
                key = f"lab_ms_{milestone}_{st['strategy']}"
                if st["trades"] >= milestone and flag(key) != "done":
                    add_activity(connection, "LAB", "LAB_MILESTONE",
                                 f"STRATEGY LAB: {st['strategy']} reached a {milestone}-trade sample "
                                 f"(PF {st['profitFactor']}, expectancy {st['expectancy']}R)")
                    set_flag(key, "done")
        # leader change (only among strategies with >=30 trades)
        qualified = [s for s in stats if s["trades"] >= 30 and s["expectancy"] is not None]
        if qualified:
            leader = max(qualified, key=lambda s: s["expectancy"])
            prev = flag("lab_last_leader")
            if leader["strategy"] != prev:
                add_activity(connection, "LAB", "LAB_LEADER",
                             f"NEW STRATEGY LEADER: {leader['strategy']} "
                             f"(expectancy {leader['expectancy']}R over {leader['trades']} trades, "
                             f"PF {leader['profitFactor']}) — observation only, main strategy unchanged")
                set_flag("lab_last_leader", leader["strategy"])
            # promotion candidate
            for s in qualified:
                if (s["trades"] >= 100 and (s["profitFactor"] or 0) >= 1.3
                        and (s["expectancy"] or 0) > 0 and (s["maxDrawdownPct"] or 100) < 20):
                    key = f"lab_promo_{s['strategy']}"
                    if flag(key) != "flagged":
                        add_activity(connection, "LAB", "LAB_PROMOTION_CANDIDATE",
                                     f"CANDIDATE FOR PROMOTION: {s['strategy']} — {s['trades']} trades, "
                                     f"PF {s['profitFactor']}, maxDD {s['maxDrawdownPct']}%. "
                                     f"No automatic change will be made.")
                        set_flag(key, "flagged")
        connection.commit()
    except Exception:
        pass  # notifications must never break the scan
