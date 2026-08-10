"""Address/domain normalization helpers used across adapters and entity resolution."""

import re
from urllib.parse import urlparse

_WHITESPACE_PATTERN = re.compile(r"\s+")

# Best-effort: matches a 6-digit Indian PIN code.
_INDIAN_POSTAL_CODE_PATTERN = re.compile(r"\b(\d{6})\b")


def normalize_domain(url_or_domain: str) -> str | None:
    """"https://www.Example.com/about" -> "example.com". Strips scheme, www.,
    path, query, port, and lowercases. Returns None if no usable hostname."""
    if not url_or_domain or not url_or_domain.strip():
        return None
    value = url_or_domain.strip()
    if "://" not in value:
        value = f"http://{value}"
    hostname = urlparse(value).hostname
    if not hostname:
        return None
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname or None


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text or "").strip()


def extract_postal_code(text: str) -> str | None:
    if not text:
        return None
    match = _INDIAN_POSTAL_CODE_PATTERN.search(text)
    return match.group(1) if match else None
