import pytest

from app.db.base import SessionLocal
from app.models.company import Company, CompanyAlias
from app.models.evidence import Evidence, EvidenceObservation
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration
from app.models.ingestion_job import IngestionJob
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.models.source import Source

# Deletion order respects FK constraints (children before parents).
_CLEANUP_ORDER = [
    EvidenceObservation,
    EntityMatchCandidate,
    IngestionJob,
    Evidence,
    CompanyFinancials,
    CompanyGSTRegistration,
    RawObservation,
    CompanyAlias,
    Company,
    Source,
]


@pytest.fixture(autouse=True, scope="session")
def _clean_database_before_test_session():
    """Guard against leftover rows from manual/ad-hoc scripts run against the
    same local database outside pytest -- every test run starts from empty
    tables, not just whatever the previous test happened to clean up."""
    session = SessionLocal()
    try:
        for model in _CLEANUP_ORDER:
            session.query(model).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def db():
    """A real session against the local Postgres test database, wiped clean
    after every test. Entity resolution and search rely on Postgres-specific
    behavior (pg_trgm similarity, JSONB), so these tests intentionally run
    against real Postgres rather than a lighter in-memory substitute."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for model in _CLEANUP_ORDER:
            session.query(model).delete()
        session.commit()
        session.close()


@pytest.fixture()
def website_source(db):
    source = Source(
        name="example_company_website",
        source_type="website",
        collection_enabled=True,
        rate_limit_per_minute=10,
        max_concurrency=1,
        reliability_weight=40,
        license_notes="Synthetic fixture source for tests/demo only.",
    )
    db.add(source)
    db.commit()
    return source


@pytest.fixture()
def mca_source(db):
    source = Source(
        name="mca_company_master_data",
        source_type="government_dataset",
        collection_enabled=True,
        rate_limit_per_minute=30,
        max_concurrency=2,
        reliability_weight=95,
        license_notes="Government Open Data License - India (GODL)",
    )
    db.add(source)
    db.commit()
    return source
