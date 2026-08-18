#!/usr/bin/env python3
"""Tests for the parallel ACTIVE (15m) strategy.

Covers:
- ACTIVE evaluation: independent LONG/SHORT scoring, 4/6 gate, tie → WAIT
- ACTIVE entry on a new completed 15m candle; ACTIVE + CORE coexist per asset
- ACTIVE SL/TP exits record trades tagged strategy='ACTIVE'
- Re-entry protection on the ACTIVE slot
- Portfolio open risk counts both CORE and ACTIVE positions
- Strategy stats buckets (CORE / ACTIVE / COMBINED) incl. profit factor & max DD
- ACTIVE data failure never breaks the CORE scan
- PAPER-ONLY assertion still guards all opens/closes

Run with: python3 python_bot/test_active_strategy.py
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
             interval: float = 900.0, last_volume: float | None = None,
             recent: bool = True) -> list[list]:
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

    def _active_pos(self, coin: str) -> dict | None:
        row = pt.load_coin_state(self.conn, coin)
        return json.loads(row["active_position"]) if row["active_position"] else None

    def _core_pos(self, coin: str) -> dict | None:
        row = pt.load_coin_state(self.conn, coin)
        return json.loads(row["open_position"]) if row["open_position"] else None

    def _run_active(self, coin: str, fifteen, price=None, trend="BULLISH", spread=0.01):
        px = price if price is not None else float(fifteen[-1][4])
        with patch.object(pt, "fetch_active_candles", return_value=fifteen):
            pt.refresh_active(self.conn, coin, px, trend, spread)
        return px


class TestActiveEvaluation(unittest.TestCase):
    def test_long_gate_and_independence(self) -> None:
        up = _candles(80, "UP")
        ev = pt.evaluate_active_directional(up, "BULLISH")
        self.assertEqual(ev["decision"], "LONG")
        self.assertGreaterEqual(ev["long"]["score"], pt.ACTIVE_MIN_PASS)
        self.assertLess(ev["short"]["score"], pt.ACTIVE_MIN_PASS)
        self.assertEqual(ev["maxScore"], 6)
        self.assertEqual(len(ev["long"]["conditions"]), 6)
        self.assertEqual(len(ev["short"]["conditions"]), 6)

    def test_short_gate(self) -> None:
        down = _candles(80, "DOWN")
        ev = pt.evaluate_active_directional(down, "BEARISH")
        self.assertEqual(ev["decision"], "SHORT")
        self.assertGreaterEqual(ev["short"]["score"], pt.ACTIVE_MIN_PASS)

    def test_not_enough_history(self) -> None:
        ev = pt.evaluate_active_directional(_candles(20, "UP"), "BULLISH")
        self.assertEqual(ev["decision"], "NO_TRADE")
        self.assertIn("15m candle history", ev["decisionReason"])

    def test_one_hour_context_is_a_condition(self) -> None:
        up = _candles(80, "UP")
        with_confirm = pt.evaluate_active_directional(up, "BULLISH")
        without = pt.evaluate_active_directional(up, "BEARISH")
        self.assertEqual(
            with_confirm["long"]["score"] - without["long"]["score"], 1,
            "1h confirmation must count exactly one condition",
        )


class TestActiveExecution(_DBTestCase):
    def test_entry_opens_active_slot_only(self) -> None:
        up = _candles(80, "UP")
        self._run_active("BTC", up)
        pos = self._active_pos("BTC")
        self.assertIsNotNone(pos, "ACTIVE LONG should open")
        self.assertEqual(pos["strategy"], "ACTIVE")
        self.assertEqual(pos["direction"], "LONG")
        self.assertIsNone(self._core_pos("BTC"), "CORE slot must be untouched")
        # Stop/target geometry: 1.5×ATR stop, 1.5R target
        self.assertLess(pos["stopLoss"], pos["entry"])
        self.assertGreater(pos["takeProfit"], pos["entry"])
        rr = (pos["takeProfit"] - pos["entry"]) / (pos["entry"] - pos["stopLoss"])
        self.assertAlmostEqual(rr, pt.ACTIVE_REWARD_TO_RISK, places=2)

    def test_core_and_active_coexist(self) -> None:
        # Fake a CORE position, then open an ACTIVE one on the same asset
        self.conn.execute(
            "UPDATE coin_state SET open_position = ? WHERE coin = 'BTC'",
            (json.dumps({"direction": "LONG", "entry": 100.0, "stopLoss": 90.0,
                         "takeProfit": 120.0, "quantity": 1.0, "riskAmount": 5.0,
                         "openedAt": pt.now_iso()}),),
        )
        self.conn.commit()
        self._run_active("BTC", _candles(80, "UP"))
        self.assertIsNotNone(self._core_pos("BTC"))
        self.assertIsNotNone(self._active_pos("BTC"))
        pr = pt.portfolio_open_risk(self.conn)
        self.assertEqual(pr["openPositions"], 2, "both slots must count toward portfolio risk")

    def test_stop_loss_exit_records_active_trade(self) -> None:
        self._run_active("BTC", _candles(80, "UP"))
        pos = self._active_pos("BTC")
        self.assertIsNotNone(pos)
        # Next scan: price gaps below the stop → exit
        self._run_active("BTC", _candles(80, "UP"), price=pos["stopLoss"] * 0.99)
        self.assertIsNone(self._active_pos("BTC"))
        row = self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["strategy"], "ACTIVE")
        self.assertEqual(row["exit_reason"], "STOP_LOSS")
        self.assertLess(row["profit_loss"], 0)

    def test_reentry_protection_after_close(self) -> None:
        up = _candles(80, "UP")
        self._run_active("BTC", up)
        pos = self._active_pos("BTC")
        self._run_active("BTC", up, price=pos["takeProfit"] * 1.01)  # TP exit
        self.assertIsNone(self._active_pos("BTC"))
        # Same setup, NEW candle → must be blocked by re-entry protection
        up2 = _candles(81, "UP")
        self._run_active("BTC", up2)
        self.assertIsNone(self._active_pos("BTC"), "same-signal re-entry must be blocked")
        state = pt.load_coin_state(self.conn, "BTC")
        snap = json.loads(state["active_snapshot"])
        self.assertIn("Re-entry protection", snap.get("blockReason") or "")

    def test_no_entry_without_new_candle(self) -> None:
        up = _candles(80, "UP")
        self._run_active("BTC", up)
        first = self._active_pos("BTC")
        self.assertIsNotNone(first)
        # Close manually, then re-run with the SAME last candle: no new entry
        self.conn.execute("UPDATE coin_state SET active_position = NULL, active_reentry = NULL WHERE coin = 'BTC'")
        self.conn.commit()
        self._run_active("BTC", up)
        self.assertIsNone(self._active_pos("BTC"), "no entry without a new completed candle")

    def test_stale_data_blocks_entry(self) -> None:
        stale = _candles(80, "UP", recent=False)  # candles from 2023
        self._run_active("BTC", stale)
        self.assertIsNone(self._active_pos("BTC"))
        snap = json.loads(pt.load_coin_state(self.conn, "BTC")["active_snapshot"])
        self.assertEqual(snap["status"], "DANGER")

    def test_wide_spread_blocks_entry(self) -> None:
        self._run_active("BTC", _candles(80, "UP"), spread=5.0)
        self.assertIsNone(self._active_pos("BTC"))

    def test_portfolio_ceiling_blocks_active_entry(self) -> None:
        with patch.object(pt, "MAX_TOTAL_OPEN_RISK_PERCENT", 0.0001):
            self._run_active("BTC", _candles(80, "UP"))
        self.assertIsNone(self._active_pos("BTC"))
        row = self.conn.execute(
            "SELECT message FROM activity_log WHERE coin='BTC' AND event='ENTRY_BLOCKED' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("portfolio risk limit", row["message"])

    def test_exit_runs_even_when_15m_fetch_fails(self) -> None:
        # Regression: SL/TP protection must not depend on 15m data availability.
        self._run_active("BTC", _candles(80, "UP"))
        pos = self._active_pos("BTC")
        self.assertIsNotNone(pos)
        with patch.object(pt, "fetch_active_candles", side_effect=RuntimeError("feed down")):
            pt.refresh_active(self.conn, "BTC", pos["stopLoss"] * 0.99, "NEUTRAL", 0.01)
        self.assertIsNone(self._active_pos("BTC"), "breached stop must close despite fetch failure")
        row = self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["strategy"], "ACTIVE")
        self.assertEqual(row["exit_reason"], "STOP_LOSS")

    def test_stop_gap_fills_at_live_price(self) -> None:
        # Regression: a gap through the stop must fill at the observed live
        # price (adverse fill), never capped at exactly the stop.
        self._run_active("BTC", _candles(80, "UP"))
        pos = self._active_pos("BTC")
        gap_price = pos["stopLoss"] * 0.95
        self._run_active("BTC", _candles(80, "UP"), price=gap_price)
        row = self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(row["exit"], gap_price, places=6)
        self.assertLess(row["profit_loss"], -0.9 * row["risk_amount"],
                        "gap loss must exceed the modelled 1R stop loss")

    def test_short_stop_gap_fills_at_live_price(self) -> None:
        self._run_active("BTC", _candles(80, "DOWN"), trend="BEARISH")
        pos = self._active_pos("BTC")
        self.assertEqual(pos["direction"], "SHORT")
        gap_price = pos["stopLoss"] * 1.05
        self._run_active("BTC", _candles(80, "DOWN"), price=gap_price, trend="BEARISH")
        row = self.conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
        self.assertAlmostEqual(row["exit"], gap_price, places=6)
        self.assertLess(row["profit_loss"], 0)

    def test_core_waiting_for_data_still_runs_active(self) -> None:
        # Regression: crypto CORE early-return (not enough 1h/4h candles) must
        # still run the ACTIVE scan with the live price.
        short_history = _candles(10, "UP", interval=3600.0)
        fifteen = _candles(80, "UP")
        px = float(fifteen[-1][4])
        with patch.object(pt, "fetch_market_data", return_value=(px, 0.01, short_history, short_history)), \
             patch.object(pt, "fetch_active_candles", return_value=fifteen):
            result = pt.refresh_coin(self.conn, "BTC")
        self.assertEqual(result["botStatus"], "WAITING_FOR_DATA")
        self.assertIsNotNone(self._active_pos("BTC"), "ACTIVE must still scan and open")

    def test_open_position_persists_pass_count(self) -> None:
        self._run_active("BTC", _candles(80, "UP"))
        pos = self._active_pos("BTC")
        self.assertIsNotNone(pos.get("passCount"))
        self.assertEqual(pos["passCount"], pos["entryScore"])
        self.assertEqual(pos["maxScore"], pt.ACTIVE_MAX_SCORE)

    def test_fetch_failure_writes_api_error_snapshot(self) -> None:
        with patch.object(pt, "fetch_active_candles", side_effect=RuntimeError("boom")):
            pt.refresh_active(self.conn, "BTC", 100.0, "NEUTRAL", 0.01)
        snap = json.loads(pt.load_coin_state(self.conn, "BTC")["active_snapshot"])
        self.assertEqual(snap["status"], "API_ERROR")
        self.assertIsNone(self._active_pos("BTC"))

    def test_paper_only_guard(self) -> None:
        with patch.object(pt, "PAPER_TRADING", False):
            with self.assertRaises(RuntimeError):
                self._run_active("BTC", _candles(80, "UP"))


class TestStrategyStats(_DBTestCase):
    def _insert_trade(self, strategy: str, pnl: float, closed_at: str) -> None:
        self.conn.execute(
            "INSERT INTO trades (coin, opened_at, closed_at, direction, entry, exit, "
            "stop_loss, take_profit, trend_4h, profit_loss, account_balance, exit_reason, strategy) "
            "VALUES ('BTC', ?, ?, 'LONG', 100, 110, 95, 120, 'BULLISH', ?, 500, 'TAKE_PROFIT', ?)",
            (closed_at, closed_at, pnl, strategy),
        )
        self.conn.commit()

    def test_buckets_and_metrics(self) -> None:
        self._insert_trade("CORE", 10.0, "2026-08-01T00:00:00+00:00")
        self._insert_trade("CORE", -5.0, "2026-08-02T00:00:00+00:00")
        self._insert_trade("ACTIVE", -4.0, "2026-08-03T00:00:00+00:00")
        self._insert_trade("ACTIVE", 8.0, "2026-08-04T00:00:00+00:00")
        st = pt.strategy_stats(self.conn)
        self.assertEqual(st["core"]["trades"], 2)
        self.assertEqual(st["active"]["trades"], 2)
        self.assertEqual(st["combined"]["trades"], 4)
        self.assertEqual(st["core"]["pnl"], 5.0)
        self.assertEqual(st["active"]["pnl"], 4.0)
        self.assertEqual(st["combined"]["pnl"], 9.0)
        self.assertAlmostEqual(st["core"]["profitFactor"], 2.0)
        self.assertAlmostEqual(st["active"]["profitFactor"], 2.0)
        self.assertEqual(st["core"]["maxDrawdown"], 5.0)
        self.assertEqual(st["active"]["maxDrawdown"], 4.0)
        self.assertEqual(st["combined"]["winRate"], 50.0)

    def test_null_strategy_counts_as_core(self) -> None:
        self.conn.execute(
            "INSERT INTO trades (coin, opened_at, closed_at, direction, entry, exit, "
            "stop_loss, take_profit, trend_4h, profit_loss, account_balance, exit_reason) "
            "VALUES ('BTC', '2026-08-01T00:00:00+00:00', '2026-08-01T01:00:00+00:00', "
            "'LONG', 100, 110, 95, 120, 'BULLISH', 10.0, 510, 'TAKE_PROFIT')",
        )
        self.conn.commit()
        st = pt.strategy_stats(self.conn)
        self.assertEqual(st["core"]["trades"], 1)
        self.assertEqual(st["active"]["trades"], 0)


class TestChartCommand(unittest.TestCase):
    def test_interval_defaults(self) -> None:
        self.assertEqual(pt._default_interval("24H"), "15m")
        self.assertEqual(pt._default_interval("7D"), "1h")
        self.assertEqual(pt._default_interval("90D"), "4h")

    def test_unknown_interval_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            pt.chart_command("BTC", "7D", "3m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
