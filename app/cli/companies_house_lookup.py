"""Look up ONE company via the UK Companies House API and run it through the
same ingestion pipeline as every other source.

    python -m app.cli.companies_house_lookup --company-number 00000006 --dry-run
    python -m app.cli.companies_house_lookup --company-number 00000006

Two independent compliance gates must both be satisfied before any request
is made:
  1. COMPANIES_HOUSE_COLLECTION_ENABLED=true -- a config-level hard switch,
     checked directly in CompaniesHouseAdapter.fetch(), independent of
     database state.
  2. Source.collection_enabled=true -- the standard DB-level compliance
     gate (see app/compliance/source_policy.py). This command creates the
     `companies_house` Source row with collection_enabled=True the first
     time it runs, since running this CLI with an explicit company number
     is itself the human authorization step -- mirroring
     app/cli/filesure_lookup.py's Source bootstrap.

--dry-run runs the real pipeline (fetch -> parse -> validate -> normalize
-> entity resolution -> evidence computation) so the reported company-match
decision and evidence are the SAME decision a real run would make, then
rolls back every write at the end (the Source-row bootstrap aside).

See docs/companies_house_data_access.md for verified endpoint/auth/rate-limit
details and known coverage limitations (no filed financial figures).
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.compliance.source_policy import SourcePolicy
from app.core.config import get_settings
from app.core.logging import scrub_secrets
from app.db.base import SessionLocal
from app.ingestion.pipeline import ingest_parsed_record
from app.models.source import Source
from app.source_adapters.companies_house_adapter import CompaniesHouseAdapter
from app.source_adapters.companies_house_client import CompaniesHouseError
from app.source_adapters.companies_house_field_mapping import compare_fields

# Companies House's own "Test the API" example company (a well-known,
# innocuous test fixture -- not a real business being probed).
DEFAULT_TEST_COMPANY_NUMBER = "00000006"
COMPANIES_HOUSE_SOURCE_NAME = "companies_house"


def _get_or_create_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == COMPANIES_HOUSE_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=COMPANIES_HOUSE_SOURCE_NAME,
        display_name="UK Companies House",
        source_type="government_dataset",
        countries=["GB"],
        access_method="official_api",
        compliance_status="active",
        collection_enabled=True,
        # 600 requests / 5 minutes per key, confirmed live (see
        # docs/companies_house_data_access.md) -- rate_limit_per_minute is a
        # per-minute figure, so 600/5 = 120/min stays safely under the limit.
        rate_limit_per_minute=120,
        max_concurrency=1,
        reliability_weight=95,
        license_notes=(
            "UK Companies House Public Data API (api.company-information.service.gov.uk) -- "
            "official UK government company registry, Crown copyright. "
            "See docs/companies_house_data_access.md."
        ),
        robots_notes="Not applicable -- REST API, not a scraped page.",
    )
    db.add(source)
    db.commit()
    return source


def run(company_number: str, *, dry_run: bool) -> int:
    settings = get_settings()
    print(f"Company number: {company_number}")
    print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'REAL INGESTION'}")
    print()

    db = SessionLocal()
    try:
        source = _get_or_create_source(db)
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
            license_notes=source.license_notes or "",
            robots_notes=source.robots_notes or "",
        )
        adapter = CompaniesHouseAdapter(source_name=source.name)

        try:
            fetch_result = adapter.fetch(company_number)
        except CompaniesHouseError as exc:
            print(f"Companies House request failed: {scrub_secrets(str(exc))}", file=sys.stderr)
            return 1

        records = adapter.parse(fetch_result)
        if not records:
            print("Companies House returned no usable company data for this number.", file=sys.stderr)
            return 1
        record = records[0]

        envelope = json.loads(fetch_result.content.decode("utf-8"))
        profile_keys = list((envelope.get("profile") or {}).keys())

        print("=" * 70)
        print("NORMALIZED FIELDS (Companies House profile -> canonical)")
        print("=" * 70)
        for key, value in record.fields.items():
            if key.startswith("_"):
                continue
            print(f"  {key:25s} = {value!r}")

        comparison = compare_fields(profile_keys)
        print(f"\n  matched canonical fields: {comparison.matched_canonical_fields}")
        print(f"  unknown fields (present in response, not in our mapping): {comparison.unknown_fields or '(none)'}")
        print(f"  expected-but-missing canonical fields: {comparison.missing_canonical_fields or '(none)'}")

        if not adapter.validate(record):
            print("\nvalidate(): FAILED -- this record would NOT be ingested.", file=sys.stderr)
            return 1
        print("\nvalidate(): passed")

        drafts = adapter.normalize(record)
        print()
        print("=" * 70)
        print(f"OBSERVATIONS THAT WOULD BE CREATED ({len(drafts)})")
        print("=" * 70)
        for draft in drafts:
            print(
                f"  {draft.field:30s} = {draft.normalized_value!r:40s} "
                f"conf={draft.confidence} [{draft.verification_type}]"
            )

        print()
        print("=" * 70)
        print("ENTITY RESOLUTION / INGESTION")
        print("=" * 70)
        result = ingest_parsed_record(db, adapter, source, policy, record)
        print(f"  decision: {result.decision}")
        print(f"  company_id: {result.company_id}")
        print(f"  observation_ids created: {len(result.observation_ids)}")

        if dry_run:
            db.rollback()
            print("\nDRY RUN complete -- all writes rolled back (Source-row bootstrap aside).")
        else:
            db.commit()
            print("\nIngestion committed.")
        return 0
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--company-number",
        default=DEFAULT_TEST_COMPANY_NUMBER,
        help=f"UK company number to look up (default: {DEFAULT_TEST_COMPANY_NUMBER}, Companies House's own test example)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; roll back all writes")
    args = parser.parse_args()

    get_settings()  # fail fast on config errors before opening a DB session
    sys.exit(run(args.company_number, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
