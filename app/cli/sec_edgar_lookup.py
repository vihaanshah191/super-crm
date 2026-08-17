"""Look up ONE company via SEC EDGAR and run it through the same ingestion
pipeline as every other source.

    python -m app.cli.sec_edgar_lookup --cik 320193 --dry-run
    python -m app.cli.sec_edgar_lookup --cik 320193

SEC EDGAR requires no API key, but SEC's fair-access policy requires a
compliant User-Agent (SEC_EDGAR_USER_AGENT) identifying the requester --
checked directly in SecEdgarAdapter.fetch(). This command creates the
`sec_edgar` Source row with collection_enabled=True the first time it runs,
since running this CLI with an explicit CIK is itself the human
authorization step -- mirroring app/cli/filesure_lookup.py and
app/cli/companies_house_lookup.py's Source bootstrap.

--dry-run runs the real pipeline (fetch -> parse -> validate -> normalize
-> entity resolution -> evidence computation) so the reported company-match
decision and evidence are the SAME decision a real run would make, then
rolls back every write at the end (the Source-row bootstrap aside).

IMPORTANT: SEC EDGAR only covers companies that file with the SEC (public
companies) -- see docs/sec_edgar_data_access.md for the full coverage
limitations (no employee count, no incorporation date, USD revenue only).
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
from app.source_adapters.sec_edgar_adapter import SecEdgarAdapter
from app.source_adapters.sec_edgar_client import SecEdgarError
from app.source_adapters.sec_edgar_field_mapping import compare_fields, select_annual_revenue

# Apple Inc. -- a well-known, innocuous public company (not a real
# investigation target), used the same way Companies House's own example
# company number is used as this CLI's default.
DEFAULT_TEST_CIK = "320193"
SEC_EDGAR_SOURCE_NAME = "sec_edgar"


def _get_or_create_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == SEC_EDGAR_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=SEC_EDGAR_SOURCE_NAME,
        display_name="SEC EDGAR (US public company filings)",
        source_type="public_filing",
        countries=["US"],
        access_method="official_api",
        compliance_status="active",
        collection_enabled=True,
        # 10 requests/second limit, confirmed live -- 600/min stays safely
        # under that even accounting for the 2 calls (submissions +
        # companyfacts) this adapter makes per lookup.
        rate_limit_per_minute=300,
        max_concurrency=1,
        reliability_weight=95,
        license_notes=(
            "SEC EDGAR (data.sec.gov) -- official US Securities and Exchange Commission filing "
            "repository, public domain US government data. Covers SEC-registered (public) "
            "companies only, not the private-company universe. See docs/sec_edgar_data_access.md."
        ),
        robots_notes="Not applicable -- REST API, not a scraped page. Requires a compliant User-Agent per SEC policy.",
    )
    db.add(source)
    db.commit()
    return source


def run(cik: str, *, dry_run: bool) -> int:
    settings = get_settings()
    print(f"CIK: {cik}")
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
        adapter = SecEdgarAdapter(source_name=source.name)

        try:
            fetch_result = adapter.fetch(cik)
        except SecEdgarError as exc:
            print(f"SEC EDGAR request failed: {scrub_secrets(str(exc))}", file=sys.stderr)
            return 1

        records = adapter.parse(fetch_result)
        if not records:
            print("SEC EDGAR returned no usable company data for this CIK.", file=sys.stderr)
            return 1
        record = records[0]

        envelope = json.loads(fetch_result.content.decode("utf-8"))
        submission_keys = list((envelope.get("submissions") or {}).keys())

        print("=" * 70)
        print("NORMALIZED FIELDS (SEC EDGAR submissions -> canonical)")
        print("=" * 70)
        for key, value in record.fields.items():
            if key.startswith("_"):
                continue
            print(f"  {key:25s} = {value!r}")

        comparison = compare_fields(submission_keys)
        print(f"\n  matched canonical fields: {comparison.matched_canonical_fields}")
        print(f"  unknown fields (present in response, not in our mapping): {comparison.unknown_fields or '(none)'}")
        print(f"  expected-but-missing canonical fields: {comparison.missing_canonical_fields or '(none)'}")

        revenue = select_annual_revenue(envelope.get("company_facts"))
        print()
        print("REVENUE (from XBRL company facts, if available):")
        if revenue:
            print(
                f"  ${revenue.value_usd:,} USD, FY{revenue.fiscal_year}, "
                f"concept={revenue.concept}, form={revenue.form}"
            )
        else:
            print("  (no usable revenue figure found -- see company_facts_error below if present)")
        if envelope.get("company_facts_error"):
            print(f"  company_facts_error: {envelope['company_facts_error']}")

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
                f"  {draft.field:25s} = {draft.normalized_value!r:40s} "
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
    parser.add_argument("--cik", default=DEFAULT_TEST_CIK, help=f"SEC CIK to look up (default: {DEFAULT_TEST_CIK}, Apple Inc.)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; roll back all writes")
    args = parser.parse_args()

    get_settings()  # fail fast on config errors before opening a DB session
    sys.exit(run(args.cik, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
