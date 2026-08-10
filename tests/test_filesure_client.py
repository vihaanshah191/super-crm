"""Tests for app/source_adapters/filesure_client.py. No real network calls --
every HTTP interaction is a fake ScraplingCollector double returning a
canned FetchResult (or raising FetchError), the same style used by
tests/test_scrapling_collector.py's retry tests. Normal pytest runs must
never consume a real FileSure API call; see docs/filesure_data_access.md
and the module docstring in app/cli/filesure_lookup.py for where the one
real call in this project happens (a deliberate, manual, one-time
verification step, not part of the test suite).
"""

import json
from datetime import datetime, timezone

import pytest

from app.ingestion.collectors.scrapling_collector import FetchError
from app.source_adapters.base import FetchResult
from app.source_adapters.filesure_client import (
    FileSureAuthenticationError,
    FileSureInvalidCINError,
    FileSureNotFoundError,
    FileSureProviderError,
    FileSureRateLimitError,
    fetch_company,
    validate_cin_format,
)

VALID_CIN = "L74110KA2013PLC096530"


class _FakeCollector:
    """Test double for ScraplingCollector -- returns a scripted sequence of
    FetchResults (or raises) per call, one entry per fetch_static() call in
    order (master data call, then extractions call)."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def fetch_static(self, url, *, headers=None, timeout=None, max_retries=2):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout, "max_retries": max_retries})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json_result(status: int, payload: dict, url: str = "https://api.filesure.in/v1/companies/X") -> FetchResult:
    return FetchResult(
        url=url,
        status_code=status,
        content=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
        fetched_at=datetime.now(timezone.utc),
    )


class TestValidateCinFormat:
    def test_accepts_well_formed_cin(self):
        validate_cin_format(VALID_CIN)  # does not raise

    def test_rejects_wrong_length(self):
        with pytest.raises(FileSureInvalidCINError):
            validate_cin_format("SHORT123")

    def test_rejects_empty(self):
        with pytest.raises(FileSureInvalidCINError):
            validate_cin_format("")

    def test_rejects_wrong_pattern(self):
        with pytest.raises(FileSureInvalidCINError):
            validate_cin_format("1234567890123456789AB")


class TestFetchCompanyMasterData:
    def test_successful_lookup_returns_master_data(self):
        master_payload = {"data": {"cin": VALID_CIN, "masterData": {"companyData": {"cin": VALID_CIN}}}}
        collector = _FakeCollector([_json_result(200, master_payload), _json_result(200, {"data": {}})])

        result = fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

        assert result.cin == VALID_CIN
        assert result.master_data == master_payload["data"]

    def test_sends_api_key_in_x_api_key_header(self):
        master_payload = {"data": {"cin": VALID_CIN, "masterData": {"companyData": {}}}}
        collector = _FakeCollector([_json_result(200, master_payload), _json_result(200, {"data": {}})])

        fetch_company(VALID_CIN, api_key="fsk_test_secret123", base_url="https://api.filesure.in/v1", collector=collector)

        assert collector.calls[0]["headers"]["x-api-key"] == "fsk_test_secret123"

    def test_invalid_cin_never_reaches_network(self):
        collector = _FakeCollector([])  # would raise IndexError if called
        with pytest.raises(FileSureInvalidCINError):
            fetch_company("BAD", api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)
        assert collector.calls == []

    def test_401_raises_authentication_error(self):
        collector = _FakeCollector([_json_result(401, {"error": {"code": "MISSING_API_KEY", "message": "API key is required."}})])
        with pytest.raises(FileSureAuthenticationError):
            fetch_company(VALID_CIN, api_key="", base_url="https://api.filesure.in/v1", collector=collector)

    def test_403_raises_authentication_error(self):
        collector = _FakeCollector([_json_result(403, {"error": {"code": "INVALID_API_KEY", "message": "bad key"}})])
        with pytest.raises(FileSureAuthenticationError):
            fetch_company(VALID_CIN, api_key="fsk_test_bad", base_url="https://api.filesure.in/v1", collector=collector)

    def test_404_raises_not_found_error(self):
        collector = _FakeCollector([_json_result(404, {"error": {"code": "NOT_FOUND", "message": "no such CIN"}})])
        with pytest.raises(FileSureNotFoundError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_429_raises_rate_limit_error(self):
        collector = _FakeCollector([_json_result(429, {"error": {"code": "RATE_LIMITED", "message": "slow down"}})])
        with pytest.raises(FileSureRateLimitError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_500_raises_provider_error(self):
        collector = _FakeCollector([_json_result(500, {"error": {"code": "INTERNAL", "message": "oops"}})])
        with pytest.raises(FileSureProviderError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_network_failure_raises_provider_error(self):
        collector = _FakeCollector([FetchError("connection reset")])
        with pytest.raises(FileSureProviderError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_timeout_raises_provider_error(self):
        collector = _FakeCollector([FetchError("fetch_static timed out after 30s")])
        with pytest.raises(FileSureProviderError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_malformed_response_missing_data_key_raises_provider_error(self):
        collector = _FakeCollector([_json_result(200, {"unexpected": "shape"})])
        with pytest.raises(FileSureProviderError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_non_json_response_raises_provider_error(self):
        result = FetchResult(
            url="https://api.filesure.in/v1/companies/X",
            status_code=200,
            content=b"<html>not json</html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        collector = _FakeCollector([result])
        with pytest.raises(FileSureProviderError):
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

    def test_extractions_failure_does_not_block_master_data(self):
        """A failure fetching /extractions must not prevent the master-data
        result from being returned -- financial data is best-effort."""
        master_payload = {"data": {"cin": VALID_CIN, "masterData": {"companyData": {"cin": VALID_CIN}}}}
        collector = _FakeCollector([_json_result(200, master_payload), _json_result(500, {"error": {"code": "INTERNAL", "message": "extractions down"}})])

        result = fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

        assert result.master_data == master_payload["data"]
        assert result.extractions_raw is None
        assert result.extractions_error is not None

    def test_extractions_success_is_preserved_raw(self):
        master_payload = {"data": {"cin": VALID_CIN, "masterData": {"companyData": {"cin": VALID_CIN}}}}
        extractions_payload = {"data": {"some": "future-schema-field"}}
        collector = _FakeCollector([_json_result(200, master_payload), _json_result(200, extractions_payload)])

        result = fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)

        assert result.extractions_raw == extractions_payload
        assert result.extractions_error is None


class TestSecretRedactionInErrors:
    def test_provider_error_message_does_not_leak_api_key_from_url(self):
        """If a FetchError's message embeds the request URL (as
        ScraplingCollector's does), any api-key query param must be
        redacted before it reaches a FileSureError message."""
        leaking_error = FetchError(
            "fetch_static failed for 'https://api.filesure.in/v1/companies/X?api-key=fsk_test_REALSECRET' after 2 attempt(s)"
        )
        collector = _FakeCollector([leaking_error])
        with pytest.raises(FileSureProviderError) as exc_info:
            fetch_company(VALID_CIN, api_key="fsk_test_x", base_url="https://api.filesure.in/v1", collector=collector)
        assert "REALSECRET" not in str(exc_info.value)
