"""Tests for app/source_adapters/companies_house_client.py. No real network
calls -- every HTTP interaction is a fake ScraplingCollector double
returning a canned FetchResult (or raising FetchError). Normal pytest runs
must never consume a real Companies House API call; see
docs/companies_house_data_access.md and app/cli/companies_house_lookup.py
for where the one real (manual, human-invoked) call in this project would
happen.
"""

import base64
import json
from datetime import datetime, timezone

import pytest

from app.ingestion.collectors.scrapling_collector import FetchError
from app.source_adapters.base import FetchResult
from app.source_adapters.companies_house_client import (
    CompaniesHouseAuthenticationError,
    CompaniesHouseInvalidCompanyNumberError,
    CompaniesHouseNotFoundError,
    CompaniesHouseProviderError,
    CompaniesHouseRateLimitError,
    fetch_company,
    validate_company_number_format,
)

VALID_COMPANY_NUMBER = "00000006"
BASE_URL = "https://api.company-information.service.gov.uk"


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


def _json_result(status: int, payload: dict, url: str = f"{BASE_URL}/company/{VALID_COMPANY_NUMBER}") -> FetchResult:
    return FetchResult(
        url=url,
        status_code=status,
        content=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestValidateCompanyNumberFormat:
    def test_accepts_well_formed_all_digit_number(self):
        validate_company_number_format(VALID_COMPANY_NUMBER)  # does not raise

    def test_accepts_well_formed_scotland_prefix(self):
        validate_company_number_format("SC123456")  # does not raise

    def test_rejects_wrong_length(self):
        with pytest.raises(CompaniesHouseInvalidCompanyNumberError):
            validate_company_number_format("SHORT")

    def test_rejects_empty(self):
        with pytest.raises(CompaniesHouseInvalidCompanyNumberError):
            validate_company_number_format("")


class TestFetchCompany:
    def test_successful_lookup_returns_profile(self):
        profile = {"company_number": VALID_COMPANY_NUMBER, "company_name": "TEST COMPANY LIMITED"}
        collector = _FakeCollector([_json_result(200, profile)])

        result = fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

        assert result.company_number == VALID_COMPANY_NUMBER
        assert result.profile == profile

    def test_sends_api_key_as_basic_auth_username_with_blank_password(self):
        profile = {"company_number": VALID_COMPANY_NUMBER, "company_name": "TEST COMPANY LIMITED"}
        collector = _FakeCollector([_json_result(200, profile)])

        fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_secret123", base_url=BASE_URL, collector=collector)

        auth_header = collector.calls[0]["headers"]["Authorization"]
        assert auth_header.startswith("Basic ")
        decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode("utf-8")
        assert decoded == "ch_test_secret123:"

    def test_invalid_company_number_never_reaches_network(self):
        collector = _FakeCollector([])  # would raise IndexError if called
        with pytest.raises(CompaniesHouseInvalidCompanyNumberError):
            fetch_company("BAD", api_key="ch_test_key", base_url=BASE_URL, collector=collector)
        assert collector.calls == []

    def test_401_raises_authentication_error(self):
        collector = _FakeCollector([_json_result(401, {"errors": [{"error": "Invalid Authorization"}]})])
        with pytest.raises(CompaniesHouseAuthenticationError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="", base_url=BASE_URL, collector=collector)

    def test_403_raises_authentication_error(self):
        collector = _FakeCollector([_json_result(403, {"errors": [{"error": "forbidden"}]})])
        with pytest.raises(CompaniesHouseAuthenticationError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_bad", base_url=BASE_URL, collector=collector)

    def test_404_raises_not_found_error(self):
        collector = _FakeCollector([_json_result(404, {"errors": [{"error": "not-found"}]})])
        with pytest.raises(CompaniesHouseNotFoundError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

    def test_429_raises_rate_limit_error(self):
        collector = _FakeCollector([_json_result(429, {"errors": [{"error": "rate-limited"}]})])
        with pytest.raises(CompaniesHouseRateLimitError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

    def test_500_raises_provider_error(self):
        collector = _FakeCollector([_json_result(500, {"errors": [{"error": "internal"}]})])
        with pytest.raises(CompaniesHouseProviderError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

    def test_network_failure_raises_provider_error(self):
        collector = _FakeCollector([FetchError("connection reset")])
        with pytest.raises(CompaniesHouseProviderError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

    def test_malformed_response_missing_company_number_raises_provider_error(self):
        collector = _FakeCollector([_json_result(200, {"unexpected": "shape"})])
        with pytest.raises(CompaniesHouseProviderError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)

    def test_non_json_response_raises_provider_error(self):
        result = FetchResult(
            url=f"{BASE_URL}/company/{VALID_COMPANY_NUMBER}",
            status_code=200,
            content=b"<html>not json</html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        collector = _FakeCollector([result])
        with pytest.raises(CompaniesHouseProviderError):
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_key", base_url=BASE_URL, collector=collector)


class TestSecretRedactionInErrors:
    def test_provider_error_message_never_contains_the_raw_api_key(self):
        """The API key is sent as a base64 Basic-Auth token, not embedded in
        the URL -- so unlike FileSure's x-api-key-in-URL case, the raw key
        should simply never appear anywhere in a raised error's message at
        all (network failures only echo the URL/attempt count, never
        headers) -- verified directly rather than assumed."""
        collector = _FakeCollector([FetchError("fetch_static failed for 'https://example.test' after 2 attempt(s)")])
        with pytest.raises(CompaniesHouseProviderError) as exc_info:
            fetch_company(VALID_COMPANY_NUMBER, api_key="ch_test_REALSECRET", base_url=BASE_URL, collector=collector)
        assert "ch_test_REALSECRET" not in str(exc_info.value)
