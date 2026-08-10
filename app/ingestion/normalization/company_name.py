"""Company legal-name normalization.

Used both to produce Company.normalized_name / CompanyAlias.normalized_alias
and as the primary deterministic-ish signal in entity resolution (exact match
on normalized name is still just one signal among several -- never sufficient
alone to auto-merge, see app.ingestion.entity_resolution.matcher).
"""

import re

_LEGAL_SUFFIXES = [
    "private limited",
    "pvt ltd",
    "pvt. ltd.",
    "pvt limited",
    "public limited",
    "limited liability partnership",
    "llp",
    "limited",
    "ltd",
    "incorporated",
    "inc",
    "corporation",
    "corp",
    "company",
    "co",
]

# Longest-first so "private limited" matches before "limited" does.
_SUFFIX_PATTERN = re.compile(
    r"[\s,.]*\b(" + "|".join(re.escape(s) for s in sorted(_LEGAL_SUFFIXES, key=len, reverse=True)) + r")\.?\s*$",
    re.IGNORECASE,
)

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def strip_legal_suffix(name: str) -> str:
    """Strip a single trailing legal-entity suffix (case-insensitive)."""
    stripped = _SUFFIX_PATTERN.sub("", name).strip()
    return stripped or name.strip()


def normalize_company_name(name: str) -> str:
    """Produce a comparison-safe normalized form: suffix stripped, lowercased,
    punctuation removed, whitespace collapsed.

    "ABC Industries Pvt. Ltd." -> "abc industries"
    "ABC INDUSTRIES PRIVATE LIMITED" -> "abc industries"
    """
    if not name:
        return ""
    without_suffix = strip_legal_suffix(name)
    lowered = without_suffix.lower()
    no_punct = _PUNCTUATION_PATTERN.sub(" ", lowered)
    collapsed = _WHITESPACE_PATTERN.sub(" ", no_punct).strip()
    return collapsed
