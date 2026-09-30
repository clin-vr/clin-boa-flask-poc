"""SharePoint library column names."""

from __future__ import annotations


def internal_column_name(display_name: str) -> str:
    """Return a column's internal name, encoding each character other than alphanumerics and _ as _xHHHH_."""
    return "".join(ch if ch.isalnum() or ch == "_" else f"_x{ord(ch):04x}_" for ch in display_name)
