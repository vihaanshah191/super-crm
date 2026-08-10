"""Thin HTTP client for the FileSure API (api.filesure.in), used only by
FileSureAdapter. Goes through ScraplingCollector for the actual HTTP GET
(uniform retry/backoff/SSRF-guard/logging, same as every other HTTP-based
source), but owns FileSure-specific concerns: the x-api-key header, CIN
format pre-validation, and translating FileSure's error response shape into
a typed exception hierarchy the adapter/pipeline can handle without any
single failure mode taking down ingestion for other sources.

Auth: `x-api-key: <key>` header. Confirmed directly against the live API --
an unauthenticated request returns
`{"error":{"code":"MISSING_API_KEY","message":"API key is required. Pass it
via the x-api-key header."}}`. FileSure's own docs also show
`Authorization: Bearer <key>` as an alternative; only x-api-key is used here
since it's the one actually confirmed against the live service, not
guessed. See docs/filesure_data_access.md.

Endpoints (base https://api.filesure.in/v1, confirmed -- see
docs/filesure_data_access.md):
  - GET /companies/{cin}              -- master data (confirmed schema)
  - GET /companies/{cin}/extractions  -- statutory-form extractions (schema
    NOT confirmed; response is preserved raw for provenance, not parsed)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.logging import get_logger, scrub_secrets
from app.ingestion.collectors.scrapling_collector import FetchError, ScraplingCollector

logger = get_logger(__name__)

_CIN_PATTERN = re.compile(r"^[A-Z]{1}[0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")


class FileSureError(RuntimeError):
    """Base class for all FileSure-specific errors. Catching this at the
    call site (see app/cli/filesure_lookup.py and the Celery task layer)
    guarantees a FileSure failure never propagates as an unhandled
    exception that could be confused with a different source's failure."""


class FileSureConfigurationError(FileSureError):
    """Raised when FILESURE_COLLECTION_ENABLED is false or no API key is
    configured. Checked before any network call is made."""


class FileSureInvalidCINError(FileSureError):
    """CIN is not well-formed (wrong length/character pattern). Never sent
    to the API -- failing fast here saves a billed/rate-limited call."""


class FileSureAuthenticationError(FileSureError):
    """401/403 -- missing or invalid API key."""


class FileSureNotFoundError(FileSureError):
    """404 -- CIN not found (or, in sandbox, not in the test-key whitelist)."""


class FileSureRateLimitError(FileSureError):
    """429 -- caller should back off; retry_after_seconds is populated when
    the response provides a Retry-After header."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class FileSureProviderError(FileSureError):
    """5xx, timeout, network failure, or a response that doesn't match
    FileSure's documented shape at all (malformed response)."""


@dataclass(frozen=True)
class FileSureCompanyResponse:
    """Result of a company lookup: master data is required and validated;
    extractions is best-effort and preserved raw (see module docstring --
    no confirmed schema exists for it yet)."""

    cin: str
    master_data: dict
    extractions_raw: dict | None
    extractions_error: str | None
    retrieved_at_iso: str
    source_url: str


def _redact_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    return {k: ("***" if k.lower() in ("x-api-key", "authorization") else v) for k, v in headers.items()}


def validate_cin_format(cin: str) -> None:
    """CIN is a fixed 21-character MCA identifier: 1 letter + 5 digits + 2
    letters + 4 digits + 3 letters + 6 digits. Raises before any network
    call if the shape is wrong."""
    if not cin or not _CIN_PATTERN.match(cin.strip().upper()):
        raise FileSureInvalidCINError(
            f"{cin!r} is not a well-formed CIN (expected 21 characters: "
            "1 letter, 5 digits, 2 letters, 4 digits, 3 letters, 6 digits)"
        )


def _parse_error_body(content: bytes) -> str:
    try:
        payload = json.loads(content.decode("utf-8", errors="replace"))
        err = payload.get("error", {})
        code = err.get("code", "UNKNOWN")
        message = err.get("message", "")
        return f"{code}: {message}"
    except (json.JSONDecodeError, AttributeError):
        return content.decode("utf-8", errors="replace")[:500]


def _get_json(
    collector: ScraplingCollector, url: str, *, api_key: str, timeout: float, max_retries: int
) -> tuple[int, dict | None, bytes]:
    """GET one URL with the FileSure auth header, returning
    (status_code, parsed_json_or_None, raw_content). Network/5xx failures
    that exhaust ScraplingCollector's own retries surface as FileSureProviderError
    here -- callers never see a raw FetchError."""
    headers = {"x-api-key": api_key}
    try:
        result = collector.fetch_static(url, headers=headers, timeout=timeout, max_retries=max_retries)
    except FetchError as exc:
        message = scrub_secrets(str(exc))
        logger.warning("filesure_request_failed", extra={"extra_fields": {"url": scrub_secrets(url), "error": message}})
        raise FileSureProviderError(f"FileSure request failed: {message}") from exc

    try:
        parsed = json.loads(result.content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    return result.status_code, parsed, result.content


def fetch_company(
    cin: str,
    *,
    api_key: str,
    base_url: str,
    collector: ScraplingCollector | None = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> FileSureCompanyResponse:
    """Fetch master data (required) and extractions (best-effort) for one
    CIN. Raises a typed FileSureError subclass for every documented failure
    mode; never lets a raw requests/scrapling exception escape."""
    from datetime import datetime, timezone

    validate_cin_format(cin)
    cin = cin.strip().upper()
    collector = collector or ScraplingCollector()

    master_url = f"{base_url}/companies/{cin}"
    status, payload, raw = _get_json(collector, master_url, api_key=api_key, timeout=timeout, max_retries=max_retries)

    if status == 401 or status == 403:
        raise FileSureAuthenticationError(f"FileSure authentication failed ({status}): {_parse_error_body(raw)}")
    if status == 404:
        raise FileSureNotFoundError(f"FileSure has no record for CIN {cin} (404): {_parse_error_body(raw)}")
    if status == 429:
        raise FileSureRateLimitError(f"FileSure rate limit exceeded (429): {_parse_error_body(raw)}")
    if status >= 500:
        raise FileSureProviderError(f"FileSure returned server error {status}: {_parse_error_body(raw)}")
    if status >= 400:
        raise FileSureProviderError(f"FileSure returned unexpected status {status}: {_parse_error_body(raw)}")

    if payload is None or "data" not in payload:
        raise FileSureProviderError(
            f"FileSure response for {cin} did not match the documented shape "
            f"(missing top-level 'data' key): {raw[:300]!r}"
        )

    master_data = payload["data"]

    # Extractions: best-effort. No confirmed schema exists (see module
    # docstring) -- a failure here must never block the master-data result,
    # since master data alone is still valid, provenance-preserving evidence.
    extractions_url = f"{base_url}/companies/{cin}/extractions"
    extractions_raw: dict | None = None
    extractions_error: str | None = None
    try:
        ext_status, ext_payload, ext_raw = _get_json(
            collector, extractions_url, api_key=api_key, timeout=timeout, max_retries=1
        )
        if ext_status == 200 and ext_payload is not None:
            extractions_raw = ext_payload
        else:
            extractions_error = f"status={ext_status}: {_parse_error_body(ext_raw)}"
    except FileSureError as exc:
        extractions_error = str(exc)

    return FileSureCompanyResponse(
        cin=cin,
        master_data=master_data,
        extractions_raw=extractions_raw,
        extractions_error=extractions_error,
        retrieved_at_iso=datetime.now(timezone.utc).isoformat(),
        source_url=master_url,
    )
