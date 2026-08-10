"""Regression tests proving a FileSure API key cannot appear in logs or
exception messages, end-to-end through FileSureAdapter.fetch() (not just
the low-level client function already covered in test_filesure_client.py).
Uses a synthetic secret value -- never the real configured key -- so these
tests are meaningful regardless of whether a real key happens to be
configured in the environment they run in.
"""

import logging

import pytest

from app.core.config import Settings
from app.ingestion.collectors.scrapling_collector import FetchError
from app.source_adapters.filesure_adapter import FileSureAdapter
from app.source_adapters.filesure_client import FileSureProviderError

SYNTHETIC_SECRET = "fsk_test_SHOULD_NEVER_APPEAR_IN_OUTPUT_98765"
VALID_CIN = "L74110KA2013PLC096530"


class _LeakyCollector:
    """Simulates ScraplingCollector's real behavior: a FetchError whose
    message embeds the full request URL, including the api-key query
    param, exactly as app.ingestion.collectors.scrapling_collector.retry()
    actually formats its exception message."""

    def fetch_static(self, url, *, headers=None, timeout=None, max_retries=2):
        # Real headers ARE passed here (matching production), so a naive
        # implementation logging `headers` directly would also leak.
        assert headers.get("x-api-key") == SYNTHETIC_SECRET
        raise FetchError(
            f"fetch_static failed for {url!r} after 2 attempt(s): "
            f"connection refused (headers={{'x-api-key': '{SYNTHETIC_SECRET}'}})"
        )


class TestSecretNeverInExceptionMessage:
    def test_adapter_fetch_exception_does_not_contain_key(self, monkeypatch):
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=True, filesure_api_key=SYNTHETIC_SECRET),
        )
        adapter = FileSureAdapter(source_name="filesure", collector=_LeakyCollector())

        with pytest.raises(FileSureProviderError) as exc_info:
            adapter.fetch(VALID_CIN)

        assert SYNTHETIC_SECRET not in str(exc_info.value)

    def test_adapter_fetch_exception_does_not_contain_key_in_repr(self, monkeypatch):
        """Also check repr(), not just str() -- pytest/logging sometimes
        renders exceptions via repr in tracebacks."""
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=True, filesure_api_key=SYNTHETIC_SECRET),
        )
        adapter = FileSureAdapter(source_name="filesure", collector=_LeakyCollector())

        with pytest.raises(FileSureProviderError) as exc_info:
            adapter.fetch(VALID_CIN)

        assert SYNTHETIC_SECRET not in repr(exc_info.value)


class TestSecretNeverInLogOutput:
    def test_warning_log_emitted_on_failure_does_not_contain_key(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=True, filesure_api_key=SYNTHETIC_SECRET),
        )
        adapter = FileSureAdapter(source_name="filesure", collector=_LeakyCollector())

        with caplog.at_level(logging.WARNING):
            with pytest.raises(FileSureProviderError):
                adapter.fetch(VALID_CIN)

        for record in caplog.records:
            assert SYNTHETIC_SECRET not in record.getMessage()
            if hasattr(record, "extra_fields"):
                assert SYNTHETIC_SECRET not in str(record.extra_fields)

    def test_redacting_filter_scrubs_extra_fields_containing_key_in_a_url(self):
        """Direct test of the shared RedactingFilter (app/core/logging.py)
        with a FileSure-shaped URL string under an unrelated key name, the
        same scenario app/source_adapters/filesure_client.py triggers on a
        failed request."""
        from app.core.logging import RedactingFilter

        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0, msg="filesure_request_failed", args=(), exc_info=None
        )
        record.extra_fields = {"url": f"https://api.filesure.in/v1/companies/X?api-key={SYNTHETIC_SECRET}"}

        RedactingFilter().filter(record)

        assert SYNTHETIC_SECRET not in str(record.extra_fields)
        assert "api-key=***" in record.extra_fields["url"]


class TestSecretNeverInCliOutput:
    def test_cli_stderr_does_not_contain_key_on_failure(self, monkeypatch, capsys, db):
        from app.cli import filesure_lookup

        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=True, filesure_api_key=SYNTHETIC_SECRET),
        )

        def _leaky_fetch(self, target):
            raise FileSureProviderError(
                f"FileSure request failed: fetch_static failed for "
                f"'https://api.filesure.in/v1/companies/{target}?api-key={SYNTHETIC_SECRET}' after 2 attempt(s)"
            )

        monkeypatch.setattr(FileSureAdapter, "fetch", _leaky_fetch)

        filesure_lookup.run(VALID_CIN, dry_run=True)

        captured = capsys.readouterr()
        assert SYNTHETIC_SECRET not in captured.out
        assert SYNTHETIC_SECRET not in captured.err
