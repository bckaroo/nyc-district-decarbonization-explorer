"""Identifier parsing for NYC buildings data: BBL, BIN, multi-valued and malformed inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BBLParse:
    """Result of parsing a BBL expression.

    None field = unparseable / missing, never silently zero.
    """

    raw: str | None
    borough: str | None
    block: int | None
    lot: int | None
    bbl: str | None  # 10-digit canonical BBL string


_BOROUGHS = {
    "1": "manhattan",
    "2": "bronx",
    "3": "brooklyn",
    "4": "queens",
    "5": "staten island",
}

_BORO_NAMES = {"manhattan": "1", "new york": "1", "bronx": "2", "brooklyn": "3",
               "kings": "3", "queens": "4", "staten island": "5", "richmond": "5"}


def _clean_float_str(v: str) -> str:
    """LL84 BBLs arrive as float-ish strings like '1005850042.00000000'."""
    # strip ONLY a trailing all-zero fractional part; keep dotted '4.00585.0042' intact
    return re.sub(r"\.0+$", "", v).strip()


def parse_bbl(raw: str | float | None) -> BBLParse:
    if raw is None:
        return BBLParse(None, None, None, None, None)
    if isinstance(raw, float):
        raw = f"{raw:.0f}" if raw == raw else ""
    text = _clean_float_str(str(raw))
    if not text:
        return BBLParse(str(raw), None, None, None, None)

    # Multi-valued: dot-joined "boro.block.lot" (canonical) or list forms
    parts = re.split(r"[;,\s]+", text)
    candidates: list[str] = []
    for part in parts:
        if not part:
            continue
        segs = part.split(".")
        if len(segs) == 3:
            candidates.append(segs[0] + segs[1] + segs[2])
        elif len(segs) == 1 and segs[0].isdigit():
            # only zero-fill AFTER borough-header check: never repair garbage
            d = segs[0]
            candidates.append(d.zfill(10) if len(d) == 10 and _BOROUGHS.get(d[0]) else "")
        else:
            candidates.append("")  # malformed: boro.block without lot etc.

    if not candidates:
        return BBLParse(str(raw), None, None, None, None)

    first = candidates[0]
    if len(first) == 10 and first.isdigit() and _BOROUGHS.get(first[0]):
        return BBLParse(str(raw), _BOROUGHS[first[0]], int(first[1:6]), int(first[6:]), first)

    return BBLParse(str(raw), None, None, None, None)


def parse_bbl_multi(raw: str | float | None) -> list[str]:
    """Return every canonical BBL found in a possibly multi-valued field."""
    if raw is None:
        return []
    if isinstance(raw, float):
        raw = f"{raw:.0f}" if raw == raw else ""
    text = str(raw)
    out: list[str] = []
    for part in re.split(r"[;,\s]+", text):
        part = _clean_float_str(part)
        if not part:
            continue
        segs = part.split(".")
        if len(segs) == 3:
            cand = segs[0] + segs[1] + segs[2]
        elif len(segs) == 1 and segs[0].isdigit():
            cand = segs[0].zfill(10)
        else:
            cand = ""
        if len(cand) == 10 and cand[0] in _BOROUGHS:
            if cand not in out:
                out.append(cand)
    return out


def boro_code(name: str) -> str | None:
    return _BORO_NAMES.get(name.strip().lower())


def parse_bin(raw: str | float | None) -> str | None:
    """BINs are 7-digit ids (real range 1000000..8999999); shorter = not a BIN."""
    if raw is None:
        return None
    if isinstance(raw, float):
        raw = f"{raw:.0f}" if raw == raw else ""
    digits = str(raw).strip()
    if "." in digits:
        digits = re.sub(r"\.0+$", "", digits)
    if digits.isdigit() and len(digits) == 7 and digits != "0000000" and int(digits) > 0:
        return digits
    return None
