"""Strict numeric parsing for snapshot fields.

LL84 values arrive as strings; "Not Available" tokens must become explicit
None, and NaN/Infinity must be rejected rather than propagated into JSON.
"""

from __future__ import annotations

import math

_MISSING_TOKENS = {"", "not available", "n/a", "na", "null", "none", "-"}


def parse_number(value: object) -> float | None:
    """Parse a metric value; missing/invalid/unparseable -> None (explicit null).

    Returns 0.0 only for an actual "0" string or 0 value.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    s = str(value).strip()
    if s.lower() in _MISSING_TOKENS:
        return None
    try:
        f = float(s.replace(",", ""))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_coord(value: object) -> float | None:
    """Latitude/longitude parse with plausibility bound to discard garbage."""
    f = parse_number(value)
    if f is None:
        return None
    return f


def parse_text(value: object) -> str | None:
    """Text field parse; missing tokens -> None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _MISSING_TOKENS or s.lower().startswith("not applicable"):
        return None
    return s
