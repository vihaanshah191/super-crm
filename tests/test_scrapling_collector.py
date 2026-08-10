"""Tests for the ScraplingCollector wrapper. No live network calls: retry/
normalize_response are exercised with fabricated Scrapling Response objects
and simple stand-in callables, and SSRF blocking is verified against literal
IP targets (no DNS lookup needed, so no live dependency)."""

from datetime import datetime, timezone

import pytest

from app.compliance.url_safety import UnsafeURLError
from app.ingestion.collectors.scrapling_collector import (
    FetchError,
    ResponseTooLargeError,
    ScraplingCollector,
)
from app.source_adapters.base import FetchResult


def _make_response(status=200, url="https://example.test/page", body=b"<html></html>", headers=None):
    from scrapling.engines.toolbelt.custom import Response

    return Response(
        url=url,
        content=body,
        status=status,
        reason="OK" if status < 400 else "Error",
        cookies={},
        headers=headers or {"Content-Type": "text/html"},
        request_headers={},
    )


<<<<<<< HEAD
=======
class TestFetchStaticCallsScraplingWithOneAttempt:
    """Regression test: fetch_static() previously passed retries=0 to
    Fetcher.get(), which Scrapling treats as "make zero attempts" (a
    `for attempt in range(0)` loop that never executes), not "no retries" --
    every real network call silently failed with "No active session
    available." before ever reaching curl_cffi. This was never caught
    because every other test here mocks at the collector.retry() level,
    never exercising the actual kwargs fetch_static() builds. See
    docs/filesure_data_access.md for how this was found."""

    def test_passes_retries_equal_to_one_not_zero(self, monkeypatch):
        captured_kwargs = {}

        def fake_get(url, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_response(status=200)

        monkeypatch.setattr("scrapling.fetchers.Fetcher.get", staticmethod(fake_get))

        collector = ScraplingCollector()
        collector.fetch_static("https://example.com/page")

        assert captured_kwargs["retries"] == 1

    def test_impersonate_setting_is_forwarded(self, monkeypatch):
        captured_kwargs = {}

        def fake_get(url, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_response(status=200)

        monkeypatch.setattr("scrapling.fetchers.Fetcher.get", staticmethod(fake_get))

        collector = ScraplingCollector()
        monkeypatch.setattr(collector._settings, "scrapling_impersonate", "")
        collector.fetch_static("https://example.com/page")

        assert captured_kwargs["impersonate"] is None
        assert captured_kwargs["stealthy_headers"] is False


>>>>>>> 3698f6932ecf2969d1d18f2fc5466ee0f4fd2b55
class TestNormalizeResponse:
    def test_converts_response_to_fetch_result(self):
        collector = ScraplingCollector()
        response = _make_response(body=b"<html><body>hi</body></html>")
        result = collector.normalize_response(response)
        assert isinstance(result, FetchResult)
        assert result.status_code == 200
        assert result.content_type == "text/html"
        assert b"hi" in result.content

    def test_rejects_oversized_response(self, monkeypatch):
        collector = ScraplingCollector()
        # collector._settings is the process-wide lru_cache'd Settings singleton
        # -- use monkeypatch (auto-restoring) rather than a direct assignment,
        # which would leak into every other test in the run.
        monkeypatch.setattr(collector._settings, "scrapling_max_response_bytes", 10)
        response = _make_response(body=b"x" * 1000)
        with pytest.raises(ResponseTooLargeError):
            collector.normalize_response(response)

    def test_header_lookup_is_case_insensitive(self):
        collector = ScraplingCollector()
        response = _make_response(headers={"content-type": "application/json"})
        result = collector.normalize_response(response)
        assert result.content_type == "application/json"


class TestExtract:
    def test_css_extraction(self):
        collector = ScraplingCollector()
        fetch_result = FetchResult(
            url="https://example.test/page",
            status_code=200,
            content=b"<html><body><h1 class='name'>ABC Industries</h1></body></html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        assert collector.extract(fetch_result, css="h1.name::text") == ["ABC Industries"]

    def test_xpath_extraction(self):
        collector = ScraplingCollector()
        fetch_result = FetchResult(
            url="https://example.test/page",
            status_code=200,
            content=b"<html><body><span>hello</span></body></html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        assert collector.extract(fetch_result, xpath="//span/text()") == ["hello"]

    def test_extract_requires_a_selector(self):
        collector = ScraplingCollector()
        fetch_result = FetchResult(
            url="https://example.test/page",
            status_code=200,
            content=b"<html></html>",
            content_type="text/html",
            fetched_at=datetime.now(timezone.utc),
        )
        with pytest.raises(ValueError):
            collector.extract(fetch_result)


class TestRetry:
    def test_succeeds_first_try_without_retry(self):
        collector = ScraplingCollector()
        calls = []

        def fn():
            calls.append(1)
            return _make_response(status=200)

        result = collector.retry(fn, max_retries=3, source_name="test", target="x")
        assert result.status == 200
        assert len(calls) == 1

    def test_retries_on_exception_then_succeeds(self, monkeypatch):
        collector = ScraplingCollector()
        monkeypatch.setattr("app.ingestion.collectors.scrapling_collector.time.sleep", lambda _: None)
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("boom")
            return _make_response(status=200)

        result = collector.retry(fn, max_retries=3, source_name="test", target="x")
        assert result.status == 200
        assert attempts["n"] == 3

    def test_retries_on_5xx_then_succeeds(self, monkeypatch):
        collector = ScraplingCollector()
        monkeypatch.setattr("app.ingestion.collectors.scrapling_collector.time.sleep", lambda _: None)
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            return _make_response(status=503 if attempts["n"] < 2 else 200)

        result = collector.retry(fn, max_retries=3, source_name="test", target="x")
        assert result.status == 200
        assert attempts["n"] == 2

    def test_gives_up_after_max_retries(self, monkeypatch):
        collector = ScraplingCollector()
        monkeypatch.setattr("app.ingestion.collectors.scrapling_collector.time.sleep", lambda _: None)

        def fn():
            raise ConnectionError("always fails")

        with pytest.raises(FetchError):
            collector.retry(fn, max_retries=2, source_name="test", target="x")

    def test_does_not_retry_4xx(self, monkeypatch):
        collector = ScraplingCollector()
        monkeypatch.setattr("app.ingestion.collectors.scrapling_collector.time.sleep", lambda _: None)
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            return _make_response(status=404)

        result = collector.retry(fn, max_retries=3, source_name="test", target="x")
        assert result.status == 404
        assert attempts["n"] == 1

    def test_never_retries_unsafe_url_error(self):
        collector = ScraplingCollector()
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            raise UnsafeURLError("blocked")

        with pytest.raises(UnsafeURLError):
            collector.retry(fn, max_retries=3, source_name="test", target="x")
        assert attempts["n"] == 1


class TestFetchStaticSSRFGuard:
    def test_blocks_loopback_target(self):
        collector = ScraplingCollector()
        with pytest.raises(UnsafeURLError):
            collector.fetch_static("http://127.0.0.1/admin")

    def test_blocks_private_network_target(self):
        collector = ScraplingCollector()
        with pytest.raises(UnsafeURLError):
            collector.fetch_static("http://10.0.0.5/internal")

    def test_blocks_cloud_metadata_endpoint(self):
        collector = ScraplingCollector()
        with pytest.raises(UnsafeURLError):
            collector.fetch_static("http://169.254.169.254/latest/meta-data/")

    def test_blocks_non_http_scheme(self):
        collector = ScraplingCollector()
        with pytest.raises(UnsafeURLError):
            collector.fetch_static("file:///etc/passwd")
