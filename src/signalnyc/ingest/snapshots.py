"""Immutable snapshots: fetch, hash, and write raw source payloads to disk.

Every pilot run captures: the exact query URLs, UTC timestamp, SHA256 of the
raw response body, and the raw rows themselves. Snapshots are never edited in
place; a re-run writes a NEW timestamped directory.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os

from .socrata import fetch_all


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_dataset(
    out_dir: str,
    fid: str,
    where: str | None,
    name: str,
    fields: list[str] | None = None,
    page_size: int = 10_000,
) -> dict:
    """Fetch one slice and write raw.jsonl + manifest.json under out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, meta = fetch_all(fid, where=where, fields=fields, page_size=page_size)
    raw_path = os.path.join(out_dir, f"{name}.raw.jsonl")
    with open(raw_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    digest = _sha256_file(raw_path)
    manifest = {
        "socrata_id": fid,
        "slice": name,
        "where": where,
        "selected_fields": fields,
        "captured_utc": ts,
        "rows": len(rows),
        "sha256": digest,
        "raw_file": raw_path,
        "count_query_url": meta["urls"]["count_query"],
        "fetch_log": meta["fetch_log"],
    }
    with open(os.path.join(out_dir, f"{name}.manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return manifest
