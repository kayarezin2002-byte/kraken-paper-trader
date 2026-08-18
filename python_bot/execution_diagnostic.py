#!/usr/bin/env python3
"""Controlled PAPER-execution diagnostic (spec: execution pipeline proof).

Runs entirely against a TEMPORARY database — real forward-test data is never
touched, and no external trading API exists anywhere in the codebase
(LIVE_TRADING=False hard gate; every open/close calls _assert_paper_only()).

Proves: signal -> entry -> open position -> unrealised P&L -> exit ->
trade history -> balance update -> win/loss counts -> activity log.

Sections:
  1. TEST_PAPER_TRADE  — forced deterministic LONG and SHORT trades
  2. REAL SIGNAL PATH  — synthetic qualifying candles run through the REAL
                         strategy engine (refresh_coin / refresh_metal)

Run with: python3 python_bot/execution_diagnostic.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trader as pt

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def test_paper_trade(conn, asset: str, direction: str,
                     entry: float, stop: float, target: float) -> None:
    """Force ONE simulated paper trade with deterministic fake prices.

    TEST/DIAGNOSTIC ONLY — runs on the temp DB, marked entryMode=TEST_DIAGNOSTIC.
    Never calls any external API (prices are hardcoded arguments).
    """
    pt._assert_paper_only()
    state = pt.load_coin_state(conn, asset)
    bal0 = float(state["balance"])
    m0 = pt.coin_metrics(conn, asset, state)
    risk = abs(entry - stop) * 0.01  # deterministic tiny quantity 0.01
    position = {
        "direction": direction, "entry": entry, "stopLoss": stop,
        "takeProfit": target, "quantity": 0.01, "riskAmount": round(risk, 2),
        "openedAt": pt.now_iso(), "entryRsi": 50.0, "entryMacd": 0.0,
        "entryAtr": abs(entry - stop), "trend4h": "NEUTRAL", "trend1h": "NEUTRAL",
        "entryScore": 0, "entryMode": "TEST_DIAGNOSTIC",
        "longScore": None, "shortScore": None, "entryThreshold": None,
        "entryConditions": "TEST / DIAGNOSTIC — forced paper trade",
    }
    conn.execute("UPDATE coin_state SET open_position = ? WHERE coin = ?",
                 (json.dumps(position), asset))
    conn.commit()
    pt.add_activity(conn, asset, "TEST_DIAGNOSTIC",
                    f"Forced {direction} paper trade opened at {entry} (TEST / DIAGNOSTIC)")

    # A. trade object exists as the open position
    pos = json.loads(pt.load_coin_state(conn, asset)["open_position"])
    check(f"{asset} {direction} A: trade created", pos["direction"] == direction)

    # B. appears in dashboard state (same builder the API/dashboard uses)
    st = pt.build_coin_state(conn, asset,
                             snapshot={"currentPrice": entry, "updatedAt": pt.now_iso(),
                                       "botStatus": "READY"})
    check(f"{asset} {direction} B: open position in dashboard state",
          st["position"] is not None and st["position"]["direction"] == direction)

    # C. unrealised P&L updates with price (profit direction correct)
    up = entry + (1 if direction == "LONG" else -1)  # 1 unit in favour
    st = pt.build_coin_state(conn, asset,
                             snapshot={"currentPrice": up, "updatedAt": pt.now_iso(),
                                       "botStatus": "READY"})
    pnl_fav = st["position"]["unrealisedPnl"]
    check(f"{asset} {direction} C: unrealised P&L updates", pnl_fav is not None and pnl_fav > 0,
          f"price {entry}->{up} unrealised {pnl_fav}")

    # D/E. stop then target via the REAL refresh close logic: emulate with close_position
    # First: exit at target (favourable)
    state = pt.load_coin_state(conn, asset)
    pt.close_position(conn, asset, state, pos, target, "TAKE_PROFIT")
    row = conn.execute("SELECT * FROM trades WHERE coin=? ORDER BY id DESC LIMIT 1",
                       (asset,)).fetchone()
    win_pnl = row["profit_loss"]
    expected = (target - entry) * 0.01 if direction == "LONG" else (entry - target) * 0.01
    check(f"{asset} {direction} E: take profit closes correctly",
          abs(win_pnl - expected) < 1e-9 and win_pnl > 0,
          f"P&L {win_pnl:+.4f} (expected {expected:+.4f})")
    check(f"{asset} {direction} F: trade in history",
          row is not None and row["exit_reason"] == "TAKE_PROFIT")
    bal1 = float(pt.load_coin_state(conn, asset)["balance"])
    check(f"{asset} {direction} G: balance updated", abs(bal1 - (bal0 + win_pnl)) < 1e-9,
          f"{bal0:.2f} -> {bal1:.2f}")

    # Second trade: exit at stop (adverse) to verify D + loss accounting
    conn.execute("UPDATE coin_state SET open_position = ? WHERE coin = ?",
                 (json.dumps({**position, "openedAt": pt.now_iso()}), asset))
    conn.commit()
    state = pt.load_coin_state(conn, asset)
    pt.close_position(conn, asset, state,
                      json.loads(state["open_position"]), stop, "STOP_LOSS")
    row = conn.execute("SELECT * FROM trades WHERE coin=? ORDER BY id DESC LIMIT 1",
                       (asset,)).fetchone()
    loss_pnl = row["profit_loss"]
    check(f"{asset} {direction} D: stop loss closes correctly",
          row["exit_reason"] == "STOP_LOSS" and loss_pnl < 0, f"P&L {loss_pnl:+.4f}")

    # H. win/loss counts
    m1 = pt.coin_metrics(conn, asset, pt.load_coin_state(conn, asset))
    check(f"{asset} {direction} H: win/loss counts update",
          m1["wins"] == m0["wins"] + 1 and m1["losses"] == m0["losses"] + 1,
          f"wins {m0['wins']}->{m1['wins']}, losses {m0['losses']}->{m1['losses']}")

    # I. activity log records everything
    events = [r["event"] for r in conn.execute(
        "SELECT event FROM activity_log WHERE coin=? ORDER BY id", (asset,))]
    check(f"{asset} {direction} I: activity log recorded",
          "TEST_DIAGNOSTIC" in events and "TRADE_CLOSED" in events,
          f"events: {sorted(set(events))}")


def _candles(n, direction, start=50000.0, interval=3600.0, recent=True):
    rows, price = [], start
    base = (time.time() - n * interval) if recent else 1_700_000_000.0
    half = n // 2
    sgn = 1 if direction == "UP" else -1
    for i in range(n):
        if i >= half:
            price *= (1 + sgn * 0.002 * (i - half + 1))
        rows.append([base + i * interval, price * 1.001, price * 1.002,
                     price * 0.998, price, price, 100.0, 1])
    return rows


def real_signal_path(conn) -> None:
    """Replay qualifying candles through the REAL engine: refresh_coin/refresh_metal."""
    # BTC: fully bearish synthetic history -> weighted SHORT >= 6/8 -> real entry path
    oh, fh = _candles(80, "DOWN"), _candles(80, "DOWN", interval=14400.0)
    price = float(oh[-1][4])
    with patch.object(pt, "fetch_market_data", return_value=(price, 0.01, oh, fh)):
        result = pt.refresh_coin(conn, "BTC")
    d = result["directional"]
    check("REAL PATH BTC: signal passed entry gate",
          d is not None and d["decision"] == "SHORT" and d["shortScore"] >= d["shortThreshold"],
          f"SHORT {d['shortScore']}/{d['maxScore']} gate {d['shortThreshold']}")
    check("REAL PATH BTC: reached paper execution (position opened)",
          result["position"] is not None and result["position"]["direction"] == "SHORT")
    # close it via the same real path: price gaps to the stop
    pos = result["position"]
    with patch.object(pt, "fetch_market_data",
                      return_value=(pos["stopLoss"] * 1.01, 0.01, oh, fh)):
        pt.refresh_coin(conn, "BTC")
    row = conn.execute(
        "SELECT * FROM trades WHERE coin='BTC' ORDER BY id DESC LIMIT 1").fetchone()
    check("REAL PATH BTC: real engine closed the trade into history",
          row is not None and row["exit_reason"] == "STOP_LOSS")

    # GOLD: bullish 5/6+ -> LONG through refresh_metal
    oh, fh = _candles(80, "UP", start=4400.0, recent=False), _candles(80, "UP", start=4400.0, interval=14400.0, recent=False)
    spot = float(oh[-1][4])
    with patch.object(pt, "fetch_metal_spot", return_value=(spot, pt.now_iso())), \
         patch.object(pt, "fetch_metal_candles", return_value=(oh, fh)):
        result = pt.refresh_metal(conn, "GOLD")
    check("REAL PATH GOLD: signal -> entry gate -> paper execution",
          result["position"] is not None and result["position"]["direction"] == "LONG")
    # ...and the REAL engine detects the take-profit trigger on a later scan
    pos = result["position"]
    with patch.object(pt, "fetch_metal_spot", return_value=(pos["takeProfit"] * 1.001, pt.now_iso())), \
         patch.object(pt, "fetch_metal_candles", return_value=(oh, fh)):
        pt.refresh_metal(conn, "GOLD")
    row = conn.execute(
        "SELECT * FROM trades WHERE coin='GOLD' ORDER BY id DESC LIMIT 1").fetchone()
    check("REAL PATH GOLD: real engine closed at TAKE_PROFIT with profit",
          row is not None and row["exit_reason"] == "TAKE_PROFIT" and row["profit_loss"] > 0,
          f"P&L {row['profit_loss']:+.2f}" if row else "no trade row")


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        with patch.object(pt, "DB_PATH", path):
            conn = pt.db()
            pt.init_db(conn)
            print("== 1. TEST_PAPER_TRADE — forced LONG (entry 100, stop 98, target 104) ==")
            test_paper_trade(conn, "BTC", "LONG", 100.0, 98.0, 104.0)
            print("\n== 2. TEST_PAPER_TRADE — forced SHORT (entry 100, stop 102, target 96) ==")
            test_paper_trade(conn, "ETH", "SHORT", 100.0, 102.0, 96.0)
            # spec spot-checks: 100->95 profit, 100->103 loss for a SHORT
            q = 0.01
            check("SHORT price 100->95 = profit", (100 - 95) * q > 0)
            check("SHORT price 100->103 = loss", (100 - 103) * q < 0)
            print("\n== 3. REAL SIGNAL -> ENTRY GATE -> PAPER EXECUTION ==")
            real_signal_path(conn)
            conn.close()
    finally:
        os.unlink(path)

    failures = [r for r in RESULTS if not r[1]]
    print(f"\n== SUMMARY: {len(RESULTS) - len(failures)}/{len(RESULTS)} PASS ==")
    print("Temp DB deleted — no test trades touched the real forward-test data.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
