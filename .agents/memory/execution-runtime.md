---
name: Execution runtime & scan scheduler
description: How strategy scans actually run, and the concurrency rule for bot invocations
---

- Strategy scans run server-side: the API server has a background scheduler (120s interval) calling the bot's multi-refresh — the browser does NOT need to be open. Before Aug 2026 scans only ran while the dashboard polled, which was the root cause of "zero trades ever".
- **Rule:** every bot CLI invocation from the API server must go through the single serialized `runBot` queue (shared lib). **Why:** each invocation is a separate Python process on one SQLite file; concurrent writers race entry decisions and hit "database is locked". SQLite busy_timeout is defense-in-depth only.
- Continuous scanning in production requires an always-on deployment (Reserved VM); autoscale/dev workflows stop when idle.
- Execution pipeline is proven end-to-end by `python_bot/execution_diagnostic.py` (temp DB, forced + real-signal-path trades incl. SL/TP through refresh_*).
- Metals (Yahoo COMEX futures) legitimately report volume=0 in some hours (quiet/settlement rows) — data quirk, not a bug; it fails the Volume condition only when the 20-period average is > 0.
