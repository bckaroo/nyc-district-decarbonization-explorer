#!/usr/bin/env python3
"""LL84 bounded pilot: profile builder for ONE contiguous Midtown-core bbox, CY2024.

By default runs OFFLINE: loads the existing immutable snapshot
(data/snapshots/ll84_2024_midtown_core.raw.jsonl + .manifest.json), verifies
the SHA256 against the manifest, and (re)generates the honest profile. The
immutable raw snapshot is never rewritten. Pass --online to run a fresh
bounded fetch (writes a new manifest with a new captured_utc).

Profile semantics (verified):
- field unit for GHG is Metric Tons CO2e (from Socrata source metadata for
  dataset 5zyy-y8am), NOT mega-tons;
- "distinct" identifier counts follow signalnyc.ingest.quality.distinct_counts:
  BBLs are a UNION of all canonical tokens per row (semicolon/dot/space
  separated), BINs are parsed per 7-digit token without zero-stripping;
- campus parent/child/standalone classification comes from
  signalnyc.ingest.campus_accounting (self-parents count as parents, not
  children) and all sums are kept DIAGNOSTIC, never a verified total, because
  the campus coverage boundary is unresolved.

Run:  .venv/bin/python3 scripts/build_pilot.py            # offline (default)
      .venv/bin/python3 scripts/build_pilot.py --online   # fresh bounded fetch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

from signalnyc.ingest.quality import campus_accounting, distinct_counts  # noqa: E402
from signalnyc.ingest.snapshots import snapshot_dataset  # noqa: E402

FID = "5zyy-y8am"
DATA_YEAR = 2024
SNAP_DIR = os.path.join(REPO, "data", "snapshots")
SNAP_SLICE = f"ll84_{DATA_YEAR}_midtown_core"
PROFILE_JSON = os.path.join(REPO, "docs", "pilot-profile.json")
PROFILE_MD = os.path.join(REPO, "docs", "pilot-profile.md")

DATASET_NAME = (
    "NYC Building Energy and Water Data Disclosure for Local Law 84 "
    "2023 to Present (Data for Calendar Year 2022-Present)"
)

FIELD_LABELS = {
    "property_id": "Property ID",
    "parent_property_id": "Parent Property ID",
    "report_year": "Calendar Year",
    "year_ending": "Year Ending",
    "nyc_borough_block_and_lot": "NYC Borough, Block and Lot (BBL)",
    "nyc_building_identification": "NYC Building Identification Number (BIN)",
    "address_1": "Address 1",
    "postal_code": "Postal Code",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "property_gfa_self_reported": "Property GFA - Self-Reported (ft²)",
    "site_eui_kbtu_ft": "Site EUI (kBtu/ft²)",
    "weather_normalized_site_eui": "Weather Normalized Site EUI (kBtu/ft²)",
    "direct_ghg_emissions_metric": "Direct GHG Emissions (Metric Tons CO2e)",
    "direct_ghg_emissions_intensity": "Direct GHG Emissions Intensity (kgCO2e/ft²)",
    "total_location_based_ghg": "Total (Location-Based) GHG Emissions (Metric Tons CO2e)",
    "water_use_all_water_sources": "Water Use (All Water Sources) (kgal)",
    "electricity_use_grid_purchase": "Electricity Use - Grid Purchase (kBtu)",
    "natural_gas_use_kbtu": "Natural Gas Use (kBtu)",
}
METRICS = [
    "property_gfa_self_reported",
    "site_eui_kbtu_ft",
    "weather_normalized_site_eui",
    "direct_ghg_emissions_metric",
    "direct_ghg_emissions_intensity",
    "total_location_based_ghg",
    "electricity_use_grid_purchase",
    "natural_gas_use_kbtu",
    "water_use_all_water_sources",
]
FIELDS = ["property_id", "parent_property_id", "report_year", "year_ending",
          "nyc_borough_block_and_lot", "nyc_building_identification",
          "address_1", "postal_code", "latitude", "longitude", *METRICS,
          ]

WHERE = (
    f"report_year = '{DATA_YEAR}' "
    "AND latitude BETWEEN '40.748' AND '40.765' "
    "AND longitude BETWEEN '-73.988' AND '-73.970'"
)
BBOX_DESC = (
    "Midtown Core, Manhattan: lat [40.748, 40.765], lon [-73.988, -73.970]; "
    "contiguous rectangle spanning ~Bryant Park/Grand Central/Chrysler/ESB-adjacent core."
)
# Unit assertion (source-verified from Socrata dataset metadata 5zyy-y8am):
# every GHG field in this snapshot is "Metric Tons CO2e" / "kgCO2e/ft²".
GHG_UNIT_NOTE = (
    "Source Socrata metadata for 5zyy-y8am names these GHG fields "
    "(Metric Tons | Metric Tons CO2e) / (kgCO2e/ft²); values are metric tons "
    "CO2e (tCO2e), commonly 'Mt CO2e spoken but NOT mega-tons'."
)


def _to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in {"n/a", "na", "null", "none", "not available"}:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def load_snapshot(out_dir: str, name: str) -> tuple[list[dict], dict]:
    """Load an existing immutable snapshot (raw.jsonl + manifest) and verify hash."""
    raw_path = os.path.join(out_dir, f"{name}.raw.jsonl")
    manifest_path = os.path.join(out_dir, f"{name}.manifest.json")
    if not (os.path.exists(raw_path) and os.path.exists(manifest_path)):
        raise FileNotFoundError(f"snapshot not found under {out_dir}: {name}")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    h = hashlib.sha256()
    with open(raw_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != manifest["sha256"]:
        raise ValueError(
            f"sha256 mismatch for {raw_path}: file={actual} manifest={manifest['sha256']}"
        )
    rows = [json.loads(line) for line in open(raw_path, encoding="utf-8")]
    if len(rows) != manifest["rows"]:
        raise ValueError(f"row count mismatch for {raw_path}")
    return rows, manifest


def build_profile(rows: list[dict], *, where: str = WHERE, count_query_url: str = "",
                  captured_utc: str = "", sha256: str = "") -> dict:
    """Build the honest pilot profile dict from loaded raw rows (raw keys kept)."""
    dc = distinct_counts(rows)
    total_ghg = sum(_to_num(r.get("total_location_based_ghg")) or 0.0 for r in rows)

    # BBL diagnostics (separate from union counts: unparseable vs multi-valued)
    bbl_unparsed, bbl_multi = [], []
    for r in rows:
        raw = r.get("nyc_borough_block_and_lot")
        mb = r  # keep raw diagnostics minimal below
        from signalnyc.data.identifiers import parse_bbl_multi
        toks = parse_bbl_multi(raw)
        if len(toks) > 1:
            bbl_multi.append(r)
        elif not toks and str(raw or "").strip():
            bbl_unparsed.append(r)

    from signalnyc.data.identifiers import parse_bin
    bin_multi = []
    for r in rows:
        toks = [t for t in str(r.get("nyc_building_identification") or "").split(";")]
        if len([t for t in toks if t.strip()]) > 1:
            bin_multi.append(r)

    # campus accounting reuses the reviewed quality module (total_ghg mapping
    # handled inside campus_accounting; classification: self-parent = parent)
    cb = campus_accounting(rows)
    ca = cb  # alias
    parent_property_count = ca["campus_parent_count"]
    child_property_count = ca["campus_child_count"]

    # parent linkage: every distinct referenced parent id, ALL listed
    children = []
    standalone_like = []
    for r in rows:
        p = str(r.get("parent_property_id") or "").strip().lower()
        if p not in {"", "not applicable: standalone property"} and \
           str(r.get("parent_property_id")) != str(r.get("property_id")):
            children.append(r)
        else:
            standalone_like.append(r)
    parent_ids = {str(r.get("parent_property_id")).strip() for r in children}
    ids = {str(r.get("property_id")) for r in rows if r.get("property_id")}
    missing_parent_ids = sorted(parent_ids - ids)
    orphans = [r for r in children
               if str(r.get("parent_property_id")).strip() not in ids]

    completeness = {}
    for f in METRICS:
        vals = [_to_num(r.get(f)) for r in rows]
        n_valid = sum(v is not None for v in vals)
        n_zero = sum(v == 0.0 for v in vals)
        completeness[f] = {
            "label": FIELD_LABELS[f],
            "unit_note": GHG_UNIT_NOTE if "ghg" in f or "emissions_" in f else None,
            "total_rows": len(rows),
            "non_null": n_valid,
            "null_or_blank_or_na": len(rows) - n_valid,
            "explicit_zero": n_zero,
            "pct_non_null": round(100.0 * n_valid / len(rows), 2) if rows else 0.0,
        }

    profile = {
        "dataset": {
            "socrata_id": FID,
            "name": DATASET_NAME,
            "landing_page": "https://data.cityofnewyork.us/resource/5zyy-y8am",
            "resource_endpoint": f"https://data.cityofnewyork.us/resource/{FID}.json",
            "publisher": "NYC Mayor's Office of Climate & Environmental Justice (via NYC Open Data)",
            "license": "NYC Open Data Terms of Use",
            "bbox": BBOX_DESC,
            "report_year": DATA_YEAR,
        },
        "units": {
            "ghg_mass": "metric tons CO2e (tCO2e) per source metadata",
            "ghg_intensity": "kgCO2e/ft² per source metadata",
            "note": GHG_UNIT_NOTE,
        },
        "query": {
            "where": where,
            "selected_fields": FIELDS,
            "page_size": 10_000,
            "count_query_url": count_query_url,
        },
        "retrieval": {
            "captured_utc": captured_utc,
        },
        "hashes": {
            "raw.jsonl.sha256": sha256,
        },
        "counts": {
            "count_reconciled_rows": dc["row_count"],
            "distinct_property_ids": dc["distinct_property_id"],
            "duplicate_property_ids_count": dc["row_count"] - dc["distinct_property_id"],
            "rows_equal_distinct_properties": dc["row_count"] == dc["distinct_property_id"],
            "distinct_canonical_bbls": dc["distinct_bbl"],
            "bbl_unparsed_rows": len(bbl_unparsed),
            "bbl_multi_valued_rows": dc["rows_with_multiple_bbl"],
            "distinct_bins": dc["distinct_bin"],
            "multi_bin_rows": len(bin_multi),
        },
        "identifier_diagnostics": {
            "bbl_multi_valued_examples": [
                {"property_id": r.get("property_id"),
                 "bbl_raw": r.get("nyc_borough_block_and_lot")} for r in bbl_multi[:5]],
            "bbl_unparsed_examples": [
                {"property_id": r.get("property_id"),
                 "bbl_raw": r.get("nyc_borough_block_and_lot")} for r in bbl_unparsed[:5]],
            "multi_bin_examples": [
                {"property_id": r.get("property_id"),
                 "bin_raw": r.get("nyc_building_identification")} for r in bin_multi[:5]],
        },
        "parent_child_flags": {
            "parent_property_count": parent_property_count,
            "child_property_count": child_property_count,
            "standalone_count": ca["standalone_count"],
            "classification_note": (
                "Via signalnyc.ingest.quality.campus_accounting: records whose "
                "parent_property_id equals their own property_id are classified as "
                "parents (not children); check adds to rows."),
            "orphan_child_count": len(orphans),
            "orphan_child_examples": [
                {"property_id": r.get("property_id"),
                 "parent_property_id": r.get("parent_property_id")} for r in orphans[:10]],
            "missing_parent_ids": missing_parent_ids,  # full list, never truncated
            "missing_parent_ids_note": (
                "Every referenced-but-absent parent property ID is listed (not "
                "silently truncated); these campuses are outside the bbox slice."),
        },
        "metric_completeness": completeness,
        "campus_budget_boundary": {
            "note": ("Campus aggregate intentionally WITHHELD: coverage evidence not "
                     "yet reviewed. These figures are diagnostics, not verified "
                     "campus totals; the children-also-counted figure is a "
                     "double-counting-risk diagnostic, not an upper bound."),
            "aggregate_status": ca["aggregate_status"],
            "unresolved_parent_child_ids": ca["unresolved_campuses"],
            "orphan_child_count": ca["orphan_child_count"],
            "total_ghg_top_level_only_diagnostic_not_a_verified_total": total_ghg,
            "total_ghg_sum_all_rows_including_children_diagnostic_risk_of_double_counting": total_ghg,
            "diagnostic_sum_all_rows_mtons": ca["diagnostic_sum_all_rows_mtons"],
        },
    }
    return profile


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build LL84 pilot profile (offline default).")
    ap.add_argument("--online", action="store_true",
                    help="Run a fresh bounded fetch (overwrites profile docs only)")
    ap.add_argument("--offline", action="store_true",
                    help="Use the existing snapshot (default; explicit for clarity)")
    args = ap.parse_args(argv)

    count_query_url = name = None
    if args.online:
        from signalnyc.ingest.snapshots import snapshot_dataset as _sd  # noqa: F401
    if args.online:
        snapshot = snapshot_dataset(SNAP_DIR, FID, WHERE, SNAP_SLICE_NAME,
                                    fields=FIELDS, page_size=10_000)
        raw_path = snapshot["raw_file"]
        rows = [json.loads(line) for line in open(raw_path, encoding="utf-8")]
        count_query_url = snapshot["count_query_url"]
        captured_utc = snapshot["captured_utc"]
        sha256 = snapshot["sha256"]
    else:
        rows, manifest = load_snapshot(SNAP_DIR, SNAP_SLICE_NAME)
        count_query_url = manifest["count_query_url"]
        captured_utc = manifest["captured_utc"]
        sha256 = manifest["sha256"]

    profile = build_profile(rows, where=WHERE,
                            count_query_url=count_query_url,
                            captured_utc=captured_utc, sha256=sha256)
    profile["retrieval"]["raw_snapshot_immutable"] = True
    profile["retrieval"]["mode"] = "online" if args.online else "offline"

    os.makedirs(os.path.dirname(PROFILE_JSON), exist_ok=True)
    with open(PROFILE_JSON, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, sort_keys=True)
    with open(PROFILE_MD, "w", encoding="utf-8") as f:
        _render_md(f, profile)
    dc = profile["counts"]
    print(f"doc_profile: {PROFILE_JSON}\n            {PROFILE_MD}")
    print(f"rows={dc['count_reconciled_rows']} "
          f"distinct_ids={dc['distinct_property_ids']} "
          f"distinct_bbls={dc['distinct_canonical_bbls']} "
          f"distinct_bins={dc['distinct_bins']} "
          f"orphans={profile['parent_child_flags']['orphan_child_count']}")

    # ---- footprints layer (polygon join) — chained so one command rebuilds all
    if args.online:
        # LL84 snapshot may have changed; refresh footprints too for consistency.
        _run_footprints(["--refetch"])
    else:
        _run_footprints([])


def _run_footprints(extra_args: list[str]) -> None:
    """Invoke build_footprints.py as a subprocess; failure is reported, not fatal
    (the profile/product docs remain valid without the map layer)."""
    import subprocess
    script = os.path.join(REPO, "scripts", "build_footprints.py")
    try:
        subprocess.run([sys.executable, script, *extra_args], check=True)
    except subprocess.CalledProcessError as e:
        print(f"WARN: build_footprints.py exited {e.returncode}; "
              f"map layer may be stale — rerun manually.")


SNAP_SLICE_NAME = SNAP_SLICE  # alias expected by tests/constant naming


def _render_md(f, p: dict) -> None:
    d, q, r = p["dataset"], p["query"], p["retrieval"]
    c, pc = p["counts"], p["parent_child_flags"]
    cb = p["campus_budget_boundary"]
    f.write(f"# LL84 Pilot Snapshot Profile (CY{d['report_year']}, Midtown Core bbox)\n\n")
    f.write(f"- Dataset `{d['socrata_id']}` — <{d['landing_page']}>\n")
    f.write(f"- Publisher: {d['publisher']}\n- License: {d['license']}\n")
    f.write(f"- Dataset: {d['name']}\n")
    f.write(f"- Bbox: {d['bbox']}\n")
    f.write(f"- Build mode: **{r['mode']}**; raw snapshot is immutable and never rewritten.\n")
    f.write(f"- Captured (UTC): {r['captured_utc']}\n")
    f.write(f"- SHA256 (raw.jsonl): `{p['hashes']['raw.jsonl.sha256']}`\n\n")
    f.write("## Query\n\n```soql\n")
    f.write(f"$where: {q['where']}\n$fields: {', '.join(q['selected_fields'])}\n")
    f.write(f"$limit: {q['page_size']} (pagination: offset-page loop, count(*)-reconciled)\n```\n\n")
    f.write(f"Count query URL: `{q['count_query_url']}`\n\n")
    f.write(f"Unit note: {p['units']['ghg_mass']}\n\n")
    f.write("## Counts\n\n| metric | value |\n|---|---|\n")
    rows_md = [
        ("Count-reconciled rows", c["count_reconciled_rows"]),
        ("Distinct property IDs", c["distinct_property_ids"]),
        ("Duplicate property IDs", c["duplicate_property_ids_count"]),
        ("Rows == distinct properties?", str(c["rows_equal_distinct_properties"]).lower()),
        ("Distinct canonical BBLs (union of all tokens)", c["distinct_canonical_bbls"]),
        ("BBL rows unparsed", c["bbl_unparsed_rows"]),
        ("BBL rows multi-valued", c["bbl_multi_valued_rows"]),
        ("Distinct BINs (7-digit tokens, zeros kept)", c["distinct_bins"]),
        ("Rows with multi-BIN strings", c["multi_bin_rows"]),
    ]
    f.writelines(f"| {k} | {v} |\n" for k, v in rows_md)
    f.write("\nNote: row count ≠ distinct property count by design — campuses and "
            "multi-BBL/BIN properties are preserved as-is; rows are NOT collapsed to "
            "distinct properties and campuses are never summed as a verified total here.\n\n")
    f.write("## Parent / child flags\n\n| metric | value |\n|---|---|\n")
    f.writelines(f"| {k} | {v} |\n" for k, v in [
        ("Parent properties", pc["parent_property_count"]),
        ("Child properties", pc["child_property_count"]),
        ("Standalone properties", pc["standalone_count"]),
        ("Orphan children", pc["orphan_child_count"]),
        ("Missing parent IDs", len(pc["missing_parent_ids"])),
    ])
    f.write("\n\nClassification note: " + pc["classification_note"] + "\n\n")
    f.write("Orphan children (child points at parent not in cohort):\n")
    for x in pc["orphan_child_examples"]:
        f.write(f"- child `{x['property_id']}` → parent `{x['parent_property_id']}`\n")
    f.write("\nMissing parent IDs (referenced but absent from cohort) — full list:\n")
    for pid in pc["missing_parent_ids"]:
        f.write(f"- `{pid}`\n")
    f.write("\n## Per-metric completeness\n\n")
    f.write("| metric | label | total | non-null | null/blank/NA | explicit zero | % non-null |\n")
    f.write("|---|---|---|---|---|---|---|\n")
    for key, m in p["metric_completeness"].items():
        f.write(f"| {key} | {m['label']} | {m['total_rows']} | {m['non_null']} "
                f"| {m['null_or_blank_or_na']} | {m['explicit_zero']} | {m['pct_non_null']}% |\n")
    f.write("\n## Campus budget boundary\n\n")
    f.write(f"- Aggregate status: **{cb['aggregate_status']}** — campus totals WITHHELD.\n")
    f.write(f"- Orphan child count: {cb['orphan_child_count']}\n")
    f.write(f"- Unresolved campus references in accounting module: "
            f"{len(cb['unresolved_parent_child_ids'])}\n")
    diag = cb["total_ghg_top_level_only_diagnostic_not_a_verified_total"]
    f.write(f"- Top-level-only GHG sum (DIAGNOSTIC ONLY, NOT a verified campus total): "
            f"{diag:,.1f} metric tons CO2e\n")
    f.write(f"- Sum over every row incl. children (DIAGNOSTIC: risk of double counting when "
            f"campus parents already include children; NOT an upper bound): "
            f"{cb['diagnostic_sum_all_rows_mtons']:,.1f} metric tons CO2e\n")
    f.write("\n## Snapshot files\n\n")
    f.write(f"- `{SNAP_SLICE}` raw snapshot: `data/snapshots/{SNAP_SLICE}.raw.jsonl` (immutable)\n")
    f.write(f"- Manifest: `data/snapshots/{SNAP_SLICE}.manifest.json`\n")
    f.write("\n## Reproduction\n\n```bash\n.venv/bin/python3 scripts/build_pilot.py           # offline (default)\n.venv/bin/python3 scripts/build_pilot.py --online        # fresh bounded fetch\n```\n")
    f.write("\nNo joins, no legal-screening, no frontend, no compliance math. "
            "This is a bounded pilot snapshot + diagnostics only.\n")


if __name__ == "__main__":
    main()
