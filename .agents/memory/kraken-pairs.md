---
name: Kraken GBP pair names
description: Verified Kraken REST API pair names for the four paper-traded coins against GBP
---

Verified correct Kraken public API pair names (as of Aug 2026):

| Coin | Pair name | Notes |
|------|-----------|-------|
| BTC  | XXBTZGBP  | Standard Kraken legacy format |
| ETH  | XETHZGBP  | Standard Kraken legacy format |
| SOL  | SOLGBP    | No X prefix or Z suffix |
| XRP  | XRPGBP    | NOT XXRPZGBP — just XRPGBP |

**Why:** XRP's pair was initially set to XXRPZGBP (following BTC/ETH convention) but Kraken uses XRPGBP. This caused API_ERROR status on all XRP refreshes.

**How to apply:** Any future coin additions should verify the exact pair name via `GET /0/public/AssetPairs` before hardcoding.
