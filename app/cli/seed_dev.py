"""Create obviously-synthetic companies for frontend development.

    python -m app.cli.seed_dev --yes

Every seeded company uses a placeholder name (Acme / Example / Demo /
Sample / Placeholder / Fictitious ...) and a clearly-fake CIN -- never a
real company identity. This is for exercising the frontend UI (Discover,
company profile, evidence/provenance, financial history, GST registrations,
ingestion status, entity-resolution review queue) against something other
than an empty database; it must never be pointed at a production database.
`--yes` is required so this can't run by accident.

Idempotent: re-running clears and recreates only the rows this command owns
(tagged via the "dev_seed_synthetic" Source and a fixed list of synthetic
company names) -- it never touches real ingested data.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.base import SessionLocal
from app.ingestion.pipeline import recompute_company_evidence
from app.models.company import Company, CompanyAlias
from app.models.financials import CompanyFinancials
from app.models.gst_registration import CompanyGSTRegistration
from app.models.ingestion_job import IngestionJob
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.models.source import Source

DEV_SEED_SOURCE_NAME = "dev_seed_synthetic"

# (canonical_name, fake_cin, state, city, postal_code, industry, category,
#  employee_min, employee_max, revenue_inr_or_None, gstin_state_code)
_SYNTHETIC_COMPANIES = [
    (
        "Acme Industrial Systems Pvt Ltd",
        "U00000MH0000PTC000001",
        "Maharashtra",
        "Pune",
        "411001",
        "Industrial Equipment Manufacturing",
        "manufacturer",
        80,
        250,
        180_000_000,
        "27",
    ),
    (
        "Example Chemicals Pvt Ltd",
        "U00000GJ0000PTC000002",
        "Gujarat",
        "Ahmedabad",
        "380001",
        "Specialty Chemicals",
        "manufacturer",
        40,
        120,
        95_000_000,
        "24",
    ),
    (
        "Demo Engineering Works",
        "U00000TN0000PTC000003",
        "Tamil Nadu",
        "Chennai",
        "600001",
        "Precision Engineering",
        "manufacturer",
        15,
        50,
        None,
        "33",
    ),
    (
        "Sample Textiles Trading Co",
        "U00000MH0000PTC000004",
        "Maharashtra",
        "Mumbai",
        "400001",
        "Textile Trading",
        "distributor",
        10,
        30,
        40_000_000,
        "27",
    ),
    (
        "Placeholder Logistics Services",
        "U00000KA0000PTC000005",
        "Karnataka",
        "Bengaluru",
        "560001",
        "Freight & Logistics",
        "service_provider",
        200,
        500,
        None,
        "29",
    ),
    (
        "Fictitious Auto Components Ltd",
        "U00000GJ0000PTC000006",
        "Gujarat",
        "Surat",
        "395001",
        "Automotive Components",
        "manufacturer",
        300,
        800,
        620_000_000,
        "24",
    ),
]

_SYNTHETIC_NORMALIZED_NAMES = [name.lower() for name, *_ in _SYNTHETIC_COMPANIES]
# CIN, not normalized_name, is the stable identity anchor for cleanup: a
# reviewer confirming a match in the UI runs recompute_company_evidence(),
# which can legitimately change Company.normalized_name to whichever
# observation's value won (see app/ingestion/pipeline._apply_field_to_company).
# Matching cleanup on normalized_name alone can then miss a previously-seeded
# company entirely, leaving its CIN behind and breaking the next seed run
# with a UNIQUE constraint violation on re-insert.
_SYNTHETIC_CINS = [cin for _, cin, *_ in _SYNTHETIC_COMPANIES]


def _clear_previous_seed(db) -> None:
    source = db.scalar(select(Source).where(Source.name == DEV_SEED_SOURCE_NAME))
    if source is not None:
        obs_ids = list(db.scalars(select(RawObservation.id).where(RawObservation.source_id == source.id)))
        if obs_ids:
            db.query(EntityMatchCandidate).filter(EntityMatchCandidate.observation_id.in_(obs_ids)).delete(
                synchronize_session=False
            )
        db.query(RawObservation).filter(RawObservation.source_id == source.id).delete(synchronize_session=False)
        db.query(IngestionJob).filter(IngestionJob.source_id == source.id).delete(synchronize_session=False)
        db.commit()

    existing_ids = list(
        db.scalars(
            select(Company.id).where(
                Company.cin.in_(_SYNTHETIC_CINS) | Company.normalized_name.in_(_SYNTHETIC_NORMALIZED_NAMES)
            )
        )
    )
    for company_id in existing_ids:
        company = db.get(Company, company_id)
        if company is not None:
            db.delete(company)
    db.commit()


def _get_or_create_dev_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == DEV_SEED_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=DEV_SEED_SOURCE_NAME,
        display_name="Dev Seed (synthetic)",
        source_type="directory",
        # Not obtained via any real mechanism -- synthesized in-process --
        # and not available as a real collection source at all, hence
        # NOT_AVAILABLE rather than UNDER_REVIEW (there is nothing to
        # review; this will never become a live source).
        access_method="unknown",
        compliance_status="not_available",
        collection_enabled=False,
        rate_limit_per_minute=0,
        max_concurrency=0,
        reliability_weight=50,
        license_notes="Synthetic development fixture data. Not a real source; never collected from.",
        robots_notes="Not applicable.",
    )
    db.add(source)
    db.commit()
    return source


def _seed_company(db, source: Source, spec: tuple, index: int) -> Company:
    (
        name,
        cin,
        state,
        city,
        postal_code,
        industry,
        category,
        emp_min,
        emp_max,
        revenue,
        gst_state_code,
    ) = spec

    company = Company(
        canonical_name=name,
        normalized_name=name.lower(),
        legal_name=f"{name} (Registered)",
        cin=cin,
        website_domain=f"{name.split()[0].lower()}.example",
        website=f"https://{name.split()[0].lower()}.example",
        city=city,
        state=state,
        country="India",
        postal_code=postal_code,
        industry=industry,
        company_category=category,
        export_status=(index % 2 == 0),
        employee_range_min=emp_min,
        employee_range_max=emp_max,
        annual_revenue_inr=revenue,
        revenue_year=2026 if revenue else None,
        products=[f"{industry} product line {n}" for n in ("A", "B")],
        confidence=0.0,
        source_count=0,
    )
    db.add(company)
    db.flush()

    db.add(CompanyAlias(company_id=company.id, alias=name, normalized_alias=name.lower()))

    now = datetime.now(timezone.utc)
    observations = [
        RawObservation(
            company_id=company.id,
            source_id=source.id,
            source_type=source.source_type,
            field="canonical_name",
            raw_value=name,
            normalized_value=name.lower(),
            collected_at=now,
            confidence=0.9,
            verification_type="observed",
            collector_version="seed_dev/1.0.0",
            metadata_json={"synthetic": True},
        ),
        RawObservation(
            company_id=company.id,
            source_id=source.id,
            source_type=source.source_type,
            field="state",
            raw_value=state,
            normalized_value=state.lower(),
            collected_at=now,
            confidence=0.9,
            verification_type="observed",
            collector_version="seed_dev/1.0.0",
            metadata_json={"synthetic": True},
        ),
        RawObservation(
            company_id=company.id,
            source_id=source.id,
            source_type=source.source_type,
            field="industry",
            raw_value=industry,
            normalized_value=industry,
            collected_at=now,
            confidence=0.7,
            verification_type="observed",
            collector_version="seed_dev/1.0.0",
            metadata_json={"synthetic": True},
        ),
        RawObservation(
            company_id=company.id,
            source_id=source.id,
            source_type=source.source_type,
            field="employee_range_min",
            raw_value=str(emp_min),
            normalized_value=str(emp_min),
            collected_at=now,
            confidence=0.6,
            verification_type="estimated",
            collector_version="seed_dev/1.0.0",
            metadata_json={"synthetic": True},
        ),
        RawObservation(
            company_id=company.id,
            source_id=source.id,
            source_type=source.source_type,
            field="employee_range_max",
            raw_value=str(emp_max),
            normalized_value=str(emp_max),
            collected_at=now,
            confidence=0.6,
            verification_type="estimated",
            collector_version="seed_dev/1.0.0",
            metadata_json={"synthetic": True},
        ),
    ]
    db.add_all(observations)
    db.flush()

    recompute_company_evidence(db, company.id)

    db.add(
        CompanyGSTRegistration(
            company_id=company.id,
            gstin=f"{gst_state_code}ABCDE{1000 + index}F1Z{index % 10}",
            registered_state=state,
            registration_date=date(2019, 1, 1) + timedelta(days=index * 30),
            is_primary=True,
        )
    )

    for offset, year in enumerate(("FY2024", "FY2025", "FY2026")):
        base = revenue or 20_000_000
        db.add(
            CompanyFinancials(
                company_id=company.id,
                financial_year=year,
                annual_revenue_inr=base * (1 + 0.1 * offset) if revenue else None,
                authorized_capital_inr=base * 0.2,
                paidup_capital_inr=base * 0.15,
                verification_type="estimated",
                source_id=source.id,
                collected_at=now,
            )
        )

    db.commit()
    return company


def _seed_ingestion_jobs(db, source: Source) -> None:
    now = datetime.now(timezone.utc)
    jobs = [
        IngestionJob(
            source_id=source.id,
            status="success",
            idempotency_key="dev-seed-job-success",
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
            records_discovered=6,
            records_updated=6,
            records_failed=0,
        ),
        IngestionJob(
            source_id=source.id,
            status="failed",
            idempotency_key="dev-seed-job-failed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1) + timedelta(minutes=1),
            records_discovered=3,
            records_updated=0,
            records_failed=3,
            error_summary="Synthetic failure for UI development: simulated timeout.",
        ),
        IngestionJob(
            source_id=source.id,
            status="running",
            idempotency_key="dev-seed-job-running",
            started_at=now - timedelta(minutes=1),
        ),
    ]
    db.add_all(jobs)
    db.commit()


def _seed_review_queue(db, source: Source, companies: list[Company]) -> None:
    now = datetime.now(timezone.utc)
    candidate_company = companies[0]

    unattached_observation = RawObservation(
        company_id=None,
        source_id=source.id,
        source_type=source.source_type,
        field="canonical_name",
        raw_value="Acme Industrial Systems Private Limited",
        normalized_value="acme industrial systems",
        collected_at=now,
        confidence=0.6,
        verification_type="observed",
        collector_version="seed_dev/1.0.0",
        metadata_json={"synthetic": True},
    )
    db.add(unattached_observation)
    db.flush()

    db.add(
        EntityMatchCandidate(
            observation_id=unattached_observation.id,
            candidate_company_id=candidate_company.id,
            incoming_payload={
                "canonical_name": "acme industrial systems",
                "state": "maharashtra",
                "postal_code": "411001",
            },
            match_score=0.75,
            matched_signals={"name_similarity": 1.0, "postal_code_match": True},
            status="pending",
        )
    )
    db.commit()


def run() -> int:
    db = SessionLocal()
    try:
        print(f"Clearing any previous {DEV_SEED_SOURCE_NAME!r} seed data...")
        _clear_previous_seed(db)

        source = _get_or_create_dev_source(db)

        print(f"Seeding {len(_SYNTHETIC_COMPANIES)} synthetic companies...")
        companies = [
            _seed_company(db, source, spec, index) for index, spec in enumerate(_SYNTHETIC_COMPANIES)
        ]

        print("Seeding ingestion job history...")
        _seed_ingestion_jobs(db, source)

        print("Seeding an entity-resolution review-queue example...")
        _seed_review_queue(db, source, companies)

        print(f"\nDone. Seeded {len(companies)} companies:")
        for c in companies:
            print(f"  - {c.canonical_name}  ({c.city}, {c.state})  confidence={c.confidence}")
        return 0
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--yes", action="store_true", required=True, help="Required confirmation: only run this against a DEVELOPMENT database"
    )
    args = parser.parse_args()
    if not args.yes:
        print("Refusing to run without --yes.", file=sys.stderr)
        sys.exit(1)

    print("*** This writes obviously-synthetic company data. Only run against a DEVELOPMENT database. ***\n")
    sys.exit(run())


if __name__ == "__main__":
    main()
