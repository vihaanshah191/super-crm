"""Look up ONE company via the FileSure API and run it through the same
ingestion pipeline as every other source.

    python -m app.cli.filesure_lookup --cin <CIN> --dry-run
    python -m app.cli.filesure_lookup --cin <CIN>

Two independent compliance gates must both be satisfied before any request
is made:
  1. FILESURE_COLLECTION_ENABLED=true -- a config-level hard switch, checked
     directly in FileSureAdapter.fetch(), independent of database state.
  2. Source.collection_enabled=true -- the standard DB-level compliance
     gate (see app/compliance/source_policy.py). This command creates the
     `filesure` Source row with collection_enabled=True the first time it
     runs, since running this CLI with an explicit CIN is itself the human
     authorization step -- mirroring app/cli/import_mca.py's local-file
     Source, which is enabled the same way and for the same reason.

--dry-run runs the real pipeline (fetch -> parse -> validate -> normalize
-> entity resolution -> evidence computation) so the reported company-match
decision and evidence are the SAME decision a real run would make, then
rolls back every write at the end (the Source-row bootstrap aside, which is
registry metadata, not company data -- same convention as import_mca.py).

Only use CINs FileSure's sandbox actually supports -- do not probe random
CINs against it. See docs/filesure_data_access.md for the one CIN this
research could directly confirm from FileSure's own documentation (used
here as the default, override with --cin).
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
from app.source_adapters.filesure_adapter import FileSureAdapter
from app.source_adapters.filesure_client import FileSureError
from app.source_adapters.filesure_field_mapping import compare_fields

# Swiggy Limited -- confirmed directly from FileSure's own developer-portal
# documentation (a worked curl example), not guessed. See
# docs/filesure_data_access.md for exactly how this was found and what
# "confirmed" means here (FileSure's own example, not an independently
# published sandbox whitelist).
DEFAULT_TEST_CIN = "L74110KA2013PLC096530"
FILESURE_SOURCE_NAME = "filesure"


def _get_or_create_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == FILESURE_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=FILESURE_SOURCE_NAME,
        display_name="FileSure (MCA registry reseller)",
        source_type="registry_data_provider",
        countries=["IN"],
        access_method="official_api",
        # ACTIVE reflects the sandbox/test-key tier specifically (live-
        # verified, see docs/filesure_data_access.md) -- production/bulk
        # access terms are a separate, not-yet-reviewed question (still
        # noted in license_notes below).
        compliance_status="active",
        collection_enabled=True,
        rate_limit_per_minute=30,
        max_concurrency=1,
        reliability_weight=85,
        license_notes=(
            "FileSure API (api.filesure.in) -- third-party MCA registry data reseller. "
            "Sandbox/test-key usage only; production terms not yet reviewed. "
            "See docs/filesure_data_access.md."
        ),
        robots_notes="Not applicable -- REST API, not a scraped page.",
    )
    db.add(source)
    db.commit()
    return source


def run(cin: str, *, dry_run: bool) -> int:
    settings = get_settings()
    print(f"FileSure environment: {settings.filesure_env}")
    print(f"CIN: {cin}")
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
        adapter = FileSureAdapter(source_name=source.name)

        try:
            fetch_result = adapter.fetch(cin)
        except FileSureError as exc:
            print(f"FileSure request failed: {scrub_secrets(str(exc))}", file=sys.stderr)
            return 1

        records = adapter.parse(fetch_result)
        if not records:
            print("FileSure returned no usable company data for this CIN.", file=sys.stderr)
            return 1
        record = records[0]

        envelope = json.loads(fetch_result.content.decode("utf-8"))
        master_data_section = (envelope.get("master_data") or {}).get("masterData") or {}
        company_data_keys = list(master_data_section.get("companyData", {}).keys()) + list(
            master_data_section.get("commonData", {}).keys()
        )

        print("=" * 70)
        print("NORMALIZED FIELDS (FileSure companyData -> canonical)")
        print("=" * 70)
        for key, value in record.fields.items():
            if key.startswith("_"):
                continue
            print(f"  {key:25s} = {value!r}")

        comparison = compare_fields(company_data_keys)
        print(f"\n  matched canonical fields: {comparison.matched_canonical_fields}")
        print(f"  unknown fields (present in response, not in our mapping): {comparison.unknown_fields or '(none)'}")
        print(f"  expected-but-missing canonical fields: {comparison.missing_canonical_fields or '(none)'}")

        if not adapter.validate(record):
            print("\nvalidate(): FAILED -- this record would NOT be ingested (e.g. malformed CIN).", file=sys.stderr)
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
        print("FINANCIAL RECORDS (company_financials)")
        print("=" * 70)
        extractions_error = record.fields.get("_extractions_error")
        extractions_raw = record.fields.get("_extractions_raw")
        if extractions_error:
            print(f"  extractions endpoint error: {extractions_error}")
        elif extractions_raw:
            print(
                "  Raw /extractions response below -- no confirmed field mapping exists yet"
                " (see docs/filesure_data_access.md and"
                " FileSureAdapter._normalize_financials), so nothing is normalized from it:"
            )
            print("  " + json.dumps(extractions_raw, indent=2).replace("\n", "\n  ")[:4000])
        else:
            print("  (extractions endpoint returned no data)")
        print("  -> 0 company_financials records would be created from this call.")

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
    parser.add_argument("--cin", default=DEFAULT_TEST_CIN, help=f"CIN to look up (default: {DEFAULT_TEST_CIN}, see docs/filesure_data_access.md)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; roll back all writes")
    args = parser.parse_args()
    sys.exit(run(args.cin, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
