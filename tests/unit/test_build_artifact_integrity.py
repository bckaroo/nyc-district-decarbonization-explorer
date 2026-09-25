"""Guards for build-artifact integrity: a manifest must describe the bytes it
sits beside.

A stale manifest is worse than a missing one. During this project an interrupted
demand build published its SQLite and died before writing the manifest, leaving
a manifest that claimed sha256 7ab58754…/T1 25616 while the served database was
9f9dc678…/T1 25645. Nothing detected the mismatch; it was found by a human
comparing numbers by hand. These tests make that class of failure loud.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CITYWIDE = REPO / "data" / "citywide"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- demand manifest

@pytest.mark.skipif(
    not (CITYWIDE / "annual_demand_citywide" / "manifest.json").exists(),
    reason="citywide demand manifest not built on this checkout",
)
def test_demand_manifest_describes_the_published_db():
    d = CITYWIDE / "annual_demand_citywide"
    manifest = json.loads((d / "manifest.json").read_text())
    db = d / manifest["outputs"]["sqlite"]["file"]

    assert db.exists(), "manifest names a database that is not on disk"
    assert sha256(db) == manifest["outputs"]["sqlite"]["sha256"], (
        "manifest sha256 does not match the database beside it — the build was "
        "interrupted between publishing the DB and writing the manifest"
    )
    assert db.stat().st_size == manifest["outputs"]["sqlite"]["bytes"], (
        "manifest byte count does not match the database beside it"
    )


@pytest.mark.skipif(
    not (CITYWIDE / "annual_demand_citywide" / "manifest.json").exists(),
    reason="citywide demand manifest not built on this checkout",
)
def test_demand_manifest_tier_counts_match_the_db():
    d = CITYWIDE / "annual_demand_citywide"
    manifest = json.loads((d / "manifest.json").read_text())
    con = sqlite3.connect(f"file:{d / 'annual_demand_citywide.sqlite'}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT COUNT(*) FROM annual_demand").fetchone()[0]
        actual = {t: n for t, n in con.execute(
            "SELECT evidence_tier, COUNT(*) FROM annual_demand GROUP BY 1")}
    finally:
        con.close()

    assert rows == manifest["counts"]["lots_total"], (
        f"manifest claims {manifest['counts']['lots_total']} lots, db has {rows}"
    )
    for tier, claimed in manifest["counts_by_tier"].items():
        assert actual.get(tier) == claimed, (
            f"tier {tier}: manifest claims {claimed}, db has {actual.get(tier)}"
        )


# ------------------------------------------------------------- owner clusters pair

@pytest.mark.skipif(
    not (CITYWIDE / "owner_clusters.manifest.json").exists(),
    reason="owner clusters not built on this checkout",
)
def test_owner_clusters_manifest_matches_its_geojson():
    gj = CITYWIDE / "owner_clusters.geojson"
    manifest = json.loads((CITYWIDE / "owner_clusters.manifest.json").read_text())
    assert sha256(gj) == manifest["geojson_sha256"]
    assert gj.stat().st_size == manifest["geojson_bytes"]
    assert len(json.loads(gj.read_text())["features"]) == manifest["campus_count"]


# ------------------------------------------------------------------ BID layer pair

@pytest.mark.skipif(
    not (CITYWIDE / "bids.geojson").exists(),
    reason="BID layer not built on this checkout",
)
def test_bids_manifest_matches_its_geojson():
    gj = CITYWIDE / "bids.geojson"
    manifest = json.loads((CITYWIDE / "bids.manifest.json").read_text())
    assert sha256(gj) == manifest["geojson_sha256"]
    assert len(json.loads(gj.read_text())["features"]) == manifest["bid_count"]
    # The BID layer must stay small enough to serve whole (it is not bbox-paged).
    assert gj.stat().st_size < 8_000_000, "BID layer grew past the serve-whole budget"


# --------------------------------------------------- citywide footprint DB sanity

@pytest.mark.skipif(
    not (CITYWIDE / "footprints_citywide.sqlite").exists(),
    reason="citywide footprints not built on this checkout",
)
def test_footprint_db_has_spatial_index_and_demand_join():
    con = sqlite3.connect(
        f"file:{CITYWIDE / 'footprints_citywide.sqlite'}?mode=ro", uri=True
    )
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fp_rtree'"
        ).fetchone(), "spatial index missing — viewport queries degrade to ~19s"

        total = con.execute("SELECT COUNT(*) FROM footprints").fetchone()[0]
        assert total > 1_000_000, f"expected citywide footprints, found {total:,}"

        # The demand join must have actually populated: building footprints while
        # the demand store was empty produced 0 and painted an all-grey layer.
        modeled = con.execute(
            "SELECT COUNT(*) FROM footprints WHERE net_thermal_kbtu_ft2_yr IS NOT NULL"
        ).fetchone()[0]
        assert modeled > 0, "net_thermal is empty — demand was not joined"
    finally:
        con.close()
