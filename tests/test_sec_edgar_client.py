"""Tests for app/source_adapters/sec_edgar_client.py. No real network calls
here -- every HTTP interaction is a fake ScraplingCollector double. The one
real, live verification call this project made against SEC EDGAR (which
needs no credential and is explicitly permitted) is documented in
docs/sec_edgar_data_access.md, not repeated in the automated test suite.
"""

import json
from datetime import datetime, timezone

import pytest

from app.ingestion.collectors.scrapling_collector import FetchError
from app.source_adapters.base import FetchResult
from app.source_adapters.sec_edgar_client import (
    SecEdgarInvalidCIKError,
    SecEdgarNotFoundError,
    SecEdgarProviderError,
    SecEdgarRateLimitError,
    fetch_company,
    normalize_cik,
)

VALID_CIK = "320193"
PADDED_CIK = "0000320193"
BASE_URL = "https://data.sec.gov"


class _FakeCollector:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def fetch_static(self, url, *, headers=None, timeout=None, max_retries=2):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, "max_retries": max_retries})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json_result(status: int, payload: dict, url: str = f"{BASE_URL}/submissions/CIK{PADDED_CIK}.json") -> FetchResult:
    return FetchResult(
        url=url,
        status_code=status,
        content=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestNormalizeCik:
    def test_pads_short_number(self):
        assert normalize_cik("320193") == "0000320193"

    def test_already_padded_stays_as_is(self):
        assert normalize_cik("0000320193") == "0000320193"

    def test_accepts_cik_prefix(self):
        assert normalize_cik("CIK0000320193") == "0000320193"

    def test_rejects_non_numeric(self):
        with pytest.raises(SecEdgarInvalidCIKError):
            normalize_cik("NOTANUMBER")

    def test_rejects_too_long(self):
        with pytest.raises(SecEdgarInvalidCIKError):
            normalize_cik("12345678901")


class TestFetchCompany:
    def test_successful_lookup_returns_submissions(self):
        submissions = {"cik": PADDED_CIK, "name": "Test Public Co"}
        collector = _FakeCollector([_json_result(200, submissions), _json_result(200, {"cik": PADDED_CIK, "facts": {}})])

        result = fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

        assert result.cik == PADDED_CIK
        assert result.submissions == submissions

    def test_sends_user_agent_header(self):
        submissions = {"cik": PADDED_CIK, "name": "Test Public Co"}
        collector = _FakeCollector([_json_result(200, submissions), _json_result(200, {"cik": PADDED_CIK, "facts": {}})])

        fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

        assert collector.calls[0]["headers"]["User-Agent"] == "Super CRM Test test@example.test"

    def test_invalid_cik_never_reaches_network(self):
        collector = _FakeCollector([])  # would raise IndexError if called
        with pytest.raises(SecEdgarInvalidCIKError):
            fetch_company("NOTANUMBER", user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)
        assert collector.calls == []

    def test_404_raises_not_found_error(self):
        collector = _FakeCollector([_json_result(404, {})])
        with pytest.raises(SecEdgarNotFoundError):
            fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

    def test_429_raises_rate_limit_error(self):
        collector = _FakeCollector([_json_result(429, {})])
        with pytest.raises(SecEdgarRateLimitError):
            fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

    def test_500_raises_provider_error(self):
        collector = _FakeCollector([_json_result(500, {})])
        with pytest.raises(SecEdgarProviderError):
            fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

    def test_network_failure_raises_provider_error(self):
        collector = _FakeCollector([FetchError("connection reset")])
        with pytest.raises(SecEdgarProviderError):
            fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

    def test_malformed_response_missing_cik_raises_provider_error(self):
        collector = _FakeCollector([_json_result(200, {"unexpected": "shape"})])
        with pytest.raises(SecEdgarProviderError):
            fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

    def test_company_facts_failure_does_not_block_submissions(self):
        """A failure fetching companyfacts must not prevent the
        submissions-based result from being returned -- not every filer has
        XBRL data (see module docstring)."""
        submissions = {"cik": PADDED_CIK, "name": "Test Public Co"}
        collector = _FakeCollector([_json_result(200, submissions), _json_result(500, {})])

        result = fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

        assert result.submissions == submissions
        assert result.company_facts is None
        assert result.company_facts_error is not None

    def test_company_facts_success_is_preserved(self):
        submissions = {"cik": PADDED_CIK, "name": "Test Public Co"}
        facts = {"cik": PADDED_CIK, "entityName": "Test Public Co", "facts": {"us-gaap": {}}}
        collector = _FakeCollector([_json_result(200, submissions), _json_result(200, facts)])

        result = fetch_company(VALID_CIK, user_agent="Super CRM Test test@example.test", base_url=BASE_URL, collector=collector)

        assert result.company_facts == facts
        assert result.company_facts_error is None
