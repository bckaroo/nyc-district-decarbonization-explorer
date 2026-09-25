# Development Log

Append-only log of each round of development on this repo (part of DevOS
project **PROJ-026**). New entries go at the top.

## Round 1 — <YYYY-MM-DD> · Initial commit
- Created via `devos init-repo`.

## Round 1 — 2026-09-25 (hermes)

- Registered in DevOS as PROJ-026; repoPath = this repo.
- Built missing web/src/app.css (build was failing on unresolved import) and
  produced web/dist via `npm run build` (1.24 MB bundle).
- Verified full stack live: FastAPI :3320 serves SPA at / (HTTP 200),
  /assets bundle 200, /api/snapshot {rows:1096, sha256 verified},
  /api/properties?q=Vanderbilt works.
- 63/63 pytest pass (unit + integration).
- Initial commit 92c3f4b (backend + tests + snapshot manifest + dist build
  inputs; dist itself gitignored per Vite default).
- Known follow-ups: no git remote yet (optional promote); port 3320 is dev
  range — register in ports.json if promoted to persistent service.

## Round 2 — 2026-09-25 (hermes)

- Chained footprints build into build_pilot.py: one command now rebuilds the
  full stack (LL84 snapshot+profile → footprints fetch/join → geojson layer).
  Online mode refetches footprints too; offline reuses + sha-verifies.
- Campus disaggregation: for the 53 properties with >1 footprint polygon,
  GHG/gas/electricity totals are area-weighted across polygons (spherical
  ring-sum areas in ft²). Intensities (EUI etc.) intentionally stay
  property-level. Per-feature flags: disagg=area-weighted, disagg_weight,
  disagg_n. Two bugs found+fixed en route: lat0 passed to cos() in degrees
  (areas computed 0), and disagg applied twice (weights squared).
  Conservation verified: worst error 0.0038% (GHG), 0.0000% (gas/elec).
- Promoted to private GitHub remote bckaroo/signalnyc (main, all commits
  pushed, doctor: OK, 0 dirty, 0 unpushed).
- Tests 63/63. Live on :3320 (tailscale serve active).
