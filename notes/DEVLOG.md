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

## Round 3 — 2026-09-25 (hermes) · DEV-154: map layer fix finalized

- Fix summary (carried over from Rounds 2/3 work): `web/vite.config.ts` now
  copies BOTH `maplibre-gl-worker.mjs` AND `maplibre-gl-shared.mjs` into
  `dist/assets` — the worker statically imports the shared chunk, so with only
  the worker copied the import 404'd (HTML fallthrough) and no tiles/polygons
  ever parsed. `MapPanel.tsx`: idempotent layer init (guard against duplicate
  sources from load + fallback timer), latest-props ref for async layer
  attachment, null-safe EUI color (missing EUI drawn gray, not dropped), and
  surfaced load errors instead of silent catch.
- Regression test added: `tests/unit/test_static_assets.py`
  asserts the built worker's relative `import`/`import()` specifiers resolve to
  real JS files in `dist/assets` (not just that config strings exist). Skips
  cleanly if `web/dist` is not built.
- Verification: production `npm run build` OK (1.24 MB bundle built in 1.21s);
  `dist/assets/{maplibre-gl-worker.mjs, maplibre-gl-shared.mjs}` present;
  pytest 65/65 pass (63 pre-existing + 2 new asset tests);
  live service :3320 serves `/` 200 text/html, `/assets/maplibre-gl-worker.mjs`
  200 text/javascript, `/assets/maplibre-gl-shared.mjs` 200 text/javascript.
- Desktop visual evidence (obtained in prior round): colored outlined teal /
  green / yellow / orange building polygons render in Midtown
  (evidence/postfix_desktop.png).
- Honest limitation: mobile rendering and footprint-selection interaction not
  visually verified in this round; polygon paint/execution verified via API
  asset checks + desktop screenshot only.
- Manifest `data/snapshots/footprints_joined.manifest.json` timestamp bumped by
  an unrelated run — not included in the DEV-154 commit.

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

## Roadmap note — 2026-09-25 (Andrew)

What we have right now is a **demand model**: annual heating/DHW/cooling
demand per footprint, net thermal, evidence-tiered. Useful for screening, but
it is only one side of the ledger. Two new layers are needed:

1. **Thermal generation layer (supply side).** Anchor loads and sources that
   could serve as generation/distribution anchors for district thermal
   networks:
   - hospitals
   - data centers (large, always-on thermal loads)
   - subway infrastructure — tunnels and underground stations (ventilation
     heat rejection, station heating demand)
   - wastewater treatment plants (sewage-heat-recovery potential)
   - etc. — other large thermal anchors to be identified as we go.

2. **Borehole / GSHX opportunity layer.** Open surfaces suitable for ground
   heat exchanger drilling fields:
   - parking lots
   - plazas
   - parks
   - other open space

Neither layer exists yet — both need source data identification and a
scoring/siting methodology before build. Eligibility screening (air quality,
distance to demand, load coherence) comes after the raw layers land.
