"""Data-quality and accounting at the LL84 reporting-property grain.

Rules (binding, per task invariant):
- Row counts are NEVER treated as distinct property counts; property counts are
  always computed by identifying fields (property_id / canonical BBL / BIN).
- Campus (parent/child) aggregates are ONLY produced when documented boundary
  evidence exists for a campus; otherwise the campus is flagged unresolved and
  its aggregate is withheld outright (not an estimate, not silently 0).
- parent_property_id alone does NOT prove inclusion: the only evidence this
  dataset itself carries is a child row whose parent_property_id points at a
  parent row present in the same report year (self-consistency check).
- identity multiplicity (one property row naming several BBL/BINs) is reported
  explicitly, never silently mapped to one "building".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from nyc_decarbonization.data.identifiers import parse_bbl, parse_bbl_multi, parse_bin
from nyc_decarbonization.data.observations import _to_num


def distinct_counts(rows: Iterable[Mapping]) -> dict:
    """Distinguish row count from distinct property identity counts."""
    rows = list(rows)
    pids = {r.get("property_id") for r in rows if r.get("property_id")}
    bbls: set[str] = set()
    bins: set[str] = set()
    bbl_ambiguous = bin_ambiguous = 0
    for r in rows:
        mb = parse_bbl_multi(r.get("nyc_borough_block_and_lot"))
        bbls.update(mb)
        if len(mb) > 1:
            bbl_ambiguous += 1
        import re
        for token in re.split(r"[;,\s]+", str(r.get("nyc_building_identification") or "")):
            parsed = parse_bin(token)
            if parsed:
                bins.add(parsed)
    return {
        "row_count": len(rows),
        "distinct_property_id": len(pids),
        "distinct_bbl": len(bbls),
        "distinct_bin": len(bins),
        "rows_with_multiple_bbl": bbl_ambiguous,
        "note": "row_count != distinct counts; never report rows as 'properties'",
    }


def campus_accounting(rows: Iterable[Mapping], *, coverage_evidence=None) -> dict:
    """Use the same conservative reporting-boundary rules as all other totals."""
    from nyc_decarbonization.data.observations import campus_report_boundary
    rows = list(rows)
    result = campus_report_boundary(
        [dict(r, total_ghg=r.get("total_location_based_ghg")) for r in rows],
        coverage_evidence=coverage_evidence,
    )
    return {
        "row_count": len(rows),
        "standalone_count": result["standalone_count"],
        "campus_parent_count": result["parent_property_count"],
        "campus_child_count": result["child_property_count"],
        "orphan_child_count": result["orphan_child_count"],
        "unresolved_campuses": result["unresolved_child_ids"] + result["unresolved_parent_ids"],
        "aggregate_top_level_emissions_mtons": result["aggregate_ghg"],
        "aggregate_status": result["aggregate_status"],
        "diagnostic_sum_all_rows_mtons": result["total_ghg_if_children_also_counted"],
        "reasons": [] if result["aggregate_status"] == "ok" else [
            "Aggregate withheld: missing values or unverified campus boundary"],
    }


def completeness(rows: Iterable[Mapping], year: str | None = None) -> dict:
    """Completeness of the pilot metrics the frontend needs (annual only)."""
    rows = list(rows)
    n = len(rows)

    def frac(key):
        have = sum(1 for r in rows if _to_num(r.get(key)) is not None)
        return {"present": have, "missing": n - have, "pct": round(100.0 * have / n, 2) if n else 0.0}

    out = {"row_count": n}
    for k in (
        "property_gfa_self_reported",
        "property_gfa_calculated_1",
        "site_eui_kbtu_ft",
        "weather_normalized_site_eui",
        "electricity_use_grid_purchase",
        "natural_gas_use_kbtu",
        "total_location_based_ghg",
        "direct_ghg_emissions_metric",
        "energy_star_score",
    ):
        out[k] = frac(k)
    return out


def sample_identity_cases(rows: Iterable[Mapping], n: int = 8) -> list[dict]:
    """One row per identity case, raw values preserved for the feasibility report."""
    rows = list(rows)
    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        bbls = parse_bbl_multi(r.get("nyc_borough_block_and_lot"))
        if r.get("nyc_borough_block_and_lot") in (None, ""):
            buckets["no_bbl"].append(r)
        elif len(bbls) == 0:
            buckets["unparseable_bbl"].append(r)
        elif len(bbls) > 1:
            buckets["multi_bbl"].append(r)
        else:
            b = parse_bin(r.get("nyc_building_identification"))
            buckets["single_bin" if b else "no_bin"].append(r)
    out = []
    for kind, lst in sorted(buckets.items()):
        for r in lst[: n // max(len(buckets), 3)]:
            canonical = parse_bbl(r.get("nyc_borough_block_and_lot")).bbl
            out.append(
                {
                    "case": kind,
                    "property_id": r.get("property_id"),
                    "raw_bbl_field": r.get("nyc_borough_block_and_lot"),
                    "raw_bin_field": r.get("nyc_building_identification"),
                    "canonical_bbl": canonical,
                }
            )
    return out
