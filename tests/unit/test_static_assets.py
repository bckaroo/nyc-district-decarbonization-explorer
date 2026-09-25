"""DEV-154 regression: the built MapLibre worker must actually load.

The vite build copies maplibre-gl-worker.mjs (and, since DEV-154, its
statically-imported chunk maplibre-gl-shared.mjs) into dist/assets. If either
file is missing, the worker fetch falls through to index.html and the map never
parses a single building polygon. This test asserts the built worker's relative
imports resolve to real JavaScript assets in dist, not 404 fallbacks.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
DIST = WEB / "dist"

pytestmark = pytest.mark.skipif(
    not DIST.is_dir(), reason="web/dist not built — run `npm run build` in web/"
)


def test_dist_assets_include_maplibre_worker_and_shared_chunk():
    assets = {p.name for p in (DIST / "assets").iterdir()}
    assert "maplibre-gl-worker.mjs" in assets, "worker missing from dist/assets"
    assert "maplibre-gl-shared.mjs" in assets, "shared chunk missing from dist/assets"


def test_worker_relative_imports_resolve_to_real_js_assets():
    worker_text = (DIST / "assets" / "maplibre-gl-worker.mjs").read_text(encoding="utf-8")
    # import statements + `import(...)` dynamic imports with relative specifiers
    specifiers = set(
        re.findall(
            r"""(?:^|[\s;])import\s*(?:[\w$]+,?\s*)?(?:\{[^}]*\})?\s*from\s*['"](\./[^'"]+)['"]"""
            r"""|import\(\s*['"](\./[^'"]+)['"]\s*\)""",
            worker_text,
        )
    )
    rel_specs = {a or b for a, b in specifiers}
    assert rel_specs, "worker unexpectedly has no relative imports to resolve"
    for spec in rel_specs:
        target = (DIST / "assets" / spec).resolve()
        assert target.is_file(), f"worker imports {spec} which does not exist in dist/assets"
        head = target.read_bytes()[:256].lstrip()
        assert not head.startswith(b"<!doctype html"), (
            f"{spec} resolved to an HTML 404 fallthrough, not a JS asset"
        )
