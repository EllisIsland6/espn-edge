"""Small input normalizers shared by routers/CLI."""

from __future__ import annotations


def normalize_swid_braced(swid: str | None) -> str:
    """Store SWID with its curly braces (ESPN's cookie expects them; SPEC 2.3).

    Users may paste with or without braces; we normalize to braced-uppercase.
    """
    if not swid:
        return ""
    s = swid.strip().strip("{}").upper()
    return "{" + s + "}" if s else ""
