#!/usr/bin/env python3
"""Kraken multi-coin paper trader.

Uses Kraken's public market-data endpoints only.
Never authenticates with Kraken and never submits real orders.
Paper trading only — all trades are simulated.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COINS: dict[str, dict[str, str]] = {
    "BTC": {"pair": "XXBTZGBP", "display": "BTC/GBP"},
    "ETH": {"pair": "XETHZGBP", "display": "ETH/GBP"},
    "SOL": {"pair": "SOLGBP",   "display": "SOL/GBP"},
    "XRP": {"pair": "XRPGBP",   "display": "XRP/GBP"},
}

# Precious metals — PAPER TRADING (UNVALIDATED STRATEGY).
# The crypto 6/6 strategy has NOT passed validated backtesting on metals.
# Per user instruction (Aug 2026), metals now open SIMULATED paper trades
# when a full 6/6 signal appears, clearly labelled as unvalidated.
# No real orders are ever sent (see PAPER_TRADING / LIVE_TRADING flags).
METALS: dict[str, dict[str, str]] = {
    "GOLD": {
        "display": "XAU/USD",
        "name": "Gold",
        "spot_symbol": "XAU",       # gold-api.com spot symbol
        "candles_symbol": "GC=F",   # COMEX gold futures (Yahoo Finance) — scan only
    },
    "SILVER": {
        "display": "XAG/USD",
        "name": "Silver",
        "spot_symbol": "XAG",
        "candles_symbol": "SI=F",   # COMEX silver futures (Yahoo Finance) — scan only
    },
}

# All instruments in display order (crypto first, then metals)
INSTRUMENTS: dict[str, dict[str, str]] = {**COINS, **METALS}


def instrument_display(symbol: str) -> str:
    return INSTRUMENTS[symbol]["display"]


def instrument_info(symbol: str) -> dict[str, Any]:
    """Honest labelling of each instrument for the dashboard."""
    if symbol in METALS:
        meta = METALS[symbol]
        return {
            "kind":        "METAL",
            "tradingMode": "PAPER_UNVALIDATED",
            "statusLabel": "UNVALIDATED STRATEGY — PAPER TRADING ONLY",
            "currency":    "USD",
            "priceType":   "SPOT",
            "dataSource":  f"Spot price: gold-api.com ({meta['display']}, spot) · "
                           f"Scan candles: {meta['candles_symbol']} COMEX futures via Yahoo Finance",
        }
    return {
        "kind":        "CRYPTO",
        "tradingMode": "ACTIVE",
        "statusLabel": "PAPER TRADING ACTIVE",
        "currency":    "GBP",
        "priceType":   "SPOT",
        "dataSource":  f"Kraken public API ({COINS[symbol]['display']}, spot)",
    }
STARTING_BALANCE    = 100.0

# ── Global execution safety flags ───────────────────────────────────────────
# Every function that opens or closes a position MUST pass through
# _assert_paper_only(). LIVE_TRADING=False guarantees no real external order
# can ever be submitted, even if some other code path requests one.
PAPER_TRADING = True    # simulated fills against public market data only
LIVE_TRADING  = False   # NEVER set to True — no brokerage/exchange execution exists


def _assert_paper_only() -> None:
    """Hard safety gate for all order/execution paths."""
    if LIVE_TRADING or not PAPER_TRADING:
        raise RuntimeError(
            "SAFETY HALT: live trading is not supported. "
            "PAPER_TRADING must be True and LIVE_TRADING must be False."
        )
# ── Opportunity scoring (paper mode) ───────────────────────────────────────
# Weighted score replaces the strict 6/6 gate:
#   4h trend=2, 1h trend=2, RSI=1, MACD=1, Price vs MA=1, Volume=1 → max 8
OPP_WEIGHTS         = [2, 2, 1, 1, 1, 1]
OPP_MAX_SCORE       = 8
OPP_ENTRY_SCORE     = 6        # minimum weighted score to enter
MAX_SPREAD_PCT      = 0.5      # skip entries if bid/ask spread wider than this
ABNORMAL_RANGE_ATR  = 3.0      # skip if current candle range > 3× ATR
STALE_DATA_SECONDS  = 9000     # last completed 1h candle older than 2.5h = stale
RANGE_RSI_OVERSOLD  = 35.0
RANGE_RSI_OVERBOUGHT = 65.0
RISK_PER_TRADE      = 0.01     # 1 %
DAILY_LOSS_LIMIT    = 0.03     # 3 % of starting balance
MAX_CONSECUTIVE_LOSSES = 3
GOLD_MIN_PASS       = 5        # GOLD paper gate: at least 5/6 directional conditions (backtest-validated)
SILVER_MIN_PASS     = 5        # SILVER any-5/6 — user-chosen after 365d backtest review (Aug 2026)

# ── Per-asset directional entry thresholds ─────────────────────────────────
# Every asset evaluates LONG and SHORT INDEPENDENTLY each completed candle.
# "scale" describes what the threshold applies to:
#   weighted8    — weighted condition score (4h=2, 1h=2, RSI/MACD/MA/Vol=1 → max 8)
#   conditions6  — raw pass count of the six conditions (max 6)
# LONG and SHORT thresholds are configured separately so later backtests can
# tune them per direction without code changes.
DIRECTIONAL_THRESHOLDS: dict[str, dict[str, Any]] = {
    "BTC":    {"long": OPP_ENTRY_SCORE, "short": OPP_ENTRY_SCORE, "scale": "weighted8"},
    "ETH":    {"long": OPP_ENTRY_SCORE, "short": OPP_ENTRY_SCORE, "scale": "weighted8"},
    "SOL":    {"long": OPP_ENTRY_SCORE, "short": OPP_ENTRY_SCORE, "scale": "weighted8"},
    "XRP":    {"long": OPP_ENTRY_SCORE, "short": OPP_ENTRY_SCORE, "scale": "weighted8"},
    "GOLD":   {"long": GOLD_MIN_PASS,   "short": GOLD_MIN_PASS,   "scale": "conditions6"},
    "SILVER": {"long": SILVER_MIN_PASS, "short": SILVER_MIN_PASS, "scale": "conditions6"},
}

# ── ACTIVE strategy (15-minute entries, 1h trend context) ──────────────────
# A second, faster strategy that runs IN PARALLEL with the existing CORE
# strategy on every asset. It evaluates completed 15m candles and requires
# fewer conditions to agree (more opportunities), while keeping all hard
# safety rules: 1% risk sizing, ATR stops, portfolio risk ceiling, re-entry
# protection, daily-loss/streak pauses, and PAPER-ONLY execution.
ACTIVE_MIN_PASS        = 4      # of 6 conditions (configurable gate, both directions)
ACTIVE_MAX_SCORE       = 6
ACTIVE_ATR_MULTIPLIER  = 1.5    # stop = 1.5 × ATR(15m)
ACTIVE_REWARD_TO_RISK  = 1.5    # faster targets than CORE's 2.0
ACTIVE_STALE_SECONDS   = 2700   # last completed 15m candle older than 45min = stale

# ── Portfolio-level risk ceiling ────────────────────────────────────────────
# Maximum aggregate open risk (sum of riskAmount across all open positions)
# as a percentage of total starting capital across all six paper accounts.
# £ and $ accounts are aggregated 1:1 for this ceiling (paper-mode
# simplification). Each trade individually still risks max 1% of its own
# account; all six assets open at once ≈ 1% of the portfolio, so 2% is a
# conservative ceiling that permits normal one-position-per-asset operation
# while capping any pathological accumulation.
MAX_TOTAL_OPEN_RISK_PERCENT = 2.0
REWARD_TO_RISK      = 2.0
ATR_MULTIPLIER      = 1.5
POLLING_SECONDS     = 60
DB_PATH = os.environ.get(
    "PAPER_TRADER_DB",
    os.path.join(os.path.dirname(__file__), "paper_trader.sqlite3"),
)
ACTIVITY_KEEP = 200  # rows to retain in the activity log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def date_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def round_price(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def round_amount(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    # busy_timeout: defense-in-depth against "database is locked" if two bot
    # processes ever overlap (the API server also serializes invocations).
    connection = sqlite3.connect(DB_PATH, timeout=30.0)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    """Create tables and migrate any pre-multi-coin BTC state."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS coin_state (
            coin            TEXT PRIMARY KEY,
            starting_balance REAL NOT NULL,
            balance         REAL NOT NULL,
            open_position   TEXT,
            last_candle_at  TEXT,
            day_key         TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            snapshot        TEXT,
            message         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            coin            TEXT NOT NULL DEFAULT 'BTC',
            opened_at       TEXT NOT NULL,
            closed_at       TEXT NOT NULL,
            direction       TEXT NOT NULL,
            entry           REAL NOT NULL,
            exit            REAL NOT NULL,
            stop_loss       REAL NOT NULL,
            take_profit     REAL NOT NULL,
            rsi             REAL,
            macd            REAL,
            atr             REAL,
            trend_4h        TEXT NOT NULL,
            profit_loss     REAL NOT NULL,
            account_balance REAL NOT NULL,
            exit_reason     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            coin    TEXT NOT NULL,
            event   TEXT NOT NULL,
            message TEXT NOT NULL,
            ts      TEXT NOT NULL
        );
        """
    )
    # Audit columns added Aug 2026 (metals paper execution): ALTER TABLE is
    # required for existing databases — CREATE TABLE IF NOT EXISTS won't add them.
    existing_cols = {r[1] for r in connection.execute("PRAGMA table_info(trades)")}
    for col, col_type in (
        ("risk_amount",      "REAL"),
        ("r_multiple",       "REAL"),
        ("pnl_pct",          "REAL"),
        ("duration_seconds", "REAL"),
        ("result",           "TEXT"),
        ("entry_score",      "REAL"),
        ("pass_count",       "INTEGER"),
        ("trend_1h",         "TEXT"),
        ("entry_mode",       "TEXT"),
        ("entry_conditions", "TEXT"),
        # Directional audit columns (Aug 2026): full LONG/SHORT scores and the
        # gate the trade entered at. Nullable — historical trades preserved.
        ("long_score",       "REAL"),
        ("short_score",      "REAL"),
        ("entry_threshold",  "REAL"),
    ):
        if col not in existing_cols:
            connection.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
    # Migrate: add coin column to trades if it doesn't exist yet
    trades_cols = [row[1] for row in connection.execute("PRAGMA table_info(trades)").fetchall()]
    if "coin" not in trades_cols:
        connection.execute("ALTER TABLE trades ADD COLUMN coin TEXT NOT NULL DEFAULT 'BTC'")
    # Migrate: add re-entry protection state to coin_state
    state_cols = [row[1] for row in connection.execute("PRAGMA table_info(coin_state)").fetchall()]
    if "reentry" not in state_cols:
        connection.execute("ALTER TABLE coin_state ADD COLUMN reentry TEXT")
    # ACTIVE strategy slots (parallel 15m strategy) — one extra position per
    # asset, with its own candle gate and re-entry protection.
    for col in ("active_position", "active_last_candle_at", "active_reentry", "active_snapshot"):
        if col not in state_cols:
            connection.execute(f"ALTER TABLE coin_state ADD COLUMN {col} TEXT")
    # Strategy attribution on trades: CORE (1h strategy) vs ACTIVE (15m).
    trades_cols_now = {r[1] for r in connection.execute("PRAGMA table_info(trades)")}
    if "strategy" not in trades_cols_now:
        connection.execute("ALTER TABLE trades ADD COLUMN strategy TEXT")
        connection.execute("UPDATE trades SET strategy = 'CORE' WHERE strategy IS NULL")
    # Migrate old single-coin bot_state if present
    has_old = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_state'"
    ).fetchone()
    if has_old:
        btc_row = connection.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
        if btc_row:
            exists = connection.execute(
                "SELECT coin FROM coin_state WHERE coin = 'BTC'"
            ).fetchone()
            if not exists:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO coin_state
                        (coin, starting_balance, balance, open_position,
                         last_candle_at, day_key, updated_at, snapshot, message)
                    VALUES ('BTC', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        btc_row["starting_balance"],
                        btc_row["balance"],
                        btc_row["open_position"],
                        btc_row["last_candle_at"],
                        btc_row["day_key"],
                        btc_row["updated_at"],
                        btc_row["snapshot"],
                        btc_row["message"],
                    ),
                )
            # migrate old trades
            old_trades = connection.execute("SELECT * FROM trades WHERE true").fetchall()
            for t in old_trades:
                # only migrate if they have no 'coin' column (old schema)
                try:
                    _ = t["coin"]
                except IndexError:
                    connection.execute(
                        """
                        INSERT INTO trades
                            (coin, opened_at, closed_at, direction, entry, exit,
                             stop_loss, take_profit, rsi, macd, atr, trend_4h,
                             profit_loss, account_balance, exit_reason)
                        VALUES ('BTC', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            t["opened_at"], t["closed_at"], t["direction"],
                            t["entry"], t["exit"], t["stop_loss"], t["take_profit"],
                            t["rsi"], t["macd"], t["atr"], t["trend_4h"],
                            t["profit_loss"], t["account_balance"], t["exit_reason"],
                        ),
                    )
        connection.execute("DROP TABLE IF EXISTS bot_state")

    # Ensure each instrument has a state row
    for coin in INSTRUMENTS:
        existing = connection.execute(
            "SELECT coin FROM coin_state WHERE coin = ?", (coin,)
        ).fetchone()
        if not existing:
            _insert_default_state(connection, coin, STARTING_BALANCE)

    connection.commit()


def _insert_default_state(
    connection: sqlite3.Connection,
    coin: str,
    starting_balance: float,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO coin_state
            (coin, starting_balance, balance, open_position,
             last_candle_at, day_key, updated_at, snapshot, message)
        VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL, ?)
        """,
        (
            coin,
            starting_balance,
            starting_balance,
            date_key(),
            now_iso(),
            f"Paper account ready. Waiting for market data.",
        ),
    )


def load_coin_state(connection: sqlite3.Connection, coin: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM coin_state WHERE coin = ?", (coin,)
    ).fetchone()
    if row is None:
        _insert_default_state(connection, coin, STARTING_BALANCE)
        connection.commit()
        row = connection.execute(
            "SELECT * FROM coin_state WHERE coin = ?", (coin,)
        ).fetchone()
    assert row is not None
    return row


def reset_coin(
    connection: sqlite3.Connection,
    coin: str,
    starting_balance: float = STARTING_BALANCE,
    clear_trades: bool = True,
) -> None:
    starting_balance = max(0.0, float(starting_balance))
    if clear_trades:
        connection.execute("DELETE FROM trades WHERE coin = ?", (coin,))
    connection.execute(
        """
        INSERT OR REPLACE INTO coin_state
            (coin, starting_balance, balance, open_position,
             last_candle_at, day_key, updated_at, snapshot, message)
        VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL, ?)
        """,
        (
            coin,
            starting_balance,
            starting_balance,
            date_key(),
            now_iso(),
            f"Paper account reset. Waiting for market data.",
        ),
    )
    add_activity(connection, coin, "ACCOUNT_RESET", f"Paper account reset to £{starting_balance:.2f}")
    connection.commit()


def add_activity(
    connection: sqlite3.Connection,
    coin: str,
    event: str,
    message: str,
) -> None:
    connection.execute(
        "INSERT INTO activity_log (coin, event, message, ts) VALUES (?, ?, ?, ?)",
        (coin, event, message, now_iso()),
    )
    # Prune old rows
    connection.execute(
        """
        DELETE FROM activity_log WHERE id NOT IN (
            SELECT id FROM activity_log ORDER BY id DESC LIMIT ?
        )
        """,
        (ACTIVITY_KEEP,),
    )


# ---------------------------------------------------------------------------
# Kraken public API
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Replit-Kraken-Paper-Trader/1.0",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"Kraken public API error: {error}") from error
    errors = payload.get("error", [])
    if errors:
        raise RuntimeError("Kraken error: " + ", ".join(map(str, errors)))
    return payload.get("result", {})


def fetch_market_data(pair: str) -> tuple[float, float | None, list[list[Any]], list[list[Any]]]:
    ticker_result = fetch_json(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
    ticker = ticker_result.get(pair) or next(iter(ticker_result.values()), None)
    if not ticker or not ticker.get("c"):
        raise RuntimeError(f"No ticker data for {pair}")
    current_price = safe_float(ticker["c"][0])
    if current_price is None:
        raise RuntimeError(f"Invalid price for {pair}")

    # Bid/ask spread as % of price (used as an entry safety guard)
    bid = safe_float((ticker.get("b") or [None])[0])
    ask = safe_float((ticker.get("a") or [None])[0])
    spread_pct: float | None = None
    if bid and ask and bid > 0 and ask >= bid:
        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100

    one_hour_result = fetch_json(
        f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=60"
    )
    four_hour_result = fetch_json(
        f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240"
    )
    cutoff = time.time()

    def ohlc_rows(result: dict[str, Any]) -> list[list[Any]]:
        rows = result.get(pair) or next(
            (v for k, v in result.items() if k != "last"), []
        )
        return [r for r in rows if len(r) >= 8 and float(r[0]) < cutoff][:-1]

    return current_price, spread_pct, ohlc_rows(one_hour_result), ohlc_rows(four_hour_result)


# ---------------------------------------------------------------------------
# Metals market data (public sources — used for simulated paper fills only)
# ---------------------------------------------------------------------------

def fetch_metal_spot(spot_symbol: str) -> tuple[float, str]:
    """Live spot price in USD from gold-api.com (free, no key, true spot)."""
    request = Request(
        f"https://api.gold-api.com/price/{spot_symbol}",
        headers={"Accept": "application/json", "User-Agent": "Replit-Paper-Trader/1.0"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(f"gold-api.com error: {error}") from error
    price = safe_float(payload.get("price"))
    if price is None or price <= 0:
        raise RuntimeError(f"Invalid spot price for {spot_symbol}")
    return price, str(payload.get("updatedAt") or now_iso())


def fetch_metal_candles(futures_symbol: str) -> tuple[list[list[Any]], list[list[Any]]]:
    """1h candles for the COMEX futures contract via Yahoo Finance (scan only).

    Returns (one_hour_rows, four_hour_rows) in Kraken OHLC row format:
    [ts, open, high, low, close, vwap, volume, count]. 4h rows are
    aggregated locally from the 1h series.
    """
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for host in ("query1", "query2"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/{futures_symbol}"
            "?interval=1h&range=60d"
        )
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last_error = error
    if payload is None:
        raise RuntimeError(f"Yahoo Finance error for {futures_symbol}: {last_error}")

    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"Yahoo Finance returned no candles for {futures_symbol}") from error

    cutoff = time.time()
    one_hour: list[list[Any]] = []
    for i, ts in enumerate(timestamps):
        o = safe_float(quote["open"][i]); h = safe_float(quote["high"][i])
        l = safe_float(quote["low"][i]);  c = safe_float(quote["close"][i])
        v = safe_float(quote["volume"][i]) or 0.0
        if None in (o, h, l, c) or float(ts) >= cutoff:
            continue
        one_hour.append([float(ts), o, h, l, c, c, v, 1])
    # Drop the still-forming most recent candle
    if one_hour:
        one_hour = one_hour[:-1]

    # Aggregate 1h → 4h buckets
    buckets: dict[int, list[list[Any]]] = {}
    for row in one_hour:
        buckets.setdefault(int(row[0]) // 14400, []).append(row)
    four_hour: list[list[Any]] = []
    for key in sorted(buckets):
        rows = buckets[key]
        four_hour.append([
            float(key * 14400),
            rows[0][1],
            max(r[2] for r in rows),
            min(r[3] for r in rows),
            rows[-1][4],
            rows[-1][4],
            sum(r[6] for r in rows),
            len(rows),
        ])
    return one_hour, four_hour


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

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
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
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
    macd_vals = [v for v in macd if v is not None]
    sig_vals  = ema_series(macd_vals, 9)
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
        "rsi": lv(rsi),
        "macd": lv(macd),
        "macdSignal": lv(macd_signal),
        "atr": lv(atr),
        "ema20": lv(ema20),
        "ema50": lv(ema50),
        "volume": volumes[-1] if volumes else None,
        "_avg_volume": sum(volumes[-20:]) / min(20, len(volumes)) if volumes else None,
    }


def trend_for(rows: list[list[Any]]) -> str:
    if len(rows) < 55:
        return "NEUTRAL"
    snap = indicator_snapshot(rows)
    close = float(rows[-1][4])
    e20, e50, macd, sig = snap["ema20"], snap["ema50"], snap["macd"], snap["macdSignal"]
    if e20 is not None and e50 is not None and macd is not None and sig is not None:
        if close > e20 > e50 and macd > sig:
            return "BULLISH"
        if close < e20 < e50 and macd < sig:
            return "BEARISH"
    return "NEUTRAL"
def evaluate_conditions(
    one_hour: list[list[Any]],
    four_hour: list[list[Any]],
) -> dict[str, Any]:
    """
    Evaluate the strategy conditions used by the engine — for BOTH directions
    independently (dual-direction system, Aug 2026).

    LONG and SHORT each get their own 6-condition evaluation and weighted
    score every scan; neither direction is gated behind a pre-computed bias.
    `bias` is the direction with the higher pass count (NEUTRAL on a tie) and
    the legacy `conditions`/`passCount` fields mirror that direction so
    existing dashboard components keep working.
    """
    _empty = {
        "conditions": [],
        "passCount": 0,
        "totalCount": 0,
        "bias": "NEUTRAL",
        "signal": "NO_TRADE",
        "oneHourTrend": "NEUTRAL",
        "fourHourTrend": "NEUTRAL",
        "indicators": {
            "rsi": None, "macd": None, "macdSignal": None,
            "atr": None, "ema20": None, "ema50": None, "volume": None,
        },
        "long":  {"conditions": [], "passCount": 0, "score": 0},
        "short": {"conditions": [], "passCount": 0, "score": 0},
    }
    if len(one_hour) < 55 or len(four_hour) < 55:
        return _empty

    snap  = indicator_snapshot(one_hour)
    close = float(one_hour[-1][4])

    one_hour_trend  = trend_for(one_hour)
    four_hour_trend = trend_for(four_hour)

    long_conds  = _direction_conditions("LONG",  snap, close, one_hour_trend, four_hour_trend)
    short_conds = _direction_conditions("SHORT", snap, close, one_hour_trend, four_hour_trend)
    long_pass   = sum(1 for cd in long_conds if cd["pass"])
    short_pass  = sum(1 for cd in short_conds if cd["pass"])
    long_score  = sum(w for w, cd in zip(OPP_WEIGHTS, long_conds)  if cd["pass"])
    short_score = sum(w for w, cd in zip(OPP_WEIGHTS, short_conds) if cd["pass"])

    # Display bias = direction with the higher pass count (tie → NEUTRAL).
    if long_pass > short_pass:
        bias, conds, pass_count = "LONG", long_conds, long_pass
    elif short_pass > long_pass:
        bias, conds, pass_count = "SHORT", short_conds, short_pass
    else:
        # Tie (usually only the direction-neutral Volume condition passing
        # on both sides) — no meaningful directional edge.
        bias = "NEUTRAL"
        conds, pass_count = long_conds, long_pass

    # Legacy strict signal: full 6/6 pass in the bias direction
    if bias in ("LONG", "SHORT") and all(cd["pass"] for cd in conds):
        signal = bias
    else:
        signal = "NO_TRADE"

    indicators = {k: v for k, v in snap.items() if not k.startswith("_")}

    return {
        "conditions": conds,
        "passCount": pass_count,
        "totalCount": len(conds),
        "bias": bias,
        "signal": signal,
        "oneHourTrend": one_hour_trend,
        "fourHourTrend": four_hour_trend,
        "indicators": indicators,
        "long":  {"conditions": long_conds,  "passCount": long_pass,  "score": long_score},
        "short": {"conditions": short_conds, "passCount": short_pass, "score": short_score},
    }


def _direction_conditions(
    direction: str,
    snap: dict[str, Any],
    close: float,
    one_hour_trend: str,
    four_hour_trend: str,
) -> list[dict[str, Any]]:
    """Build the six entry conditions for one explicit direction (LONG or SHORT).

    Uses the exact same indicator definitions as evaluate_conditions() and the
    historical backtester (metals_backtest.py): trend_for() trends, RSI 50 line,
    MACD vs signal, close vs EMA20 vs EMA50 alignment, volume >= 70% of the
    20-period average.
    """
    rsi_val  = snap["rsi"]
    macd_val = snap["macd"]
    sig_val  = snap["macdSignal"]
    e20      = snap["ema20"]
    e50      = snap["ema50"]
    avg_vol  = snap.get("_avg_volume") or 0.0
    volume   = snap["volume"] or 0.0

    def c(name: str, current_val: str, required_val: str, passed: bool) -> dict[str, Any]:
        return {"name": name, "currentValue": current_val,
                "requiredValue": required_val, "pass": passed}

    cond_vol = avg_vol > 0 and volume >= avg_vol * 0.7
    vol_cond = c("Volume", f"{volume:.4f}", f"≥ {avg_vol * 0.7:.4f} (70% avg)", cond_vol)

    if direction == "LONG":
        return [
            c("4h Trend", four_hour_trend, "BULLISH", four_hour_trend == "BULLISH"),
            c("1h Trend", one_hour_trend, "BULLISH", one_hour_trend == "BULLISH"),
            c("RSI", f"{rsi_val:.1f}" if rsi_val is not None else "—", "≥ 50",
              rsi_val is not None and rsi_val >= 50),
            c("MACD Momentum",
              f"{macd_val:.4f} > {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
              "MACD above signal",
              macd_val is not None and sig_val is not None and macd_val > sig_val),
            c("Price vs MA",
              f"{close:.2f} > EMA20 {e20:.2f}" if e20 else "—",
              "Price > EMA20 > EMA50",
              e20 is not None and e50 is not None and close > e20 > e50),
            vol_cond,
        ]
    return [
        c("4h Trend", four_hour_trend, "BEARISH", four_hour_trend == "BEARISH"),
        c("1h Trend", one_hour_trend, "BEARISH", one_hour_trend == "BEARISH"),
        c("RSI", f"{rsi_val:.1f}" if rsi_val is not None else "—", "≤ 50",
          rsi_val is not None and rsi_val <= 50),
        c("MACD Momentum",
          f"{macd_val:.4f} < {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
          "MACD below signal",
          macd_val is not None and sig_val is not None and macd_val < sig_val),
        c("Price vs MA",
          f"{close:.2f} < EMA20 {e20:.2f}" if e20 else "—",
          "Price < EMA20 < EMA50",
          e20 is not None and e50 is not None and close < e20 < e50),
        vol_cond,
    ]


def evaluate_conditions_directional(
    one_hour: list[list[Any]],
    four_hour: list[list[Any]],
    threshold: int = 5,
    short_threshold: int | None = None,
    weighted: bool = False,
) -> dict[str, Any]:
    """Independent LONG and SHORT evaluation with per-direction entry gates.

    Unlike evaluate_conditions() (which picks a single bias from the trends and
    only scores that direction), this scores BOTH directional setups on every
    scan.

    threshold        — LONG entry gate; short_threshold defaults to the same.
    weighted=False   — score = raw pass count of the 6 conditions (max 6);
                       used by GOLD (5/6) and SILVER (6/6).
    weighted=True    — score = weighted condition score (4h=2, 1h=2, others 1;
                       max 8); mirrors the validated crypto gate (>= 6/8).

    A direction qualifies when its score reaches its gate. If both qualify,
    the direction with the strictly higher score wins; a tie means WAIT
    (never a random choice).
    """
    short_gate = short_threshold if short_threshold is not None else threshold
    max_score = OPP_MAX_SCORE if weighted else 6
    empty_ind = {"rsi": None, "macd": None, "macdSignal": None,
                 "atr": None, "ema20": None, "ema50": None, "volume": None}
    if len(one_hour) < 55 or len(four_hour) < 55:
        return {
            "long":  {"conditions": [], "passCount": 0, "score": 0},
            "short": {"conditions": [], "passCount": 0, "score": 0},
            "threshold": threshold, "shortThreshold": short_gate,
            "maxScore": max_score, "weighted": weighted,
            "decision": "NO_TRADE",
            "decisionReason": "Waiting for enough candle history (55+ per timeframe)",
            "oneHourTrend": "NEUTRAL", "fourHourTrend": "NEUTRAL",
            "indicators": empty_ind,
        }

    snap  = indicator_snapshot(one_hour)
    close = float(one_hour[-1][4])
    one_hour_trend  = trend_for(one_hour)
    four_hour_trend = trend_for(four_hour)

    long_conds  = _direction_conditions("LONG",  snap, close, one_hour_trend, four_hour_trend)
    short_conds = _direction_conditions("SHORT", snap, close, one_hour_trend, four_hour_trend)

    def _score(conds: list[dict[str, Any]]) -> int:
        if weighted:
            return sum(w for w, cd in zip(OPP_WEIGHTS, conds) if cd["pass"])
        return sum(1 for cd in conds if cd["pass"])

    long_score, short_score = _score(long_conds), _score(short_conds)

    long_ok, short_ok = long_score >= threshold, short_score >= short_gate
    if long_ok and short_ok:
        if long_score > short_score:
            decision, reason = "LONG", (
                f"Both directions qualified — LONG selected on stronger "
                f"evidence ({long_score}/{max_score} vs SHORT {short_score}/{max_score})")
        elif short_score > long_score:
            decision, reason = "SHORT", (
                f"Both directions qualified — SHORT selected on stronger "
                f"evidence ({short_score}/{max_score} vs LONG {long_score}/{max_score})")
        else:
            decision, reason = "NO_TRADE", (
                f"Conflict: LONG {long_score}/{max_score} and SHORT {short_score}/{max_score} tied — "
                f"no clearly stronger direction, waiting")
    elif long_ok:
        decision, reason = "LONG", (
            f"LONG {long_score}/{max_score} reached the {threshold}/{max_score} gate "
            f"(SHORT {short_score}/{max_score})")
    elif short_ok:
        decision, reason = "SHORT", (
            f"SHORT {short_score}/{max_score} reached the {short_gate}/{max_score} gate "
            f"(LONG {long_score}/{max_score})")
    else:
        decision, reason = "NO_TRADE", (
            f"Neither direction reached its gate (LONG {long_score}/{max_score} "
            f"needs {threshold}, SHORT {short_score}/{max_score} needs {short_gate})")

    return {
        "long":  {"conditions": long_conds,  "passCount": sum(1 for cd in long_conds if cd["pass"]),
                  "score": long_score},
        "short": {"conditions": short_conds, "passCount": sum(1 for cd in short_conds if cd["pass"]),
                  "score": short_score},
        "threshold": threshold, "shortThreshold": short_gate,
        "maxScore": max_score, "weighted": weighted,
        "decision": decision,
        "decisionReason": reason,
        "oneHourTrend": one_hour_trend,
        "fourHourTrend": four_hour_trend,
        "indicators": {k: v for k, v in snap.items() if not k.startswith("_")},
    }


def _directional_snapshot_block(dir_eval: dict[str, Any]) -> dict[str, Any]:
    """Directional block stored in the coin snapshot / returned by the API."""
    return {
        "longScore":       dir_eval["long"]["score"],
        "shortScore":      dir_eval["short"]["score"],
        "threshold":       dir_eval["threshold"],
        "shortThreshold":  dir_eval["shortThreshold"],
        "maxScore":        dir_eval["maxScore"],
        "decision":        dir_eval["decision"],
        "reason":          dir_eval["decisionReason"],
        "longConditions":  dir_eval["long"]["conditions"],
        "shortConditions": dir_eval["short"]["conditions"],
    }


def _directional_diag_block(dir_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "longScore":   dir_eval["long"]["score"],
        "shortScore":  dir_eval["short"]["score"],
        "threshold":   dir_eval["threshold"],
        "shortThreshold": dir_eval["shortThreshold"],
        "maxScore":    dir_eval["maxScore"],
        "decision":    dir_eval["decision"],
        "reason":      dir_eval["decisionReason"],
        "longFailed":  [cd["name"] for cd in dir_eval["long"]["conditions"] if not cd["pass"]],
        "shortFailed": [cd["name"] for cd in dir_eval["short"]["conditions"] if not cd["pass"]],
    }


def build_execution_diagnostics(
    *,
    dir_eval: dict[str, Any] | None,
    open_position: dict[str, Any] | None,
    is_new_candle: bool,
    armed: bool,
    risk_paused: bool,
    danger_reason: str | None,
    portfolio_block: str | None,
    completed_candle_at: str | None,
    data_error: str | None = None,
    volume: float | None = None,
    counter_trend_block: str | None = None,
    signal: str = "NO_TRADE",
) -> dict[str, Any]:
    """Explicit per-asset entry-eligibility report: every active blocker, named.

    Powers the dashboard 'Execution diagnostics' panel so 'NO TRADE' is never
    shown without the exact reasons.
    """
    blockers: list[str] = []
    if data_error:
        blockers.append(f"Market data problem: {data_error}")
    if dir_eval is None:
        if not data_error:
            blockers.append("No indicator data (scan feed unavailable)")
    elif signal not in ("LONG", "SHORT"):
        # A live LONG/SHORT signal (trend gate passed, or a valid range setup)
        # supersedes the raw directional scores — only report the score gate
        # as a blocker when there is genuinely no entry signal.
        ls, ss = dir_eval["long"]["score"], dir_eval["short"]["score"]
        mx = dir_eval["maxScore"]
        lg, sg = dir_eval["threshold"], dir_eval["shortThreshold"]
        if ls < lg and ss < sg:
            blockers.append(
                f"Signal below threshold (LONG {ls}/{mx} needs {lg}, SHORT {ss}/{mx} needs {sg})")
        elif dir_eval["decision"] == "NO_TRADE":
            blockers.append(f"Directional conflict: {dir_eval['decisionReason']}")
    if open_position is not None:
        blockers.append(
            f"Position already open ({open_position['direction']}) — one position per asset")
    if not is_new_candle:
        nxt = None
        if completed_candle_at:
            try:
                # completed_candle_at is the candle's START time; the NEXT
                # candle (start +1h) finishes — and becomes evaluable — at +2h.
                nxt = (datetime.fromisoformat(completed_candle_at)
                       + timedelta(hours=2)).isoformat()
            except ValueError:
                nxt = None
        blockers.append(
            "Waiting for a new completed 1h candle"
            + (f" (last completed candle started {completed_candle_at}; next one completes ~{nxt})" if nxt else ""))
    if not armed:
        blockers.append("Duplicate-entry protection: same setup already traded — needs the signal to reset")
    if risk_paused:
        blockers.append("Safety pause active (daily loss limit or 3-loss streak — resets next UTC day)")
    if danger_reason:
        blockers.append(f"DANGER mode: {danger_reason}")
    if counter_trend_block:
        blockers.append(counter_trend_block)
    if portfolio_block:
        blockers.append(portfolio_block)
    if volume is not None and volume <= 0:
        blockers.append("Latest candle volume is 0 (fails the Volume condition while the 20-period average is > 0)")
    return {
        "eligible": len(blockers) == 0,
        "blockers": blockers,
        "lastCompletedCandleAt": completed_candle_at,
        "checkedAt": now_iso(),
    }


def portfolio_open_risk(connection: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate open risk across all six paper accounts.

    £ and $ balances are aggregated 1:1 (paper-mode simplification, documented
    on MAX_TOTAL_OPEN_RISK_PERCENT). Returns totals used both for dashboard
    visibility and for the configurable portfolio risk ceiling.
    """
    total_risk = 0.0
    total_start = 0.0
    open_count = 0
    for sym in INSTRUMENTS:
        st = load_coin_state(connection, sym)
        total_start += float(st["starting_balance"])
        for col in ("open_position", "active_position"):
            pos = json.loads(st[col]) if st[col] else None
            if pos:
                open_count += 1
                total_risk += float(pos.get("riskAmount") or 0.0)
    pct = (total_risk / total_start * 100) if total_start else 0.0
    return {
        "openPositions":   open_count,
        "totalInstruments": len(INSTRUMENTS),
        "totalOpenRisk":   round(total_risk, 2),
        "openRiskPercent": round(pct, 3),
        "ceilingPercent":  MAX_TOTAL_OPEN_RISK_PERCENT,
        "totalStarting":   total_start,
    }


def _portfolio_risk_block_reason(
    connection: sqlite3.Connection, new_risk: float
) -> str | None:
    """Return a block reason if opening a trade risking `new_risk` would breach
    the portfolio risk ceiling, else None."""
    pr = portfolio_open_risk(connection)
    if not pr["totalStarting"]:
        return None
    projected = (pr["totalOpenRisk"] + new_risk) / pr["totalStarting"] * 100
    if projected > MAX_TOTAL_OPEN_RISK_PERCENT + 1e-9:
        return (
            f"Entry blocked by portfolio risk limit. Open risk {pr['totalOpenRisk']:.2f} "
            f"(~{pr['openRiskPercent']:.2f}%) + new risk {new_risk:.2f} would be "
            f"{projected:.2f}% of the paper portfolio (> ceiling {MAX_TOTAL_OPEN_RISK_PERCENT}%)"
        )
    return None


def evaluate_opportunity(
    cond_eval: dict[str, Any],
    one_hour: list[list[Any]],
    current_price: float,
    spread_pct: float | None,
) -> dict[str, Any]:
    """Weighted opportunity score, market mode and range setup (paper mode).

    Score: 4h trend=2, 1h trend=2, RSI=1, MACD=1, Price vs MA=1, Volume=1 → max 8.
    Mode: TREND (directional), RANGE (both timeframes neutral), DANGER (unsafe).
    """
    conds = cond_eval.get("conditions", [])
    weighted = []
    score = 0
    for weight, cd in zip(OPP_WEIGHTS, conds):
        if cd["pass"]:
            score += weight
        weighted.append({"name": cd["name"], "weight": weight, "pass": cd["pass"]})

    indicators = cond_eval.get("indicators", {})
    one_hour_trend  = cond_eval.get("oneHourTrend", "NEUTRAL")
    four_hour_trend = cond_eval.get("fourHourTrend", "NEUTRAL")

    # ── Danger checks (hard safety) ────────────────────────────────────────
    danger_reason: str | None = None
    if not one_hour or indicators.get("atr") is None or indicators.get("rsi") is None:
        danger_reason = "Required indicator data missing"
    else:
        last_candle_ts = float(one_hour[-1][0])
        if time.time() - last_candle_ts > STALE_DATA_SECONDS:
            danger_reason = "Market data is stale (last completed candle too old)"
        elif spread_pct is None:
            danger_reason = "Spread data unavailable — cannot validate execution cost"
        elif spread_pct > MAX_SPREAD_PCT:
            danger_reason = f"Spread too wide ({spread_pct:.2f}% > {MAX_SPREAD_PCT}%)"
        else:
            atr = float(indicators["atr"])
            candle_range = float(one_hour[-1][2]) - float(one_hour[-1][3])
            if atr > 0 and candle_range > ABNORMAL_RANGE_ATR * atr:
                danger_reason = (
                    f"Abnormal volatility (candle range {candle_range:.4f} "
                    f"> {ABNORMAL_RANGE_ATR}× ATR {atr:.4f})"
                )

    if danger_reason:
        mode = "DANGER"
    elif one_hour_trend == "NEUTRAL" and four_hour_trend == "NEUTRAL":
        mode = "RANGE"
    else:
        mode = "TREND"

    # ── Range / mean-reversion setup (only in RANGE mode) ─────────────────
    range_setup: dict[str, Any] | None = None
    if mode == "RANGE" and len(one_hour) >= 21:
        closes = [float(r[4]) for r in one_hour]
        window = closes[-20:]
        mid = sum(window) / 20
        variance = sum((x - mid) ** 2 for x in window) / 20
        std = math.sqrt(variance)
        lower = mid - 2 * std
        upper = mid + 2 * std
        rsis = rsi_series(closes)
        rsi_now, rsi_prev = rsis[-1], rsis[-2]
        atr = indicators.get("atr")
        if std > 0 and rsi_now is not None and rsi_prev is not None and atr:
            close = closes[-1]
            if (
                close <= lower * 1.01
                and rsi_now < RANGE_RSI_OVERSOLD
                and rsi_now > rsi_prev              # momentum recovering
                and four_hour_trend != "BEARISH"    # HTF not strongly against
            ):
                range_setup = {
                    "direction":  "LONG",
                    "stopLoss":   close - float(atr) * ATR_MULTIPLIER,
                    "takeProfit": mid,              # exit toward middle of range
                    "reason":     f"Price at lower band ({close:.2f} ≤ {lower:.2f}), "
                                  f"RSI {rsi_now:.1f} oversold and recovering",
                }
            elif (
                close >= upper * 0.99
                and rsi_now > RANGE_RSI_OVERBOUGHT
                and rsi_now < rsi_prev              # momentum rolling over
                and four_hour_trend != "BULLISH"
            ):
                range_setup = {
                    "direction":  "SHORT",
                    "stopLoss":   close + float(atr) * ATR_MULTIPLIER,
                    "takeProfit": mid,
                    "reason":     f"Price at upper band ({close:.2f} ≥ {upper:.2f}), "
                                  f"RSI {rsi_now:.1f} overbought and rolling over",
                }
            # Reject setups where the target is on the wrong side of entry
            if range_setup:
                tp, direction = range_setup["takeProfit"], range_setup["direction"]
                if (direction == "LONG" and tp <= close) or (direction == "SHORT" and tp >= close):
                    range_setup = None

    return {
        "score":        score,
        "maxScore":     OPP_MAX_SCORE,
        "weighted":     weighted,
        "mode":         mode,
        "dangerReason": danger_reason,
        "rangeSetup":   range_setup,
        "spreadPct":    round(spread_pct, 4) if spread_pct is not None else None,
    }


def proposed_trade(
    signal: str,
    current_price: float,
    indicators: dict[str, Any],
    balance: float,
) -> dict[str, Any] | None:
    """Compute what the trade would look like at current price, without opening it."""
    if signal not in ("LONG", "SHORT"):
        return None
    atr = indicators.get("atr")
    if not atr or atr <= 0:
        return None
    stop_distance = float(atr) * ATR_MULTIPLIER
    risk_amount   = balance * RISK_PER_TRADE
    quantity = min(
        risk_amount / stop_distance,
        balance / current_price if current_price > 0 else 0,
    )
    if quantity <= 0:
        return None
    stop_loss = (
        current_price - stop_distance if signal == "LONG"
        else current_price + stop_distance
    )
    take_profit = (
        current_price + stop_distance * REWARD_TO_RISK if signal == "LONG"
        else current_price - stop_distance * REWARD_TO_RISK
    )
    reward_amount = stop_distance * REWARD_TO_RISK * quantity
    return {
        "direction":    signal,
        "entry":        round_price(current_price),
        "stopLoss":     round_price(stop_loss),
        "takeProfit":   round_price(take_profit),
        "riskAmount":   round(risk_amount, 2),
        "rewardAmount": round(reward_amount, 2),
        "rrRatio":      round(REWARD_TO_RISK, 2),
        "quantity":     round_amount(quantity),
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def coin_metrics(connection: sqlite3.Connection, coin: str, state: sqlite3.Row) -> dict[str, float]:
    trades = connection.execute(
        "SELECT profit_loss, account_balance, closed_at FROM trades WHERE coin = ? ORDER BY id",
        (coin,),
    ).fetchall()
    profits    = [float(r["profit_loss"]) for r in trades if float(r["profit_loss"]) > 0]
    losses     = [float(r["profit_loss"]) for r in trades if float(r["profit_loss"]) < 0]
    balance    = max(0.0, float(state["balance"]))
    starting   = float(state["starting_balance"])
    total_pnl  = balance - starting
    gross_profit = sum(profits)
    gross_loss   = abs(sum(losses))
    equity = [starting] + [float(r["account_balance"]) for r in trades]
    peak = starting
    max_dd = 0.0
    for val in equity:
        peak = max(peak, val)
        if peak > 0:
            max_dd = max(max_dd, (peak - val) / peak * 100)
    today = date_key()
    daily_loss = sum(
        abs(float(r["profit_loss"]))
        for r in trades
        if r["closed_at"].startswith(today) and float(r["profit_loss"]) < 0
    )
    consecutive = 0
    for r in reversed(trades):
        if float(r["profit_loss"]) < 0:
            consecutive += 1
        else:
            break
    # Day the streak most recently extended at-or-above MAX_CONSECUTIVE_LOSSES.
    # Using the most recent loss ensures the pause is re-applied each new day
    # as long as the streak continues (matches metals_backtest.py behaviour).
    # The pause expires at the next UTC midnight so the bot can resume without
    # requiring a winning trade to break the deadlock.
    streak_block_day: str | None = None
    if consecutive >= MAX_CONSECUTIVE_LOSSES:
        # trades[-1] is the most recent trade and is always a loss here
        streak_block_day = trades[-1]["closed_at"][:10]
    return {
        "virtualBalance":     balance,
        "startingBalance":    starting,
        "totalProfitLoss":    total_pnl,
        "roi":                (total_pnl / starting * 100) if starting else 0.0,
        "numberOfTrades":     len(trades),
        "wins":               len(profits),
        "losses":             len(losses),
        "winRate":            (len(profits) / len(trades) * 100) if trades else 0.0,
        "profitFactor":       (gross_profit / gross_loss) if gross_loss else 0.0,
        "maximumDrawdown":    max_dd,
        "dailyLoss":          daily_loss,
        "consecutiveLosses":  consecutive,
        "streakBlockDay":     streak_block_day,
    }


# ---------------------------------------------------------------------------
# State builder
# ---------------------------------------------------------------------------

def trade_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id":             int(row["id"]),
        "coin":           row["coin"],
        "openedAt":       row["opened_at"],
        "closedAt":       row["closed_at"],
        "direction":      row["direction"],
        "entry":          row["entry"],
        "exit":           row["exit"],
        "stopLoss":       row["stop_loss"],
        "takeProfit":     row["take_profit"],
        "rsi":            row["rsi"],
        "macd":           row["macd"],
        "atr":            row["atr"],
        "trend4h":        row["trend_4h"],
        "profitLoss":     row["profit_loss"],
        "accountBalance": row["account_balance"],
        "exitReason":     row["exit_reason"],
        # Audit fields (nullable for trades recorded before Aug 2026)
        "riskAmount":      row["risk_amount"]      if "risk_amount"      in row.keys() else None,
        "rMultiple":       row["r_multiple"]       if "r_multiple"       in row.keys() else None,
        "pnlPct":          row["pnl_pct"]          if "pnl_pct"          in row.keys() else None,
        "durationSeconds": row["duration_seconds"] if "duration_seconds" in row.keys() else None,
        "result":          row["result"]           if "result"           in row.keys() else None,
        "entryScore":      row["entry_score"]      if "entry_score"      in row.keys() else None,
        "passCount":       row["pass_count"]       if "pass_count"       in row.keys() else None,
        "trend1h":         row["trend_1h"]         if "trend_1h"         in row.keys() else None,
        "entryMode":       row["entry_mode"]       if "entry_mode"       in row.keys() else None,
        "entryConditions": row["entry_conditions"] if "entry_conditions" in row.keys() else None,
        # Directional audit (nullable for trades before the LONG+SHORT upgrade)
        "longScore":       row["long_score"]       if "long_score"       in row.keys() else None,
        "shortScore":      row["short_score"]      if "short_score"      in row.keys() else None,
        "entryThreshold":  row["entry_threshold"]  if "entry_threshold"  in row.keys() else None,
        # Strategy attribution: CORE (1h) or ACTIVE (15m). Pre-upgrade rows → CORE.
        "strategy":        (row["strategy"] if "strategy" in row.keys() and row["strategy"] else "CORE"),
    }


def _opportunity_with_last_trade(
    connection: sqlite3.Connection,
    coin: str,
    data: dict[str, Any],
    open_position: dict[str, Any] | None,
) -> dict[str, Any]:
    opportunity = dict(data.get("opportunity") or {
        "score": 0, "maxScore": OPP_MAX_SCORE, "mode": "TREND",
        "entryStatus": "WAIT", "reason": "Waiting for market data",
        "nextEligible": None, "lastTradeAt": None,
    })
    if open_position:
        opportunity["lastTradeAt"] = open_position.get("openedAt")
    else:
        row = connection.execute(
            "SELECT closed_at FROM trades WHERE coin = ? ORDER BY id DESC LIMIT 1", (coin,)
        ).fetchone()
        opportunity["lastTradeAt"] = row["closed_at"] if row else None
    return opportunity


def build_coin_state(
    connection: sqlite3.Connection,
    coin: str,
    snapshot: dict[str, Any] | None = None,
    message: str | None = None,
    status: str = "WAITING_FOR_DATA",
) -> dict[str, Any]:
    state = load_coin_state(connection, coin)
    stored = json.loads(state["snapshot"]) if state["snapshot"] else {}
    data = snapshot or stored
    open_position = json.loads(state["open_position"]) if state["open_position"] else None
    keys = state.keys()
    active_position = (
        json.loads(state["active_position"])
        if "active_position" in keys and state["active_position"] else None
    )
    active_snap = (
        json.loads(state["active_snapshot"])
        if "active_snapshot" in keys and state["active_snapshot"] else None
    )
    recent_trades = connection.execute(
        "SELECT * FROM trades WHERE coin = ? ORDER BY id DESC LIMIT 50", (coin,)
    ).fetchall()
    current_message = message or state["message"]
    # Always propagate the persisted botStatus when one is stored — this ensures
    # that API_ERROR (with null currentPrice) survives multi-state reads without
    # reverting to the "WAITING_FOR_DATA" default.
    status = data.get("botStatus") or status
    # On data-error / no-data states, never show stale diagnostics: report the
    # data problem itself as the current blocker.
    exec_diag = data.get("executionDiagnostics")
    if status in ("API_ERROR", "WAITING_FOR_DATA"):
        exec_diag = {
            "eligible": False,
            "blockers": [
                f"Market data problem: {current_message}" if current_message
                else "Waiting for market data (no successful scan yet)"
            ],
            "lastCompletedCandleAt": data.get("lastCompletedCandleAt"),
            "checkedAt": now_iso(),
        }
    metrics = coin_metrics(connection, coin, state)

    # Compute unrealised PnL for open position
    if open_position and data.get("currentPrice"):
        price = data["currentPrice"]
        entry = float(open_position["entry"])
        qty   = float(open_position["quantity"])
        if open_position["direction"] == "LONG":
            open_position["unrealisedPnl"] = round((price - entry) * qty, 2)
            open_position["unrealisedPct"] = round((price - entry) / entry * 100, 3) if entry else 0.0
        else:
            open_position["unrealisedPnl"] = round((entry - price) * qty, 2)
            open_position["unrealisedPct"] = round((entry - price) / entry * 100, 3) if entry else 0.0
        open_position["currentPrice"] = price

    # Unrealised PnL for the ACTIVE-strategy position (same maths)
    if active_position and data.get("currentPrice"):
        price = data["currentPrice"]
        entry = float(active_position["entry"])
        qty   = float(active_position["quantity"])
        sign  = 1 if active_position["direction"] == "LONG" else -1
        active_position["unrealisedPnl"] = round((price - entry) * qty * sign, 2)
        active_position["unrealisedPct"] = round((price - entry) / entry * 100 * sign, 3) if entry else 0.0
        active_position["currentPrice"] = price

    return {
        "coin":          coin,
        "instrument":    instrument_info(coin),
        "market": {
            "pair":                 instrument_display(coin),
            "currentPrice":         data.get("currentPrice"),
            "updatedAt":            data.get("updatedAt", state["updated_at"]),
            "lastCompletedCandleAt": data.get("lastCompletedCandleAt"),
        },
        "signal":        data.get("signal", "NO_TRADE"),
        "oneHourTrend":  data.get("oneHourTrend", "NEUTRAL"),
        "fourHourTrend": data.get("fourHourTrend", "NEUTRAL"),
        "indicators":    data.get("indicators", {
            "rsi": None, "macd": None, "macdSignal": None,
            "atr": None, "ema20": None, "ema50": None, "volume": None,
        }),
        "strategyConditions": data.get("strategyConditions"),
        "proposedTrade":      data.get("proposedTrade"),
        "directional":        data.get("directional"),
        "executionDiagnostics": exec_diag,
        "opportunity":        _opportunity_with_last_trade(connection, coin, data, open_position),
        "position":           open_position,
        "activePosition":     active_position,
        "active":             active_snap,
        "metrics":            metrics,
        "risk": {
            "dailyLossLimit":           float(state["starting_balance"]) * DAILY_LOSS_LIMIT,
            "maximumConsecutiveLosses": MAX_CONSECUTIVE_LOSSES,
            "riskPerTrade":            RISK_PER_TRADE * 100,
            "rewardToRisk":            REWARD_TO_RISK,
            "pollingSeconds":          POLLING_SECONDS,
        },
        "recentTrades":  [trade_from_row(r) for r in recent_trades],
        "botStatus":     status,
        "message":       current_message,
        # Surfaced only for metals when Yahoo Finance scan candles are unavailable
        # but the gold-api.com spot price succeeded. Null for crypto and normal metals.
        "scanNote":      data.get("scanNote"),
    }


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def close_position(
    connection: sqlite3.Connection,
    coin: str,
    state: sqlite3.Row,
    position: dict[str, Any],
    exit_price: float,
    reason: str,
    strategy: str = "CORE",
) -> None:
    _assert_paper_only()   # simulated close only — no real order can be sent
    direction = position["direction"]
    quantity  = float(position["quantity"])
    entry     = float(position["entry"])
    pnl = (
        (exit_price - entry) * quantity if direction == "LONG"
        else (entry - exit_price) * quantity
    )
    balance = max(0.0, float(state["balance"]) + pnl)
    closed_at = now_iso()
    risk_amount_val = safe_float(position.get("riskAmount")) or 0.0
    r_multiple_val  = (pnl / risk_amount_val) if risk_amount_val > 0 else None
    pnl_pct_val     = (pnl / (entry * quantity) * 100) if entry and quantity else None
    try:
        duration_val = (
            datetime.fromisoformat(closed_at) - datetime.fromisoformat(position["openedAt"])
        ).total_seconds()
    except (ValueError, TypeError, KeyError):
        duration_val = None
    if risk_amount_val > 0 and abs(pnl) <= risk_amount_val * 0.1:
        result_val = "BREAKEVEN"
    else:
        result_val = "WIN" if pnl > 0 else "LOSS"
    connection.execute(
        """
        INSERT INTO trades
            (coin, opened_at, closed_at, direction, entry, exit,
             stop_loss, take_profit, rsi, macd, atr, trend_4h,
             profit_loss, account_balance, exit_reason,
             risk_amount, r_multiple, pnl_pct, duration_seconds, result,
             entry_score, pass_count, trend_1h, entry_mode, entry_conditions,
             long_score, short_score, entry_threshold, strategy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            coin, position["openedAt"], closed_at, direction,
            entry, exit_price, position["stopLoss"], position["takeProfit"],
            position.get("entryRsi"), position.get("entryMacd"), position.get("entryAtr"),
            position["trend4h"], pnl, balance, reason,
            risk_amount_val, r_multiple_val, pnl_pct_val, duration_val, result_val,
            safe_float(position.get("entryScore")), position.get("passCount"),
            position.get("trend1h"), position.get("entryMode"),
            position.get("entryConditions"),
            safe_float(position.get("longScore")), safe_float(position.get("shortScore")),
            safe_float(position.get("entryThreshold")), strategy,
        ),
    )
    # Disarm re-entry protection on the strategy's own slot: no same-signal
    # recycling until the setup resets.
    pos_col, reentry_col = (
        ("active_position", "active_reentry") if strategy == "ACTIVE"
        else ("open_position", "reentry")
    )
    connection.execute(
        f"UPDATE coin_state SET balance = ?, {pos_col} = NULL, {reentry_col} = ?, updated_at = ? WHERE coin = ?",
        (balance, json.dumps({"armed": False, "lastDirection": direction}), now_iso(), coin),
    )
    risk_amount = risk_amount_val
    r_multiple  = r_multiple_val
    result_word = result_val
    cur = "$" if coin in METALS else "£"
    pnl_str = f"+{cur}{pnl:.2f}" if pnl >= 0 else f"-{cur}{abs(pnl):.2f}"
    r_str   = f" | R {r_multiple:+.2f}" if r_multiple is not None else ""
    entry_score = position.get("entryScore")
    entry_mode  = position.get("entryMode")
    audit = ""
    if entry_score is not None or entry_mode:
        # Strategy-aware denominator: ACTIVE scores are raw x/6 conditions,
        # CORE scores are weighted x/8.
        score_max = ACTIVE_MAX_SCORE if strategy == "ACTIVE" else OPP_MAX_SCORE
        audit = (
            f" | entry score {entry_score}/{score_max}" if entry_score is not None else ""
        ) + (f" | mode {entry_mode}" if entry_mode else "")
        if position.get("entryConditions"):
            audit += f" | passed: {position['entryConditions']}"
    add_activity(
        connection, coin,
        "TRADE_CLOSED",
        f"{strategy} {direction} position closed ({reason.replace('_',' ')}) | exit {cur}{exit_price:,.2f} "
        f"| P&L {pnl_str}{r_str} | {result_word}{audit} "
        f"| SL {cur}{float(position['stopLoss']):,.2f} | TP {cur}{float(position['takeProfit']):,.2f} "
        f"| risk {cur}{risk_amount:.2f} | balance {cur}{balance:.2f}",
    )
    connection.commit()


def _block_stale_opportunity(connection: sqlite3.Connection, coin: str, reason: str) -> None:
    """Fail closed: never leave a stale READY/WAIT opportunity when data is unavailable."""
    state = load_coin_state(connection, coin)
    stored = json.loads(state["snapshot"]) if state["snapshot"] else None
    if not stored:
        return
    stored["opportunity"] = {
        "score":        0,
        "maxScore":     OPP_MAX_SCORE,
        "mode":         "DANGER",
        "entryStatus":  "BLOCKED",
        "reason":       reason,
        "nextEligible": "When market data is available again",
        "lastTradeAt":  None,
    }
    stored["signal"] = "NO_TRADE"
    stored["botStatus"] = "API_ERROR"   # honest status: never show stale READY
    connection.execute(
        "UPDATE coin_state SET snapshot = ?, updated_at = ? WHERE coin = ?",
        (json.dumps(stored), now_iso(), coin),
    )


# ---------------------------------------------------------------------------
# ACTIVE strategy — 15-minute entries with 1h trend context (PAPER ONLY)
# Runs in parallel with CORE on every asset. Separate position slot,
# separate candle gate and re-entry protection; shares the account balance,
# 1% risk sizing, portfolio risk ceiling and daily-loss/streak pauses.
# ---------------------------------------------------------------------------

def fetch_active_candles(coin: str) -> list[list[Any]]:
    """Completed 15m candles in Kraken row shape for the ACTIVE strategy."""
    if coin in COINS:
        pair = COINS[coin]["pair"]
        result = fetch_json(
            f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=15"
        )
        cutoff = time.time()
        rows = result.get(pair) or next(
            (v for k, v in result.items() if k != "last"), []
        )
        return [r for r in rows if len(r) >= 8 and float(r[0]) < cutoff][:-1]
    # Metals: Yahoo Finance 15m candles on the COMEX futures contract
    symbol = METALS[coin]["candles_symbol"]
    last_error: Exception | None = None
    for host in ("query1", "query2"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
            "?interval=15m&range=5d"
        )
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            cutoff = time.time()
            rows: list[list[Any]] = []
            for i, ts in enumerate(timestamps):
                o = safe_float(quote["open"][i]); h = safe_float(quote["high"][i])
                l = safe_float(quote["low"][i]);  c = safe_float(quote["close"][i])
                v = safe_float(quote["volume"][i]) or 0.0
                if None in (o, h, l, c) or float(ts) >= cutoff:
                    continue
                rows.append([float(ts), o, h, l, c, c, v, 1])
            return rows[:-1] if rows else rows
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError,
                IndexError, TypeError) as error:
            last_error = error
    raise RuntimeError(f"Yahoo Finance 15m error for {symbol}: {last_error}")


def _active_direction_conditions(
    direction: str,
    snap: dict[str, Any],
    close: float,
    fifteen_trend: str,
    one_hour_trend: str,
) -> list[dict[str, Any]]:
    """Six ACTIVE entry conditions for one direction, computed on 15m candles."""
    rsi_val  = snap["rsi"]
    macd_val = snap["macd"]
    sig_val  = snap["macdSignal"]
    e20      = snap["ema20"]
    avg_vol  = snap.get("_avg_volume") or 0.0
    volume   = snap["volume"] or 0.0

    def c(name: str, current_val: str, required_val: str, passed: bool) -> dict[str, Any]:
        return {"name": name, "currentValue": current_val,
                "requiredValue": required_val, "pass": passed}

    want = "BULLISH" if direction == "LONG" else "BEARISH"
    cond_vol = avg_vol > 0 and volume >= avg_vol * 0.7
    if direction == "LONG":
        rsi_ok  = rsi_val is not None and rsi_val >= 50
        macd_ok = macd_val is not None and sig_val is not None and macd_val > sig_val
        px_ok   = e20 is not None and close > e20
        rsi_req, macd_req, px_req = "≥ 50", "MACD above signal", "Price > EMA20"
    else:
        rsi_ok  = rsi_val is not None and rsi_val <= 50
        macd_ok = macd_val is not None and sig_val is not None and macd_val < sig_val
        px_ok   = e20 is not None and close < e20
        rsi_req, macd_req, px_req = "≤ 50", "MACD below signal", "Price < EMA20"
    return [
        c("15m Trend", fifteen_trend, want, fifteen_trend == want),
        c("1h Confirmation", one_hour_trend, want, one_hour_trend == want),
        c("RSI", f"{rsi_val:.1f}" if rsi_val is not None else "—", rsi_req, rsi_ok),
        c("MACD Momentum",
          f"{macd_val:.4f} vs {sig_val:.4f}" if (macd_val is not None and sig_val is not None) else "—",
          macd_req, macd_ok),
        c("Price vs EMA20",
          f"{close:.4f} vs {e20:.4f}" if e20 is not None else "—", px_req, px_ok),
        c("Volume", f"{volume:.4f}", f"≥ {avg_vol * 0.7:.4f} (70% avg)", cond_vol),
    ]


def evaluate_active_directional(
    fifteen: list[list[Any]],
    one_hour_trend: str,
    threshold: int = ACTIVE_MIN_PASS,
) -> dict[str, Any]:
    """Independent LONG/SHORT scoring for the ACTIVE 15m strategy.

    Raw pass count of 6 conditions, gate = threshold (default 4/6, configurable).
    Both directions are always evaluated; a tie above the gate means WAIT.
    """
    empty_ind = {"rsi": None, "macd": None, "macdSignal": None,
                 "atr": None, "ema20": None, "ema50": None, "volume": None}
    if len(fifteen) < 55:
        return {
            "long":  {"conditions": [], "passCount": 0, "score": 0},
            "short": {"conditions": [], "passCount": 0, "score": 0},
            "threshold": threshold, "shortThreshold": threshold,
            "maxScore": ACTIVE_MAX_SCORE, "weighted": False,
            "decision": "NO_TRADE",
            "decisionReason": "Waiting for enough 15m candle history (55+)",
            "fifteenTrend": "NEUTRAL", "oneHourTrend": one_hour_trend,
            "indicators": empty_ind,
        }
    snap  = indicator_snapshot(fifteen)
    close = float(fifteen[-1][4])
    fifteen_trend = trend_for(fifteen)
    long_conds  = _active_direction_conditions("LONG",  snap, close, fifteen_trend, one_hour_trend)
    short_conds = _active_direction_conditions("SHORT", snap, close, fifteen_trend, one_hour_trend)
    long_score  = sum(1 for cd in long_conds if cd["pass"])
    short_score = sum(1 for cd in short_conds if cd["pass"])
    long_ok, short_ok = long_score >= threshold, short_score >= threshold
    if long_ok and short_ok:
        if long_score > short_score:
            decision, reason = "LONG", f"Both qualified — LONG stronger ({long_score}/6 vs {short_score}/6)"
        elif short_score > long_score:
            decision, reason = "SHORT", f"Both qualified — SHORT stronger ({short_score}/6 vs {long_score}/6)"
        else:
            decision, reason = "NO_TRADE", f"Tie at {long_score}/6 both directions — waiting"
    elif long_ok:
        decision, reason = "LONG", f"LONG {long_score}/6 reached the {threshold}/6 gate (SHORT {short_score}/6)"
    elif short_ok:
        decision, reason = "SHORT", f"SHORT {short_score}/6 reached the {threshold}/6 gate (LONG {long_score}/6)"
    else:
        decision, reason = "NO_TRADE", (
            f"Neither direction reached the gate (LONG {long_score}/6, "
            f"SHORT {short_score}/6, need {threshold})"
        )
    return {
        "long":  {"conditions": long_conds,  "passCount": long_score,  "score": long_score},
        "short": {"conditions": short_conds, "passCount": short_score, "score": short_score},
        "threshold": threshold, "shortThreshold": threshold,
        "maxScore": ACTIVE_MAX_SCORE, "weighted": False,
        "decision": decision, "decisionReason": reason,
        "fifteenTrend": fifteen_trend, "oneHourTrend": one_hour_trend,
        "indicators": {k: v for k, v in snap.items() if not k.startswith("_")},
    }


def refresh_active(
    connection: sqlite3.Connection,
    coin: str,
    current_price: float,
    one_hour_trend: str,
    spread_pct: float | None,
) -> None:
    """One ACTIVE-strategy scan for one asset. Manages the ACTIVE position
    slot and evaluates new 15m entries. All writes go to the active_* columns;
    the CORE strategy is never touched. PAPER ONLY."""
    state = load_coin_state(connection, coin)
    active_position = json.loads(state["active_position"]) if state["active_position"] else None

    # ── Manage existing ACTIVE position FIRST (every scan, live price) ────
    # Exit management must never depend on 15m candle availability: we have a
    # live price, so SL/TP protection runs before any entry-data fetch.
    if active_position:
        hit_stop = (
            current_price <= active_position["stopLoss"]
            if active_position["direction"] == "LONG"
            else current_price >= active_position["stopLoss"]
        )
        hit_target = (
            current_price >= active_position["takeProfit"]
            if active_position["direction"] == "LONG"
            else current_price <= active_position["takeProfit"]
        )
        if hit_stop or hit_target:
            if hit_stop:
                # Adverse fill model: if price gapped THROUGH the stop between
                # scans, fill at the observed live price, not the stop — never
                # understate the loss.
                if active_position["direction"] == "LONG":
                    exit_price = min(current_price, active_position["stopLoss"])
                else:
                    exit_price = max(current_price, active_position["stopLoss"])
            else:
                # Take-profit is a limit order: fill at target by policy.
                exit_price = active_position["takeProfit"]
            close_position(
                connection, coin, state, active_position, exit_price,
                "STOP_LOSS" if hit_stop else "TAKE_PROFIT",
                strategy="ACTIVE",
            )
            state = load_coin_state(connection, coin)
            active_position = None

    try:
        fifteen = fetch_active_candles(coin)
    except Exception as error:
        _write_active_snapshot(connection, coin, {
            "status": "API_ERROR",
            "message": f"15m data unavailable: {error}",
            "updatedAt": now_iso(),
        })
        return

    dir_eval = evaluate_active_directional(fifteen, one_hour_trend, ACTIVE_MIN_PASS)
    decision = dir_eval["decision"]
    indicators = dir_eval["indicators"]

    completed_candle_at = (
        datetime.fromtimestamp(float(fifteen[-1][0]), timezone.utc).isoformat()
        if fifteen else None
    )
    is_new_candle = completed_candle_at is not None and completed_candle_at != state["active_last_candle_at"]

    # ── Safety filters (never weakened) ────────────────────────────────────
    danger: str | None = None
    if fifteen and (time.time() - float(fifteen[-1][0])) > ACTIVE_STALE_SECONDS:
        danger = "15m candle data is stale (last completed candle older than 45 min)"
    elif spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
        danger = f"Spread {spread_pct:.2f}% wider than {MAX_SPREAD_PCT}% safety limit"
    elif fifteen and indicators.get("atr"):
        last = fifteen[-1]
        candle_range = float(last[2]) - float(last[3])
        if candle_range > float(indicators["atr"]) * ABNORMAL_RANGE_ATR:
            danger = f"Abnormal 15m candle range ({candle_range:.4f} > 3× ATR)"

    try:
        reentry = json.loads(state["active_reentry"]) if state["active_reentry"] else {"armed": True, "lastDirection": None}
    except (TypeError, ValueError):
        reentry = {"armed": True, "lastDirection": None}
    candidate = decision if decision in ("LONG", "SHORT") else "NONE"
    if is_new_candle and not reentry.get("armed", True):
        if candidate != reentry.get("lastDirection"):
            reentry = {"armed": True, "lastDirection": reentry.get("lastDirection")}
            connection.execute(
                "UPDATE coin_state SET active_reentry = ?, updated_at = ? WHERE coin = ?",
                (json.dumps(reentry), now_iso(), coin),
            )
    armed = bool(reentry.get("armed", True))

    block_reason: str | None = None
    opened: dict[str, Any] | None = None
    cur = "$" if coin in METALS else "£"

    if is_new_candle and active_position is None and decision in ("LONG", "SHORT"):
        m = coin_metrics(connection, coin, state)
        daily_limit = float(state["starting_balance"]) * DAILY_LOSS_LIMIT
        streak_paused = (
            m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
            and m.get("streakBlockDay") == date_key()
        )
        if float(state["balance"]) <= 0:
            block_reason = "Account balance exhausted"
        elif m["dailyLoss"] >= daily_limit:
            block_reason = f"Daily loss limit reached ({cur}{m['dailyLoss']:.2f} / {cur}{daily_limit:.2f})"
        elif streak_paused:
            block_reason = f"Max consecutive losses reached ({int(m['consecutiveLosses'])})"
        elif danger:
            block_reason = danger
        elif not armed:
            block_reason = ("Re-entry protection: same ACTIVE setup already traded — "
                            "waiting for a changed signal on a new 15m candle")
        elif not indicators.get("atr") or float(indicators["atr"]) <= 0:
            block_reason = "No valid ATR on 15m candles"
        else:
            atr_val     = float(indicators["atr"])
            risk_amount = float(state["balance"]) * RISK_PER_TRADE
            stop_dist   = atr_val * ACTIVE_ATR_MULTIPLIER
            stop_loss   = current_price - stop_dist if decision == "LONG" else current_price + stop_dist
            take_profit = (
                current_price + stop_dist * ACTIVE_REWARD_TO_RISK if decision == "LONG"
                else current_price - stop_dist * ACTIVE_REWARD_TO_RISK
            )
            quantity = min(
                risk_amount / stop_dist if stop_dist > 0 else 0,
                float(state["balance"]) / current_price if current_price > 0 else 0,
            )
            risk_block = _portfolio_risk_block_reason(connection, risk_amount) if quantity > 0 else None
            if risk_block:
                block_reason = "Entry blocked by portfolio risk limit."
                add_activity(
                    connection, coin, "ENTRY_BLOCKED",
                    f"ACTIVE {decision} qualifies (LONG {dir_eval['long']['score']}/6, "
                    f"SHORT {dir_eval['short']['score']}/6) but was skipped — {risk_block}",
                )
            elif quantity > 0:
                _assert_paper_only()   # simulated open only — no real order can be sent
                opened = {
                    "strategy":   "ACTIVE",
                    "direction":  decision,
                    "entry":      round_price(current_price),
                    "stopLoss":   round_price(stop_loss),
                    "takeProfit": round_price(take_profit),
                    "quantity":   round_amount(quantity),
                    "riskAmount": round(risk_amount, 2),
                    "openedAt":   now_iso(),
                    "entryRsi":   indicators.get("rsi"),
                    "entryMacd":  indicators.get("macd"),
                    "entryAtr":   indicators.get("atr"),
                    # ACTIVE has no 4h context; store its 1h context so the
                    # trades table enum (BULLISH/BEARISH/NEUTRAL) stays valid.
                    "trend4h":    one_hour_trend if one_hour_trend in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL",
                    "trend1h":    one_hour_trend,
                    "entryScore": dir_eval[decision.lower()]["score"],
                    "passCount":  dir_eval[decision.lower()]["score"],
                    "maxScore":   ACTIVE_MAX_SCORE,
                    "entryMode":  "ACTIVE",
                    "longScore":  dir_eval["long"]["score"],
                    "shortScore": dir_eval["short"]["score"],
                    "entryThreshold": ACTIVE_MIN_PASS,
                    "entryConditions": ", ".join(
                        cd["name"] for cd in dir_eval[decision.lower()]["conditions"] if cd["pass"]
                    ),
                }
                connection.execute(
                    "UPDATE coin_state SET active_position = ?, updated_at = ? WHERE coin = ?",
                    (json.dumps(opened), now_iso(), coin),
                )
                add_activity(
                    connection, coin, "TRADE_OPENED",
                    f"ACTIVE {decision} opened at {cur}{current_price:,.2f} "
                    f"| LONG {dir_eval['long']['score']}/6 vs SHORT {dir_eval['short']['score']}/6 "
                    f"(gate {ACTIVE_MIN_PASS}/6) | passed: {opened['entryConditions']} "
                    f"| SL {cur}{stop_loss:,.2f} | TP {cur}{take_profit:,.2f} | risk {cur}{risk_amount:.2f}",
                )
                active_position = opened

    # ── Diagnostic log (same evaluation feeds log + dashboard) ─────────────
    if is_new_candle:
        add_activity(connection, coin, "STRATEGY_EVALUATED", json.dumps({
            "strategy":         "ACTIVE",
            "price":            round_price(current_price),
            "signal":           decision if (opened or decision == "NO_TRADE") else decision,
            "passCount":        max(dir_eval["long"]["score"], dir_eval["short"]["score"]),
            "totalCount":       ACTIVE_MAX_SCORE,
            "score":            max(dir_eval["long"]["score"], dir_eval["short"]["score"]),
            "maxScore":         ACTIVE_MAX_SCORE,
            "conditions":       dir_eval["long"]["conditions"] if dir_eval["long"]["score"] >= dir_eval["short"]["score"] else dir_eval["short"]["conditions"],
            "noTradeReason":    None if decision in ("LONG", "SHORT") else dir_eval["decisionReason"],
            "executionBlocked": block_reason is not None,
            "blockReason":      block_reason,
            "directional":      _directional_diag_block(dir_eval),
        }))

    if is_new_candle and completed_candle_at:
        connection.execute(
            "UPDATE coin_state SET active_last_candle_at = ?, updated_at = ? WHERE coin = ?",
            (completed_candle_at, now_iso(), coin),
        )

    _write_active_snapshot(connection, coin, {
        "status": "READY" if not danger else "DANGER",
        "message": danger,
        "updatedAt": now_iso(),
        "lastCompletedCandleAt": completed_candle_at,
        "decision": decision,
        "decisionReason": dir_eval["decisionReason"],
        "longScore": dir_eval["long"]["score"],
        "shortScore": dir_eval["short"]["score"],
        "threshold": ACTIVE_MIN_PASS,
        "maxScore": ACTIVE_MAX_SCORE,
        "longConditions": dir_eval["long"]["conditions"],
        "shortConditions": dir_eval["short"]["conditions"],
        "fifteenTrend": dir_eval.get("fifteenTrend", "NEUTRAL"),
        "blockReason": block_reason,
    })
    connection.commit()


def _write_active_snapshot(connection: sqlite3.Connection, coin: str, snap: dict[str, Any]) -> None:
    connection.execute(
        "UPDATE coin_state SET active_snapshot = ?, updated_at = ? WHERE coin = ?",
        (json.dumps(snap), now_iso(), coin),
    )
    # Commit immediately: refresh_active can return early on this path and an
    # uncommitted write would keep the SQLite lock held (blocks other readers).
    connection.commit()


# ---------------------------------------------------------------------------
# Core refresh — one coin
# ---------------------------------------------------------------------------

def refresh_coin(connection: sqlite3.Connection, coin: str) -> dict[str, Any]:
    pair = COINS[coin]["pair"]
    try:
        current_price, spread_pct, one_hour, four_hour = fetch_market_data(pair)
        add_activity(connection, coin, "MARKET_DATA_UPDATED",
                     f"Price: £{current_price:,.2f}")
    except Exception as error:
        add_activity(connection, coin, "API_ERROR", str(error))
        _block_stale_opportunity(connection, coin, f"Market data unavailable: {error}")
        connection.commit()
        return build_coin_state(connection, coin, message=str(error), status="API_ERROR")

    if len(one_hour) < 55 or len(four_hour) < 55:
        _block_stale_opportunity(connection, coin, "Not enough candle data to evaluate safely")
        connection.commit()
        # ACTIVE runs on its own 15m data — a live price is available, so its
        # position management (and independent scan) must not be skipped just
        # because CORE lacks 1h/4h history.
        try:
            refresh_active(connection, coin, current_price, "NEUTRAL", spread_pct)
        except Exception as error:  # never let ACTIVE break the CORE scan
            add_activity(connection, coin, "API_ERROR", f"ACTIVE scan failed: {error}")
            connection.commit()
        return build_coin_state(
            connection, coin,
            message=f"Waiting for enough candles ({len(one_hour)}/55 1h, {len(four_hour)}/55 4h).",
            status="WAITING_FOR_DATA",
        )

    cond_eval    = evaluate_conditions(one_hour, four_hour)
    one_hour_trend  = cond_eval["oneHourTrend"]
    four_hour_trend = cond_eval["fourHourTrend"]
    indicators      = cond_eval["indicators"]

    # ── Independent LONG/SHORT directional scoring (same weighted gate) ────
    th = DIRECTIONAL_THRESHOLDS[coin]
    dir_eval = evaluate_conditions_directional(
        one_hour, four_hour,
        threshold=th["long"], short_threshold=th["short"], weighted=True,
    )
    decision = dir_eval["decision"]

    # ── Weighted opportunity scoring (paper mode) ──────────────────────────
    opp       = evaluate_opportunity(cond_eval, one_hour, current_price, spread_pct)
    mode      = opp["mode"]
    score     = opp["score"]
    trend_dir = decision if decision in ("LONG", "SHORT") else cond_eval["bias"]

    # Dual-direction entry gate (Aug 2026): either direction may qualify
    # independently at its own threshold (same validated >= 6/8 weighted
    # score as before). Hard safety rule preserved: never trade against a
    # clear opposing 4h trend.
    trend_entry_ok = (
        mode == "TREND"
        and decision in ("LONG", "SHORT")
        and not (decision == "LONG" and four_hour_trend == "BEARISH")
        and not (decision == "SHORT" and four_hour_trend == "BULLISH")
    )
    range_setup = opp["rangeSetup"] if mode == "RANGE" else None

    if trend_entry_ok:
        signal = trend_dir
    elif range_setup:
        signal = range_setup["direction"]
    else:
        signal = "NO_TRADE"
    cond_eval = dict(cond_eval)
    cond_eval["signal"] = signal
    cond_eval["decision"] = signal

    completed_candle_at = datetime.fromtimestamp(
        float(one_hour[-1][0]), timezone.utc
    ).isoformat()

    state = load_coin_state(connection, coin)
    if state["day_key"] != date_key():
        connection.execute(
            "UPDATE coin_state SET day_key = ?, updated_at = ? WHERE coin = ?",
            (date_key(), now_iso(), coin),
        )
        connection.commit()
        state = load_coin_state(connection, coin)

    open_position = json.loads(state["open_position"]) if state["open_position"] else None

    # --- Re-entry protection state ---
    try:
        reentry = json.loads(state["reentry"]) if state["reentry"] else {"armed": True, "lastDirection": None}
    except (TypeError, ValueError, KeyError):
        reentry = {"armed": True, "lastDirection": None}

    # --- Check stop/target on existing position ---
    if open_position:
        hit_stop = (
            current_price <= open_position["stopLoss"]
            if open_position["direction"] == "LONG"
            else current_price >= open_position["stopLoss"]
        )
        hit_target = (
            current_price >= open_position["takeProfit"]
            if open_position["direction"] == "LONG"
            else current_price <= open_position["takeProfit"]
        )
        if hit_stop or hit_target:
            exit_price = open_position["stopLoss"] if hit_stop else open_position["takeProfit"]
            close_position(
                connection, coin, state, open_position, exit_price,
                "STOP_LOSS" if hit_stop else "TAKE_PROFIT",
            )
            state = load_coin_state(connection, coin)
            try:
                reentry = json.loads(state["reentry"]) if state["reentry"] else {"armed": False, "lastDirection": open_position["direction"]}
            except (TypeError, ValueError, KeyError):
                reentry = {"armed": False, "lastDirection": open_position["direction"]}
            open_position = None

    # --- Evaluate new entry on completed candle ---
    is_new_candle = completed_candle_at != state["last_candle_at"]
    prop_trade: dict[str, Any] | None = None
    execution_block_reason: str | None = None
    opened_this_cycle = False
    opened_position: dict[str, Any] | None = None

    # Re-arm re-entry protection: requires a NEW candle AND the previous setup
    # actually going away (changed signal state / threshold no longer crossed).
    # candidate_signal is what would trigger an entry right now, in either mode.
    if trend_entry_ok:
        candidate_signal = trend_dir
    elif range_setup:
        candidate_signal = range_setup["direction"]
    else:
        candidate_signal = "NONE"
    if is_new_candle and not reentry.get("armed", True):
        if candidate_signal != reentry.get("lastDirection"):
            reentry = {"armed": True, "lastDirection": reentry.get("lastDirection")}
            connection.execute(
                "UPDATE coin_state SET reentry = ?, updated_at = ? WHERE coin = ?",
                (json.dumps(reentry), now_iso(), coin),
            )
    armed = bool(reentry.get("armed", True))

    # --- Track the opposite direction while a position is open (log only) ---
    if is_new_candle and open_position is not None:
        _opp_dir  = "SHORT" if open_position["direction"] == "LONG" else "LONG"
        _opp_gate = th["short"] if _opp_dir == "SHORT" else th["long"]
        _opp_score = dir_eval[_opp_dir.lower()]["score"]
        if _opp_score >= _opp_gate:
            add_activity(
                connection, coin, "OPPOSITE_SIGNAL",
                f"Strong opposite signal detected: {_opp_dir} {_opp_score}/{dir_eval['maxScore']} "
                f"while {open_position['direction']} position open "
                f"(LONG {dir_eval['long']['score']}/{dir_eval['maxScore']}, "
                f"SHORT {dir_eval['short']['score']}/{dir_eval['maxScore']}) — "
                f"not auto-reversing (no validated reversal rule yet)",
            )

    if is_new_candle and open_position is None:
        m = coin_metrics(connection, coin, state)
        daily_limit = float(state["starting_balance"]) * DAILY_LOSS_LIMIT
        # Streak pause expires at the next UTC day boundary so the bot resumes
        # automatically without needing a winning trade to break the deadlock.
        streak_paused = (
            m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
            and m.get("streakBlockDay") == date_key()
        )
        risk_paused = (
            m["dailyLoss"] >= daily_limit
            or streak_paused
            or float(state["balance"]) <= 0
        )
        if risk_paused:
            add_activity(connection, coin, "RISK_LIMIT_REACHED",
                         f"Daily loss £{m['dailyLoss']:.2f}/{daily_limit:.2f} or streak {m['consecutiveLosses']}")
            if signal in ("LONG", "SHORT"):
                if float(state["balance"]) <= 0:
                    execution_block_reason = "Account balance exhausted"
                elif m["dailyLoss"] >= daily_limit:
                    execution_block_reason = f"Daily loss limit reached (£{m['dailyLoss']:.2f} / £{daily_limit:.2f})"
                elif m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES:
                    execution_block_reason = f"Max consecutive losses reached ({int(m['consecutiveLosses'])})"
                else:
                    execution_block_reason = "Risk limit reached"
        elif signal in ("LONG", "SHORT") and not armed:
            execution_block_reason = (
                "Re-entry protection: same setup already traded — waiting for a "
                "changed signal or fresh threshold crossing on a new candle"
            )

        if (
            not risk_paused
            and armed
            and signal in ("LONG", "SHORT")
            and mode != "DANGER"
            and indicators.get("atr") is not None
            and indicators["atr"] > 0
        ):
            atr_val      = float(indicators["atr"])
            risk_amount  = float(state["balance"]) * RISK_PER_TRADE  # 1% max risk

            if range_setup and not trend_entry_ok:
                # Mean-reversion entry: stop 1.5×ATR away, target = middle of range
                entry_mode  = "RANGE"
                stop_loss   = float(range_setup["stopLoss"])
                take_profit = float(range_setup["takeProfit"])
                stop_dist   = abs(current_price - stop_loss)
            else:
                entry_mode  = "TREND"
                stop_dist   = atr_val * ATR_MULTIPLIER
                stop_loss   = current_price - stop_dist if signal == "LONG" else current_price + stop_dist
                take_profit = (
                    current_price + stop_dist * REWARD_TO_RISK if signal == "LONG"
                    else current_price - stop_dist * REWARD_TO_RISK
                )

            quantity = min(
                risk_amount / stop_dist if stop_dist > 0 else 0,
                float(state["balance"]) / current_price if current_price > 0 else 0,
            )
            risk_block = _portfolio_risk_block_reason(connection, risk_amount) if quantity > 0 else None
            if risk_block:
                execution_block_reason = "Entry blocked by portfolio risk limit."
                add_activity(
                    connection, coin, "ENTRY_BLOCKED",
                    f"{signal} qualifies (LONG {dir_eval['long']['score']}/{dir_eval['maxScore']}, "
                    f"SHORT {dir_eval['short']['score']}/{dir_eval['maxScore']}) but was skipped — {risk_block}",
                )
            elif quantity > 0:
                _assert_paper_only()   # simulated open only — no real order can be sent
                entry_gate = th["long"] if signal == "LONG" else th["short"]
                position = {
                    "direction":  signal,
                    "entry":      round_price(current_price),
                    "stopLoss":   round_price(stop_loss),
                    "takeProfit": round_price(take_profit),
                    "quantity":   round_amount(quantity),
                    "riskAmount": round(risk_amount, 2),
                    "openedAt":   now_iso(),
                    "entryRsi":   indicators.get("rsi"),
                    "entryMacd":  indicators.get("macd"),
                    "entryAtr":   indicators.get("atr"),
                    "trend4h":    four_hour_trend,
                    "trend1h":    one_hour_trend,
                    "entryScore": (
                        dir_eval[signal.lower()]["score"] if entry_mode == "TREND" else score
                    ),
                    "entryMode":  entry_mode,
                    "longScore":  dir_eval["long"]["score"],
                    "shortScore": dir_eval["short"]["score"],
                    "entryThreshold": entry_gate,
                    "entryConditions": ", ".join(
                        w["name"] for w in opp["weighted"] if w["pass"]
                    ) or "range setup",
                }
                connection.execute(
                    "UPDATE coin_state SET open_position = ?, updated_at = ? WHERE coin = ?",
                    (json.dumps(position), now_iso(), coin),
                )
                passed_names = ", ".join(
                    w["name"] for w in opp["weighted"] if w["pass"]
                ) or "range setup"
                add_activity(
                    connection, coin, "TRADE_OPENED",
                    f"{signal} opened at £{current_price:,.2f} "
                    f"| LONG {dir_eval['long']['score']}/{dir_eval['maxScore']} vs "
                    f"SHORT {dir_eval['short']['score']}/{dir_eval['maxScore']} (gate {position['entryThreshold']}) "
                    f"| mode {entry_mode} | passed: {passed_names} "
                    f"| SL £{stop_loss:,.2f} | TP £{take_profit:,.2f} | risk £{risk_amount:.2f}",
                )
                opened_this_cycle = True
                opened_position = position
        elif signal in ("LONG", "SHORT") and open_position is None:
            prop_trade = proposed_trade(signal, current_price, indicators, float(state["balance"]))

    else:
        # Between candles: show proposed trade if there's a live signal
        if signal in ("LONG", "SHORT") and open_position is None:
            prop_trade = proposed_trade(signal, current_price, indicators, float(state["balance"]))

    # --- Update snapshot (re-read state after any writes above) ---
    current_m = coin_metrics(connection, coin, load_coin_state(connection, coin))
    status = "READY"
    _streak_paused_now = (
        current_m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
        and current_m.get("streakBlockDay") == date_key()
    )
    if current_m["dailyLoss"] >= float(state["starting_balance"]) * DAILY_LOSS_LIMIT or \
       _streak_paused_now:
        status = "RISK_PAUSED"

    # --- Opportunity status block for the dashboard ---
    if open_position or opened_this_cycle:
        entry_status, entry_reason = "BLOCKED", "Position open — waiting for stop or target"
        next_eligible = "After the open position closes"
    elif status == "RISK_PAUSED":
        entry_status, entry_reason = "BLOCKED", execution_block_reason or "Risk limit reached"
        next_eligible = "After risk limits reset (next day or streak break)"
    elif execution_block_reason is not None:
        entry_status, entry_reason = "BLOCKED", execution_block_reason
        next_eligible = "When portfolio open risk drops below the ceiling"
    elif mode == "DANGER":
        entry_status, entry_reason = "BLOCKED", opp["dangerReason"] or "Unsafe market conditions"
        next_eligible = "When market conditions normalise"
    elif signal in ("LONG", "SHORT") and not armed:
        entry_status = "BLOCKED"
        entry_reason = "Re-entry protection: same setup already traded — needs a changed signal or fresh crossing"
        next_eligible = "After the signal resets on a new candle"
    elif decision in ("LONG", "SHORT") and not trend_entry_ok and mode == "TREND":
        entry_status = "BLOCKED"
        entry_reason = f"Hard rule: cannot go {decision} against a {four_hour_trend} 4h trend"
        next_eligible = "If the 4h trend turns or goes neutral"
    elif signal in ("LONG", "SHORT"):
        entry_status = "READY"
        entry_reason = (
            f"Range setup: {range_setup['reason']}" if (range_setup and not trend_entry_ok)
            else f"{dir_eval['decisionReason']} — 4h trend not opposed"
        )
        next_eligible = "Next completed 1h candle" if not is_new_candle else "Now"
    else:
        entry_status = "WAIT"
        if mode == "RANGE":
            entry_reason = (
                f"Ranging market — waiting for a band-edge setup "
                f"(score {score}/{OPP_MAX_SCORE} below {OPP_ENTRY_SCORE}, no mean-reversion trigger)"
            )
        else:
            entry_reason = f"Score {score}/{OPP_MAX_SCORE} — need ≥ {OPP_ENTRY_SCORE} to enter"
        next_eligible = "Next completed 1h candle"

    opportunity: dict[str, Any] = {
        "score":        score,
        "maxScore":     OPP_MAX_SCORE,
        "mode":         mode,
        "entryStatus":  entry_status,
        "reason":       entry_reason,
        "nextEligible": next_eligible,
        "lastTradeAt":  None,  # filled in by build_coin_state from trade history
    }

    # --- Rich STRATEGY_EVALUATED diagnostic log ---
    _cond_list = cond_eval.get("conditions", [])
    _failed = [cd["name"] for cd in _cond_list if not cd["pass"]]
    if signal != "NO_TRADE":
        _no_trade_reason: str | None = None
    elif cond_eval.get("bias") == "NEUTRAL":
        _no_trade_reason = f"No directional trend and no range setup (score {score}/{OPP_MAX_SCORE})"
    elif _failed:
        _no_trade_reason = f"Score {score}/{OPP_MAX_SCORE} — failed: " + ", ".join(_failed)
    else:
        _no_trade_reason = entry_reason if entry_status != "READY" else None
    _diag: dict[str, Any] = {
        "price":            round_price(current_price),
        "signal":           signal,
        "bias":             cond_eval.get("bias", "NEUTRAL"),
        "oneHourTrend":     one_hour_trend,
        "fourHourTrend":    four_hour_trend,
        "passCount":        cond_eval.get("passCount", 0),
        "totalCount":       cond_eval.get("totalCount", 6),
        "score":            score,
        "maxScore":         OPP_MAX_SCORE,
        "mode":             mode,
        "conditions":       _cond_list,
        "noTradeReason":    _no_trade_reason,
        "executionBlocked": execution_block_reason is not None,
        "blockReason":      execution_block_reason,
        "directional":      _directional_diag_block(dir_eval),
    }
    add_activity(connection, coin, "STRATEGY_EVALUATED", json.dumps(_diag))

    snapshot: dict[str, Any] = {
        "currentPrice":           round_price(current_price),
        "updatedAt":              now_iso(),
        "lastCompletedCandleAt":  completed_candle_at,
        "signal":                 signal,
        "oneHourTrend":           one_hour_trend,
        "fourHourTrend":          four_hour_trend,
        "indicators":             indicators,
        "strategyConditions":     cond_eval,
        "proposedTrade":          prop_trade,
        "directional":            _directional_snapshot_block(dir_eval),
        "executionDiagnostics":   build_execution_diagnostics(
            dir_eval=dir_eval,
            open_position=open_position or opened_position,
            is_new_candle=is_new_candle,
            armed=armed,
            risk_paused=(status == "RISK_PAUSED"),
            danger_reason=opp.get("dangerReason") if mode == "DANGER" else None,
            portfolio_block=execution_block_reason,
            completed_candle_at=completed_candle_at,
            volume=indicators.get("volume"),
            signal=signal,
            counter_trend_block=(
                f"Hard rule: cannot go {decision} against a {four_hour_trend} 4h trend"
                if (decision in ("LONG", "SHORT") and not trend_entry_ok and mode == "TREND")
                else None
            ),
        ),
        "opportunity":            opportunity,
        "botStatus":              status,
    }
    connection.execute(
        """
        UPDATE coin_state
        SET last_candle_at = ?, snapshot = ?, updated_at = ?, message = ?
        WHERE coin = ?
        """,
        (
            completed_candle_at,
            json.dumps(snapshot),
            now_iso(),
            "Watching completed 1h candles. No real orders are sent.",
            coin,
        ),
    )
    connection.commit()

    # ── ACTIVE strategy scan (parallel 15m strategy, own position slot) ────
    try:
        refresh_active(connection, coin, current_price, one_hour_trend, spread_pct)
    except Exception as error:  # never let ACTIVE break the CORE scan
        add_activity(connection, coin, "API_ERROR", f"ACTIVE scan failed: {error}")
        connection.commit()

    return build_coin_state(connection, coin, snapshot, status=status)


# ---------------------------------------------------------------------------
# Core refresh — one metal (PAPER TRADING — unvalidated strategy, simulated only)
# ---------------------------------------------------------------------------

def refresh_metal(connection: sqlite3.Connection, metal: str) -> dict[str, Any]:
    meta = METALS[metal]
    warning_note = (
        "UNVALIDATED STRATEGY — PAPER TRADING ONLY. Metals strategy has not "
        "passed validated backtesting; all trades are simulated."
    )

    # --- Spot price (gold-api.com, true spot, USD) ---
    try:
        spot_price, spot_updated = fetch_metal_spot(meta["spot_symbol"])
        add_activity(
            connection, metal, "MARKET_DATA_UPDATED",
            f"Spot ${spot_price:,.2f} ({meta['display']} spot, gold-api.com)",
        )
    except Exception as error:
        add_activity(connection, metal, "API_ERROR", str(error))
        # Null out stale price in the stored snapshot so the dashboard shows
        # API_ERROR honestly rather than displaying a stale price.
        state_row = load_coin_state(connection, metal)
        stored_snap = json.loads(state_row["snapshot"]) if state_row["snapshot"] else {}
        error_msg = f"Spot price feed unavailable: {error}"
        if stored_snap:
            stored_snap["currentPrice"] = None
            stored_snap["botStatus"] = "API_ERROR"
        else:
            # No prior snapshot — write a minimal one so multi_state reads
            # know this coin is in API_ERROR, not WAITING_FOR_DATA.
            stored_snap = {"currentPrice": None, "botStatus": "API_ERROR"}
        # Persist both the cleared snapshot AND the error message so that
        # subsequent multi_state / build_coin_state reads remain honest.
        connection.execute(
            "UPDATE coin_state SET snapshot = ?, message = ?, updated_at = ? WHERE coin = ?",
            (json.dumps(stored_snap), error_msg, now_iso(), metal),
        )
        connection.commit()
        return build_coin_state(connection, metal, message=error_msg, status="API_ERROR")

    # --- Strategy scan on futures candles (research visibility only) ---
    cond_eval: dict[str, Any] | None = None
    dir_eval: dict[str, Any] | None = None
    completed_candle_at: str | None = None
    scan_note: str | None = None
    try:
        one_hour, four_hour = fetch_metal_candles(meta["candles_symbol"])
        if len(one_hour) < 55 or len(four_hour) < 55:
            scan_note = (
                f"Waiting for enough candles ({len(one_hour)}/55 1h, {len(four_hour)}/55 4h)."
            )
        else:
            # Both metals: independent LONG and SHORT setups, each gated by its
            # own configured threshold (GOLD 5/6 backtest-validated; SILVER
            # strict 6/6 until backtest evidence supports loosening).
            _th = DIRECTIONAL_THRESHOLDS[metal]
            dir_eval = evaluate_conditions_directional(
                one_hour, four_hour,
                threshold=_th["long"], short_threshold=_th["short"],
            )
            decision = dir_eval["decision"]
            long_score, short_score = dir_eval["long"]["passCount"], dir_eval["short"]["passCount"]
            if decision in ("LONG", "SHORT"):
                lead, bias = decision.lower(), decision
            elif long_score == short_score:
                lead, bias = "long", "NEUTRAL"
            else:
                lead = "long" if long_score > short_score else "short"
                bias = lead.upper()
            cond_eval = {
                "conditions":    dir_eval[lead]["conditions"],
                "passCount":     dir_eval[lead]["passCount"],
                "totalCount":    6,
                "bias":          bias,
                "signal":        decision,
                "oneHourTrend":  dir_eval["oneHourTrend"],
                "fourHourTrend": dir_eval["fourHourTrend"],
                "indicators":    dir_eval["indicators"],
            }
            completed_candle_at = datetime.fromtimestamp(
                float(one_hour[-1][0]), timezone.utc
            ).isoformat()
    except Exception as error:
        scan_note = f"Scan data unavailable (Yahoo Finance): {error}"

    # Metals entry gate: any 5/6 conditions per direction for BOTH metals —
    # chosen by user after 365-day backtest review (Aug 2026):
    #   any-5/6 → +26.6% ROI, Sharpe 1.73; strict 6/6 → +12.0% ROI, Sharpe 1.01
    # (thresholds live in DIRECTIONAL_THRESHOLDS / GOLD_MIN_PASS / SILVER_MIN_PASS)
    if cond_eval is not None:
        signal          = cond_eval["signal"]      # per-metal directional gate
        one_hour_trend  = cond_eval["oneHourTrend"]
        four_hour_trend = cond_eval["fourHourTrend"]
        indicators      = cond_eval["indicators"]
    else:
        signal, one_hour_trend, four_hour_trend = "NO_TRADE", "NEUTRAL", "NEUTRAL"
        indicators = {
            "rsi": None, "macd": None, "macdSignal": None,
            "atr": None, "ema20": None, "ema50": None, "volume": None,
        }

    # Weighted score for the dashboard opportunity panel (display only —
    # metals ENTRY gate stays the strict 6/6 signal above)
    if cond_eval is not None:
        metal_score = sum(
            w for w, cd in zip(OPP_WEIGHTS, cond_eval.get("conditions", [])) if cd["pass"]
        )
        metal_mode = "RANGE" if (one_hour_trend == "NEUTRAL" and four_hour_trend == "NEUTRAL") else "TREND"
    else:
        metal_score, metal_mode = 0, "TREND"

    # --- Account state / day rollover ---
    state = load_coin_state(connection, metal)
    if state["day_key"] != date_key():
        connection.execute(
            "UPDATE coin_state SET day_key = ?, updated_at = ? WHERE coin = ?",
            (date_key(), now_iso(), metal),
        )
        connection.commit()
        state = load_coin_state(connection, metal)

    open_position = json.loads(state["open_position"]) if state["open_position"] else None
    try:
        reentry = json.loads(state["reentry"]) if state["reentry"] else {"armed": True, "lastDirection": None}
    except (TypeError, ValueError, KeyError):
        reentry = {"armed": True, "lastDirection": None}

    # --- Check stop/target on existing position (uses live spot price) ---
    if open_position:
        hit_stop = (
            spot_price <= open_position["stopLoss"]
            if open_position["direction"] == "LONG"
            else spot_price >= open_position["stopLoss"]
        )
        hit_target = (
            spot_price >= open_position["takeProfit"]
            if open_position["direction"] == "LONG"
            else spot_price <= open_position["takeProfit"]
        )
        if hit_stop or hit_target:
            exit_price = open_position["stopLoss"] if hit_stop else open_position["takeProfit"]
            close_position(
                connection, metal, state, open_position, exit_price,
                "STOP_LOSS" if hit_stop else "TAKE_PROFIT",
            )
            state = load_coin_state(connection, metal)
            try:
                reentry = json.loads(state["reentry"]) if state["reentry"] else {"armed": False, "lastDirection": open_position["direction"]}
            except (TypeError, ValueError, KeyError):
                reentry = {"armed": False, "lastDirection": open_position["direction"]}
            open_position = None

    # --- Entry evaluation on a NEW completed scan candle only ---
    is_new_candle = (
        completed_candle_at is not None
        and completed_candle_at != state["last_candle_at"]
    )
    execution_block_reason: str | None = None
    opened_this_cycle = False
    opened_position: dict[str, Any] | None = None

    # Re-arm re-entry protection: needs a new candle AND the signal to change
    candidate_signal = signal if signal in ("LONG", "SHORT") else "NONE"
    if is_new_candle and not reentry.get("armed", True):
        if candidate_signal != reentry.get("lastDirection"):
            reentry = {"armed": True, "lastDirection": reentry.get("lastDirection")}
            connection.execute(
                "UPDATE coin_state SET reentry = ?, updated_at = ? WHERE coin = ?",
                (json.dumps(reentry), now_iso(), metal),
            )
    armed = bool(reentry.get("armed", True))

    # --- Track the opposite direction while a position is open (log only) ---
    if is_new_candle and open_position is not None and dir_eval is not None:
        _mth = DIRECTIONAL_THRESHOLDS[metal]
        _opp_dir  = "SHORT" if open_position["direction"] == "LONG" else "LONG"
        _opp_gate = _mth["short"] if _opp_dir == "SHORT" else _mth["long"]
        _opp_score = dir_eval[_opp_dir.lower()]["score"]
        if _opp_score >= _opp_gate:
            add_activity(
                connection, metal, "OPPOSITE_SIGNAL",
                f"Strong opposite signal detected: {_opp_dir} {_opp_score}/6 "
                f"while {open_position['direction']} position open "
                f"(LONG {dir_eval['long']['score']}/6, SHORT {dir_eval['short']['score']}/6) — "
                f"not auto-reversing (no validated reversal rule yet)",
            )

    if is_new_candle and open_position is None and cond_eval is not None:
        m = coin_metrics(connection, metal, state)
        daily_limit = float(state["starting_balance"]) * DAILY_LOSS_LIMIT
        # Streak pause expires at the next UTC day boundary (same rule as crypto).
        streak_paused = (
            m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
            and m.get("streakBlockDay") == date_key()
        )
        risk_paused = (
            m["dailyLoss"] >= daily_limit
            or streak_paused
            or float(state["balance"]) <= 0
        )
        if risk_paused and signal in ("LONG", "SHORT"):
            if float(state["balance"]) <= 0:
                execution_block_reason = "Account balance exhausted"
            elif m["dailyLoss"] >= daily_limit:
                execution_block_reason = f"Daily loss limit reached (£{m['dailyLoss']:.2f} / £{daily_limit:.2f})"
            else:
                execution_block_reason = f"Max consecutive losses reached ({int(m['consecutiveLosses'])})"
            add_activity(connection, metal, "RISK_LIMIT_REACHED", execution_block_reason)
        elif signal in ("LONG", "SHORT") and not armed:
            execution_block_reason = (
                "Re-entry protection: same setup already traded — waiting for a "
                "changed signal on a new candle"
            )

        if (
            not risk_paused
            and armed
            and signal in ("LONG", "SHORT")
            and indicators.get("atr") is not None
            and indicators["atr"] > 0
        ):
            _assert_paper_only()   # simulated open only — no real order can be sent
            atr_val     = float(indicators["atr"])
            risk_amount = float(state["balance"]) * RISK_PER_TRADE   # 1% max risk
            stop_dist   = atr_val * ATR_MULTIPLIER
            stop_loss   = spot_price - stop_dist if signal == "LONG" else spot_price + stop_dist
            take_profit = (
                spot_price + stop_dist * REWARD_TO_RISK if signal == "LONG"
                else spot_price - stop_dist * REWARD_TO_RISK
            )
            quantity = min(
                risk_amount / stop_dist if stop_dist > 0 else 0,
                float(state["balance"]) / spot_price if spot_price > 0 else 0,
            )
            risk_block = _portfolio_risk_block_reason(connection, risk_amount) if quantity > 0 else None
            if risk_block:
                execution_block_reason = "Entry blocked by portfolio risk limit."
                add_activity(
                    connection, metal, "ENTRY_BLOCKED",
                    f"{signal} qualifies (LONG {dir_eval['long']['score'] if dir_eval else '?'}/6, "
                    f"SHORT {dir_eval['short']['score'] if dir_eval else '?'}/6) but was skipped — {risk_block}",
                )
            elif quantity > 0:
                _conds = {cd["name"]: cd["pass"] for cd in cond_eval.get("conditions", [])}
                _mth = DIRECTIONAL_THRESHOLDS[metal]
                position = {
                    "direction":  signal,
                    "entry":      round_price(spot_price),
                    "stopLoss":   round_price(stop_loss),
                    "takeProfit": round_price(take_profit),
                    "quantity":   round_amount(quantity),
                    "riskAmount": round(risk_amount, 2),
                    "openedAt":   now_iso(),
                    "entryRsi":   indicators.get("rsi"),
                    "entryMacd":  indicators.get("macd"),
                    "entryAtr":   indicators.get("atr"),
                    "trend4h":    four_hour_trend,
                    "trend1h":    one_hour_trend,
                    "entryScore": metal_score,
                    "entryMode":  f"{metal}_{_mth['long'] if signal == 'LONG' else _mth['short']}OF6_DIRECTIONAL",
                    "longScore":  dir_eval["long"]["passCount"] if dir_eval else None,
                    "shortScore": dir_eval["short"]["passCount"] if dir_eval else None,
                    "entryThreshold": _mth["long"] if signal == "LONG" else _mth["short"],
                    "passCount":  cond_eval.get("passCount", 0),
                    "totalCount": cond_eval.get("totalCount", 6),
                    "maCondition":     bool(_conds.get("Price vs MA")),
                    "volumeCondition": bool(_conds.get("Volume")),
                    "entryConditions": ", ".join(
                        cd["name"] for cd in cond_eval.get("conditions", []) if cd["pass"]
                    ),
                    "unvalidatedStrategy": True,
                }
                connection.execute(
                    "UPDATE coin_state SET open_position = ?, updated_at = ? WHERE coin = ?",
                    (json.dumps(position), now_iso(), metal),
                )
                _dir_note = (
                    f" | LONG {dir_eval['long']['passCount']}/6 vs SHORT {dir_eval['short']['passCount']}/6"
                    if dir_eval else ""
                )
                add_activity(
                    connection, metal, "TRADE_OPENED",
                    f"{signal} PAPER trade opened at ${spot_price:,.2f} (UNVALIDATED STRATEGY) "
                    f"| {cond_eval.get('passCount', 0)}/6 conditions{_dir_note} | score {metal_score}/{OPP_MAX_SCORE} "
                    f"| entry ${spot_price:,.2f} | SL ${stop_loss:,.2f} | TP ${take_profit:,.2f} "
                    f"| risk ${risk_amount:.2f} | ATR {atr_val:.2f} "
                    f"| conditions: {position['entryConditions'] or 'none'}",
                )
                opened_this_cycle = True
                opened_position = position
                open_position = position

    if cond_eval is not None:
        cond_eval = dict(cond_eval)
        cond_eval["signal"] = signal
        cond_eval["decision"] = signal

    # --- Status / opportunity panel ---
    current_m = coin_metrics(connection, metal, load_coin_state(connection, metal))
    status = "READY"
    _streak_paused_now = (
        current_m["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
        and current_m.get("streakBlockDay") == date_key()
    )
    if current_m["dailyLoss"] >= float(state["starting_balance"]) * DAILY_LOSS_LIMIT or \
       _streak_paused_now:
        status = "RISK_PAUSED"

    if open_position or opened_this_cycle:
        entry_status, entry_reason = "BLOCKED", "Position open — waiting for stop or target"
        next_eligible = "After the open position closes"
    elif status == "RISK_PAUSED":
        entry_status, entry_reason = "BLOCKED", execution_block_reason or "Risk limit reached"
        next_eligible = "After risk limits reset (next day or streak break)"
    elif execution_block_reason is not None:
        entry_status, entry_reason = "BLOCKED", execution_block_reason
        next_eligible = "When portfolio open risk drops below the ceiling"
    elif signal in ("LONG", "SHORT") and not armed:
        entry_status = "BLOCKED"
        entry_reason = "Re-entry protection: same setup already traded — needs a changed signal on a new candle"
        next_eligible = "After the signal resets on a new candle"
    elif signal in ("LONG", "SHORT"):
        entry_status = "READY"
        if dir_eval is not None:
            entry_reason = f"{dir_eval['decisionReason']} ({warning_note})"
        else:
            entry_reason = f"6/6 entry conditions met ({warning_note})"
        next_eligible = "Next completed 1h candle" if not is_new_candle else "Now"
    else:
        entry_status = "WAIT"
        if cond_eval is None and scan_note:
            entry_reason = scan_note
        elif dir_eval is not None:
            entry_reason = dir_eval["decisionReason"]
        else:
            entry_reason = f"{cond_eval.get('passCount', 0) if cond_eval else 0}/6 conditions — need all 6 to enter"
        next_eligible = "Next completed 1h candle"

    metal_opportunity: dict[str, Any] = {
        "score":        metal_score,
        "maxScore":     OPP_MAX_SCORE,
        "mode":         metal_mode,
        "entryStatus":  entry_status,
        "reason":       entry_reason,
        "nextEligible": next_eligible,
        "lastTradeAt":  None,
    }

    # --- Diagnostic log ---
    _cond_list = (cond_eval or {}).get("conditions", [])
    _failed = [cd["name"] for cd in _cond_list if not cd["pass"]]
    if signal != "NO_TRADE":
        _no_trade_reason: str | None = None
    elif cond_eval is None:
        _no_trade_reason = scan_note or "Scan data unavailable"
    elif dir_eval is not None:
        _no_trade_reason = dir_eval["decisionReason"]
    elif cond_eval.get("bias") == "NEUTRAL":
        _no_trade_reason = "No directional trend on 1h or 4h timeframe"
    elif _failed:
        _no_trade_reason = "Failed: " + ", ".join(_failed)
    else:
        _no_trade_reason = entry_reason if entry_status != "READY" else None
    _diag: dict[str, Any] = {
        "price":            round_price(spot_price),
        "signal":           signal,
        "bias":             (cond_eval or {}).get("bias", "NEUTRAL"),
        "oneHourTrend":     one_hour_trend,
        "fourHourTrend":    four_hour_trend,
        "passCount":        (cond_eval or {}).get("passCount", 0),
        "totalCount":       (cond_eval or {}).get("totalCount", 6),
        "score":            metal_score,
        "maxScore":         OPP_MAX_SCORE,
        "conditions":       _cond_list,
        "noTradeReason":    _no_trade_reason,
        "executionBlocked": execution_block_reason is not None,
        "blockReason":      execution_block_reason,
        "paperTradingOnly": True,
    }
    if dir_eval is not None:
        _diag["directional"] = _directional_diag_block(dir_eval)
    add_activity(connection, metal, "STRATEGY_EVALUATED", json.dumps(_diag))

    message = warning_note if scan_note is None else f"{warning_note} {scan_note}"
    snapshot: dict[str, Any] = {
        "currentPrice":          round_price(spot_price),
        "updatedAt":             now_iso(),
        "lastCompletedCandleAt": completed_candle_at,
        "signal":                signal,
        "oneHourTrend":          one_hour_trend,
        "fourHourTrend":         four_hour_trend,
        "indicators":            indicators,
        "strategyConditions":    cond_eval,
        "proposedTrade":         None,
        "directional":           (
            _directional_snapshot_block(dir_eval) if dir_eval is not None else None
        ),
        "executionDiagnostics":  build_execution_diagnostics(
            dir_eval=dir_eval,
            open_position=open_position or opened_position,
            is_new_candle=is_new_candle,
            armed=armed,
            risk_paused=(status == "RISK_PAUSED"),
            danger_reason=None,
            portfolio_block=execution_block_reason,
            completed_candle_at=completed_candle_at,
            data_error=scan_note,
            volume=(indicators or {}).get("volume") if indicators else None,
            signal=signal,
        ),
        "opportunity":           metal_opportunity,
        "botStatus":             status,
        # Surface Yahoo Finance unavailability honestly (distinct from gold-api.com errors)
        "scanNote":              scan_note,
    }
    connection.execute(
        """
        UPDATE coin_state
        SET last_candle_at = COALESCE(?, last_candle_at), snapshot = ?, updated_at = ?, message = ?
        WHERE coin = ?
        """,
        (completed_candle_at, json.dumps(snapshot), now_iso(), message, metal),
    )
    connection.commit()

    # ── ACTIVE strategy scan (parallel 15m strategy, own position slot) ────
    try:
        refresh_active(connection, metal, spot_price, one_hour_trend, None)
    except Exception as error:  # never let ACTIVE break the CORE scan
        add_activity(connection, metal, "API_ERROR", f"ACTIVE scan failed: {error}")
        connection.commit()

    return build_coin_state(connection, metal, snapshot, status=status)


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

def _priority_order(connection: sqlite3.Connection) -> list[str]:
    """Objective refresh order when several assets could signal at once.

    Assets whose last-known directional score is closest to (or beyond) its
    entry gate are refreshed first, so if the portfolio risk ceiling can only
    accommodate some entries this cycle, the strongest signals get first
    claim on the capacity (never random). Ties fall back to the fixed
    INSTRUMENTS order. Uses the previous scan's snapshot (one-cycle lag);
    skipped eligible entries are always logged with their scores.
    """
    def strength(sym: str) -> float:
        try:
            st = load_coin_state(connection, sym)
            snap = json.loads(st["snapshot"]) if st["snapshot"] else {}
            d = snap.get("directional") or {}
            max_score = d.get("maxScore") or 1
            gate = min(d.get("threshold") or max_score, d.get("shortThreshold") or max_score)
            best = max(d.get("longScore") or 0, d.get("shortScore") or 0)
            return best / gate if gate else 0.0
        except (ValueError, TypeError, KeyError):
            return 0.0
    order = list(INSTRUMENTS)   # INSTRUMENTS is a dict — fixed fallback order is key order
    return sorted(order, key=lambda s: (-strength(s), order.index(s)))


def multi_refresh() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        result: dict[str, Any] = {}
        for symbol in _priority_order(connection):
            if symbol in METALS:
                result[symbol] = refresh_metal(connection, symbol)
            else:
                result[symbol] = refresh_coin(connection, symbol)
        return {symbol: result[symbol] for symbol in INSTRUMENTS}
    finally:
        connection.close()


def multi_state() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        result: dict[str, Any] = {}
        for symbol in INSTRUMENTS:
            result[symbol] = build_coin_state(connection, symbol)
        return result
    finally:
        connection.close()


def _strategy_stats_block(trades: list[sqlite3.Row]) -> dict[str, Any]:
    """Stats for one strategy bucket: trades, wins, losses, win rate, P&L,
    ROI (vs risked capital is ambiguous → ROI reported vs total starting
    balances), profit factor and max drawdown of the cumulative P&L curve."""
    n = len(trades)
    wins = sum(1 for t in trades if (t["profit_loss"] or 0) > 0)
    losses = n - wins
    pnl = sum(float(t["profit_loss"] or 0) for t in trades)
    gross_win  = sum(float(t["profit_loss"]) for t in trades if (t["profit_loss"] or 0) > 0)
    gross_loss = -sum(float(t["profit_loss"]) for t in trades if (t["profit_loss"] or 0) < 0)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    # Max drawdown on the cumulative P&L curve, oldest → newest
    cum = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda r: str(r["closed_at"] or "")):
        cum += float(t["profit_loss"] or 0)
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "trades":   n,
        "wins":     wins,
        "losses":   losses,
        "winRate":  round(wins / n * 100, 2) if n else 0.0,
        "pnl":      round(pnl, 2),
        "profitFactor": (round(profit_factor, 3) if isinstance(profit_factor, float) and profit_factor != float("inf") else None),
        "maxDrawdown":  round(max_dd, 2),
    }


def strategy_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    """CORE / ACTIVE / COMBINED performance stats from the trades table."""
    rows = connection.execute(
        "SELECT profit_loss, closed_at, strategy FROM trades"
    ).fetchall()
    core   = [r for r in rows if (r["strategy"] or "CORE") == "CORE"]
    active = [r for r in rows if (r["strategy"] or "CORE") == "ACTIVE"]
    total_starting = sum(
        float(load_coin_state(connection, c)["starting_balance"]) for c in INSTRUMENTS
    )
    out: dict[str, Any] = {}
    for key, bucket in (("core", core), ("active", active), ("combined", rows)):
        block = _strategy_stats_block(bucket)
        block["roi"] = round(block["pnl"] / total_starting * 100, 3) if total_starting else 0.0
        out[key] = block
    return out


def portfolio_summary() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        coins_summary: dict[str, Any] = {}
        total_starting = 0.0
        total_balance  = 0.0
        total_trades   = 0
        total_wins     = 0
        for coin in INSTRUMENTS:
            state = load_coin_state(connection, coin)
            m = coin_metrics(connection, coin, state)
            coins_summary[coin] = m
            total_starting += m["startingBalance"]
            total_balance  += m["virtualBalance"]
            total_trades   += int(m["numberOfTrades"])
            total_wins     += int(m["wins"])
        total_pnl  = total_balance - total_starting
        total_roi  = (total_pnl / total_starting * 100) if total_starting else 0.0
        total_losses = total_trades - total_wins
        win_rate = (total_wins / total_trades * 100) if total_trades else 0.0
        open_risk = portfolio_open_risk(connection)
        return {
            "openPositions":   open_risk["openPositions"],
            "totalInstruments": open_risk["totalInstruments"],
            "totalOpenRisk":   open_risk["totalOpenRisk"],
            "openRiskPercent": open_risk["openRiskPercent"],
            "riskCeilingPercent": open_risk["ceilingPercent"],
            "totalStarting":  total_starting,
            "totalBalance":   total_balance,
            "totalPnl":       total_pnl,
            "totalRoi":       total_roi,
            "totalTrades":    total_trades,
            "totalWins":      total_wins,
            "totalLosses":    total_losses,
            "overallWinRate": win_rate,
            "coins":          coins_summary,
            "strategyStats":  strategy_stats(connection),
        }
    finally:
        connection.close()


def coin_trades(coin: str, limit: int = 100) -> list[dict[str, Any]]:
    connection = db()
    init_db(connection)
    try:
        bounded = max(1, min(200, int(limit)))
        rows = connection.execute(
            "SELECT * FROM trades WHERE coin = ? ORDER BY id DESC LIMIT ?",
            (coin, bounded),
        ).fetchall()
        return [trade_from_row(r) for r in rows]
    finally:
        connection.close()


def all_trades(limit: int = 200) -> list[dict[str, Any]]:
    connection = db()
    init_db(connection)
    try:
        bounded = max(1, min(500, int(limit)))
        rows = connection.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (bounded,)
        ).fetchall()
        return [trade_from_row(r) for r in rows]
    finally:
        connection.close()


def pnl_series() -> dict[str, Any]:
    """Return cumulative P&L time-series per coin plus a combined crypto series."""
    connection = db()
    init_db(connection)
    try:
        # Oldest-first so we can accumulate correctly
        rows = connection.execute(
            "SELECT coin, closed_at, profit_loss, account_balance FROM trades ORDER BY closed_at ASC, id ASC"
        ).fetchall()

        # Starting balances per coin
        states = connection.execute(
            "SELECT coin, starting_balance FROM coin_state"
        ).fetchall()
        starting: dict[str, float] = {r["coin"]: float(r["starting_balance"]) for r in states}

        # Per-coin series
        series: dict[str, list[dict[str, Any]]] = {}
        cum_pnl: dict[str, float] = {}

        for row in rows:
            coin = row["coin"]
            if coin not in series:
                series[coin] = []
                cum_pnl[coin] = 0.0
            cum_pnl[coin] += float(row["profit_loss"])
            start = starting.get(coin, 100.0)
            series[coin].append({
                "ts": row["closed_at"],
                "cumulativePnl": round(cum_pnl[coin], 4),
                "cumulativePnlPct": round(cum_pnl[coin] / start * 100, 4) if start else 0.0,
                "balance": round(float(row["account_balance"]), 4),
            })

        # Crypto aggregate (£ denominated: BTC, ETH, SOL, XRP)
        crypto_coins = {"BTC", "ETH", "SOL", "XRP"}
        crypto_rows = [r for r in rows if r["coin"] in crypto_coins]
        crypto_start = sum(starting.get(c, 100.0) for c in crypto_coins if c in starting)
        if not crypto_start:
            crypto_start = len(crypto_coins) * 100.0
        cum_crypto = 0.0
        crypto_series: list[dict[str, Any]] = []
        for row in crypto_rows:
            cum_crypto += float(row["profit_loss"])
            crypto_series.append({
                "ts": row["closed_at"],
                "cumulativePnl": round(cum_crypto, 4),
                "cumulativePnlPct": round(cum_crypto / crypto_start * 100, 4) if crypto_start else 0.0,
            })
        series["CRYPTO"] = crypto_series

        starting_with_crypto = dict(starting)
        starting_with_crypto["CRYPTO"] = crypto_start

        return {
            "series": series,
            "startingBalances": starting_with_crypto,
        }
    finally:
        connection.close()


def activity_log(limit: int = 50) -> list[dict[str, Any]]:
    connection = db()
    init_db(connection)
    try:
        bounded = max(1, min(200, int(limit)))
        rows = connection.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (bounded,)
        ).fetchall()
        return [
            {"id": int(r["id"]), "coin": r["coin"], "event": r["event"],
             "message": r["message"], "ts": r["ts"]}
            for r in rows
        ]
    finally:
        connection.close()


CHART_RANGES: dict[str, int] = {
    "24H": 24 * 3600,
    "7D": 7 * 86400,
    "30D": 30 * 86400,
    "90D": 90 * 86400,
}


def _macd_series(closes: list[float]) -> tuple[list[float | None], list[float | None]]:
    """MACD(12,26,9) line + signal series, aligned with closes."""
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd: list[float | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(ema12, ema26)
    ]
    macd_vals = [m for m in macd if m is not None]
    signal_tail = ema_series(macd_vals, 9)
    signal: list[float | None] = [None] * len(macd)
    offset = len(macd) - len(macd_vals)
    for i, s in enumerate(signal_tail):
        signal[offset + i] = s
    return macd, signal


CHART_INTERVALS: dict[str, int] = {"15m": 900, "1h": 3600, "4h": 14400}


def _default_interval(range_key: str) -> str:
    return {"24H": "15m", "7D": "1h", "30D": "1h", "90D": "4h"}[range_key]


def _fetch_chart_rows(asset: str, range_key: str, interval_key: str) -> tuple[list[list[Any]], int]:
    """Completed candles covering the range plus indicator warm-up.

    Returns (rows, interval_seconds). Uses the SAME data sources as the
    strategy engine: Kraken OHLC for crypto, Yahoo Finance COMEX futures
    for metals. Kraken history caps apply (15m ≈ 7.5 days, 1h ≈ 30 days) —
    the chart simply shows what the exchange provides for that timeframe.
    """
    if asset in COINS:
        pair = COINS[asset]["pair"]
        interval = CHART_INTERVALS[interval_key] // 60
        result = fetch_json(
            f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
        )
        cutoff = time.time()
        rows = result.get(pair) or next(
            (v for k, v in result.items() if k != "last"), []
        )
        rows = [
            [float(r[0])] + [safe_float(x) for x in r[1:7]] + [int(r[7])]
            for r in rows
            if len(r) >= 8 and float(r[0]) < cutoff
        ][:-1]
        return rows, interval * 60
    if asset in METALS:
        symbol = METALS[asset]["candles_symbol"]
        # extra days beyond the window cover EMA50/MACD warm-up.
        # Yahoo limits 15m data to ~60 days; 4h is aggregated locally from 1h.
        if interval_key == "15m":
            yahoo_range = {"24H": "10d", "7D": "20d", "30D": "59d", "90D": "59d"}[range_key]
            yahoo_interval = "15m"
        else:
            yahoo_range = {"24H": "10d", "7D": "20d", "30D": "60d", "90D": "120d"}[range_key]
            yahoo_interval = "1h"
        last_error: Exception | None = None
        for host in ("query1", "query2"):
            url = (
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}"
                f"?interval={yahoo_interval}&range={yahoo_range}"
            )
            request = Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            })
            try:
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                result = payload["chart"]["result"][0]
                timestamps = result["timestamp"]
                quote = result["indicators"]["quote"][0]
                cutoff = time.time()
                rows = []
                for i, ts in enumerate(timestamps):
                    o = safe_float(quote["open"][i]); h = safe_float(quote["high"][i])
                    l = safe_float(quote["low"][i]);  c = safe_float(quote["close"][i])
                    v = safe_float(quote["volume"][i]) or 0.0
                    if None in (o, h, l, c) or float(ts) >= cutoff:
                        continue
                    rows.append([float(ts), o, h, l, c, c, v, 1])
                rows = rows[:-1] if rows else rows
                if interval_key == "4h":
                    buckets: dict[int, list[list[Any]]] = {}
                    for row in rows:
                        buckets.setdefault(int(row[0]) // 14400, []).append(row)
                    rows = [
                        [float(k * 14400), b[0][1], max(r[2] for r in b),
                         min(r[3] for r in b), b[-1][4], b[-1][4],
                         sum(r[6] for r in b), len(b)]
                        for k, b in sorted(buckets.items())
                    ]
                return rows, CHART_INTERVALS[interval_key]
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError,
                    IndexError, TypeError) as error:
                last_error = error
        raise RuntimeError(f"Yahoo Finance error for {symbol}: {last_error}")
    raise RuntimeError(f"Unknown asset {asset}")


def chart_command(asset: str, range_key: str, interval_key: str | None = None) -> dict[str, Any]:
    """Candles + indicator overlays + recent signal points for the dashboard
    charts. Read-only visualisation: never touches positions or balances.
    Renders fine with zero trades — candles and indicators never depend on
    trade history."""
    asset = asset.upper()
    if asset not in INSTRUMENTS:
        raise RuntimeError(f"Unknown asset {asset}")
    if range_key not in CHART_RANGES:
        raise RuntimeError(f"Unknown range {range_key}")
    if interval_key is None:
        interval_key = _default_interval(range_key)
    if interval_key not in CHART_INTERVALS:
        raise RuntimeError(f"Unknown interval {interval_key}")

    # Live market price for the current-price line (best effort)
    current_price: float | None = None
    try:
        if asset in COINS:
            pair = COINS[asset]["pair"]
            tr = fetch_json(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
            tk = tr.get(pair) or next(iter(tr.values()), None)
            if tk and tk.get("c"):
                current_price = safe_float(tk["c"][0])
        else:
            current_price, _ = fetch_metal_spot(METALS[asset]["spot_symbol"])
    except Exception:
        current_price = None

    rows, interval_seconds = _fetch_chart_rows(asset, range_key, interval_key)
    closes = [float(r[4]) for r in rows]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    rsi = rsi_series(closes)
    macd, macd_signal = _macd_series(closes)

    window_start = time.time() - CHART_RANGES[range_key]
    candles = []
    for i, r in enumerate(rows):
        if float(r[0]) < window_start:
            continue
        candles.append({
            "t": float(r[0]),
            "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
            "v": float(r[6]),
            "ema20": round_price(ema20[i]),
            "ema50": round_price(ema50[i]),
            "rsi": round(rsi[i], 2) if rsi[i] is not None else None,
            "macd": round(macd[i], 6) if macd[i] is not None else None,
            "macdSignal": round(macd_signal[i], 6) if macd_signal[i] is not None else None,
        })

    # Historical signal points from the activity log (best-effort — the log
    # keeps the most recent ~200 events across all assets).
    signals: list[dict[str, Any]] = []
    # Strictly read-only DB access: no init_db/migration, no writes.
    connection = db()
    try:
        try:
            log_rows = connection.execute(
                "SELECT event, message, ts FROM activity_log "
                "WHERE coin = ? AND event IN ('STRATEGY_EVALUATED', 'ENTRY_BLOCKED') "
                "ORDER BY id DESC LIMIT 200",
                (asset,),
            ).fetchall()
        except sqlite3.OperationalError:
            log_rows = []  # table not created yet — no signal history
    finally:
        connection.close()
    for r in log_rows:
        ts_val = safe_float(
            datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
        ) if r["ts"] else None
        if ts_val is None or ts_val < window_start:
            continue
        if r["event"] == "ENTRY_BLOCKED":
            signals.append({
                "ts": ts_val, "direction": None, "executed": False,
                "blockedReason": r["message"],
            })
            continue
        try:
            diag = json.loads(r["message"])
        except (ValueError, TypeError):
            continue
        signal = diag.get("signal") or "NO_TRADE"
        if signal == "NO_TRADE":
            continue
        blocked = bool(diag.get("executionBlocked"))
        signals.append({
            "ts": ts_val,
            "direction": "SHORT" if "SHORT" in str(signal) else "LONG",
            "executed": not blocked,
            "blockedReason": diag.get("blockReason") or diag.get("noTradeReason"),
        })
    signals.reverse()

    info = instrument_info(asset)
    return {
        "asset": asset,
        "display": instrument_display(asset),
        "currency": info["currency"],
        "range": range_key,
        "interval": interval_key,
        "currentPrice": round_price(current_price) if current_price is not None else None,
        "intervalSeconds": interval_seconds,
        "dataSource": info["dataSource"],
        "candles": candles,
        "signals": signals,
    }


def reset_all_command(payload: str | None = None) -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        starting = STARTING_BALANCE
        if payload:
            decoded = json.loads(payload)
            requested = safe_float(decoded.get("startingBalance"))
            if requested is not None:
                starting = max(0.0, requested)
        for coin in INSTRUMENTS:
            # Metals always reset to the fixed £100 baseline,
            # never a caller-supplied balance.
            reset_coin(connection, coin, STARTING_BALANCE if coin in METALS else starting)
        # return portfolio summary after reset
        coins_summary: dict[str, Any] = {}
        for coin in INSTRUMENTS:
            state = load_coin_state(connection, coin)
            coins_summary[coin] = build_coin_state(connection, coin)
        return coins_summary
    finally:
        connection.close()


def reset_coin_command(coin: str, payload: str | None = None) -> dict[str, Any]:
    if coin not in INSTRUMENTS:
        raise ValueError(f"Unknown coin: {coin}")
    connection = db()
    init_db(connection)
    try:
        starting = STARTING_BALANCE
        if payload and coin not in METALS:
            # Metals reset balance is fixed at the £100 baseline.
            decoded = json.loads(payload)
            requested = safe_float(decoded.get("startingBalance"))
            if requested is not None:
                starting = max(0.0, requested)
        reset_coin(connection, coin, starting)
        return build_coin_state(connection, coin)
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "multi-state"

    if command == "multi-refresh":
        result: Any = multi_refresh()
    elif command == "multi-state":
        result = multi_state()
    elif command == "portfolio":
        result = portfolio_summary()
    elif command == "activity":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        result = activity_log(limit)
    elif command == "coin-trades":
        coin  = sys.argv[2] if len(sys.argv) > 2 else "BTC"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 100
        result = coin_trades(coin, limit)
    elif command == "all-trades":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        result = all_trades(limit)
    elif command == "pnl-series":
        result = pnl_series()
    elif command == "chart":
        asset = sys.argv[2] if len(sys.argv) > 2 else "BTC"
        range_key = sys.argv[3] if len(sys.argv) > 3 else "7D"
        interval_key = sys.argv[4] if len(sys.argv) > 4 else None
        result = chart_command(asset, range_key, interval_key)
    elif command == "reset-all":
        payload = sys.argv[2] if len(sys.argv) > 2 else None
        result = reset_all_command(payload)
    elif command == "reset-coin":
        coin    = sys.argv[2] if len(sys.argv) > 2 else "BTC"
        payload = sys.argv[3] if len(sys.argv) > 3 else None
        result  = reset_coin_command(coin, payload)
    # --- Legacy BTC-only commands (backward compat) ---
    elif command == "refresh":
        connection = db(); init_db(connection)
        try:
            result = refresh_coin(connection, "BTC")
        finally:
            connection.close()
    elif command == "state":
        connection = db(); init_db(connection)
        try:
            result = build_coin_state(connection, "BTC")
        finally:
            connection.close()
    elif command == "trades":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        result = coin_trades("BTC", limit)
    elif command == "reset":
        result = reset_coin_command("BTC", sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        result = multi_state()

    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
