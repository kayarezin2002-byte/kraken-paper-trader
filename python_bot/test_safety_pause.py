"""Tests for the consecutive-loss safety pause day-expiry logic.

Covers:
- Three losses on day 1 → paused on day 1, resumes on day 2
- A fourth loss on day 2 → day 2 is paused again
- A winning trade resets the streak (no pause)
"""

import sqlite3
import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trader as pt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    pt.init_db(conn)
    return conn


def insert_trade(conn: sqlite3.Connection, pnl: float, closed_date: str) -> None:
    """Insert a closed trade with the given PnL on the given UTC date."""
    conn.execute(
        """
        INSERT INTO trades
            (coin, opened_at, closed_at, direction, entry, exit,
             stop_loss, take_profit, rsi, macd, atr, trend_4h,
             profit_loss, account_balance, exit_reason)
        VALUES ('BTC', ?, ?, 'LONG', 100.0, ?, 90.0, 120.0,
                50.0, 0.001, 5.0, 'BULLISH', ?, ?, ?)
        """,
        (
            f"{closed_date}T10:00:00+00:00",
            f"{closed_date}T12:00:00+00:00",
            110.0 if pnl > 0 else 90.0,
            pnl,
            100.0 + pnl,
            "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS",
        ),
    )
    conn.commit()


def state_row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM coin_state WHERE coin='BTC'").fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStreakBlockDay(unittest.TestCase):

    def test_three_losses_set_streak_block_day_to_loss_date(self):
        """Three consecutive losses → streakBlockDay == their date."""
        conn = make_db()
        day = "2026-08-10"
        for _ in range(3):
            insert_trade(conn, -1.0, day)
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        self.assertEqual(m["consecutiveLosses"], 3)
        self.assertEqual(m["streakBlockDay"], day)

    def test_pause_active_on_same_day(self):
        """streak >= MAX and streakBlockDay == today → paused."""
        conn = make_db()
        day = "2026-08-10"
        for _ in range(3):
            insert_trade(conn, -1.0, day)
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        with patch("paper_trader.date_key", return_value=day):
            streak_paused = (
                m["consecutiveLosses"] >= pt.MAX_CONSECUTIVE_LOSSES
                and m.get("streakBlockDay") == day
            )
        self.assertTrue(streak_paused)

    def test_pause_expires_next_day(self):
        """Streak from day 1 does NOT pause entries on day 2."""
        conn = make_db()
        loss_day = "2026-08-10"
        next_day  = "2026-08-11"
        for _ in range(3):
            insert_trade(conn, -1.0, loss_day)
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        with patch("paper_trader.date_key", return_value=next_day):
            streak_paused = (
                m["consecutiveLosses"] >= pt.MAX_CONSECUTIVE_LOSSES
                and m.get("streakBlockDay") == next_day
            )
        self.assertFalse(streak_paused)

    def test_fourth_loss_on_day2_reapplies_pause(self):
        """A 4th consecutive loss on day 2 updates streakBlockDay to day 2."""
        conn = make_db()
        day1 = "2026-08-10"
        day2 = "2026-08-11"
        for _ in range(3):
            insert_trade(conn, -1.0, day1)
        insert_trade(conn, -1.0, day2)           # 4th loss on day 2
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        self.assertEqual(m["consecutiveLosses"], 4)
        # Block day must now be day2, not day1
        self.assertEqual(m["streakBlockDay"], day2)
        with patch("paper_trader.date_key", return_value=day2):
            streak_paused = (
                m["consecutiveLosses"] >= pt.MAX_CONSECUTIVE_LOSSES
                and m.get("streakBlockDay") == day2
            )
        self.assertTrue(streak_paused)
        # And day1 is no longer blocking
        with patch("paper_trader.date_key", return_value=day1):
            streak_paused_day1 = (
                m["consecutiveLosses"] >= pt.MAX_CONSECUTIVE_LOSSES
                and m.get("streakBlockDay") == day1
            )
        self.assertFalse(streak_paused_day1)

    def test_win_resets_streak(self):
        """A winning trade after losses resets consecutiveLosses to 0."""
        conn = make_db()
        day = "2026-08-10"
        for _ in range(3):
            insert_trade(conn, -1.0, day)
        insert_trade(conn, +5.0, day)            # winner
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        self.assertEqual(m["consecutiveLosses"], 0)
        self.assertIsNone(m["streakBlockDay"])

    def test_fewer_than_max_losses_no_block_day(self):
        """Two consecutive losses (< MAX) → streakBlockDay is None."""
        conn = make_db()
        day = "2026-08-10"
        for _ in range(2):
            insert_trade(conn, -1.0, day)
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        self.assertEqual(m["consecutiveLosses"], 2)
        self.assertIsNone(m["streakBlockDay"])

    def test_no_trades_no_streak(self):
        """Fresh account with no trades has no streak and no block day."""
        conn = make_db()
        m = pt.coin_metrics(conn, "BTC", state_row(conn))
        self.assertEqual(m["consecutiveLosses"], 0)
        self.assertIsNone(m["streakBlockDay"])


if __name__ == "__main__":
    unittest.main()
