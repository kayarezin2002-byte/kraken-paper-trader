---
name: Elliott Wave module
description: Design constraints and lessons from the Elliott/Fibonacci analytics module (Aug 2026)
---

# Elliott Wave + Fibonacci module

**Rule:** Elliott analysis is observation-only — it must never gate, veto, or alter ACTIVE/SCANNER entries or exits. Flags `elliott_score_influence` / `elliott_wave5_veto` in bot_config default OFF and are not consumed anywhere in execution paths; only the counterfactual `wave5VetoWouldBlock` is recorded on entries.
**Why:** The experiment's whole value is unbiased forward data comparing Elliott-aligned vs non-aligned trades; any influence contaminates it.
**How to apply:** Any future change that reads Elliott state inside an entry/exit decision needs an explicit user go-ahead and should only happen after the ELLIOTT LAB stats show a real edge.

Durable lessons:
- ZigZag seeding must track the *index* of extremes seen during the undecided phase, not just the price — otherwise the first pivot pairs an old price with a later timestamp and divergence checks compare wrong bars.
- Elliott is computed once per completed 15m candle inside scan_market (cached in the scanner snapshot between candles) to keep the 30-asset sweep fast; chart overlay is computed per request on the chart's own candles.
- £ ACTIVE entries reuse the scanner's cached Elliott snapshot (same coin, same data) instead of refetching.
- Trade context persists as a JSON `elliott` TEXT column on trades (ALTER TABLE migration, consistent with prior audit columns).
