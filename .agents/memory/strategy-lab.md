---
name: Strategy Lab
description: Design constraints and lessons from the shadow-strategy Strategy Lab (Aug 2026)
---

# Strategy Lab (shadow strategies)

**Rule:** The lab is simulation-only. It writes only `lab_*` tables and activity records; no execution path may read lab results. Promotion is a human decision — the UI flags "CANDIDATE FOR PROMOTION", never auto-applies.
**Why:** The lab exists to find a repeatable statistical edge from unbiased forward data; letting it steer live entries would contaminate the experiment (same principle as the Elliott module).
**How to apply:** Any change wiring lab output into an entry/exit decision needs explicit user approval.

Durable lessons:
- One observation feeds all experiments: entry thresholds (3/6–6/6), the five risk levels, Elliott filters, and regime buckets are applied mathematically at query time. Only exit profiles need actual shadow trades (signals × 5 profiles).
- Bar-by-bar exit sims must use the candle OPEN for gap fills — a stop crossed at the open fills at that adverse open, never at the untradable stop price; intrabar crossings fill at the stop, stop-first ordering.
- Any concurrency/overlap analysis must use simulated candle timestamps (`last_ts`), never wall-clock `closed_at` — scans process results long after the simulated exit bar.
- Fees + spread are subtracted per trade (net vs gross kept separately); scalp-sized targets (0.3–0.5%) are typically net-negative at 0.26%/side — that is the finding, not a bug.
- Milestone/leader notifications are deduped via bot_config flags and run once per full scan, never per candle.
