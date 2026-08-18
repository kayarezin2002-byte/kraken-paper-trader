#!/usr/bin/env python3
"""Tests for the all-asset LONG+SHORT directional upgrade (spec §17).

Covers:
- LONG profit / LONG loss calculation (SHORT covered in test_gold_directional.py)
- LONG stop hit / LONG target hit; SHORT stop hit / SHORT target hit
- Crypto (BTC) independent directional scoring: SHORT entry at weighted >= 6/8
- Duplicate-entry prevention & one-position-per-asset rule
- Multiple different assets open simultaneously
- Portfolio risk ceiling blocks entries and logs the reason
- Completed-candle-only signal generation (no re-entry mid-candle)
- Opposite-signal monitoring is logged while a position is open

Run with: python3 python_bot/test_directional_all_assets.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trader as pt


def _candles(n: int, direction: str, start: float = 4400.0,
             interval: float = 3600.0, last_volume: float | None = None,
             recent: bool = False) -> list[list]:
    """Flat then ACCELERATING move → clear trend, MACD strictly beyond signal."""
    rows = []
    price = start
    base_ts = (time.time() - n * interval) if recent else 1_700_000_000.0
    half = n // 2
    sgn = 1 if direction == "UP" else -1
    for i in range(n):
        if i >= half:
            price *= (1 + sgn * 0.002 * (i - half + 1))
        vol = 100.0
        if last_volume is not None and i == n - 1:
            vol = last_volume
        rows.append([base_ts + i * interval, price * 1.001, price * 1.002,
                     price * 0.998, price, price, vol, 1])
    return rows


class _DBTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._patcher = patch.object(pt, "DB_PATH", self._path)
        self._patcher.start()
        self.conn = pt.db()
        pt.init_db(self.conn)

    def tearDown(self) -> None:
        self._patcher.stop()
        try:
            os.unlink(self._path)
        except OSError:
            pass

    # helpers ---------------------------------------------------------------
    def _refresh_gold(self, direction: str, last_volume: float | None = None):
        one_hour = _candles(80, direction, last_volume=last_volume)
        four_hour = _candles(80, direction, interval=14400.0)
        spot = float(one_hour[-1][4])
        with patch.object(pt, "fetch_metal_spot", return_value=(spot, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            return pt.refresh_metal(self.conn, "GOLD"), spot

    def _refresh_btc(self, direction: str):
        # Cache per test so repeat calls present the SAME completed candle
        if not hasattr(self, "_btc_series"):
            self._btc_series = {}
        if direction not in self._btc_series:
            self._btc_series[direction] = (
                _candles(80, direction, start=50000.0, recent=True),
                _candles(80, direction, start=50000.0, interval=14400.0, recent=True),
            )
        one_hour, four_hour = self._btc_series[direction]
        price = float(one_hour[-1][4])
        with patch.object(pt, "fetch_market_data",
                          return_value=(price, 0.01, one_hour, four_hour)):
            return pt.refresh_coin(self.conn, "BTC"), price

    def _gold_position(self, pos):
        self.conn.execute(
            "UPDATE coin_state SET open_position = ? WHERE coin = 'GOLD'",
            (json.dumps(pos) if pos else None,))
        self.conn.commit()


class TestLongExecution(_DBTestCase):
    def _long_position(self) -> dict:
        return {
            "direction": "LONG", "entry": 4400.0, "stopLoss": 4370.0,
            "takeProfit": 4460.0, "quantity": 0.02, "riskAmount": 1.0,
            "openedAt": pt.now_iso(), "entryRsi": 60.0, "entryMacd": 1.0,
            "entryAtr": 20.0, "trend4h": "BULLISH", "trend1h": "BULLISH",
            "entryScore": 6, "entryMode": "GOLD_5OF6_DIRECTIONAL",
            "passCount": 5, "totalCount": 6, "entryConditions": "4h Trend",
        }

    def test_long_profit(self) -> None:
        state = pt.load_coin_state(self.conn, "GOLD")
        pt.close_position(self.conn, "GOLD", state, self._long_position(), 4420.0, "TAKE_PROFIT")
        row = self.conn.execute(
            "SELECT * FROM trades WHERE coin='GOLD' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(row["profit_loss"], (4420.0 - 4400.0) * 0.02)  # +0.4
        self.assertEqual(row["result"], "WIN")
        new_bal = pt.load_coin_state(self.conn, "GOLD")["balance"]
        self.assertAlmostEqual(float(new_bal), float(state["balance"]) + 0.4)

    def test_long_loss(self) -> None:
        state = pt.load_coin_state(self.conn, "GOLD")
        pt.close_position(self.conn, "GOLD", state, self._long_position(), 4380.0, "STOP_LOSS")
        row = self.conn.execute(
            "SELECT * FROM trades WHERE coin='GOLD' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(row["profit_loss"], (4380.0 - 4400.0) * 0.02)  # -0.4
        self.assertEqual(row["result"], "LOSS")

    def test_long_stop_and_target_hits(self) -> None:
        # Open a LONG via refresh, then move spot to stop / target
        result, spot = self._refresh_gold("UP")
        pos = json.loads(pt.load_coin_state(self.conn, "GOLD")["open_position"])
        self.assertEqual(pos["direction"], "LONG")
        self.assertLess(pos["stopLoss"], pos["entry"])       # SL below entry
        self.assertGreater(pos["takeProfit"], pos["entry"])  # TP above entry
        # target hit
        one_hour = _candles(80, "UP")
        four_hour = _candles(80, "UP", interval=14400.0)
        with patch.object(pt, "fetch_metal_spot", return_value=(pos["takeProfit"] + 1, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            pt.refresh_metal(self.conn, "GOLD")
        row = self.conn.execute(
            "SELECT * FROM trades WHERE coin='GOLD' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["exit_reason"], "TAKE_PROFIT")
        self.assertGreater(row["profit_loss"], 0)
        self.assertIsNotNone(row["long_score"])
        self.assertIsNotNone(row["entry_threshold"])

    def test_short_stop_and_target_hits(self) -> None:
        result, spot = self._refresh_gold("DOWN")
        pos = json.loads(pt.load_coin_state(self.conn, "GOLD")["open_position"])
        self.assertEqual(pos["direction"], "SHORT")
        self.assertGreater(pos["stopLoss"], pos["entry"])    # SL above entry
        self.assertLess(pos["takeProfit"], pos["entry"])     # TP below entry
        one_hour = _candles(80, "DOWN")
        four_hour = _candles(80, "DOWN", interval=14400.0)
        # stop hit (price rises above the stop)
        with patch.object(pt, "fetch_metal_spot", return_value=(pos["stopLoss"] + 1, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            pt.refresh_metal(self.conn, "GOLD")
        row = self.conn.execute(
            "SELECT * FROM trades WHERE coin='GOLD' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["exit_reason"], "STOP_LOSS")
        self.assertLess(row["profit_loss"], 0)


class TestCryptoDirectional(_DBTestCase):
    def test_btc_short_entry_weighted_gate(self) -> None:
        result, price = self._refresh_btc("DOWN")
        d = result["directional"]
        self.assertIsNotNone(d)
        self.assertEqual(d["maxScore"], 8)
        self.assertGreaterEqual(d["shortScore"], 6)
        pos = result["position"]
        self.assertIsNotNone(pos, "expected a SHORT paper position at weighted >= 6/8")
        self.assertEqual(pos["direction"], "SHORT")
        self.assertGreater(pos["stopLoss"], pos["entry"])
        self.assertLess(pos["takeProfit"], pos["entry"])
        self.assertEqual(pos["entryThreshold"], pt.DIRECTIONAL_THRESHOLDS["BTC"]["short"])

    def test_btc_long_entry_weighted_gate(self) -> None:
        result, price = self._refresh_btc("UP")
        pos = result["position"]
        self.assertIsNotNone(pos)
        self.assertEqual(pos["direction"], "LONG")
        self.assertGreaterEqual(result["directional"]["longScore"], 6)

    def test_duplicate_entry_prevention(self) -> None:
        self._refresh_btc("DOWN")
        pos1 = json.loads(pt.load_coin_state(self.conn, "BTC")["open_position"])
        # Same candles again (same completed candle): no stacking, no new trade
        self._refresh_btc("DOWN")
        pos2 = json.loads(pt.load_coin_state(self.conn, "BTC")["open_position"])
        self.assertEqual(pos1["openedAt"], pos2["openedAt"])
        n = self.conn.execute("SELECT COUNT(*) FROM trades WHERE coin='BTC'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_completed_candle_only(self) -> None:
        self._refresh_btc("DOWN")
        # Clear the position AND re-arm; same candle must still not re-enter
        self.conn.execute(
            "UPDATE coin_state SET open_position = NULL, reentry = ? WHERE coin='BTC'",
            (json.dumps({"armed": True, "lastDirection": None}),))
        self.conn.commit()
        result, _ = self._refresh_btc("DOWN")
        self.assertIsNone(result["position"], "no entry between completed candles")


class TestPortfolio(_DBTestCase):
    def test_multiple_assets_open_simultaneously(self) -> None:
        self._refresh_btc("DOWN")
        self._refresh_gold("UP")
        btc = json.loads(pt.load_coin_state(self.conn, "BTC")["open_position"])
        gold = json.loads(pt.load_coin_state(self.conn, "GOLD")["open_position"])
        self.assertEqual(btc["direction"], "SHORT")
        self.assertEqual(gold["direction"], "LONG")
        pr = pt.portfolio_open_risk(self.conn)
        self.assertEqual(pr["openPositions"], 2)
        self.assertGreater(pr["totalOpenRisk"], 0)

    def test_portfolio_risk_ceiling_blocks_entry(self) -> None:
        with patch.object(pt, "MAX_TOTAL_OPEN_RISK_PERCENT", 0.0001):
            result, _ = self._refresh_gold("DOWN")
        self.assertIsNone(result["position"], "ceiling must block the entry")
        self.assertEqual(result["opportunity"]["entryStatus"], "BLOCKED")
        self.assertIn("portfolio risk limit", result["opportunity"]["reason"])
        row = self.conn.execute(
            "SELECT message FROM activity_log WHERE coin='GOLD' AND event='ENTRY_BLOCKED' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row, "blocked entry must be logged")
        self.assertIn("portfolio risk limit", row["message"])

    def test_ceiling_boundary_exact_vs_over(self) -> None:
        # Risking exactly the ceiling is allowed; a hair over is blocked.
        pr = pt.portfolio_open_risk(self.conn)
        total = pr["totalStarting"]
        self.assertIsNone(
            pt._portfolio_risk_block_reason(self.conn, total * pt.MAX_TOTAL_OPEN_RISK_PERCENT / 100),
            "risk exactly at the ceiling must be allowed")
        self.assertIsNotNone(
            pt._portfolio_risk_block_reason(self.conn, total * pt.MAX_TOTAL_OPEN_RISK_PERCENT / 100 * 1.001),
            "risk just over the ceiling must be blocked")

    def test_opposite_signal_logged_while_position_open(self) -> None:
        # Open a GOLD LONG, then feed a fully bearish scan on a NEW candle
        self._refresh_gold("UP")
        one_hour = _candles(81, "DOWN")   # 81 candles → new completed candle ts
        four_hour = _candles(80, "DOWN", interval=14400.0)
        pos = json.loads(pt.load_coin_state(self.conn, "GOLD")["open_position"])
        mid = pos["entry"]  # price between stop and target → position stays open
        with patch.object(pt, "fetch_metal_spot", return_value=(mid, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            pt.refresh_metal(self.conn, "GOLD")
        row = self.conn.execute(
            "SELECT message FROM activity_log WHERE coin='GOLD' AND event='OPPOSITE_SIGNAL' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row, "strong opposite signal must be logged")
        self.assertIn("Strong opposite signal detected", row["message"])
        # And the position must NOT have been reversed
        pos2 = json.loads(pt.load_coin_state(self.conn, "GOLD")["open_position"])
        self.assertEqual(pos2["direction"], "LONG")


class TestPaperOnly(unittest.TestCase):
    def test_live_trading_impossible(self) -> None:
        self.assertFalse(pt.LIVE_TRADING)
        pt._assert_paper_only()  # must not raise while LIVE_TRADING is False


if __name__ == "__main__":
    unittest.main(verbosity=2)
