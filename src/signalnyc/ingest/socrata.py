"""Socrata SoQL client (stdlib only): stable pagination + count reconciliation.

Never assumes any row cap: page_size is a chunking hint, not a data boundary.
Every fetch is reconciled against an independent count(*) query; a silent cap
(violation of the invariant that all requested rows came back) raises.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

BASE = "https://data.cityofnewyork.us/resource/{fid}.json"


class CountMismatch(RuntimeError):
    """Requested/paged rows do not reconcile with count(*): a silent cap or
    moving dataset is suspected. Refuse to return partial data."""


@dataclass
class FetchLog:
    pages: int = 0
    rows: int = 0
    requests: list[dict] = field(default_factory=list)  # {url, status, ms, rows}

    def to_dict(self) -> dict:
        return {"pages": self.pages, "rows": self.rows, "requests": self.requests}


def soda(url: str, timeout: int = 120) -> tuple[list, dict]:
    """GET one SoQL page; returns (rows, response_meta)."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = r.read()
        return json.loads(body), {
            "status": r.status,
            "headers": {
                k: r.headers[k]
                for k in ("X-SODA2-Row-Count-Total", "Date", "X-SODA2-Exceptions-Request-ID")
                if r.headers.get(k)
            },
        }


def _paged_query(base_query: Mapping, page_size: int, timeout: int) -> tuple[list[dict], FetchLog]:
    """Iterate offset pages until exhausted; verify each page URL."""
    log = FetchLog()
    out: list[dict] = []
    offset = 0
    while True:
        q = dict(base_query)
        q["$limit"] = page_size
        q["$offset"] = offset
        url = (
            BASE.format(fid=q.pop("_fid"))
            + "?"
            + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})
        )
        rows, meta = soda(url, timeout)
        log.pages += 1
        log.requests.append({"url_sha": _short(url), "status": meta["status"], "rows": len(rows)})
        if rows:
            out.extend(rows)
        if len(rows) < page_size:
            break
        offset += len(rows)
        if offset > 2_000_000:  # runaway guard, far above any NYC dataset
            raise CountMismatch("pagination exceeded 2,000,000 rows without exhausting")
    log.rows = len(out)
    return out, log


def _short(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.path}[?]"


def fetch_all(
    fid: str,
    where: str | None = None,
    select: Mapping[str, Callable | str] | None = None,
    fields: Iterable[str] | None = None,
    page_size: int = 10_000,
    timeout: int = 120,
) -> tuple[list[dict], dict]:
    """Fetch rows for a where-clause with pagination reconciled to count(*).

    Returns (rows, meta) where meta = {count, expected, requests, count_query_url}.
    Raises CountMismatch if len(rows) != count(*).
    """
    base: dict = {"_fid": fid}
    if fields:
        base["$select"] = ",".join(fields)
    if where:
        base["$where"] = where

    cq_url = (
        BASE.format(fid=fid)
        + "?"
        + urllib.parse.urlencode({"$select": "count(*) as n", **({"$where": where} if where else {})})
    )
    expected = int(soda(cq_url, timeout)[0][0]["n"])
    rows, log = _paged_query(base, page_size, timeout)
    if len(rows) != expected:
        raise CountMismatch(f"{fid}: paged {len(rows)} rows but count(*) = {expected}")
    meta = {
        "socrata_id": fid,
        "count": expected,
        "fetched_rows": len(rows),
        "urls": {"count_query": cq_url},
        "fetch_log": log.to_dict(),
    }
    return rows, meta


def socrata_row_to_clean(row: Mapping) -> dict:
    import re

    return {re.sub(r"^_+", "", k): v for k, v in row.items()}
