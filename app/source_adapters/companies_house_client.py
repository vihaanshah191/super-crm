"""Thin HTTP client for the UK Companies House Public Data API
(api.company-information.service.gov.uk), used only by
CompaniesHouseAdapter. Goes through ScraplingCollector for the actual HTTP
GET (uniform retry/backoff/SSRF-guard/logging, same as every other
HTTP-based source), but owns Companies-House-specific concerns: HTTP Basic
Auth, company-number pre-validation, and translating Companies House's error
responses into a typed exception hierarchy.

Auth: HTTP Basic Access Authentication, API key as the username, password
left blank -- confirmed directly against the live official docs
(developer.company-information.service.gov.uk/authentication, fetched
2026-08-14): "The Companies House API takes the username as the API or
stream key and ignores the password, so it can be left blank." Example:
`curl -XGET -u my_api_key: https://api.company-information.service.gov.uk/company/00000006`

Rate limit: 600 requests per 5-minute window per API key, confirmed against
developer.company-information.service.gov.uk/developer-guidelines
(fetched 2026-08-14). Exceeding it returns 429; the limit resets at the end
of the 5-minute window.

Endpoint used (base https://api.company-information.service.gov.uk,
confirmed via the official API reference,
developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference,
fetched 2026-08-14):
  - GET /company/{company_number} -- company profile (confirmed field
    names: company_name, company_number, company_status, type,
    date_of_creation, sic_codes, registered_office_address {address_line_1,
    address_line_2, locality, region, postal_code, country}, jurisdiction,
    has_charges, has_insolvency_history, accounts.next_due,
    confirmation_statement.next_due)

Known limitation, confirmed by reading the documented response shape: this
endpoint does NOT include filed financial figures (revenue/turnover) --
only filing *metadata* (due dates, overdue flags). See
docs/companies_house_data_access.md.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from app.core.logging import get_logger, scrub_secrets
from app.ingestion.collectors.scrapling_collector import FetchError, ScraplingCollector

logger = get_logger(__name__)

# UK company numbers are 8 characters: either all-digit (England & Wales,
# e.g. "00000006") or a 2-letter jurisdiction prefix + 6 digits (e.g. "SC"
# Scotland, "NI" Northern Ireland, "OC"/"SO" LLPs, "FC" overseas). Validated
# loosely here (length + alphanumeric) rather than against a fixed prefix
# list, since Companies House documents more prefixes than are worth
# hardcoding and an incomplete allow-list would reject valid numbers.
_COMPANY_NUMBER_PATTERN = re.compile(r"^[A-Z0-9]{8}$")


class CompaniesHouseError(RuntimeError):
    """Base class for all Companies House-specific errors."""


class CompaniesHouseConfigurationError(CompaniesHouseError):
    """Raised when COMPANIES_HOUSE_COLLECTION_ENABLED is false or no API key
    is configured. Checked before any network call is made."""


class CompaniesHouseInvalidCompanyNumberError(CompaniesHouseError):
    """Company number is not well-formed. Never sent to the API -- failing
    fast here saves a rate-limited call."""


class CompaniesHouseAuthenticationError(CompaniesHouseError):
    """401/403 -- missing or invalid API key."""


class CompaniesHouseNotFoundError(CompaniesHouseError):
    """404 -- company number not found on the register."""


class CompaniesHouseRateLimitError(CompaniesHouseError):
    """429 -- caller should back off (600 requests / 5 minutes per key)."""


class CompaniesHouseProviderError(CompaniesHouseError):
    """5xx, timeout, network failure, or a response that doesn't match the
    documented shape at all (malformed response)."""


@dataclass(frozen=True)
class CompaniesHouseCompanyResponse:
    company_number: str
    profile: dict
    retrieved_at_iso: str
    source_url: str


def validate_company_number_format(company_number: str) -> None:
    if not company_number or not _COMPANY_NUMBER_PATTERN.match(company_number.strip().upper()):
        raise CompaniesHouseInvalidCompanyNumberError(
            f"{company_number!r} is not a well-formed UK company number "
            "(expected 8 characters, e.g. '00000006' or 'SC123456')"
        )


def _basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _parse_error_body(content: bytes) -> str:
    try:
        payload = json.loads(content.decode("utf-8", errors="replace"))
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(e.get("error", e)) for e in errors)
        return json.dumps(payload)[:500]
    except (json.JSONDecodeError, AttributeError):
        return content.decode("utf-8", errors="replace")[:500]


def fetch_company(
    company_number: str,
    *,
    api_key: str,
    base_url: str,
    collector: ScraplingCollector | None = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> CompaniesHouseCompanyResponse:
    """Fetch the company profile for one company number. Raises a typed
    CompaniesHouseError subclass for every documented failure mode; never
    lets a raw requests/scrapling exception escape."""
    from datetime import datetime, timezone

    validate_company_number_format(company_number)
    company_number = company_number.strip().upper()
    collector = collector or ScraplingCollector()

    url = f"{base_url}/company/{company_number}"
    headers = {"Authorization": _basic_auth_header(api_key)}
    try:
        result = collector.fetch_static(url, headers=headers, timeout=timeout, max_retries=max_retries)
    except FetchError as exc:
        message = scrub_secrets(str(exc))
        logger.warning(
            "companies_house_request_failed", extra={"extra_fields": {"url": url, "error": message}}
        )
        raise CompaniesHouseProviderError(f"Companies House request failed: {message}") from exc

    if result.status_code in (401, 403):
        raise CompaniesHouseAuthenticationError(
            f"Companies House authentication failed ({result.status_code}): {_parse_error_body(result.content)}"
        )
    if result.status_code == 404:
        raise CompaniesHouseNotFoundError(f"Companies House has no record for company number {company_number} (404)")
    if result.status_code == 429:
        raise CompaniesHouseRateLimitError(
            "Companies House rate limit exceeded (429) -- 600 requests per 5-minute window per API key"
        )
    if result.status_code >= 500:
        raise CompaniesHouseProviderError(
            f"Companies House returned server error {result.status_code}: {_parse_error_body(result.content)}"
        )
    if result.status_code >= 400:
        raise CompaniesHouseProviderError(
            f"Companies House returned unexpected status {result.status_code}: {_parse_error_body(result.content)}"
        )

    try:
        profile = json.loads(result.content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompaniesHouseProviderError(
            f"Companies House response for {company_number} was not valid JSON: {result.content[:300]!r}"
        ) from exc

    if not isinstance(profile, dict) or "company_number" not in profile:
        raise CompaniesHouseProviderError(
            f"Companies House response for {company_number} did not match the documented shape "
            f"(missing 'company_number'): {result.content[:300]!r}"
        )

    return CompaniesHouseCompanyResponse(
        company_number=company_number,
        profile=profile,
        retrieved_at_iso=datetime.now(timezone.utc).isoformat(),
        source_url=url,
    )
