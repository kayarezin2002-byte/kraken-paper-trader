#!/usr/bin/env python3
"""Kraken BTC/GBP paper trader.

This process uses Kraken's public market-data endpoints only. It never
authenticates with Kraken and never submits orders.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PAIR = "XXBTZGBP"
DISPLAY_PAIR = "BTC/GBP"
STARTING_BALANCE = 100.0
RISK_PER_TRADE = 0.01
DAILY_LOSS_LIMIT = 0.03
MAX_CONSECUTIVE_LOSSES = 3
REWARD_TO_RISK = 2.0
ATR_MULTIPLIER = 1.5
POLLING_SECONDS = 60
DB_PATH = os.environ.get(
    "PAPER_TRADER_DB",
    os.path.join(os.path.dirname(__file__), "paper_trader.sqlite3"),
)


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
    return round(value, 2) if value is not None else None


def round_amount(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            starting_balance REAL NOT NULL,
            balance REAL NOT NULL,
            open_position TEXT,
            last_candle_at TEXT,
            day_key TEXT NOT NULL,
            peak_balance REAL NOT NULL,
            updated_at TEXT NOT NULL,
            snapshot TEXT,
            message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry REAL NOT NULL,
            exit REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            rsi REAL,
            macd REAL,
            atr REAL,
            trend_4h TEXT NOT NULL,
            profit_loss REAL NOT NULL,
            account_balance REAL NOT NULL,
            exit_reason TEXT NOT NULL
        );
        """
    )
    row = connection.execute("SELECT id FROM bot_state WHERE id = 1").fetchone()
    if row is None:
        reset_state(connection, STARTING_BALANCE, clear_trades=False)
    connection.commit()


def reset_state(
    connection: sqlite3.Connection,
    starting_balance: float = STARTING_BALANCE,
    clear_trades: bool = True,
) -> None:
    starting_balance = max(0.0, float(starting_balance))
    if clear_trades:
        connection.execute("DELETE FROM trades")
    connection.execute(
        """
        INSERT INTO bot_state (
            id, starting_balance, balance, open_position, last_candle_at,
            day_key, peak_balance, updated_at, snapshot, message
        ) VALUES (1, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
            starting_balance = excluded.starting_balance,
            balance = excluded.balance,
            open_position = NULL,
            last_candle_at = NULL,
            day_key = excluded.day_key,
            peak_balance = excluded.peak_balance,
            updated_at = excluded.updated_at,
            snapshot = NULL,
            message = excluded.message
        """,
        (
            starting_balance,
            starting_balance,
            date_key(),
            starting_balance,
            now_iso(),
            "Paper account reset. Waiting for public Kraken market data.",
        ),
    )
    connection.commit()


def load_state(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
    if row is None:
        reset_state(connection, STARTING_BALANCE, clear_trades=False)
        row = connection.execute("SELECT * FROM bot_state WHERE id = 1").fetchone()
    assert row is not None
    return row


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
        raise RuntimeError(f"Kraken public API request failed: {error}") from error
    errors = payload.get("error", [])
    if errors:
        raise RuntimeError("Kraken public API error: " + ", ".join(map(str, errors)))
    return payload.get("result", {})


def fetch_market_data() -> tuple[float, list[list[Any]], list[list[Any]]]:
    ticker_result = fetch_json(
        f"https://api.kraken.com/0/public/Ticker?pair={PAIR}"
    )
    ticker = ticker_result.get(PAIR) or next(iter(ticker_result.values()), None)
    if not ticker or not ticker.get("c"):
        raise RuntimeError("Kraken returned no BTC/GBP ticker data")
    current_price = safe_float(ticker["c"][0])
    if current_price is None:
        raise RuntimeError("Kraken returned an invalid BTC/GBP price")

    one_hour_result = fetch_json(
        f"https://api.kraken.com/0/public/OHLC?pair={PAIR}&interval=60"
    )
    four_hour_result = fetch_json(
        f"https://api.kraken.com/0/public/OHLC?pair={PAIR}&interval=240"
    )
    one_hour = one_hour_result.get(PAIR) or next(
        (value for key, value in one_hour_result.items() if key != "last"), []
    )
    four_hour = four_hour_result.get(PAIR) or next(
        (value for key, value in four_hour_result.items() if key != "last"), []
    )
    cutoff = time.time()

    def completed(rows: list[list[Any]]) -> list[list[Any]]:
        return [row for row in rows if len(row) >= 8 and float(row[0]) < cutoff][:-1]

    return current_price, completed(one_hour), completed(four_hour)


def ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = [max(values[index] - values[index - 1], 0.0) for index in range(1, len(values))]
    losses = [max(values[index - 1] - values[index], 0.0) for index in range(1, len(values))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def value() -> float:
        if average_loss == 0:
            return 100.0
        relative_strength = average_gain / average_loss
        return 100 - (100 / (1 + relative_strength))

    result[period] = value()
    for index in range(period, len(gains)):
        average_gain = ((average_gain * (period - 1)) + gains[index]) / period
        average_loss = ((average_loss * (period - 1)) + losses[index]) / period
        result[index + 1] = value()
    return result


def indicator_snapshot(rows: list[list[Any]]) -> dict[str, float | None]:
    closes = [float(row[4]) for row in rows]
    volumes = [float(row[6]) for row in rows]
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd = [
        (fast - slow) if fast is not None and slow is not None else None
        for fast, slow in zip(ema12, ema26)
    ]
    macd_values = [value for value in macd if value is not None]
    signal_values = ema_series(macd_values, 9)
    macd_signal: list[float | None] = [None] * len(macd)
    signal_index = 0
    for index, value in enumerate(macd):
        if value is not None:
            macd_signal[index] = signal_values[signal_index]
            signal_index += 1
    true_ranges: list[float] = []
    for index, row in enumerate(rows):
        high = float(row[2])
        low = float(row[3])
        previous_close = float(rows[index - 1][4]) if index else float(row[4])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    atr = ema_series(true_ranges, 14)
    rsi = rsi_series(closes)
    latest = len(rows) - 1

    def latest_value(series: list[float | None]) -> float | None:
        return series[latest] if series and latest >= 0 else None

    return {
        "rsi": latest_value(rsi),
        "macd": latest_value(macd),
        "macdSignal": latest_value(macd_signal),
        "atr": latest_value(atr),
        "ema20": latest_value(ema20),
        "ema50": latest_value(ema50),
        "volume": volumes[-1] if volumes else None,
    }


def trend_for(rows: list[list[Any]]) -> str:
    if len(rows) < 55:
        return "NEUTRAL"
    snapshot = indicator_snapshot(rows)
    close = float(rows[-1][4])
    ema20 = snapshot["ema20"]
    ema50 = snapshot["ema50"]
    macd = snapshot["macd"]
    macd_signal = snapshot["macdSignal"]
    if (
        ema20 is not None
        and ema50 is not None
        and macd is not None
        and macd_signal is not None
    ):
        if close > ema20 > ema50 and macd > macd_signal:
            return "BULLISH"
        if close < ema20 < ema50 and macd < macd_signal:
            return "BEARISH"
    return "NEUTRAL"


def signal_for(rows: list[list[Any]]) -> tuple[str, str, dict[str, float | None]]:
    if len(rows) < 55:
        return "NO_TRADE", "NEUTRAL", indicator_snapshot(rows)
    snapshot = indicator_snapshot(rows)
    close = float(rows[-1][4])
    trend = trend_for(rows)
    average_volume = sum(float(row[6]) for row in rows[-20:]) / min(20, len(rows))
    volume = snapshot["volume"] or 0.0
    bullish = (
        trend == "BULLISH"
        and snapshot["rsi"] is not None
        and snapshot["rsi"] >= 50
        and snapshot["macd"] is not None
        and snapshot["macdSignal"] is not None
        and snapshot["macd"] > snapshot["macdSignal"]
        and volume >= average_volume * 0.7
        and snapshot["ema20"] is not None
        and snapshot["ema50"] is not None
        and close > snapshot["ema20"] > snapshot["ema50"]
    )
    bearish = (
        trend == "BEARISH"
        and snapshot["rsi"] is not None
        and snapshot["rsi"] <= 50
        and snapshot["macd"] is not None
        and snapshot["macdSignal"] is not None
        and snapshot["macd"] < snapshot["macdSignal"]
        and volume >= average_volume * 0.7
        and snapshot["ema20"] is not None
        and snapshot["ema50"] is not None
        and close < snapshot["ema20"] < snapshot["ema50"]
    )
    return ("LONG" if bullish else "SHORT" if bearish else "NO_TRADE"), trend, snapshot


def trade_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "openedAt": row["opened_at"],
        "closedAt": row["closed_at"],
        "direction": row["direction"],
        "entry": row["entry"],
        "exit": row["exit"],
        "stopLoss": row["stop_loss"],
        "takeProfit": row["take_profit"],
        "rsi": row["rsi"],
        "macd": row["macd"],
        "atr": row["atr"],
        "trend4h": row["trend_4h"],
        "profitLoss": row["profit_loss"],
        "accountBalance": row["account_balance"],
        "exitReason": row["exit_reason"],
    }


def metrics(connection: sqlite3.Connection, state: sqlite3.Row) -> dict[str, float]:
    trades = connection.execute(
        "SELECT profit_loss, account_balance, closed_at FROM trades ORDER BY id"
    ).fetchall()
    profits = [float(row["profit_loss"]) for row in trades if float(row["profit_loss"]) > 0]
    losses = [float(row["profit_loss"]) for row in trades if float(row["profit_loss"]) < 0]
    balance = max(0.0, float(state["balance"]))
    starting = float(state["starting_balance"])
    total = balance - starting
    wins = len(profits)
    loss_count = len(losses)
    gross_profit = sum(profits)
    gross_loss = abs(sum(losses))
    equity = [starting] + [float(row["account_balance"]) for row in trades]
    peak = starting
    max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    today = date_key()
    daily_loss = sum(
        abs(float(row["profit_loss"]))
        for row in trades
        if row["closed_at"].startswith(today) and float(row["profit_loss"]) < 0
    )
    consecutive = 0
    for row in reversed(trades):
        if float(row["profit_loss"]) < 0:
            consecutive += 1
        else:
            break
    return {
        "virtualBalance": balance,
        "startingBalance": starting,
        "totalProfitLoss": total,
        "roi": (total / starting * 100) if starting else 0.0,
        "numberOfTrades": len(trades),
        "wins": wins,
        "losses": loss_count,
        "winRate": (wins / len(trades) * 100) if trades else 0.0,
        "profitFactor": (gross_profit / gross_loss) if gross_loss else 0.0,
        "maximumDrawdown": max_drawdown,
        "dailyLoss": daily_loss,
        "consecutiveLosses": consecutive,
    }


def build_state(
    connection: sqlite3.Connection,
    snapshot: dict[str, Any] | None = None,
    message: str | None = None,
    status: str = "WAITING_FOR_DATA",
) -> dict[str, Any]:
    state = load_state(connection)
    stored_snapshot = json.loads(state["snapshot"]) if state["snapshot"] else {}
    data = snapshot or stored_snapshot
    open_position = json.loads(state["open_position"]) if state["open_position"] else None
    latest_trades = connection.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT 50"
    ).fetchall()
    current_message = message or state["message"]
    if data.get("currentPrice") is not None:
        status = data.get("botStatus", status)
    return {
        "market": {
            "pair": DISPLAY_PAIR,
            "currentPrice": data.get("currentPrice"),
            "updatedAt": data.get("updatedAt", state["updated_at"]),
            "lastCompletedCandleAt": data.get("lastCompletedCandleAt"),
        },
        "signal": data.get("signal", "NO_TRADE"),
        "oneHourTrend": data.get("oneHourTrend", "NEUTRAL"),
        "fourHourTrend": data.get("fourHourTrend", "NEUTRAL"),
        "indicators": data.get(
            "indicators",
            {"rsi": None, "macd": None, "macdSignal": None, "atr": None, "ema20": None, "ema50": None, "volume": None},
        ),
        "position": open_position,
        "metrics": metrics(connection, state),
        "risk": {
            "dailyLossLimit": float(state["starting_balance"]) * DAILY_LOSS_LIMIT,
            "maximumConsecutiveLosses": MAX_CONSECUTIVE_LOSSES,
            "riskPerTrade": RISK_PER_TRADE * 100,
            "rewardToRisk": REWARD_TO_RISK,
            "pollingSeconds": POLLING_SECONDS,
        },
        "recentTrades": [trade_from_row(row) for row in latest_trades],
        "botStatus": status,
        "message": current_message,
    }


def close_position(
    connection: sqlite3.Connection,
    state: sqlite3.Row,
    position: dict[str, Any],
    exit_price: float,
    reason: str,
) -> None:
    direction = position["direction"]
    quantity = float(position["quantity"])
    entry = float(position["entry"])
    pnl = (
        (exit_price - entry) * quantity
        if direction == "LONG"
        else (entry - exit_price) * quantity
    )
    balance = max(0.0, float(state["balance"]) + pnl)
    connection.execute(
        """
        INSERT INTO trades (
            opened_at, closed_at, direction, entry, exit, stop_loss, take_profit,
            rsi, macd, atr, trend_4h, profit_loss, account_balance, exit_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position["openedAt"],
            now_iso(),
            direction,
            entry,
            exit_price,
            position["stopLoss"],
            position["takeProfit"],
            position.get("entryRsi"),
            position.get("entryMacd"),
            position.get("entryAtr"),
            position["trend4h"],
            pnl,
            balance,
            reason,
        ),
    )
    connection.execute(
        "UPDATE bot_state SET balance = ?, open_position = NULL, updated_at = ? WHERE id = 1",
        (balance, now_iso()),
    )
    connection.commit()


def refresh() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        current_price, one_hour, four_hour = fetch_market_data()
        if len(one_hour) < 55 or len(four_hour) < 55:
            return build_state(
                connection,
                message="Waiting for enough completed Kraken candles to calculate all indicators.",
                status="WAITING_FOR_DATA",
            )
        one_hour_signal, one_hour_trend, indicators = signal_for(one_hour)
        four_hour_trend = trend_for(four_hour)
        signal = (
            one_hour_signal
            if one_hour_signal == ("LONG" if four_hour_trend == "BULLISH" else "SHORT" if four_hour_trend == "BEARISH" else "")
            else "NO_TRADE"
        )
        completed_candle_at = datetime.fromtimestamp(
            float(one_hour[-1][0]), timezone.utc
        ).isoformat()
        state = load_state(connection)
        if state["day_key"] != date_key():
            connection.execute(
                "UPDATE bot_state SET day_key = ?, updated_at = ? WHERE id = 1",
                (date_key(), now_iso()),
            )
            connection.commit()
            state = load_state(connection)

        open_position = json.loads(state["open_position"]) if state["open_position"] else None
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
                exit_price = (
                    open_position["stopLoss"] if hit_stop else open_position["takeProfit"]
                )
                close_position(
                    connection,
                    state,
                    open_position,
                    exit_price,
                    "STOP_LOSS" if hit_stop else "TAKE_PROFIT",
                )
                state = load_state(connection)
                open_position = None

        is_new_candle = completed_candle_at != state["last_candle_at"]
        if is_new_candle and open_position is None:
            current_metrics = metrics(connection, state)
            daily_limit = float(state["starting_balance"]) * DAILY_LOSS_LIMIT
            risk_paused = (
                current_metrics["dailyLoss"] >= daily_limit
                or current_metrics["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES
                or float(state["balance"]) <= 0
            )
            if (
                not risk_paused
                and signal in ("LONG", "SHORT")
                and signal == ("LONG" if four_hour_trend == "BULLISH" else "SHORT" if four_hour_trend == "BEARISH" else "")
                and indicators["atr"] is not None
                and indicators["atr"] > 0
            ):
                entry = current_price
                stop_distance = float(indicators["atr"]) * ATR_MULTIPLIER
                risk_amount = float(state["balance"]) * RISK_PER_TRADE
                quantity = min(
                    risk_amount / stop_distance,
                    float(state["balance"]) / entry if entry > 0 else 0,
                )
                if quantity > 0 and stop_distance > 0:
                    stop_loss = entry - stop_distance if signal == "LONG" else entry + stop_distance
                    take_profit = (
                        entry + stop_distance * REWARD_TO_RISK
                        if signal == "LONG"
                        else entry - stop_distance * REWARD_TO_RISK
                    )
                    position = {
                        "direction": signal,
                        "entry": round_price(entry),
                        "stopLoss": round_price(stop_loss),
                        "takeProfit": round_price(take_profit),
                        "quantity": round_amount(quantity),
                        "riskAmount": round(risk_amount, 2),
                        "openedAt": now_iso(),
                        "entryRsi": indicators["rsi"],
                        "entryMacd": indicators["macd"],
                        "entryAtr": indicators["atr"],
                        "trend4h": four_hour_trend,
                    }
                    connection.execute(
                        "UPDATE bot_state SET open_position = ?, updated_at = ? WHERE id = 1",
                        (json.dumps(position), now_iso()),
                    )
        current_metrics = metrics(connection, load_state(connection))
        status = "READY"
        if current_metrics["dailyLoss"] >= float(state["starting_balance"]) * DAILY_LOSS_LIMIT or current_metrics["consecutiveLosses"] >= MAX_CONSECUTIVE_LOSSES:
            status = "RISK_PAUSED"
        snapshot = {
            "currentPrice": round_price(current_price),
            "updatedAt": now_iso(),
            "lastCompletedCandleAt": completed_candle_at,
            "signal": signal,
            "oneHourTrend": one_hour_trend,
            "fourHourTrend": four_hour_trend,
            "indicators": indicators,
            "botStatus": status,
        }
        connection.execute(
            "UPDATE bot_state SET last_candle_at = ?, snapshot = ?, updated_at = ?, message = ? WHERE id = 1",
            (
                completed_candle_at,
                json.dumps(snapshot),
                now_iso(),
                "Watching completed 1-hour candles. No real orders are sent.",
            ),
        )
        connection.commit()
        return build_state(connection, snapshot, status=status)
    except Exception as error:
        return build_state(
            connection,
            message=str(error),
            status="API_ERROR",
        )
    finally:
        connection.close()


def state_command() -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        return build_state(connection)
    finally:
        connection.close()


def trades_command(limit: int) -> list[dict[str, Any]]:
    connection = db()
    init_db(connection)
    try:
        bounded = max(1, min(100, int(limit)))
        rows = connection.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (bounded,)
        ).fetchall()
        return [trade_from_row(row) for row in rows]
    finally:
        connection.close()


def reset_command(payload: str | None) -> dict[str, Any]:
    connection = db()
    init_db(connection)
    try:
        starting_balance = STARTING_BALANCE
        if payload:
            decoded = json.loads(payload)
            requested = safe_float(decoded.get("startingBalance"))
            if requested is not None:
                starting_balance = requested
        reset_state(connection, starting_balance)
        return build_state(connection)
    finally:
        connection.close()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "state"
    if command == "refresh":
        result: Any = refresh()
    elif command == "trades":
        result = trades_command(int(sys.argv[2]) if len(sys.argv) > 2 else 50)
    elif command == "reset":
        result = reset_command(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        result = state_command()
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()