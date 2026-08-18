"""Whole-market crypto scanner + SCANNER paper account (PAPER ONLY).

Scans an expanded universe of ~30 cryptocurrencies on Kraken USD pairs every
completed 15-minute candle using the existing ACTIVE strategy logic (same
6 conditions, same configurable gate — default 4/6).

Trading:
- BTC/ETH/SOL/XRP remain traded ONLY by their dedicated £ accounts (the
  existing CORE + ACTIVE engines). The scanner ranks them but never trades them.
- All other assets are traded from ONE shared USD SCANNER paper account
  ($1,000 start) with conservative portfolio limits (all configurable via
  bot_config):
      scanner_max_positions        (default 3)
      scanner_risk_pct             (default 0.5   — % of balance per trade)
      scanner_max_total_risk_pct   (default 1.5   — % of balance at risk total)
      scanner_max_same_direction   (default 2     — correlation guard)
      scanner_min_volume_usd       (default 250000 — 24h liquidity floor)

No exchange keys, no real orders — every entry point calls _assert_paper_only().
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from paper_trader import (
    ACTIVE_ATR_MULTIPLIER,
    ACTIVE_MAX_SCORE,
    ACTIVE_REWARD_TO_RISK,
    _assert_paper_only,
    _update_price_extremes,
    add_activity,
    db,
    evaluate_active_directional,
    fetch_json,
    get_active_mode,
    indicator_snapshot,
    init_db,
    now_iso,
    round_price,
    safe_float,
    trend_for,
)

# ---------------------------------------------------------------------------
# Universe — ticker -> Kraken altname base + display name.
# BTC/ETH/SOL/XRP are ranked here (USD data) but traded by their £ accounts.
# ---------------------------------------------------------------------------
CORE_COINS = ("BTC", "ETH", "SOL", "XRP")

SCANNER_ASSETS: dict[str, dict[str, str]] = {
    "BTC":  {"kraken": "XBT",  "name": "Bitcoin"},
    "ETH":  {"kraken": "ETH",  "name": "Ethereum"},
    "SOL":  {"kraken": "SOL",  "name": "Solana"},
    "XRP":  {"kraken": "XRP",  "name": "XRP"},
    "BNB":  {"kraken": "BNB",  "name": "BNB"},
    "DOGE": {"kraken": "XDG",  "name": "Dogecoin"},
    "ADA":  {"kraken": "ADA",  "name": "Cardano"},
    "AVAX": {"kraken": "AVAX", "name": "Avalanche"},
    "LINK": {"kraken": "LINK", "name": "Chainlink"},
    "DOT":  {"kraken": "DOT",  "name": "Polkadot"},
    "LTC":  {"kraken": "LTC",  "name": "Litecoin"},
    "BCH":  {"kraken": "BCH",  "name": "Bitcoin Cash"},
    "TRX":  {"kraken": "TRX",  "name": "TRON"},
    "SUI":  {"kraken": "SUI",  "name": "Sui"},
    "TON":  {"kraken": "TON",  "name": "Toncoin"},
    "XLM":  {"kraken": "XLM",  "name": "Stellar"},
    "HBAR": {"kraken": "HBAR", "name": "Hedera"},
    "UNI":  {"kraken": "UNI",  "name": "Uniswap"},
    "AAVE": {"kraken": "AAVE", "name": "Aave"},
    "NEAR": {"kraken": "NEAR", "name": "NEAR Protocol"},
    "APT":  {"kraken": "APT",  "name": "Aptos"},
    "ARB":  {"kraken": "ARB",  "name": "Arbitrum"},
    "OP":   {"kraken": "OP",   "name": "Optimism"},
    "ICP":  {"kraken": "ICP",  "name": "Internet Computer"},
    "ETC":  {"kraken": "ETC",  "name": "Ethereum Classic"},
    "FIL":  {"kraken": "FIL",  "name": "Filecoin"},
    "ATOM": {"kraken": "ATOM", "name": "Cosmos"},
    "SHIB": {"kraken": "SHIB", "name": "Shiba Inu"},
    "PEPE": {"kraken": "PEPE", "name": "Pepe"},
    "POL":  {"kraken": "POL",  "name": "Polygon"},
}

SCANNER_STARTING_BALANCE = 1000.0   # USD — paper only
SCANNER_MAX_HOLD_HOURS   = 6        # same as ACTIVE
PAIR_MAP_TTL_HOURS       = 24

CONFIG_DEFAULTS: dict[str, float] = {
    "scanner_max_positions":      3,
    "scanner_risk_pct":           0.5,
    "scanner_max_total_risk_pct": 1.5,
    "scanner_max_same_direction": 2,
    "scanner_min_volume_usd":     250000,
}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def init_scanner_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scanner_state ("
        " ticker TEXT PRIMARY KEY, snapshot TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scanner_account ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " balance REAL NOT NULL, starting_balance REAL NOT NULL, created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS scanner_positions ("
        " ticker TEXT PRIMARY KEY, position TEXT NOT NULL)"
    )
    row = connection.execute("SELECT balance FROM scanner_account WHERE id = 1").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO scanner_account (id, balance, starting_balance, created_at) VALUES (1, ?, ?, ?)",
            (SCANNER_STARTING_BALANCE, SCANNER_STARTING_BALANCE, now_iso()),
        )
    connection.commit()


def _cfg(connection: sqlite3.Connection, key: str) -> float:
    row = connection.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    if row is not None:
        val = safe_float(row["value"])
        if val is not None:
            return val
    return CONFIG_DEFAULTS[key]


def _get_json_config(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return None


def _set_json_config(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO bot_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def _load_snapshot(connection: sqlite3.Connection, ticker: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT snapshot FROM scanner_state WHERE ticker = ?", (ticker,)
    ).fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row["snapshot"])
    except (ValueError, TypeError):
        return {}


def _save_snapshot(connection: sqlite3.Connection, ticker: str, snap: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO scanner_state (ticker, snapshot, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET snapshot = excluded.snapshot, updated_at = excluded.updated_at",
        (ticker, json.dumps(snap), now_iso()),
    )


# ---------------------------------------------------------------------------
# Kraken pair discovery (cached 24h in bot_config; fail closed per asset)
# ---------------------------------------------------------------------------
def _pair_map(connection: sqlite3.Connection) -> dict[str, str]:
    """ticker -> canonical Kraken USD pair key (as returned by AssetPairs)."""
    cached = _get_json_config(connection, "scanner_pair_map")
    if cached and cached.get("fetchedAt"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetchedAt"])
            if age < timedelta(hours=PAIR_MAP_TTL_HOURS) and cached.get("pairs"):
                return cached["pairs"]
        except (ValueError, TypeError):
            pass
    pairs: dict[str, str] = {}
    try:
        data = fetch_json("https://api.kraken.com/0/public/AssetPairs")
        by_base: dict[str, str] = {}
        for key, meta in data.items():
            ws = meta.get("wsname") or ""
            if ws.endswith("/USD"):
                by_base[ws.split("/")[0]] = key
        for ticker, meta in SCANNER_ASSETS.items():
            pair = by_base.get(meta["kraken"])
            if pair:
                pairs[ticker] = pair
        if pairs:
            _set_json_config(connection, "scanner_pair_map",
                             {"fetchedAt": now_iso(), "pairs": pairs})
            connection.commit()
    except Exception:
        # Fall back to stale cache if present — never fabricate pairs.
        if cached and cached.get("pairs"):
            return cached["pairs"]
    return pairs


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
def _batch_ticker(pairs: dict[str, str]) -> dict[str, dict[str, Any]]:
    """One Kraken Ticker call for the whole universe. ticker -> parsed fields."""
    if not pairs:
        return {}
    inverse = {pair: ticker for ticker, pair in pairs.items()}
    data = fetch_json("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(pairs.values()))
    out: dict[str, dict[str, Any]] = {}
    for key, tk in data.items():
        ticker = inverse.get(key)
        if ticker is None:
            continue
        price = safe_float(tk["c"][0]) if tk.get("c") else None
        opn   = safe_float(tk["o"]) if tk.get("o") else None
        vwap  = safe_float(tk["p"][1]) if tk.get("p") else None
        vol   = safe_float(tk["v"][1]) if tk.get("v") else None
        out[ticker] = {
            "price":     price,
            "change24h": round((price - opn) / opn * 100, 2) if price and opn else None,
            "high24":    safe_float(tk["h"][1]) if tk.get("h") else None,
            "low24":     safe_float(tk["l"][1]) if tk.get("l") else None,
            "volumeUsd": round(vol * vwap, 0) if vol is not None and vwap else None,
            "spreadPct": (
                round((safe_float(tk["a"][0]) - safe_float(tk["b"][0]))
                      / safe_float(tk["c"][0]) * 100, 4)
                if tk.get("a") and tk.get("b") and tk.get("c") and safe_float(tk["c"][0])
                else None
            ),
        }
    return out


def _fetch_15m(pair: str) -> list[list[Any]]:
    data = fetch_json(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=15")
    rows = None
    for key, val in data.items():
        if key != "last" and isinstance(val, list):
            rows = val
            break
    if not rows:
        raise RuntimeError(f"No 15m candles for {pair}")
    return rows[:-1]  # drop the in-progress candle


def _aggregate(rows: list[list[Any]], factor: int) -> list[list[Any]]:
    """Aggregate 15m rows into higher-timeframe rows (factor 4 = 1h, 16 = 4h)."""
    out: list[list[Any]] = []
    # Align so the LAST bucket ends on the newest completed candle.
    start = len(rows) % factor
    for i in range(start, len(rows) - factor + 1, factor):
        chunk = rows[i:i + factor]
        out.append([
            chunk[0][0],
            float(chunk[0][1]),
            max(float(r[2]) for r in chunk),
            min(float(r[3]) for r in chunk),
            float(chunk[-1][4]),
            0.0,
            sum(float(r[6]) for r in chunk),
        ])
    return out


def _trend_lite(rows: list[list[Any]]) -> str:
    """Directional label that still works with fewer than 55 rows."""
    if len(rows) >= 55:
        return trend_for(rows)
    if len(rows) < 21:
        return "NEUTRAL"
    snap = indicator_snapshot(rows)
    close = float(rows[-1][4])
    e20 = snap.get("ema20")
    if e20 is None:
        return "NEUTRAL"
    if close > e20 * 1.001:
        return "BULLISH"
    if close < e20 * 0.999:
        return "BEARISH"
    return "NEUTRAL"


def classify_signal(long_score: int, short_score: int) -> str:
    if long_score >= 5 and long_score > short_score:
        return "STRONG LONG"
    if short_score >= 5 and short_score > long_score:
        return "STRONG SHORT"
    if long_score >= 4 and long_score > short_score:
        return "LONG"
    if short_score >= 4 and short_score > long_score:
        return "SHORT"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# SCANNER account trading (PAPER ONLY)
# ---------------------------------------------------------------------------
def _account(connection: sqlite3.Connection) -> sqlite3.Row:
    return connection.execute("SELECT * FROM scanner_account WHERE id = 1").fetchone()


def _open_positions(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = connection.execute("SELECT ticker, position FROM scanner_positions").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["ticker"]] = json.loads(r["position"])
        except (ValueError, TypeError):
            pass
    return out


def _close_scanner_position(
    connection: sqlite3.Connection, ticker: str, position: dict[str, Any],
    exit_price: float, reason: str,
) -> None:
    _assert_paper_only()
    direction = position["direction"]
    quantity  = float(position["quantity"])
    entry     = float(position["entry"])
    pnl = (exit_price - entry) * quantity if direction == "LONG" else (entry - exit_price) * quantity
    account = _account(connection)
    balance = max(0.0, float(account["balance"]) + pnl)
    closed_at = now_iso()
    risk_amount = safe_float(position.get("riskAmount")) or 0.0
    r_multiple  = (pnl / risk_amount) if risk_amount > 0 else None
    pnl_pct     = (pnl / (entry * quantity) * 100) if entry and quantity else None
    try:
        duration = (datetime.fromisoformat(closed_at)
                    - datetime.fromisoformat(position["openedAt"])).total_seconds()
    except (ValueError, TypeError, KeyError):
        duration = None
    if risk_amount > 0 and abs(pnl) <= risk_amount * 0.1:
        result = "BREAKEVEN"
    else:
        result = "WIN" if pnl > 0 else "LOSS"
    # Cost estimates (recorded only): 0.26% taker per side + half spread per side
    notional = entry * quantity + exit_price * quantity
    est_fees = round(notional * 0.0026, 4)
    spread_pct = safe_float(position.get("entrySpreadPct")) or 0.0
    est_slippage = round(notional * (spread_pct / 100.0) / 2.0, 4)
    connection.execute(
        """
        INSERT INTO trades
            (coin, opened_at, closed_at, direction, entry, exit,
             stop_loss, take_profit, rsi, macd, atr, trend_4h,
             profit_loss, account_balance, exit_reason,
             risk_amount, r_multiple, pnl_pct, duration_seconds, result,
             entry_score, pass_count, trend_1h, entry_mode, entry_conditions,
             long_score, short_score, entry_threshold, strategy,
             est_fees, est_slippage, pnl_net, elliott)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticker, position["openedAt"], closed_at, direction, entry, exit_price,
            position["stopLoss"], position["takeProfit"],
            position.get("entryRsi"), position.get("entryMacd"), position.get("entryAtr"),
            position.get("trend4h") or "NEUTRAL", pnl, balance, reason,
            risk_amount, r_multiple, pnl_pct, duration, result,
            safe_float(position.get("entryScore")), position.get("passCount"),
            position.get("trend1h"), "SCANNER", position.get("entryConditions"),
            safe_float(position.get("longScore")), safe_float(position.get("shortScore")),
            safe_float(position.get("entryThreshold")), "ACTIVE",
            est_fees, est_slippage, round(pnl - est_fees - est_slippage, 4),
            json.dumps(position["elliott"]) if position.get("elliott") else None,
        ),
    )
    connection.execute("UPDATE scanner_account SET balance = ? WHERE id = 1", (balance,))
    connection.execute("DELETE FROM scanner_positions WHERE ticker = ?", (ticker,))
    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
    add_activity(
        connection, ticker, "TRADE_CLOSED",
        f"SCANNER {direction} closed ({reason.replace('_', ' ')}) | exit ${exit_price:,.4f} "
        f"| P&L {pnl_str} | {result} | scanner balance ${balance:.2f}",
    )
    connection.commit()


def _manage_position(
    connection: sqlite3.Connection, ticker: str, position: dict[str, Any],
    current_price: float,
) -> bool:
    """Exit engine (same semantics as ACTIVE): SL/TP gap-aware fills at live
    price, break-even at +1R, trailing 1xATR, 6h max hold. Returns True if closed."""
    direction = position["direction"]
    is_long = direction == "LONG"
    entry = float(position["entry"])
    stop = float(position["stopLoss"])
    tp = float(position["takeProfit"])
    _update_price_extremes(position, current_price)

    # Stop / target — cross fills at LIVE price (gap-aware, never fantasy fills)
    if (is_long and current_price <= stop) or (not is_long and current_price >= stop):
        _close_scanner_position(connection, ticker, position, current_price, "STOP_LOSS")
        return True
    if (is_long and current_price >= tp) or (not is_long and current_price <= tp):
        _close_scanner_position(connection, ticker, position, current_price, "TAKE_PROFIT")
        return True

    # Max hold
    try:
        held = datetime.fromisoformat(now_iso()) - datetime.fromisoformat(position["openedAt"])
        if held >= timedelta(hours=SCANNER_MAX_HOLD_HOURS):
            _close_scanner_position(connection, ticker, position, current_price, "MAX_HOLD_TIME")
            return True
    except (ValueError, TypeError, KeyError):
        pass

    # Break-even at +1R, then trail 1xATR — stops only ever tighten
    atr = safe_float(position.get("entryAtr")) or 0.0
    risk_dist = abs(entry - float(position.get("initialStop") or stop))
    new_stop = stop
    if risk_dist > 0 and atr > 0:
        gain = (current_price - entry) if is_long else (entry - current_price)
        if gain >= risk_dist:  # +1R reached
            be = entry
            trail = current_price - atr if is_long else current_price + atr
            candidate = max(be, trail) if is_long else min(be, trail)
            new_stop = max(stop, candidate) if is_long else min(stop, candidate)
    changed = new_stop != stop or True  # extremes may have changed too
    if changed:
        position["stopLoss"] = round_price(new_stop)
        connection.execute(
            "UPDATE scanner_positions SET position = ? WHERE ticker = ?",
            (json.dumps(position), ticker),
        )
        connection.commit()
    return False


def _try_enter(
    connection: sqlite3.Connection, ticker: str, decision: str,
    dir_eval: dict[str, Any], tick: dict[str, Any], gate: int,
    positions: dict[str, dict[str, Any]],
    elliott: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Attempt a SCANNER entry; returns (position, block_reason)."""
    price = tick.get("price")
    if not price:
        return None, "No live price"
    indicators = dir_eval.get("indicators") or {}
    atr = safe_float(indicators.get("atr"))
    if not atr or atr <= 0:
        return None, "No valid ATR"
    account = _account(connection)
    balance = float(account["balance"])
    max_positions = int(_cfg(connection, "scanner_max_positions"))
    if len(positions) >= max_positions:
        return None, f"Max open scanner positions reached ({max_positions})"
    same_dir = sum(1 for p in positions.values() if p["direction"] == decision)
    max_same = int(_cfg(connection, "scanner_max_same_direction"))
    if same_dir >= max_same:
        return None, f"Correlation guard: already {same_dir} {decision} positions (max {max_same})"
    risk_amount = balance * _cfg(connection, "scanner_risk_pct") / 100.0
    open_risk = sum(safe_float(p.get("riskAmount")) or 0.0 for p in positions.values())
    max_total_risk = balance * _cfg(connection, "scanner_max_total_risk_pct") / 100.0
    if open_risk + risk_amount > max_total_risk:
        return None, (f"Portfolio risk ceiling: ${open_risk:.2f} open + ${risk_amount:.2f} new "
                      f"> ${max_total_risk:.2f} max")
    stop_dist = atr * ACTIVE_ATR_MULTIPLIER
    stop = price - stop_dist if decision == "LONG" else price + stop_dist
    tp = (price + stop_dist * ACTIVE_REWARD_TO_RISK if decision == "LONG"
          else price - stop_dist * ACTIVE_REWARD_TO_RISK)
    quantity = min(risk_amount / stop_dist if stop_dist > 0 else 0,
                   balance / price if price > 0 else 0)
    if quantity <= 0:
        return None, "Insufficient balance for position sizing"
    _assert_paper_only()
    position = {
        "strategy":   "ACTIVE",
        "account":    "SCANNER",
        "direction":  decision,
        "entry":      round_price(price),
        "stopLoss":   round_price(stop),
        "initialStop": round_price(stop),
        "takeProfit": round_price(tp),
        "quantity":   quantity,
        "riskAmount": round(risk_amount, 2),
        "openedAt":   now_iso(),
        "entryRsi":   indicators.get("rsi"),
        "entryMacd":  indicators.get("macd"),
        "entryAtr":   atr,
        "trend1h":    dir_eval.get("oneHourTrend"),
        "trend4h":    dir_eval.get("oneHourTrend") if dir_eval.get("oneHourTrend") in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL",
        "entryScore": dir_eval[decision.lower()]["score"],
        "passCount":  dir_eval[decision.lower()]["score"],
        "maxScore":   ACTIVE_MAX_SCORE,
        "entryMode":  "SCANNER",
        "longScore":  dir_eval["long"]["score"],
        "shortScore": dir_eval["short"]["score"],
        "entryThreshold": gate,
        "entrySpreadPct": tick.get("spreadPct"),
        "bestPrice":  round_price(price),
        "worstPrice": round_price(price),
        "entryConditions": ", ".join(
            cd["name"] for cd in dir_eval[decision.lower()]["conditions"] if cd["pass"]
        ),
    }
    # Elliott observation mode: record state at entry, never influence it.
    try:
        from elliott_wave import elliott_entry_record
        position["elliott"] = elliott_entry_record(elliott, decision)
    except Exception:
        position["elliott"] = {"available": False, "aligned": "UNKNOWN"}
    connection.execute(
        "INSERT INTO scanner_positions (ticker, position) VALUES (?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET position = excluded.position",
        (ticker, json.dumps(position)),
    )
    add_activity(
        connection, ticker, "TRADE_OPENED",
        f"SCANNER {decision} opened at ${price:,.4f} "
        f"| LONG {dir_eval['long']['score']}/6 vs SHORT {dir_eval['short']['score']}/6 (gate {gate}/6) "
        f"| passed: {position['entryConditions']} "
        f"| SL ${stop:,.4f} | TP ${tp:,.4f} | risk ${risk_amount:.2f}",
    )
    connection.commit()
    return position, None


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------
def scan_market() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    init_scanner_db(connection)
    try:
        pairs = _pair_map(connection)
        try:
            ticks = _batch_ticker(pairs)
        except Exception as exc:
            ticks = {}
            tick_error = str(exc)[:200]
        else:
            tick_error = None
        _, gate = get_active_mode(connection)
        min_volume = _cfg(connection, "scanner_min_volume_usd")
        positions = _open_positions(connection)
        now = datetime.now(timezone.utc)
        scanned = 0
        entered: list[str] = []
        closed: list[str] = []

        for ticker, meta in SCANNER_ASSETS.items():
            snap = _load_snapshot(connection, ticker)
            snap["ticker"] = ticker
            snap["name"] = meta["name"]
            pair = pairs.get(ticker)
            if pair is None:
                snap.update({"dataAvailable": False,
                             "tradingEnabled": False,
                             "disabledReason": "No reliable Kraken USD pair — data unavailable"})
                _save_snapshot(connection, ticker, snap)
                continue
            tick = ticks.get(ticker)
            if tick:
                snap.update(tick)
                snap["dataAvailable"] = True
            elif tick_error and not snap.get("price"):
                snap["dataAvailable"] = False

            # Manage an open position on every run (live ticker price)
            pos = positions.get(ticker)
            if pos and tick and tick.get("price"):
                if _manage_position(connection, ticker, pos, float(tick["price"])):
                    positions.pop(ticker, None)
                    closed.append(ticker)
                    pos = None

            # Candle gate: evaluate only when a new completed 15m candle is due
            due = True
            last_candle = snap.get("lastCandleAt")
            if last_candle:
                try:
                    nxt = datetime.fromisoformat(last_candle) + timedelta(minutes=15)
                    due = now >= nxt.replace(tzinfo=nxt.tzinfo or timezone.utc)
                except (ValueError, TypeError):
                    due = True
            if due:
                try:
                    fifteen = _fetch_15m(pair)
                except Exception as exc:
                    snap["scanError"] = str(exc)[:200]
                    _save_snapshot(connection, ticker, snap)
                    continue
                if fifteen:
                    candle_at = datetime.fromtimestamp(int(fifteen[-1][0]), tz=timezone.utc)
                    new_candle = candle_at.isoformat() != snap.get("lastCandleAt")
                    one_hour = _aggregate(fifteen, 4)
                    four_hour = _aggregate(fifteen, 16)
                    one_hour_trend = trend_for(one_hour) if len(one_hour) >= 55 else _trend_lite(one_hour)
                    dir_eval = evaluate_active_directional(fifteen, one_hour_trend, gate)
                    prev_long = snap.get("longScore")
                    prev_short = snap.get("shortScore")
                    scanned += 1
                    snap.update({
                        "scanError": None,
                        "lastCandleAt": candle_at.isoformat(),
                        "lastScanAt": now_iso(),
                        "nextScanAt": (candle_at + timedelta(minutes=15)).isoformat(),
                        "longScore": dir_eval["long"]["score"],
                        "shortScore": dir_eval["short"]["score"],
                        "threshold": gate,
                        "maxScore": ACTIVE_MAX_SCORE,
                        "decision": dir_eval["decision"],
                        "decisionReason": dir_eval["decisionReason"],
                        "signal": classify_signal(dir_eval["long"]["score"], dir_eval["short"]["score"]),
                        "longConditions": dir_eval["long"]["conditions"],
                        "shortConditions": dir_eval["short"]["conditions"],
                        "trend15m": dir_eval.get("fifteenTrend", "NEUTRAL"),
                        "trend1h": one_hour_trend,
                        "trend4h": _trend_lite(four_hour),
                    })
                    # Elliott analysis: strictly once per completed 15m candle,
                    # on already-fetched data. Analytics layer — never gates
                    # entries; cached result is kept between candles.
                    if new_candle or not snap.get("elliott"):
                        try:
                            from elliott_wave import analyze_elliott
                            snap["elliott"] = analyze_elliott(fifteen)
                            snap["elliottError"] = None
                        except Exception as exc:
                            snap["elliott"] = None
                            snap["elliottError"] = str(exc)[:120]
                    if new_candle and prev_long is not None and (
                        abs(snap["longScore"] - prev_long) >= 2
                        or abs(snap["shortScore"] - (prev_short or 0)) >= 2
                    ):
                        snap["lastSignalChange"] = {
                            "at": now_iso(),
                            "longFrom": prev_long, "longTo": snap["longScore"],
                            "shortFrom": prev_short, "shortTo": snap["shortScore"],
                        }

                    # Trading eligibility
                    if ticker in CORE_COINS:
                        snap["tradingEnabled"] = False
                        snap["disabledReason"] = "Traded by its dedicated £ account (CORE + ACTIVE engines)"
                    elif (snap.get("volumeUsd") or 0) < min_volume:
                        snap["tradingEnabled"] = False
                        snap["disabledReason"] = (
                            f"TRADE DISABLED — LOW LIQUIDITY "
                            f"(24h volume ${(snap.get('volumeUsd') or 0):,.0f} < ${min_volume:,.0f} minimum)"
                        )
                    else:
                        snap["tradingEnabled"] = True
                        snap["disabledReason"] = None

                    # Entry — only on a NEW candle, same 4/6 gate, PAPER ONLY
                    entry_block = None
                    if (new_candle and snap["tradingEnabled"]
                            and dir_eval["decision"] in ("LONG", "SHORT")
                            and ticker not in positions and tick):
                        opened, entry_block = _try_enter(
                            connection, ticker, dir_eval["decision"],
                            dir_eval, tick, gate, positions,
                            elliott=snap.get("elliott"),
                        )
                        if opened:
                            positions[ticker] = opened
                            entered.append(ticker)
                    elif ticker in positions:
                        entry_block = "Existing scanner position on this asset"
                    snap["entryBlocker"] = entry_block

                    # STRATEGY LAB — shadow simulation only, on the same cached
                    # candles. Never touches real positions; failures never
                    # break the scan. main_blocked labels MISSED/SHADOW
                    # opportunities (§19).
                    if new_candle:
                        try:
                            from strategy_lab import (advance_lab_trades, init_lab_db,
                                                      record_lab_observation)
                            init_lab_db(connection)
                            advance_lab_trades(connection, ticker, fifteen)
                            lab_blocked = None
                            if ticker in CORE_COINS:
                                lab_blocked = None  # traded by the £ account
                            elif not snap["tradingEnabled"]:
                                lab_blocked = snap.get("disabledReason")
                            elif entry_block:
                                lab_blocked = entry_block
                            record_lab_observation(
                                connection, ticker, dir_eval, fifteen,
                                snap.get("elliott"), snap.get("spreadPct"), lab_blocked,
                                trend_4h=snap.get("trend4h"),
                            )
                            snap["labError"] = None
                        except Exception as exc:
                            snap["labError"] = str(exc)[:120]
            snap["hasPosition"] = ticker in positions or (ticker in CORE_COINS)
            _save_snapshot(connection, ticker, snap)

        # Strategy Lab milestones — once per full scan, never per candle (§24)
        try:
            from strategy_lab import lab_notifications
            lab_notifications(connection)
        except Exception:
            pass

        connection.commit()
        return {
            "ok": True,
            "scanned": scanned,
            "universe": len(SCANNER_ASSETS),
            "entered": entered,
            "closed": closed,
            "openPositions": len(positions),
            "tickerError": tick_error,
        }
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Read commands
# ---------------------------------------------------------------------------
def _asset_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT coin,
               COUNT(*) AS trades,
               SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins,
               SUM(profit_loss) AS pnl,
               SUM(CASE WHEN profit_loss > 0 THEN profit_loss ELSE 0 END) AS gross_win,
               SUM(CASE WHEN profit_loss < 0 THEN -profit_loss ELSE 0 END) AS gross_loss
        FROM trades GROUP BY coin
        """
    ).fetchall()
    out = {}
    for r in rows:
        trades = r["trades"] or 0
        out[r["coin"]] = {
            "trades": trades,
            "winRate": round((r["wins"] or 0) / trades * 100, 1) if trades else None,
            "pnl": round(r["pnl"] or 0.0, 2),
            "profitFactor": (
                round(r["gross_win"] / r["gross_loss"], 2)
                if r["gross_loss"] and r["gross_loss"] > 0 else None
            ),
        }
    return out


def market_directory() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    init_scanner_db(connection)
    try:
        positions = _open_positions(connection)
        account = _account(connection)
        watchlist = _get_json_config(connection, "scanner_watchlist") or []
        stats = _asset_stats(connection)
        # Also surface core-account positions so OPEN POSITION filter matches reality
        core_open: set[str] = set()
        for r in connection.execute(
            "SELECT coin, open_position, active_position FROM coin_state"
        ).fetchall():
            if r["open_position"] or r["active_position"]:
                core_open.add(r["coin"])
        assets = []
        for ticker in SCANNER_ASSETS:
            snap = _load_snapshot(connection, ticker)
            if not snap:
                snap = {"ticker": ticker, "name": SCANNER_ASSETS[ticker]["name"],
                        "dataAvailable": False}
            snap["watchlisted"] = ticker in watchlist
            snap["hasPosition"] = ticker in positions or ticker in core_open
            pos = positions.get(ticker)
            snap["position"] = {**pos, "ticker": ticker} if pos else None
            snap["stats"] = stats.get(ticker)
            assets.append(snap)
        # Rank by 24h USD volume
        ranked = sorted(assets, key=lambda a: -(a.get("volumeUsd") or 0))
        for i, a in enumerate(ranked):
            a["rank"] = i + 1
        counts = {"STRONG LONG": 0, "LONG": 0, "NEUTRAL": 0, "SHORT": 0, "STRONG SHORT": 0}
        next_scan = None
        for a in assets:
            sig = a.get("signal")
            if sig in counts:
                counts[sig] += 1
            ns = a.get("nextScanAt")
            if ns and (next_scan is None or ns < next_scan):
                next_scan = ns
        return {
            "assets": assets,
            "marketStats": {
                "scanned": sum(1 for a in assets if a.get("lastScanAt")),
                "universe": len(SCANNER_ASSETS),
                "counts": counts,
                "openCryptoTrades": len(positions) + len([c for c in core_open if c in SCANNER_ASSETS]),
                "nextScanAt": next_scan,
            },
            "scannerAccount": {
                "currency": "USD",
                "balance": round(float(account["balance"]), 2),
                "startingBalance": round(float(account["starting_balance"]), 2),
                "openPositions": len(positions),
                "maxPositions": int(_cfg(connection, "scanner_max_positions")),
                "riskPerTradePct": _cfg(connection, "scanner_risk_pct"),
                "maxTotalRiskPct": _cfg(connection, "scanner_max_total_risk_pct"),
                "maxSameDirection": int(_cfg(connection, "scanner_max_same_direction")),
                "minVolumeUsd": _cfg(connection, "scanner_min_volume_usd"),
            },
            "watchlist": watchlist,
        }
    finally:
        connection.close()


def market_asset(ticker: str) -> dict[str, Any]:
    ticker = (ticker or "").upper()
    if ticker not in SCANNER_ASSETS:
        return {"ok": False, "error": f"Unknown asset {ticker}"}
    connection = db()
    init_db(connection)
    init_scanner_db(connection)
    try:
        snap = _load_snapshot(connection, ticker)
        positions = _open_positions(connection)
        watchlist = _get_json_config(connection, "scanner_watchlist") or []
        last_trade = connection.execute(
            "SELECT closed_at FROM trades WHERE coin = ? ORDER BY id DESC LIMIT 1", (ticker,)
        ).fetchone()
        snap.setdefault("ticker", ticker)
        snap.setdefault("name", SCANNER_ASSETS[ticker]["name"])
        return {
            "ok": True,
            "asset": snap,
            "isCoreCoin": ticker in CORE_COINS,
            "position": ({**positions[ticker], "ticker": ticker}
                         if ticker in positions else None),
            "watchlisted": ticker in watchlist,
            "lastTradeAt": last_trade["closed_at"] if last_trade else None,
            "stats": _asset_stats(connection).get(ticker),
        }
    finally:
        connection.close()


def toggle_watchlist(ticker: str) -> dict[str, Any]:
    ticker = (ticker or "").upper()
    if ticker not in SCANNER_ASSETS:
        return {"ok": False, "error": f"Unknown asset {ticker}"}
    connection = db()
    init_db(connection)
    try:
        watchlist = _get_json_config(connection, "scanner_watchlist") or []
        if ticker in watchlist:
            watchlist.remove(ticker)
            added = False
        else:
            watchlist.append(ticker)
            added = True
        _set_json_config(connection, "scanner_watchlist", watchlist)
        connection.commit()
        return {"ok": True, "ticker": ticker, "watchlisted": added, "watchlist": watchlist}
    finally:
        connection.close()


def _elliott_flag(connection: sqlite3.Connection, key: str) -> str:
    """Elliott influence flags — stored as bot_config, default OFF."""
    row = connection.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
    return (row["value"] if row else None) or "OFF"


def _elliott_trade_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    """Post-trade statistical classification (§15): ALIGNED vs NOT ALIGNED,
    plus wave3 / wave5 / ABC / uncertain buckets. Analytics only."""
    rows = connection.execute(
        "SELECT profit_loss, result, elliott FROM trades "
        "WHERE elliott IS NOT NULL AND strategy = 'ACTIVE' ORDER BY id"
    ).fetchall()

    def bucket_stats(trades: list[float]) -> dict[str, Any]:
        n = len(trades)
        if n == 0:
            return {"trades": 0, "wins": 0, "losses": 0, "winRate": None, "netPnl": 0.0,
                    "avgWin": None, "avgLoss": None, "profitFactor": None,
                    "expectancy": None, "maxDrawdown": None}
        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p <= 0]
        gross_w = sum(wins)
        gross_l = abs(sum(losses))
        equity = peak = dd = 0.0
        for p in trades:
            equity += p
            peak = max(peak, equity)
            dd = max(dd, peak - equity)
        return {
            "trades": n, "wins": len(wins), "losses": len(losses),
            "winRate": round(len(wins) / n * 100, 1),
            "netPnl": round(sum(trades), 2),
            "avgWin": round(gross_w / len(wins), 2) if wins else None,
            "avgLoss": round(-gross_l / len(losses), 2) if losses else None,
            "profitFactor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
            "expectancy": round(sum(trades) / n, 2),
            "maxDrawdown": round(dd, 2),
        }

    buckets: dict[str, list[float]] = {
        "aligned": [], "notAligned": [], "mixed": [],
        "wave3": [], "wave5": [], "abc": [], "uncertain": [],
        "vetoWouldHaveBlocked": [],
    }
    for r in rows:
        try:
            e = json.loads(r["elliott"])
        except (ValueError, TypeError):
            continue
        pnl = float(r["profit_loss"])
        aligned = e.get("aligned")
        if aligned == "YES":
            buckets["aligned"].append(pnl)
        elif aligned == "NO":
            buckets["notAligned"].append(pnl)
        else:
            buckets["mixed"].append(pnl)
        if e.get("wave3Candidate"):
            buckets["wave3"].append(pnl)
        if e.get("wave") == "5":
            buckets["wave5"].append(pnl)
        if e.get("abcCandidate"):
            buckets["abc"].append(pnl)
        if not e.get("available") or e.get("wave") is None:
            buckets["uncertain"].append(pnl)
        if e.get("wave5VetoWouldBlock"):
            buckets["vetoWouldHaveBlocked"].append(pnl)
    return {name: bucket_stats(vals) for name, vals in buckets.items()}


def elliott_lab() -> dict[str, Any]:
    """ELLIOTT LAB — current candidates across the scanner universe plus the
    aligned/not-aligned trade experiment. Informational only."""
    connection = db()
    init_db(connection)
    init_scanner_db(connection)
    try:
        wave3: list[dict[str, Any]] = []
        wave5: list[dict[str, Any]] = []
        abc: list[dict[str, Any]] = []
        strongest: list[dict[str, Any]] = []
        bullish_aligned: list[str] = []
        bearish_aligned: list[str] = []
        uncertain: list[str] = []
        for ticker in SCANNER_ASSETS:
            snap = _load_snapshot(connection, ticker)
            e = snap.get("elliott")
            if not e:
                uncertain.append(ticker)
                continue
            item = {
                "ticker": ticker,
                "name": SCANNER_ASSETS[ticker]["name"],
                "structure": e.get("structure"),
                "wave": e.get("wave"),
                "direction": e.get("direction"),
                "confidence": e.get("confidence"),
                "confidenceLabel": e.get("confidenceLabel"),
                "alignment": e.get("alignment"),
                "signal": snap.get("signal"),
                "longScore": snap.get("longScore"),
                "shortScore": snap.get("shortScore"),
            }
            if e.get("structure") == "UNCERTAIN":
                uncertain.append(ticker)
            else:
                strongest.append(item)
                if e.get("alignment") == "STRONG BULLISH ALIGNMENT":
                    bullish_aligned.append(ticker)
                elif e.get("alignment") == "STRONG BEARISH ALIGNMENT":
                    bearish_aligned.append(ticker)
            if e.get("wave3Candidate"):
                wave3.append(item)
            if e.get("wave5Exhaustion"):
                wave5.append(item)
            if e.get("abcCandidate"):
                abc.append(item)
        strongest.sort(key=lambda x: -(x.get("confidence") or 0))
        wave3.sort(key=lambda x: -(x.get("confidence") or 0))
        return {
            "flags": {
                "elliottScoreInfluence": _elliott_flag(connection, "elliott_score_influence"),
                "wave5Veto": _elliott_flag(connection, "elliott_wave5_veto"),
                "activeGate": "4/6 (unchanged)",
            },
            "wave3Candidates": wave3[:10],
            "wave5ExhaustionCandidates": wave5[:10],
            "abcCandidates": abc[:10],
            "strongestStructures": strongest[:10],
            "bullishAligned": bullish_aligned,
            "bearishAligned": bearish_aligned,
            "uncertain": uncertain,
            "tradeStats": _elliott_trade_stats(connection),
        }
    finally:
        connection.close()


def scanner_open_positions() -> list[dict[str, Any]]:
    """Scanner positions in the shape the OPEN TRADES page expects."""
    connection = db()
    init_db(connection)
    init_scanner_db(connection)
    try:
        pairs = _pair_map(connection)
        positions = _open_positions(connection)
        out = []
        for ticker, pos in positions.items():
            snap = _load_snapshot(connection, ticker)
            price = snap.get("price")
            entry = float(pos["entry"])
            qty = float(pos["quantity"])
            pnl = None
            if price:
                pnl = ((price - entry) if pos["direction"] == "LONG" else (entry - price)) * qty
            out.append({
                "ticker": ticker,
                "name": SCANNER_ASSETS.get(ticker, {}).get("name", ticker),
                "currency": "USD",
                "currentPrice": price,
                "unrealisedPnl": round(pnl, 4) if pnl is not None else None,
                **pos,
            })
        return out
    finally:
        connection.close()
