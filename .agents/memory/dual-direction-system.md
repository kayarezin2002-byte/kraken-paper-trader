---
name: Dual-direction evaluation system
description: LONG and SHORT are scored independently every scan across all 6 assets (Aug 2026); decision rules and constraints.
---

# Dual-direction evaluation (Aug 2026)

All 6 assets evaluate LONG and SHORT condition sets independently every scan
(`evaluate_conditions` returns `long`/`short` blocks; no bias pre-selection
from trends). Decision = whichever direction independently meets its gate:

- Crypto: weighted score ≥ 6/8, mode TREND, no opposing clear 4h trend (unchanged thresholds — user explicitly said NOT to loosen crypto yet).
- Metals: any 5/6 pass count per direction, no 4h-opposition rule.
- Both directions qualifying at once is structurally impossible (conditions are mutually exclusive except Volume).

**Rules the user set:** max ONE position per asset; different assets may hold
opposite directions simultaneously; a strong opposite signal while a position
is open is logged (`OPPOSITE_SIGNAL` activity event) but NEVER auto-reversed.
SHORT conditions are genuine bearish checks, not a naive inversion.

`StrategyConditions` in the OpenAPI spec gained optional `long`/`short`
(DirectionEval) and `decision` fields — any new snapshot field must be added
to `lib/api-spec/openapi.yaml` + codegen or the generated Zod schemas strip it.

**Test lesson:** synthetic trend candles for tests must accelerate in
ABSOLUTE terms or MACD converges onto its signal line and trend conditions
fail — a constant-percentage decay flattens MACD (see
`python_bot/test_dual_direction.py::_series`, which mirrors an accelerating
up-series for the down case).

Safety-pause day-expiry (3 losses → paused until next UTC midnight, auto-resumes)
was confirmed fixed and is covered by `python_bot/test_safety_pause.py`.
