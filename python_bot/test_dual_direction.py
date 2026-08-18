#!/usr/bin/env python3
"""Tests for the dual-direction (independent LONG + SHORT) evaluation system.

Covers:
1. LONG and SHORT are scored independently every scan (no bias pre-selection).
2. SHORT conditions are genuinely bearish (not a naive inversion).
3. SHORT trade mechanics: SL above entry, TP below entry, falling price = profit.
4. LONG trade mechanics: SL below entry, TP above entry.
5. Stop-loss and take-profit exits fire correctly in both directions.
6. Duplicate-entry prevention (max ONE position per asset).
7. Entries only on a NEW completed candle.
8. Different assets can hold opposite-direction positions simultaneously.
9. A strong opposite signal while a position is open is LOGGED, never auto-reversed.
10. Paper-only flags stay intact.

Run with:
    python3 python_bot/test_dual_direction.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import paper_trader as pt


# ── Synthetic candle builders ────────────────────────────────────────────────

def _series(direction: str, n: int = 90, interval: int = 3600) -> list[list]:
    """Kraken-format rows [ts, open, high, low, close, vwap, volume, count].

    Accelerating geometric trend so EMA20/EMA50 stack, MACD stays on the
    right side of its signal line, and RSI sits firmly in the trend's half.
    """
    last_ts = time.time() - 1800  # recent: passes the stale-data guard
    # Accelerating rise keeps MACD clearly above its signal line; the DOWN
    # series is the arithmetic mirror (absolute declines accelerate too, so
    # MACD stays clearly BELOW its signal line — a percentage decay would
    # shrink the absolute steps and flatten MACD onto the signal).
    closes = []
    price = 1000.0
    for i in range(n):
        price *= 1.004 ** (1 + i / 45)
        closes.append(price)
    if direction == "DOWN":
        ceiling = max(closes) + 1000.0
        closes = [ceiling - c for c in closes]
    rows = []
    prev = closes[0]
    for i, close in enumerate(closes):
        ts = last_ts - (n - 1 - i) * interval
        high = max(prev, close) * 1.0005
        low = min(prev, close) * 0.9995
        rows.append([ts, prev, high, low, close, (prev + close) / 2, 100.0, 5])
        prev = close
    return rows


def _up(n: int = 90, interval: int = 3600) -> list[list]:
    return _series("UP", n, interval)


def _down(n: int = 90, interval: int = 3600) -> list[list]:
    return _series("DOWN", n, interval)


class DualDirectionBase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self._db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._db_patch = patch.object(pt, "DB_PATH", self._db_path)
        self._db_patch.start()
        self.conn = pt.db()
        pt.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self._db_patch.stop()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    # helpers
    def _position(self, coin: str) -> dict | None:
        row = pt.load_coin_state(self.conn, coin)
        return json.loads(row["open_position"]) if row["open_position"] else None

    def _activity(self, coin: str, event: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT message FROM activity_log WHERE coin = ? AND event = ?",
            (coin, event),
        ).fetchall()
        return [r["message"] for r in rows]

    def _trades(self, coin: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM trades WHERE coin = ? ORDER BY id", (coin,)
        ).fetchall()


# ── 1+2: independent dual scoring / genuine bearish conditions ──────────────

class TestDualEvaluation(DualDirectionBase):
    def test_bullish_market_scores_long_high_short_low(self) -> None:
        ev = pt.evaluate_conditions(_up(), _up(90, 14400))
        self.assertGreaterEqual(ev["long"]["passCount"], 5)
        self.assertLessEqual(ev["short"]["passCount"], 1)
        self.assertEqual(ev["bias"], "LONG")
        self.assertGreaterEqual(ev["long"]["score"], 6)
        # both sides always evaluated
        self.assertEqual(len(ev["long"]["conditions"]), 6)
        self.assertEqual(len(ev["short"]["conditions"]), 6)

    def test_bearish_market_scores_short_high_long_low(self) -> None:
        ev = pt.evaluate_conditions(_down(), _down(90, 14400))
        self.assertGreaterEqual(ev["short"]["passCount"], 5)
        self.assertLessEqual(ev["long"]["passCount"], 1)
        self.assertEqual(ev["bias"], "SHORT")
        self.assertGreaterEqual(ev["short"]["score"], 6)

    def test_short_conditions_are_genuinely_bearish(self) -> None:
        ev = pt.evaluate_conditions(_down(), _down(90, 14400))
        by_name = {c["name"]: c for c in ev["short"]["conditions"]}
        self.assertEqual(by_name["4h Trend"]["requiredValue"], "BEARISH")
        self.assertEqual(by_name["1h Trend"]["requiredValue"], "BEARISH")
        self.assertEqual(by_name["RSI"]["requiredValue"], "≤ 50")
        self.assertEqual(by_name["MACD Momentum"]["requiredValue"], "MACD below signal")
        self.assertEqual(by_name["Price vs MA"]["requiredValue"], "Price < EMA20 < EMA50")
        for name in ("4h Trend", "1h Trend", "RSI", "MACD Momentum", "Price vs MA"):
            self.assertTrue(by_name[name]["pass"], f"{name} should pass in a downtrend")

    def test_insufficient_data_returns_empty_dual_blocks(self) -> None:
        ev = pt.evaluate_conditions(_up(10), _up(10, 14400))
        self.assertEqual(ev["long"]["passCount"], 0)
        self.assertEqual(ev["short"]["passCount"], 0)
        self.assertEqual(ev["signal"], "NO_TRADE")


# ── 3+4: P&L math both directions ────────────────────────────────────────────

class TestPnlMath(DualDirectionBase):
    def _seed_position(self, coin: str, direction: str, entry: float,
                       sl: float, tp: float, qty: float) -> dict:
        pos = {
            "direction": direction, "entry": entry, "stopLoss": sl,
            "takeProfit": tp, "quantity": qty, "riskAmount": 10.0,
            "openedAt": pt.now_iso(), "trend4h": "NEUTRAL",
        }
        self.conn.execute(
            "UPDATE coin_state SET open_position = ? WHERE coin = ?",
            (json.dumps(pos), coin),
        )
        self.conn.commit()
        return pos

    def test_short_profit_when_price_falls(self) -> None:
        state = pt.load_coin_state(self.conn, "BTC")
        start_bal = float(state["balance"])
        pos = self._seed_position("BTC", "SHORT", 100.0, 105.0, 90.0, 2.0)
        pt.close_position(self.conn, "BTC", state, pos, 90.0, "TAKE_PROFIT")
        trade = self._trades("BTC")[-1]
        self.assertAlmostEqual(trade["profit_loss"], (100.0 - 90.0) * 2.0)  # +20
        self.assertAlmostEqual(trade["account_balance"], start_bal + 20.0)
        self.assertEqual(trade["result"], "WIN")

    def test_short_loss_when_price_rises(self) -> None:
        state = pt.load_coin_state(self.conn, "BTC")
        start_bal = float(state["balance"])
        pos = self._seed_position("BTC", "SHORT", 100.0, 105.0, 90.0, 2.0)
        pt.close_position(self.conn, "BTC", state, pos, 105.0, "STOP_LOSS")
        trade = self._trades("BTC")[-1]
        self.assertAlmostEqual(trade["profit_loss"], (100.0 - 105.0) * 2.0)  # −10
        self.assertAlmostEqual(trade["account_balance"], start_bal - 10.0)
        self.assertEqual(trade["result"], "LOSS")

    def test_long_profit_and_loss(self) -> None:
        state = pt.load_coin_state(self.conn, "ETH")
        pos = self._seed_position("ETH", "LONG", 100.0, 95.0, 110.0, 1.0)
        pt.close_position(self.conn, "ETH", state, pos, 110.0, "TAKE_PROFIT")
        self.assertAlmostEqual(self._trades("ETH")[-1]["profit_loss"], 10.0)
        state = pt.load_coin_state(self.conn, "ETH")
        pos = self._seed_position("ETH", "LONG", 100.0, 95.0, 110.0, 1.0)
        pt.close_position(self.conn, "ETH", state, pos, 95.0, "STOP_LOSS")
        self.assertAlmostEqual(self._trades("ETH")[-1]["profit_loss"], -5.0)


# ── 5-9: full refresh flows with mocked market data ─────────────────────────

class TestCryptoRefreshFlows(DualDirectionBase):
    def _refresh_btc(self, price: float, one_hour: list, four_hour: list):
        with patch.object(pt, "fetch_market_data",
                          return_value=(price, 0.01, one_hour, four_hour)):
            return pt.refresh_coin(self.conn, "BTC")

    def test_short_entry_has_correct_stop_and_target(self) -> None:
        one_hour, four_hour = _down(), _down(90, 14400)
        price = float(one_hour[-1][4])
        self._refresh_btc(price, one_hour, four_hour)
        pos = self._position("BTC")
        self.assertIsNotNone(pos, "bearish market should open a SHORT")
        self.assertEqual(pos["direction"], "SHORT")
        self.assertGreater(pos["stopLoss"], pos["entry"], "SHORT stop must be ABOVE entry")
        self.assertLess(pos["takeProfit"], pos["entry"], "SHORT target must be BELOW entry")

    def test_long_entry_has_correct_stop_and_target(self) -> None:
        one_hour, four_hour = _up(), _up(90, 14400)
        price = float(one_hour[-1][4])
        self._refresh_btc(price, one_hour, four_hour)
        pos = self._position("BTC")
        self.assertIsNotNone(pos, "bullish market should open a LONG")
        self.assertEqual(pos["direction"], "LONG")
        self.assertLess(pos["stopLoss"], pos["entry"])
        self.assertGreater(pos["takeProfit"], pos["entry"])

    def test_short_take_profit_exit_is_profitable(self) -> None:
        one_hour, four_hour = _down(), _down(90, 14400)
        self._refresh_btc(float(one_hour[-1][4]), one_hour, four_hour)
        pos = self._position("BTC")
        start_bal = float(pt.load_coin_state(self.conn, "BTC")["balance"])
        # price falls through the take-profit
        self._refresh_btc(pos["takeProfit"] * 0.999, one_hour, four_hour)
        self.assertIsNone(self._position("BTC"))
        trade = self._trades("BTC")[-1]
        self.assertEqual(trade["exit_reason"], "TAKE_PROFIT")
        self.assertGreater(trade["profit_loss"], 0, "falling price must profit a SHORT")
        self.assertGreater(float(pt.load_coin_state(self.conn, "BTC")["balance"]), start_bal)

    def test_short_stop_loss_exit(self) -> None:
        one_hour, four_hour = _down(), _down(90, 14400)
        self._refresh_btc(float(one_hour[-1][4]), one_hour, four_hour)
        pos = self._position("BTC")
        self._refresh_btc(pos["stopLoss"] * 1.001, one_hour, four_hour)
        self.assertIsNone(self._position("BTC"))
        trade = self._trades("BTC")[-1]
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertLess(trade["profit_loss"], 0)

    def test_duplicate_entry_prevented(self) -> None:
        one_hour, four_hour = _up(), _up(90, 14400)
        self._refresh_btc(float(one_hour[-1][4]), one_hour, four_hour)
        first = self._position("BTC")
        # NEW candle, still bullish, price between SL and TP → no second entry
        shift = 3600
        oh2 = [[r[0] + shift, *r[1:]] for r in one_hour]
        fh2 = [[r[0] + shift, *r[1:]] for r in four_hour]
        self._refresh_btc(first["entry"] * 1.001, oh2, fh2)
        second = self._position("BTC")
        self.assertIsNotNone(second)
        self.assertEqual(second["openedAt"], first["openedAt"], "must not re-open / stack")
        self.assertEqual(len(self._trades("BTC")), 0)

    def test_entry_only_on_new_completed_candle(self) -> None:
        one_hour, four_hour = _up(), _up(90, 14400)
        # Pre-mark the latest candle as already processed
        from datetime import datetime, timezone
        seen = datetime.fromtimestamp(float(one_hour[-1][0]), timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE coin_state SET last_candle_at = ? WHERE coin = ?", (seen, "BTC"))
        self.conn.commit()
        self._refresh_btc(float(one_hour[-1][4]), one_hour, four_hour)
        self.assertIsNone(self._position("BTC"), "no entry between completed candles")

    def test_opposite_signal_logged_not_reversed(self) -> None:
        one_hour, four_hour = _down(), _down(90, 14400)
        self._refresh_btc(float(one_hour[-1][4]), one_hour, four_hour)
        pos = self._position("BTC")
        self.assertEqual(pos["direction"], "SHORT")
        # Market flips hard bullish on a NEW candle; price kept below the stop
        up1, up4 = _up(), _up(90, 14400)
        shift = 7200
        up1 = [[r[0] + shift, *r[1:]] for r in up1]
        up4 = [[r[0] + shift, *r[1:]] for r in up4]
        safe_price = pos["entry"] + (pos["stopLoss"] - pos["entry"]) * 0.5
        self._refresh_btc(safe_price, up1, up4)
        still = self._position("BTC")
        self.assertIsNotNone(still, "position must not be auto-reversed")
        self.assertEqual(still["direction"], "SHORT")
        logs = self._activity("BTC", "OPPOSITE_SIGNAL")
        self.assertTrue(logs, "opposite signal must be logged")
        self.assertIn("LONG", logs[-1])
        self.assertIn("not auto-revers", logs[-1])


class TestMetalsAndSimultaneity(DualDirectionBase):
    def _refresh_gold(self, spot: float, one_hour: list, four_hour: list):
        with patch.object(pt, "fetch_metal_spot", return_value=(spot, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            return pt.refresh_metal(self.conn, "GOLD")

    def test_metal_short_entry_any_5_of_6(self) -> None:
        one_hour, four_hour = _down(), _down(90, 14400)
        self._refresh_gold(float(one_hour[-1][4]), one_hour, four_hour)
        pos = self._position("GOLD")
        self.assertIsNotNone(pos, "bearish metal market should open SHORT on any-5/6")
        self.assertEqual(pos["direction"], "SHORT")
        self.assertGreater(pos["stopLoss"], pos["entry"])
        self.assertLess(pos["takeProfit"], pos["entry"])
        self.assertTrue(pos.get("unvalidatedStrategy"), "paper-only flag must persist")

    def test_opposite_direction_positions_simultaneously(self) -> None:
        # BTC LONG …
        up1, up4 = _up(), _up(90, 14400)
        with patch.object(pt, "fetch_market_data",
                          return_value=(float(up1[-1][4]), 0.01, up1, up4)):
            pt.refresh_coin(self.conn, "BTC")
        # … while GOLD goes SHORT
        dn1, dn4 = _down(), _down(90, 14400)
        self._refresh_gold(float(dn1[-1][4]), dn1, dn4)
        btc, gold = self._position("BTC"), self._position("GOLD")
        self.assertEqual((btc or {}).get("direction"), "LONG")
        self.assertEqual((gold or {}).get("direction"), "SHORT")

    def test_paper_only_flags(self) -> None:
        self.assertTrue(pt.PAPER_TRADING)
        self.assertFalse(pt.LIVE_TRADING)
        pt._assert_paper_only()  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
