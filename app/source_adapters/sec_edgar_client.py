"""Thin HTTP client for SEC EDGAR's public JSON APIs (data.sec.gov), used
only by SecEdgarAdapter. Goes through ScraplingCollector for the actual
HTTP GET (uniform retry/backoff/SSRF-guard/logging), but owns EDGAR-specific
concerns: the required User-Agent header, CIK formatting, and translating
error responses into a typed exception hierarchy.

Auth: none. Confirmed directly against the live docs
(sec.gov/search-filings/edgar-application-programming-interfaces, fetched
2026-08-14): "These APIs do not require any authentication or API keys to
access." A compliant `User-Agent` header IS required by SEC's fair-access
policy (sec.gov/os/webmaster-faq, fetched 2026-08-14): "declare your user
agent in request headers" as `User-Agent: Sample Company Name
AdminContact@<sample company domain>.com`; requests without one get an
"Undeclared Automated Tool" error.

Rate limit: 10 requests per second, confirmed on the same page ("carefully
monitored to preserve equitable access for all users").

Endpoints used (base https://data.sec.gov, confirmed live against a real
company on 2026-08-14 -- see docs/sec_edgar_data_access.md for the full
verification):
  - GET /submissions/CIK{10-digit-cik}.json -- entity profile + recent
    filings list (confirmed fields: cik, entityType, sic, sicDescription,
    name, tickers, exchanges, stateOfIncorporation, fiscalYearEnd, phone,
    website, addresses.business/{street1,street2,city,stateOrCountry,
    zipCode}, filings.recent.{accessionNumber,form,filingDate,reportDate})
  - GET /api/xbrl/companyfacts/CIK{10-digit-cik}.json -- structured XBRL
    financial facts (confirmed: facts.us-gaap.<concept>.units.USD[] with
    val/start/end/fy/fp/form/filed)

Confirmed by direct inspection of a real response: employee count is NOT a
reliable field here (no dei:EntityNumberOfEmployees or equivalent present),
and no incorporation/founding date is present in the submissions payload.
See docs/sec_edgar_data_access.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.logging import get_logger, scrub_secrets
from app.ingestion.collectors.scrapling_collector import FetchError, ScraplingCollector

logger = get_logger(__name__)

_CIK_DIGITS_PATTERN = re.compile(r"^\d{1,10}$")


class SecEdgarError(RuntimeError):
    """Base class for all SEC EDGAR-specific errors."""


class SecEdgarConfigurationError(SecEdgarError):
    """Raised when SEC_EDGAR_USER_AGENT is not configured. Checked before
    any network call is made -- SEC's fair-access policy rejects requests
    without a compliant User-Agent, so there is no point sending one."""


class SecEdgarInvalidCIKError(SecEdgarError):
    """CIK is not a well-formed number. Never sent to the API."""


class SecEdgarNotFoundError(SecEdgarError):
    """404 -- CIK not found in EDGAR."""


class SecEdgarRateLimitError(SecEdgarError):
    """429 -- caller should back off (10 requests/second limit)."""


class SecEdgarProviderError(SecEdgarError):
    """5xx, timeout, network failure, or a response that doesn't match the
    documented shape at all (malformed response)."""


@dataclass(frozen=True)
class SecEdgarCompanyResponse:
    """Result of a company lookup: submissions (entity profile) is
    required; company_facts (XBRL financials) is best-effort -- not every
    filer has XBRL data (e.g. very old or very small filers), and its
    absence must never block the submissions-based identity/address
    observations, which are valid evidence on their own."""

    cik: str
    submissions: dict
    company_facts: dict | None
    company_facts_error: str | None
    retrieved_at_iso: str
    source_url: str


def normalize_cik(cik: str) -> str:
    """SEC CIKs are used as 10-digit zero-padded strings in URLs
    (e.g. 320193 -> "0000320193"). Raises if the input isn't a plain
    (optionally already-padded) number."""
    stripped = str(cik).strip().upper().removeprefix("CIK")
    if not _CIK_DIGITS_PATTERN.match(stripped):
        raise SecEdgarInvalidCIKError(f"{cik!r} is not a well-formed SEC CIK (expected up to 10 digits)")
    return stripped.zfill(10)


def _user_agent_header(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _get_json(
    collector: ScraplingCollector, url: str, *, user_agent: str, timeout: float, max_retries: int
) -> tuple[int, dict | None, bytes]:
    headers = _user_agent_header(user_agent)
    try:
        result = collector.fetch_static(url, headers=headers, timeout=timeout, max_retries=max_retries)
    except FetchError as exc:
        message = scrub_secrets(str(exc))
        logger.warning("sec_edgar_request_failed", extra={"extra_fields": {"url": url, "error": message}})
        raise SecEdgarProviderError(f"SEC EDGAR request failed: {message}") from exc

    try:
        parsed = json.loads(result.content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    return result.status_code, parsed, result.content


def fetch_company(
    cik: str,
    *,
    user_agent: str,
    base_url: str,
    collector: ScraplingCollector | None = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> SecEdgarCompanyResponse:
    """Fetch submissions (required) and company facts (best-effort) for one
    CIK. Raises a typed SecEdgarError subclass for every documented failure
    mode; never lets a raw requests/scrapling exception escape."""
    from datetime import datetime, timezone

    padded_cik = normalize_cik(cik)
    collector = collector or ScraplingCollector()

    submissions_url = f"{base_url}/submissions/CIK{padded_cik}.json"
    status, payload, raw = _get_json(
        collector, submissions_url, user_agent=user_agent, timeout=timeout, max_retries=max_retries
    )

    if status == 404:
        raise SecEdgarNotFoundError(f"SEC EDGAR has no record for CIK {padded_cik} (404)")
    if status == 429:
        raise SecEdgarRateLimitError("SEC EDGAR rate limit exceeded (429) -- limit is 10 requests/second")
    if status >= 500:
        raise SecEdgarProviderError(f"SEC EDGAR returned server error {status}")
    if status >= 400:
        raise SecEdgarProviderError(f"SEC EDGAR returned unexpected status {status}: {raw[:300]!r}")
    if payload is None or "cik" not in payload:
        raise SecEdgarProviderError(
            f"SEC EDGAR submissions response for {padded_cik} did not match the documented shape "
            f"(missing 'cik'): {raw[:300]!r}"
        )

    # Company facts: best-effort. Not every filer has XBRL-tagged financial
    # data (module docstring) -- a failure here must never block the
    # submissions-based result, which is valid evidence on its own.
    facts_url = f"{base_url}/api/xbrl/companyfacts/CIK{padded_cik}.json"
    company_facts: dict | None = None
    company_facts_error: str | None = None
    try:
        facts_status, facts_payload, facts_raw = _get_json(
            collector, facts_url, user_agent=user_agent, timeout=timeout, max_retries=1
        )
        if facts_status == 200 and facts_payload is not None:
            company_facts = facts_payload
        else:
            company_facts_error = f"status={facts_status}"
    except SecEdgarError as exc:
        company_facts_error = str(exc)

    return SecEdgarCompanyResponse(
        cik=padded_cik,
        submissions=payload,
        company_facts=company_facts,
        company_facts_error=company_facts_error,
        retrieved_at_iso=datetime.now(timezone.utc).isoformat(),
        source_url=submissions_url,
    )
