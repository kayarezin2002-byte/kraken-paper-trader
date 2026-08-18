#!/usr/bin/env python3
"""Tests for the GOLD directional 5/6 paper-trading gate and SHORT execution.

Covers:
- Independent LONG vs SHORT evaluation (both directions scored every scan)
- 5/6 entry gate: 5 passes qualify, 4 do not
- Conflict resolution: stronger side wins, ties WAIT (never random)
- SHORT execution mechanics per spec: entry 4400 → 4380 = profit,
  4400 → 4420 = loss; SL above entry, TP below; balance adjustment,
  win/loss classification, R multiple
- refresh_metal opens a SHORT paper trade on a 5/6 bearish setup with the
  stop above and target below entry, and never stacks a second position
- SILVER still requires strict 6/6

Run with: python3 python_bot/test_gold_directional.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trader as pt


def _declining_candles(n: int, start: float = 4400.0,
                       interval: float = 3600.0, last_volume: float | None = None) -> list[list]:
    """Flat then ACCELERATING decline → BEARISH trend, RSI<50, MACD<signal.

    A constant-rate decline converges MACD onto its signal line, so the decline
    accelerates to keep MACD strictly below the signal at the last candle.
    """
    rows = []
    price = start
    base_ts = 1_700_000_000.0
    half = n // 2
    for i in range(n):
        if i >= half:
            price *= (1 - 0.002 * (i - half + 1))   # accelerating downtrend
        vol = 100.0
        if last_volume is not None and i == n - 1:
            vol = last_volume
        rows.append([base_ts + i * interval, price * 1.001, price * 1.002, price * 0.998, price, price, vol, 1])
    return rows


class TestDirectionalEvaluation(unittest.TestCase):
    def test_bearish_low_volume_is_short_5_of_6(self) -> None:
        one_hour = _declining_candles(80, last_volume=10.0)          # Volume fails
        four_hour = _declining_candles(80, interval=14400.0)
        ev = pt.evaluate_conditions_directional(one_hour, four_hour, threshold=5)
        self.assertEqual(ev["short"]["passCount"], 5)
        self.assertEqual(ev["decision"], "SHORT")
        failed = [c["name"] for c in ev["short"]["conditions"] if not c["pass"]]
        self.assertEqual(failed, ["Volume"])
        # LONG is scored independently and must be far from qualifying
        self.assertLessEqual(ev["long"]["passCount"], 1)

    def test_bearish_full_volume_is_short_6_of_6(self) -> None:
        ev = pt.evaluate_conditions_directional(
            _declining_candles(80), _declining_candles(80, interval=14400.0), threshold=5)
        self.assertEqual(ev["short"]["passCount"], 6)
        self.assertEqual(ev["decision"], "SHORT")

    def test_4_of_6_does_not_qualify(self) -> None:
        # Flat series: no trend either way → neither direction reaches 5
        flat = [[1_700_000_000.0 + i * 3600, 2000, 2005, 1995, 2000, 2000, 100.0, 1] for i in range(80)]
        ev = pt.evaluate_conditions_directional(flat, flat, threshold=5)
        self.assertEqual(ev["decision"], "NO_TRADE")
        self.assertLess(ev["long"]["passCount"], 5)
        self.assertLess(ev["short"]["passCount"], 5)
        self.assertIn("Neither direction", ev["decisionReason"])

    def test_conflict_resolution(self) -> None:
        def fake_conds(direction, snap, close, t1, t4):
            n = {"LONG": 5, "SHORT": 6}[direction] if self._case == "short_wins" else 5
            return [{"name": f"c{i}", "currentValue": "", "requiredValue": "", "pass": i < n} for i in range(6)]
        candles = _declining_candles(80)
        with patch.object(pt, "_direction_conditions", side_effect=fake_conds):
            self._case = "short_wins"
            ev = pt.evaluate_conditions_directional(candles, candles)
            self.assertEqual(ev["decision"], "SHORT")
            self.assertIn("stronger evidence", ev["decisionReason"])
            self._case = "tie"
            ev = pt.evaluate_conditions_directional(candles, candles)
            self.assertEqual(ev["decision"], "NO_TRADE")
            self.assertIn("tied", ev["decisionReason"])


class _DBTestCase(unittest.TestCase):
    """Fresh isolated DB per test (DB_PATH is read at import, so patch it)."""

    def setUp(self) -> None:
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self._patcher = patch.object(pt, "DB_PATH", self._path)
        self._patcher.start()
        self.conn = pt.db()
        pt.init_db(self.conn)
        # Keep legacy CORE tests deterministic: ACTIVE candle fetch is
        # stubbed to fail so the parallel ACTIVE strategy never opens trades here.
        self._active_patch = patch.object(pt, "fetch_active_candles",
                                          side_effect=RuntimeError("no 15m data in test"))
        self._active_patch.start()
        self.addCleanup(self._active_patch.stop)

    def tearDown(self) -> None:
        self._patcher.stop()
        try:
            os.unlink(self._path)
        except OSError:
            pass


class TestShortExecution(_DBTestCase):
    """Spec §14: SHORT entry at 4400 — falling price profits, rising price loses."""

    def _short_position(self) -> dict:
        return {
            "direction": "SHORT", "entry": 4400.0, "stopLoss": 4430.0,
            "takeProfit": 4340.0, "quantity": 0.02, "riskAmount": 1.0,
            "openedAt": pt.now_iso(), "entryRsi": 40.0, "entryMacd": -1.0,
            "entryAtr": 20.0, "trend4h": "BEARISH", "trend1h": "BEARISH",
            "entryScore": 6, "entryMode": "GOLD_5OF6_DIRECTIONAL",
            "passCount": 5, "totalCount": 6, "entryConditions": "4h Trend",
            "unvalidatedStrategy": True,
        }

    def test_stop_above_target_below(self) -> None:
        p = self._short_position()
        self.assertGreater(p["stopLoss"], p["entry"])
        self.assertLess(p["takeProfit"], p["entry"])

    def test_falling_price_is_profit(self) -> None:
        state = pt.load_coin_state(self.conn, "GOLD")
        start_bal = float(state["balance"])
        pt.close_position(self.conn, "GOLD", state, self._short_position(), 4380.0, "TAKE_PROFIT")
        row = self.conn.execute("SELECT * FROM trades WHERE coin='GOLD'").fetchone()
        self.assertAlmostEqual(row["profit_loss"], (4400.0 - 4380.0) * 0.02)   # +0.40
        self.assertGreater(row["profit_loss"], 0)
        self.assertEqual(row["result"], "WIN")
        self.assertAlmostEqual(row["r_multiple"], 0.40, places=6)
        new_bal = float(pt.load_coin_state(self.conn, "GOLD")["balance"])
        self.assertAlmostEqual(new_bal, start_bal + 0.40)

    def test_rising_price_is_loss(self) -> None:
        state = pt.load_coin_state(self.conn, "GOLD")
        start_bal = float(state["balance"])
        pt.close_position(self.conn, "GOLD", state, self._short_position(), 4420.0, "STOP_LOSS")
        row = self.conn.execute("SELECT * FROM trades WHERE coin='GOLD'").fetchone()
        self.assertAlmostEqual(row["profit_loss"], (4400.0 - 4420.0) * 0.02)   # −0.40
        self.assertLess(row["profit_loss"], 0)
        self.assertEqual(row["result"], "LOSS")
        new_bal = float(pt.load_coin_state(self.conn, "GOLD")["balance"])
        self.assertAlmostEqual(new_bal, start_bal - 0.40)

    def test_unrealised_pnl_directions(self) -> None:
        pos = self._short_position()
        self.conn.execute("UPDATE coin_state SET open_position=? WHERE coin='GOLD'",
                          (json.dumps(pos),))
        self.conn.commit()
        st = pt.build_coin_state(self.conn, "GOLD", snapshot={"currentPrice": 4380.0, "botStatus": "READY"})
        self.assertAlmostEqual(st["position"]["unrealisedPnl"], 0.40)
        st = pt.build_coin_state(self.conn, "GOLD", snapshot={"currentPrice": 4420.0, "botStatus": "READY"})
        self.assertAlmostEqual(st["position"]["unrealisedPnl"], -0.40)


class TestGoldRefreshGate(_DBTestCase):
    """refresh_metal must open a GOLD SHORT on a 5/6 bearish setup and never stack."""

    def _run_refresh(self):
        one_hour = _declining_candles(80, last_volume=10.0)
        four_hour = _declining_candles(80, interval=14400.0)
        spot = float(one_hour[-1][4])
        with patch.object(pt, "fetch_metal_spot", return_value=(spot, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            return pt.refresh_metal(self.conn, "GOLD"), spot

    def test_opens_short_on_5_of_6(self) -> None:
        result, spot = self._run_refresh()
        pos = result["position"]
        self.assertIsNotNone(pos, "expected a SHORT paper position at 5/6")
        self.assertEqual(pos["direction"], "SHORT")
        self.assertGreater(pos["stopLoss"], pos["entry"])
        self.assertLess(pos["takeProfit"], pos["entry"])
        # 2:1 reward-to-risk geometry
        self.assertAlmostEqual((pos["entry"] - pos["takeProfit"]) / (pos["stopLoss"] - pos["entry"]),
                               2.0, places=2)
        self.assertEqual(pos["entryMode"], "GOLD_5OF6_DIRECTIONAL")
        self.assertEqual(pos["passCount"], 5)
        self.assertEqual(result["directional"]["decision"], "SHORT")
        self.assertEqual(result["directional"]["shortScore"], 5)
        self.assertEqual(result["directional"]["threshold"], 5)
        # Risk sizing: 1% of balance
        self.assertAlmostEqual(pos["riskAmount"], 1.0, places=2)

    def test_no_stacking_second_position(self) -> None:
        self._run_refresh()
        result, _ = self._run_refresh()   # same setup again with a position open
        n = self.conn.execute("SELECT COUNT(*) FROM trades WHERE coin='GOLD'").fetchone()[0]
        open_pos = pt.load_coin_state(self.conn, "GOLD")["open_position"]
        self.assertIsNotNone(open_pos)
        self.assertEqual(n, 0)   # nothing closed, and only ONE open position exists
        self.assertEqual(result["opportunity"]["entryStatus"], "BLOCKED")

    def test_silver_enters_at_any_5_of_6(self) -> None:
        # SILVER was moved to the any-5/6 gate (same as GOLD) per the user's
        # Aug 2026 correction — a 5/6 setup (Volume failing) must now enter.
        one_hour = _declining_candles(80, last_volume=10.0)   # Volume fails → 5/6
        four_hour = _declining_candles(80, interval=14400.0)
        spot = float(one_hour[-1][4])
        with patch.object(pt, "fetch_metal_spot", return_value=(spot, pt.now_iso())), \
             patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)):
            result = pt.refresh_metal(self.conn, "SILVER")
        self.assertIsNotNone(result["position"], "SILVER must enter at any 5/6")
        self.assertEqual(result["position"]["passCount"], 5)
        self.assertEqual(result["position"]["direction"], "SHORT")
        self.assertIsNotNone(result["directional"])
        self.assertEqual(result["directional"]["threshold"], 5)
        self.assertEqual(result["directional"]["decision"], "SHORT")


class TestPaperOnly(unittest.TestCase):
    def test_live_trading_hard_gate(self) -> None:
        self.assertFalse(pt.LIVE_TRADING)
        pt._assert_paper_only()   # must not raise while LIVE_TRADING is False


if __name__ == "__main__":
    unittest.main(verbosity=2)
