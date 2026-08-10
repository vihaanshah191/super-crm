"""Wrapper around the Scrapling library (https://github.com/D4Vinci/Scrapling).

This is the ONLY module in the codebase that may `import scrapling`. Everything
else (source adapters, normalization, entity resolution, ...) talks to
`ScraplingCollector` and the source-agnostic `FetchResult` type. If Scrapling
is ever replaced, only this file and its tests need to change.

API surface used (verified against installed scrapling==0.4.12, not guessed):
  - scrapling.fetchers.Fetcher.get(url, **kwargs) -> Response   (plain HTTP)
  - scrapling.fetchers.DynamicFetcher.fetch(url, **kwargs) -> Response  (Playwright)
  - scrapling.parser.Selector(content, url=...) with .css()/.xpath()
  - Response (subclasses Selector) exposes .status, .reason, .headers, .body, .url

Redirect-SSRF protection: fetch_static() passes follow_redirects="safe" to
Fetcher.get(), which maps to curl_cffi's CurlFollow.SAFE mode (CURLOPT_FOLLOWLOCATION=4).
libcurl validates each redirect target against private/loopback/link-local ranges
*before* establishing the TCP connection -- this is pre-redirect protection, not
post-hoc. normalize_response() then re-validates the completed redirect history
as belt-and-suspenders. Remaining known gap: DNS rebinding (a hostname that
resolves to a public IP during the check but a private IP when curl connects) --
this is the same TOCTOU limitation as our url_safety.py pre-fetch check and
cannot be fully closed without a pinned-IP transport. Documented, not silenced.

Prefer fetch_static() for everything. Only use fetch_dynamic() when a source is
explicitly known to require JS rendering -- it launches a real browser and is
far more expensive.
"""

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.compliance.url_safety import UnsafeURLError, assert_safe_url
from app.core.config import get_settings
from app.core.logging import get_logger
from app.source_adapters.base import FetchResult

logger = get_logger(__name__)

COLLECTOR_VERSION = "scrapling-collector/1.0.0"


class FetchError(RuntimeError):
    pass


class ResponseTooLargeError(FetchError):
    pass


def _header(headers: dict[str, Any], name: str, default: str = "") -> str:
    lname = name.lower()
    for k, v in (headers or {}).items():
        if k.lower() == lname:
            return v
    return default


class ScraplingCollector:
    def __init__(self) -> None:
        self._settings = get_settings()

    # -- fetching -----------------------------------------------------------

    def fetch_static(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
    ) -> FetchResult:
        """Plain HTTP fetch (no browser) via Scrapling's Fetcher. This should
        cover the large majority of sources: static HTML, JSON/CSV/XML APIs."""
        assert_safe_url(url)
        from scrapling.fetchers import Fetcher

        def _do_fetch() -> Any:
            return Fetcher.get(
                url,
                headers=headers or {},
                timeout=timeout or self._settings.scrapling_default_timeout_seconds,
                retries=0,  # retries are driven by retry() below for uniform backoff + logging
                follow_redirects="safe",  # CurlFollow.SAFE: validates each redirect target
                max_redirects=5,          # against private IPs *before* following it
            )

        response = self.retry(_do_fetch, max_retries=max_retries, source_name="fetch_static", target=url)
        return self.normalize_response(response)

    def fetch_dynamic(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        timeout: float | None = None,
        max_retries: int = 1,
    ) -> FetchResult:
        """Browser-rendered fetch via Scrapling's DynamicFetcher (Playwright/
        Chromium). Only call this for sources explicitly flagged as requiring
        JS rendering -- do not use it as the default fetch path."""
        assert_safe_url(url)
        try:
            from scrapling.fetchers import DynamicFetcher
        except Exception as exc:  # pragma: no cover - depends on optional browser install
            raise FetchError(
                "Browser-based fetching is unavailable in this environment. "
                "fetch_dynamic() requires Scrapling's browser extras to be installed "
                "(`pip install scrapling[fetchers]` + `scrapling install`)."
            ) from exc

        def _do_fetch() -> Any:
            kwargs: dict[str, Any] = {"network_idle": True}
            if wait_for_selector:
                kwargs["wait_selector"] = wait_for_selector
            if timeout:
                kwargs["timeout"] = timeout * 1000
            return DynamicFetcher.fetch(url, **kwargs)

        response = self.retry(_do_fetch, max_retries=max_retries, source_name="fetch_dynamic", target=url)
        return self.normalize_response(response)

    def crawl(
        self,
        urls: list[str],
        fetcher: Callable[[str], FetchResult] | None = None,
    ) -> list[FetchResult]:
        """Fetch a bounded, adapter-supplied list of URLs.

        Kept sequential/synchronous deliberately: per-source concurrency and
        rate limiting are enforced once, in the ingestion job layer
        (app.ingestion.jobs), rather than duplicated inside the collector. A
        production job should submit one Celery task per URL rather than
        looping here, so retries/failures are tracked per-URL.
        """
        fetch = fetcher or self.fetch_static
        results: list[FetchResult] = []
        for url in urls:
            results.append(fetch(url))
        return results

    # -- parsing --------------------------------------------------------------

    def extract(
        self,
        fetch_result: FetchResult,
        *,
        css: str | None = None,
        xpath: str | None = None,
        adaptive: bool = False,
    ) -> list[str]:
        """Run a CSS or XPath selector against a fetched HTML payload.

        adaptive=True enables Scrapling's similarity-based re-matching, which
        can relocate an element after minor markup changes on the source site.
        """
        from scrapling.parser import Selector

        selector = Selector(fetch_result.content, url=fetch_result.url)
        if css:
            return selector.css(css, adaptive=adaptive).getall()
        if xpath:
            return selector.xpath(xpath, adaptive=adaptive).getall()
        raise ValueError("extract() requires either css= or xpath=")

    # -- retry ------------------------------------------------------------

    def retry(
        self,
        fn: Callable[[], Any],
        *,
        max_retries: int,
        source_name: str,
        target: str,
    ) -> Any:
        """Uniform exponential-backoff retry around a single Scrapling call.

        Retries on: exceptions (network errors, timeouts) and 5xx responses.
        Does NOT retry on 4xx (client errors are not transient) or on
        UnsafeURLError (never retry a blocked SSRF target).
        """
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                result = fn()
            except UnsafeURLError:
                raise
            except Exception as exc:  # noqa: BLE001 - genuinely need to catch any fetcher error
                last_exc = exc
                if attempt >= max_retries:
                    break
                logger.warning(
                    "collector_retry",
                    extra={
                        "extra_fields": {
                            "source": source_name,
                            "target": target,
                            "attempt": attempt + 1,
                            "error": str(exc),
                        }
                    },
                )
                time.sleep(delay)
                delay *= 2
                continue

            status = getattr(result, "status", 200)
            if status >= 500 and attempt < max_retries:
                logger.warning(
                    "collector_retry_5xx",
                    extra={
                        "extra_fields": {
                            "source": source_name,
                            "target": target,
                            "attempt": attempt + 1,
                            "status": status,
                        }
                    },
                )
                time.sleep(delay)
                delay *= 2
                continue
            return result

        raise FetchError(
            f"{source_name} failed for {target!r} after {max_retries + 1} attempt(s)"
        ) from last_exc

    # -- normalization --------------------------------------------------------

    def normalize_response(self, response: Any) -> FetchResult:
        """Convert a Scrapling Response into the source-agnostic FetchResult,
        enforcing the max-response-size guard against oversized payloads."""
        body: bytes = response.body
        if len(body) > self._settings.scrapling_max_response_bytes:
            raise ResponseTooLargeError(
                f"Response from {response.url} is {len(body)} bytes, exceeds "
                f"limit of {self._settings.scrapling_max_response_bytes} bytes"
            )

        # Belt-and-suspenders: fetch_static() already passes follow_redirects="safe"
        # to Fetcher.get(), which validates each redirect target in libcurl *before*
        # following it. This loop re-validates the completed history using our own
        # assert_safe_url() as a second independent check.
        history = getattr(response, "history", None) or []
        for hop in history:
            hop_url = getattr(hop, "url", None)
            if hop_url:
                try:
                    assert_safe_url(hop_url)
                except UnsafeURLError as exc:
                    raise FetchError(
                        f"Redirect chain for {response.url} passed through an unsafe URL"
                    ) from exc

        return FetchResult(
            url=response.url,
            status_code=response.status,
            content=body,
            content_type=_header(response.headers, "content-type"),
            fetched_at=datetime.now(timezone.utc),
            metadata={"reason": getattr(response, "reason", "")},
        )
