# Implementation roadmap (after the 2026-11-08 sprint)

Ordered by demo impact, then risk. Each row says what unblocks it and who must act. Items that only the owner can unblock are marked **(owner)**.

| # | Item | Why it matters | Unblocked by | Effort |
|---|---|---|---|---|
| 1 | Review ~60 pool + ~60 building samples in the Validation Studio | First human-measured precision with geographic holdouts. Lifts the flagship headline from "weak" once derived v1 precision is known. | a human reviewer **(owner)** | 1–2 h of reviewing |
| 2 | Complete the 6 census tiles in the Studio | Honest recall for pools and buildings | a human reviewer **(owner)** | 1 h |
| 3 | Install Foursquare / Yelp keys in `.env` (never in chat) and run the live tests | Turns the provider integrations from mocked to live; address geocoding becomes possible | keys **(owner)**; then `pytest -q tests/test_places_providers.py -k live` | 15 min |
| 4 | Yelp display requirements (logo, stars, unmodified links) before any Yelp content is shown | Terms compliance | after #3 | 2 h |
| 5 | Decide on Google (owner rule "no Google data" vs the brief; non-Google-map terms) | Address validation / geocoding | APPROVAL_REQUIRED.md **(owner/counsel)** | — |
| 6 | Capture FEMA FIRM effective dates into the evidence | Flagship evidence completeness | re-fetch the NFHL metadata (small) | 1 h |
| 7 | Fix envelope source dates (Overture empty, OSM by file mtime) | Provenance correctness | `providers/public.py:389/394` | 30 min |
| 8 | Seam mosaic (dodging + feathering) for the demo areas | Visual polish | laptop build (`scripts/heavy/build_mosaic.py`) | 3–4 h |
| 9 | Solar verifier (34k raw objects in Huntersville) | Solar is unusable as is | measured against Studio solar reviews | 3 h |
| 10 | API auth (tailnet identity headers via `tailscale serve --https`) + an owner check on investigations | Security | SECURITY_AUDIT patches | 2 h |
| 11 | Show similarity in the sheet for "Find like this" results | Explainability | UI | 30 min |
| 12 | Extend DEMO_MANIFEST with derived/risk/lidar v2/boundaries/validation | Rollback safety | `scripts/demo_manifest.py` | 30 min |
| 13 | Canopy + slope rasters for the lidar areas | "Pools under trees", steep lots | laptop job (`scripts/heavy/jobs/t4_risk.sh`) | 2 h |
| 14 | Newer imagery | Freshness | NAIP 2024 availability, or NC OneMap terms (A3) **(owner)** | — |
