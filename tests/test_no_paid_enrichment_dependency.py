"""Guards against Super CRM's production workflow ever depending on a paid
per-call enrichment provider (e.g. FileSure-style ₹-per-call company lookup
APIs).

Company data must come only from the evidence-backed pipeline documented in
docs/ingestion.md: Source Adapter -> Raw Observation -> Normalization ->
Entity Resolution -> Evidence/Confidence -> Canonical Company. Search,
company-profile access, and background ingestion jobs must never trigger an
outbound call to a paid provider, and the app must start without any such
provider's API key configured.

FileSure (Tier 4, docs/source_strategy.md) is implemented in this codebase
-- app/source_adapters/filesure_adapter.py -- as an explicit example of how
a licensed/paid provider is allowed to exist: gated behind its own settings
(collection_enabled defaults to False, no API key by default), invoked only
by the explicit `python -m app.cli.filesure_lookup` CLI, and never reachable
from search, company-profile access, or unscoped background ingestion. This
file used to assert FileSure had zero code footprint under app/ at all;
that's no longer the right guarantee to enforce now that Tier 4 has a real
example. What actually matters -- and what stays enforced below -- is that
its presence changes nothing about the paths these tests exercise.
"""

import socket

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.ingestion.jobs.celery_app import celery_app
from app.ingestion.jobs.tasks import run_source_collection
from app.main import app
from app.models.company import Company
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.filesure_adapter import FileSureAdapter
from app.source_adapters.filesure_client import FileSureConfigurationError

client = TestClient(app)


class _BlockOutboundConnections:
    """Monkeypatches socket.create_connection -- the choke point real HTTP
    clients (requests/urllib3/httpx/curl_cffi) go through -- to fail loudly
    if anything under test attempts a real network connection. Proves "no
    outbound call occurred" for arbitrary code, not just a hardcoded
    FileSure check. Deliberately does NOT patch socket.socket.connect
    directly: on Windows, asyncio's ProactorEventLoop opens a loopback
    self-pipe via raw socket calls as internal plumbing, unrelated to any
    HTTP request, and patching at that level produces false positives."""

    def __enter__(self):
        self._original_create_connection = socket.create_connection

        def _blocked_create_connection(address, *args, **kwargs):
            raise AssertionError(f"Unexpected outbound network connection attempted to {address!r}")

        socket.create_connection = _blocked_create_connection
        return self

    def __exit__(self, exc_type, exc, tb):
        socket.create_connection = self._original_create_connection


class TestFileSureIsGatedNotAutomatic:
    """FileSure existing in the codebase is fine (see module docstring) --
    what must hold is that it's inert by default and never self-invokes."""

    def test_filesure_settings_default_to_disabled_and_empty(self):
        get_settings.cache_clear()
        try:
            settings = Settings(_env_file=None)
        finally:
            get_settings.cache_clear()
        assert settings.filesure_collection_enabled is False
        assert settings.filesure_api_key == ""

    def test_filesure_adapter_refuses_to_run_when_collection_disabled(self, monkeypatch):
        """The config-level gate in FileSureAdapter.fetch() -- independent
        of any DB/Source state -- is what makes FileSure "not automatic"
        even though it has a real adapter. Patches get_settings() directly
        (not just os.environ) so this can't be defeated by a real
        FILESURE_API_KEY sitting in a local .env -- see
        tests/test_filesure_compliance_and_cli.py for the sibling coverage
        this mirrors."""
        monkeypatch.setattr(
            "app.source_adapters.filesure_adapter.get_settings",
            lambda: Settings(filesure_collection_enabled=False, filesure_api_key="fsk_test_x"),
        )
        adapter = FileSureAdapter(source_name="filesure")
        with pytest.raises(FileSureConfigurationError):
            adapter.fetch("L74110KA2013PLC096530")


class TestAppStartsWithoutFileSureConfig:
    def test_settings_construct_without_filesure_env_vars(self, monkeypatch):
        for var in ("FILESURE_API_KEY", "FILESURE_ENV", "FILESURE_COLLECTION_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        get_settings.cache_clear()
        try:
            settings = Settings(_env_file=None)
            assert settings.database_url  # constructs fine with no FileSure config at all
        finally:
            get_settings.cache_clear()

    def test_health_endpoint_works_without_filesure_env_vars(self, monkeypatch):
        for var in ("FILESURE_API_KEY", "FILESURE_ENV", "FILESURE_COLLECTION_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        response = client.get("/health")
        assert response.status_code == 200


class TestNoOutboundCallsDuringNormalOperations:
    def test_search_makes_no_outbound_network_call(self, db):
        db.add(
            Company(
                canonical_name="ABC Industries",
                normalized_name="abc industries",
                state="Maharashtra",
                employee_range_min=50,
                employee_range_max=200,
                confidence=0.9,
            )
        )
        db.commit()

        with _BlockOutboundConnections():
            response = client.post(
                "/api/search/companies",
                json={"state": "Maharashtra", "employee_min": 20, "limit": 5},
            )
        assert response.status_code == 200
        assert response.json()["total_returned"] == 1

    def test_company_profile_makes_no_outbound_network_call(self, db):
        company = Company(canonical_name="ABC Industries", normalized_name="abc industries", confidence=0.9)
        db.add(company)
        db.commit()

        with _BlockOutboundConnections():
            response = client.get(f"/api/companies/{company.id}")
        assert response.status_code == 200

    def test_background_ingestion_job_makes_no_outbound_network_call(self, db, website_source, monkeypatch):
        """Ingestion goes through the registered SourceAdapter only -- proves
        the Celery task layer never reaches out to a paid provider on the
        side, in addition to test_jobs.py's adapter-selection coverage."""

        class _FakeAdapter(SourceAdapter):
            source_type = "website"
            collector_version = "fake/1.0"

            def __init__(self, source_name: str):
                self.source_name = source_name

            def fetch(self, target: str) -> FetchResult:
                from datetime import datetime, timezone

                return FetchResult(
                    url=target, status_code=200, content=b"", content_type="text/html",
                    fetched_at=datetime.now(timezone.utc),
                )

            def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
                return [ParsedRecord(external_ref="rec-1", fields={"canonical_name": "Test Co"})]

            def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
                return [
                    ObservationDraft(
                        field="canonical_name", raw_value="Test Co", normalized_value="test co",
                        confidence=0.5, verification_type="observed",
                    )
                ]

        fake = _FakeAdapter(website_source.name)
        monkeypatch.setattr("app.ingestion.jobs.tasks._adapter_for", lambda source: fake)

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        try:
            with _BlockOutboundConnections():
                result = run_source_collection.apply(
                    args=[str(website_source.id), "https://example.test/a", "2026-01-01"]
                ).get()
        finally:
            celery_app.conf.task_always_eager = False

        assert result["status"] == "success"
