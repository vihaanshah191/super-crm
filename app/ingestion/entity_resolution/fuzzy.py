"""Fuzzy name similarity -- used only as a fallback signal in matcher.py, never
as a standalone basis for merging two companies."""

from rapidfuzz import fuzz


def name_similarity(a: str, b: str) -> float:
    """Returns a 0.0-1.0 similarity score between two already-normalized names."""
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b) / 100.0
