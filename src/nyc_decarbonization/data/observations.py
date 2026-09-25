"""Observation-level helpers: revision handling, null vs zero, campus reporting boundary.

Reporting property != tax lot != physical building. These helpers operate at the
LL84 grain (one row per reporting property per year) and deliberately do NOT
aggregate across footprints or lots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def _to_num(v):
    """Parse a metric value; empty or invalid values remain None."""
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


def null_vs_zero(row: Mapping, key: str = "v"):
    """Return None for missing/blank/unparseable and 0.0 only for explicit zero."""
    return _to_num(row.get(key))


def revision_dedupe(
    rows: Iterable[Mapping],
    year_field: str,
    keys: Sequence[str] | None = None,
    tiebreak: str = "row_id",
) -> list[dict] | dict:
    """Dedupe rows within the SAME report year (deleted/re-issued revisions keep one row).

    With keys=None returns the single latest row (max year_field, then max tiebreak).
    With keys given, returns one row per (year, key...) group, stable tiebreak.
    """
    groups: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        grp = (r.get(year_field),) + tuple(r.get(k) for k in (keys or ()))
        raw_tb = r.get(tiebreak)
        tb = (float(raw_tb), i) if raw_tb is not None else (float("-inf"), i)
        cur = groups.get(grp)
        if cur is None or tb >= cur[0]:
            groups[grp] = (tb, r)
    out = [r for (_tb, r) in groups.values()]
    out.sort(key=lambda r: (str(r.get(year_field, "")), str(r.get(tiebreak, ""))))
    if keys is None:
        return out[-1] if out else {}
    if len(keys) == 1:
        return out  # one per (year) group
    return out


def campus_report_boundary(rows: Iterable[Mapping], *, coverage_evidence=None) -> dict:
    """Withhold campus aggregates unless explicit coverage evidence is provided.

    coverage_evidence maps a parent ID to a nonempty reviewed evidence reference
    confirming its total includes the linked children. Linkage alone is not proof.
    Callers must supply one observation per property in one reporting year.
    """
    rows = list(rows)
    evidence = coverage_evidence or {}
    ids = [str(r.get("property_id") or "") for r in rows]
    if any(not pid for pid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Unique nonempty property IDs required; resolve revisions first")
    if len({r.get("report_year") for r in rows}) > 1:
        raise ValueError("Aggregate one reporting year at a time")
    markers = {"", "not applicable: standalone property"}
    children = [r for r in rows
                if str(r.get("parent_property_id") or "").strip().lower() not in markers
                and str(r.get("parent_property_id")) != str(r.get("property_id"))]
    parent_ids = {str(r.get("parent_property_id")) for r in children}
    parent_ids.update(str(r["property_id"]) for r in rows
                      if str(r.get("parent_property_id")) == str(r.get("property_id")))
    child_ids = {str(r["property_id"]) for r in children}
    parents = [r for r in rows if str(r["property_id"]) in parent_ids]
    top = [r for r in rows if str(r["property_id"]) not in child_ids]
    orphans = [r for r in children if str(r["parent_property_id"]) not in ids]
    unresolved = sorted(pid for pid in parent_ids if not evidence.get(pid))
    # Nested campuses require a richer boundary model; never infer coverage.
    nested = bool(parent_ids & child_ids)
    values = [_to_num(r.get("total_ghg")) for r in top]
    total = sum(v for v in values if v is not None)
    nulls = sum(v is None for v in values)
    status = ("WITHHELD_UNRESOLVED" if orphans or unresolved or nested else
              "WITHHELD_MISSING" if nulls else "ok")
    return {
        "reporting_property_count": len(rows),
        "parent_property_count": len(parents),
        "child_property_count": len(children),
        "orphan_child_count": len(orphans),
        "unresolved_child_ids": [r["property_id"] for r in orphans],
        "unresolved_parent_ids": unresolved,
        "standalone_count": sum(str(r["property_id"]) not in parent_ids for r in top),
        "aggregate_ghg": total if status == "ok" else None,
        "aggregate_status": status,
        "total_ghg_top_level": total,  # Partial diagnostic, not a verified aggregate.
        "total_ghg_if_children_also_counted": sum(
            _to_num(r.get("total_ghg")) or 0 for r in rows),
        "null_ghg_count": nulls,
    }
