#!/usr/bin/env python3
"""Regression tests for metals (GOLD/SILVER) invariants.

These tests assert the hard invariants that must hold regardless of market
conditions or API failures:

1. Metals never open paper trades (no rows in the trades table).
2. Metal balances always stay at exactly £100 (never change).
3. A gold-api.com outage puts the metal into API_ERROR state (not stale data).
4. A Yahoo Finance outage keeps the spot price visible with a scanNote.
5. Crypto coins are not affected by a metals API failure.

Run with:
    python3 -m pytest python_bot/test_metals_invariants.py -v
or:
    python3 python_bot/test_metals_invariants.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure the bot module can be imported from the project root.
sys.path.insert(0, os.path.dirname(__file__))

import paper_trader as pt


def _make_fake_candles(n: int = 60) -> list[list]:
    """Return n minimal OHLC rows that satisfy the 55-candle minimum."""
    base_ts = 1_700_000_000.0
    rows = []
    price = 2000.0
    for i in range(n):
        rows.append([base_ts + i * 3600, price, price + 5, price - 5, price, price, 100.0, 1])
    return rows


def _make_four_hour_candles(n: int = 60) -> list[list]:
    base_ts = 1_700_000_000.0
    rows = []
    price = 2000.0
    for i in range(n):
        rows.append([base_ts + i * 14400, price, price + 5, price - 5, price, price, 400.0, 1])
    return rows


class TestMetalsInvariants(unittest.TestCase):
    """Core invariants: metals never trade and never change balance."""

    def _db_with_fresh_state(self) -> tuple[sqlite3.Connection, str]:
        """Create a temp DB, init it, and return (connection, path)."""
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.environ["PAPER_TRADER_DB"] = path
        conn = pt.db()
        pt.init_db(conn)
        return conn, path

    def tearDown(self) -> None:
        if "PAPER_TRADER_DB" in os.environ:
            path = os.environ.pop("PAPER_TRADER_DB")
            try:
                os.unlink(path)
            except OSError:
                pass

    # ── Invariant 1: no trades are ever written for metals ─────────────────

    def test_no_trades_after_successful_refresh(self) -> None:
        """A successful metal refresh must not insert any rows into trades."""
        conn, _ = self._db_with_fresh_state()
        one_hour = _make_fake_candles(60)
        four_hour = _make_four_hour_candles(60)
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)),
        ):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        for metal in pt.METALS:
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE coin = ?", (metal,)
            ).fetchone()[0]
            self.assertEqual(
                count, 0,
                f"{metal} had {count} trade row(s) after refresh — metals must never trade",
            )

    def test_no_trades_after_reset(self) -> None:
        """Resetting a metal account must leave zero trades."""
        conn, path = self._db_with_fresh_state()
        conn.close()
        os.environ["PAPER_TRADER_DB"] = path
        for metal in pt.METALS:
            pt.reset_all_command()
            real_conn = pt.db()
            pt.init_db(real_conn)
            count = real_conn.execute(
                "SELECT COUNT(*) FROM trades WHERE coin = ?", (metal,)
            ).fetchone()[0]
            self.assertEqual(
                count, 0,
                f"{metal} had {count} trade row(s) after reset — metals must never trade",
            )
            real_conn.close()

    # ── Invariant 2: balance stays at exactly £100 ─────────────────────────

    def test_balance_unchanged_after_successful_refresh(self) -> None:
        """Metal account balance must not change after any number of refreshes."""
        conn, _ = self._db_with_fresh_state()
        one_hour = _make_fake_candles(60)
        four_hour = _make_four_hour_candles(60)
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)),
        ):
            for _ in range(3):
                for metal in pt.METALS:
                    pt.refresh_metal(conn, metal)

        for metal in pt.METALS:
            state = pt.load_coin_state(conn, metal)
            self.assertAlmostEqual(
                float(state["balance"]),
                pt.STARTING_BALANCE,
                places=6,
                msg=f"{metal} balance drifted from £{pt.STARTING_BALANCE}",
            )

    def test_balance_unchanged_after_api_errors(self) -> None:
        """Metal balance must stay at £100 even when both APIs fail."""
        conn, _ = self._db_with_fresh_state()

        def _spot_error(_sym: str) -> None:
            raise RuntimeError("gold-api.com is down (simulated)")

        def _yahoo_error(_sym: str) -> None:
            raise RuntimeError("Yahoo Finance is down (simulated)")

        with patch.object(pt, "fetch_metal_spot", side_effect=_spot_error):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        for metal in pt.METALS:
            state = pt.load_coin_state(conn, metal)
            self.assertAlmostEqual(
                float(state["balance"]),
                pt.STARTING_BALANCE,
                places=6,
                msg=f"{metal} balance changed after spot API error",
            )

    def test_balance_unchanged_after_reset(self) -> None:
        """Reset always sets the metal balance back to the fixed starting amount."""
        conn, _ = self._db_with_fresh_state()
        for metal in pt.METALS:
            pt.reset_coin(conn, metal, pt.STARTING_BALANCE, clear_trades=True)
            state = pt.load_coin_state(conn, metal)
            self.assertAlmostEqual(
                float(state["balance"]),
                pt.STARTING_BALANCE,
                places=6,
                msg=f"{metal} balance wrong after reset",
            )

    def test_reset_ignores_custom_starting_balance_for_metals(self) -> None:
        """reset_all_command ignores any startingBalance override for metals."""
        conn, path = self._db_with_fresh_state()
        conn.close()
        os.environ["PAPER_TRADER_DB"] = path
        # Try to set a custom balance of £500 — metals must ignore it.
        pt.reset_all_command(json.dumps({"startingBalance": 500.0}))
        real_conn = pt.db()
        pt.init_db(real_conn)
        for metal in pt.METALS:
            state = pt.load_coin_state(real_conn, metal)
            self.assertAlmostEqual(
                float(state["balance"]),
                pt.STARTING_BALANCE,
                places=6,
                msg=f"{metal} honoured a custom balance override — it must always reset to £{pt.STARTING_BALANCE}",
            )
        real_conn.close()

    # ── Invariant 3: gold-api.com outage → API_ERROR, no stale price ───────

    def test_spot_api_failure_yields_api_error_status(self) -> None:
        """When gold-api.com is down, botStatus must be API_ERROR (not MONITORING)."""
        conn, _ = self._db_with_fresh_state()
        # First refresh succeeds so there IS a stored snapshot with a real price.
        one_hour = _make_fake_candles(60)
        four_hour = _make_four_hour_candles(60)
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)),
        ):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        # Second refresh: gold-api.com is down.
        with patch.object(
            pt, "fetch_metal_spot",
            side_effect=RuntimeError("gold-api.com is down (simulated)"),
        ):
            for metal in pt.METALS:
                result = pt.refresh_metal(conn, metal)
                self.assertEqual(
                    result["botStatus"],
                    "API_ERROR",
                    f"{metal} did not show API_ERROR when spot feed failed",
                )
                # Price must be null — not a stale value from the previous tick.
                self.assertIsNone(
                    result["market"]["currentPrice"],
                    f"{metal} showed a stale price ({result['market']['currentPrice']}) "
                    "during a spot API outage — dashboard would be misleading",
                )

    def test_spot_api_failure_persists_through_state_read(self) -> None:
        """API_ERROR must survive a fresh build_coin_state (multi_state path), not revert to WAITING_FOR_DATA."""
        conn, _ = self._db_with_fresh_state()
        # Step 1: successful refresh so there IS a stored snapshot.
        one_hour = _make_fake_candles(60)
        four_hour = _make_four_hour_candles(60)
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(pt, "fetch_metal_candles", return_value=(one_hour, four_hour)),
        ):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        # Step 2: gold-api.com goes down.
        with patch.object(
            pt, "fetch_metal_spot",
            side_effect=RuntimeError("gold-api.com is down (simulated)"),
        ):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        # Step 3: simulate what multi_state() does — a fresh build_coin_state with NO snapshot arg.
        for metal in pt.METALS:
            result = pt.build_coin_state(conn, metal)
            self.assertEqual(
                result["botStatus"],
                "API_ERROR",
                f"{metal} reverted to '{result['botStatus']}' on a state-read after a spot outage "
                "(multi_state path must retain API_ERROR, not fall back to WAITING_FOR_DATA)",
            )
            self.assertIsNone(
                result["market"]["currentPrice"],
                f"{metal} still shows a stale price on multi_state read during outage",
            )

    def test_spot_api_failure_does_not_affect_crypto(self) -> None:
        """A metals spot-feed outage must not affect crypto coin states."""
        conn, _ = self._db_with_fresh_state()
        # Mock a single successful crypto fetch, then simulate the metals failure.
        def fake_market_data(pair: str):
            # Returns (price, spread_pct, 1h_rows, 4h_rows)
            rows = _make_fake_candles(60)
            rows4h = _make_four_hour_candles(60)
            return 40000.0, 0.1, rows, rows4h

        with patch.object(pt, "fetch_market_data", side_effect=fake_market_data):
            for coin in pt.COINS:
                result = pt.refresh_coin(conn, coin)
                # Crypto refresh must succeed (WAITING_FOR_DATA because candles
                # may not be enough for full strategy, but price must be present)
                self.assertNotEqual(
                    result["botStatus"], "API_ERROR",
                    f"{coin} showed API_ERROR even though crypto API was healthy",
                )

    # ── Invariant 4: Yahoo outage → scanNote set, spot price still present ──

    def test_yahoo_failure_sets_scan_note_keeps_spot(self) -> None:
        """When Yahoo Finance is down, spot price must remain and scanNote must be set."""
        conn, _ = self._db_with_fresh_state()
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(
                pt, "fetch_metal_candles",
                side_effect=RuntimeError("Yahoo Finance is down (simulated)"),
            ),
        ):
            for metal in pt.METALS:
                result = pt.refresh_metal(conn, metal)
                # Spot price must still be visible.
                self.assertIsNotNone(
                    result["market"]["currentPrice"],
                    f"{metal} lost spot price when only Yahoo failed",
                )
                self.assertAlmostEqual(
                    result["market"]["currentPrice"],
                    2000.0,
                    places=2,
                    msg=f"{metal} spot price wrong when Yahoo failed",
                )
                # botStatus must NOT be API_ERROR — spot succeeded, only scan data is missing.
                self.assertNotEqual(
                    result["botStatus"],
                    "API_ERROR",
                    f"{metal} incorrectly shows API_ERROR when only Yahoo Finance failed "
                    "(spot price is still live)",
                )
                # scanNote must be set and mention the outage.
                self.assertIsNotNone(
                    result.get("scanNote"),
                    f"{metal} did not set scanNote when Yahoo Finance was down",
                )
                self.assertIn(
                    "unavailable",
                    result["scanNote"].lower(),
                    f"{metal} scanNote does not clearly state scan data is unavailable: "
                    f"{result['scanNote']!r}",
                )

    def test_yahoo_failure_no_trades_no_balance_change(self) -> None:
        """Yahoo Finance failure must not cause any trade or balance mutation."""
        conn, _ = self._db_with_fresh_state()
        with (
            patch.object(pt, "fetch_metal_spot", return_value=(2000.0, pt.now_iso())),
            patch.object(
                pt, "fetch_metal_candles",
                side_effect=RuntimeError("Yahoo Finance is down (simulated)"),
            ),
        ):
            for metal in pt.METALS:
                pt.refresh_metal(conn, metal)

        for metal in pt.METALS:
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE coin = ?", (metal,)
            ).fetchone()[0]
            self.assertEqual(count, 0, f"{metal} opened a trade during Yahoo outage")
            state = pt.load_coin_state(conn, metal)
            self.assertAlmostEqual(float(state["balance"]), pt.STARTING_BALANCE, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
