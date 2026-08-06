"""Shared date parsing for source adapters. Government/registry sources
publish dates in a handful of common formats (ISO, DD-MM-YYYY, DD/MM/YYYY);
centralizing this means a newly observed format is a one-line addition here
rather than a duplicated helper per adapter."""

from datetime import date, datetime

_KNOWN_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")


def parse_flexible_date(raw: str) -> date | None:
    for fmt in _KNOWN_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None
