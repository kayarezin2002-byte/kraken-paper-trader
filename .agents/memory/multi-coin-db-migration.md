---
name: Multi-coin DB migration
description: How the SQLite schema migration from single-coin to multi-coin works, and a key gotcha
---

The paper trader migrated from a single-coin `bot_state` table to a multi-coin `coin_state` table.

**Key gotcha:** The `trades` table already existed in the DB without a `coin` column. `CREATE TABLE IF NOT EXISTS trades (coin TEXT NOT NULL, ...)` does NOT add the column to the existing table — it's a no-op. The migration must explicitly run:

```python
trades_cols = [row[1] for row in connection.execute("PRAGMA table_info(trades)").fetchall()]
if "coin" not in trades_cols:
    connection.execute("ALTER TABLE trades ADD COLUMN coin TEXT NOT NULL DEFAULT 'BTC'")
```

This is already in `init_db` in `python_bot/paper_trader.py`.

**Why:** SQLite's `CREATE TABLE IF NOT EXISTS` skips the entire statement when the table exists, so schema additions require `ALTER TABLE ADD COLUMN`.

**How to apply:** Any future schema additions to existing tables must use `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, not `CREATE TABLE IF NOT EXISTS` with the new column.
