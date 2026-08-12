"""Vertical-slice demo: two sources -> one canonical company.

Run with:  python scripts/demo_vertical_slice.py

Ingests a synthetic company-website fixture (OBSERVED) and a real-shaped MCA
Company Master Data CSV fixture (VERIFIED), shows the entity-resolution
system correctly refusing to auto-merge them on name similarity alone,
simulates a human reviewer confirming the match, and prints the resulting
canonical profile with full evidence/provenance -- then runs a structured
search filter equivalent to the product spec's example query.

This script uses fixtures, not live network calls -- see
tests/fixtures/html/example_company_website.html and
tests/fixtures/data/mca_company_master_maharashtra.csv for why (no vetted
live website source has been approved yet; see docs/compliance.md).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles default to a legacy codepage (e.g. cp1252) that can't
# encode "₹" and other non-ASCII characters this script prints; force
# UTF-8 so the demo doesn't crash mid-run on Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.compliance.source_policy import SourcePolicy
from app.db.base import SessionLocal
from app.ingestion.pipeline import confirm_match, ingest_parsed_record
from app.models.company import Company, CompanyAlias
from app.models.evidence import Evidence, EvidenceObservation
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration
from app.models.ingestion_job import IngestionJob
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.models.source import Source
from app.search.filters import CompanySearchFilters
from app.search.query import build_company_query
from app.source_adapters.base import FetchResult
from app.source_adapters.government_dataset_adapter import GovernmentDatasetAdapter
from app.source_adapters.website_adapter import WebsiteAdapter

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _policy(source: Source) -> SourcePolicy:
    return SourcePolicy(
        source_name=source.name,
        collection_enabled=source.collection_enabled,
        rate_limit_per_minute=source.rate_limit_per_minute,
        max_concurrency=source.max_concurrency,
    )


def main() -> None:
    db = SessionLocal()

    _section("0. Reset demo data")
    # Deletion order respects FK constraints (children before parents) --
    # mirrors tests/conftest.py's _CLEANUP_ORDER. IngestionJob/CompanyFinancials/
    # CompanyGSTRegistration must be cleared before Source/Company, or a prior
    # run (e.g. `python -m app.cli.seed_dev`) that created rows referencing
    # them leaves this DELETE FROM sources/companies failing on a FK violation.
    for model in [
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
    ]:
        db.query(model).delete()
    db.commit()
    print("Cleared demo tables.")

    _section("1. Register sources (compliance registry)")
    website_source = Source(
        name="example_company_website",
        source_type="website",
        countries=["IN"],
        collection_enabled=True,
        rate_limit_per_minute=10,
        max_concurrency=1,
        reliability_weight=40,
        license_notes="Synthetic fixture -- no live site targeted. See docs/compliance.md.",
    )
    mca_source = Source(
        name="mca_company_master_data",
        source_type="government_dataset",
        countries=["IN"],
        collection_enabled=True,
        rate_limit_per_minute=30,
        max_concurrency=2,
        reliability_weight=95,
        license_notes="Government Open Data License - India (GODL), published by MCA via data.gov.in",
    )
    db.add_all([website_source, mca_source])
    db.commit()
    print(f"  - {website_source.name} (reliability={website_source.reliability_weight}, enabled={website_source.collection_enabled})")
    print(f"  - {mca_source.name} (reliability={mca_source.reliability_weight}, enabled={mca_source.collection_enabled})")

    _section("2. Collect from MCA (government open data) via ScraplingCollector-fetched CSV")
    mca_adapter = GovernmentDatasetAdapter(source_name=mca_source.name)
    mca_fr = FetchResult(
        url="https://data.gov.in/resource/company-master-data-maharashtra.csv",
        status_code=200,
        content=(FIXTURES / "data" / "mca_company_master_maharashtra.csv").read_bytes(),
        content_type="text/csv",
        fetched_at=datetime.now(timezone.utc),
    )
    mca_record = mca_adapter.parse(mca_fr)[0]
    mca_result = ingest_parsed_record(db, mca_adapter, mca_source, _policy(mca_source), mca_record)
    db.commit()
    print(f"MCA record CIN={mca_record.external_ref} -> decision={mca_result.decision}, company_id={mca_result.company_id}")

    _section("3. Collect from the company website via Scrapling (Fetcher + Selector)")
    website_adapter = WebsiteAdapter(source_name=website_source.name)
    web_fr = FetchResult(
        url="https://www.abcindustries.example/about",
        status_code=200,
        content=(FIXTURES / "html" / "example_company_website.html").read_bytes(),
        content_type="text/html",
        fetched_at=datetime.now(timezone.utc),
    )
    web_record = website_adapter.parse(web_fr)[0]
    web_result = ingest_parsed_record(db, website_adapter, website_source, _policy(website_source), web_record)
    db.commit()
    print(f"Website record -> decision={web_result.decision} (company_id={web_result.company_id})")
    print(
        "The website observation was NOT auto-merged into the MCA company, even though the\n"
        "name matches exactly -- name similarity + a shared (weak) postal-code signal only\n"
        "reaches the 'review' band, never 'auto_match'. See app/ingestion/entity_resolution/matcher.py."
    )

    _section("4. Human review: confirm the ambiguous match")
    pending = db.query(EntityMatchCandidate).filter_by(status="pending").first()
    print(f"Pending EntityMatchCandidate: score={float(pending.match_score)}, signals={pending.matched_signals}")
    company = confirm_match(db, pending.id, reviewed_by="demo-reviewer@super-crm")
    db.commit()
    print(f"Confirmed -> both sources now resolve to company_id={company.id}")

    _section("5. Canonical company profile (after merging both sources' evidence)")
    db.refresh(company)
    profile = {
        "canonical_name": company.canonical_name,
        "legal_name": company.legal_name,
        "cin": company.cin,
        "state": company.state,
        "postal_code": company.postal_code,
        "industry": company.industry,
        "website": company.website,
        "employee_range": [company.employee_range_min, company.employee_range_max],
        "products": company.products,
        "confidence": float(company.confidence),
        "source_count": company.source_count,
        "last_verified_at": company.last_verified_at.isoformat() if company.last_verified_at else None,
    }
    print(json.dumps(profile, indent=2))

    _section("6. Evidence with per-field confidence + provenance")
    evidence_rows = db.query(Evidence).filter_by(company_id=company.id).order_by(Evidence.field).all()
    for e in evidence_rows:
        obs_links = db.query(EvidenceObservation).filter_by(evidence_id=e.id).all()
        obs = [db.get(RawObservation, link.observation_id) for link in obs_links]
        sources = sorted({db.get(Source, o.source_id).name for o in obs})
        print(
            f"  {e.field:22s} = {e.value!r:42s} conf={float(e.confidence):.3f}  "
            f"[{e.verification_type:9s}]  sources={sources}"
        )

    _section("7. Structured search: \"chemical manufacturers in Maharashtra, "
              "20+ employees, revenue above ₹10cr\" (illustrative -- this fixture "
              "company has no revenue evidence yet, so the revenue filter is shown but not applied)")
    filters = CompanySearchFilters(industry="Chemical", state="Maharashtra", employee_min=20)
    results = list(db.scalars(build_company_query(filters)))
    print(f"Filters: {filters.model_dump(exclude_none=True)}")
    print(f"Matches: {[r.canonical_name for r in results]}")
    assert company.id in {r.id for r in results}, "expected demo company to match the search filters"
    print("\nDemo complete.")

    db.close()


if __name__ == "__main__":
    main()
