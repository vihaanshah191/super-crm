"""Import an officially-obtained MCA Company Master Data CSV/JSON file from
disk, through the SAME normalization / observation / entity-resolution /
canonicalization pipeline the live data.gov.in adapter uses.

    python -m app.cli.import_mca \\
        --file ./data/mca.csv \\
        --source-url "https://www.data.gov.in/catalog/company-master-data" \\
        --limit 1000 \\
        --dry-run

--source-url is required: this command refuses to run without it. A file
handed to this importer is not treated as verified MCA data merely because
it was passed here -- every resulting observation is tagged
`import_provenance_status=file_import_user_declared` (never
`platform_verified`), because we have no way to independently confirm a
local file's authenticity. Declaring where it came from is the minimum bar;
it is not a claim that we checked.

Two transports, one pipeline:

    data.gov.in API  ---\\
                          +--> GovernmentDatasetAdapter.parse/normalize -->
    local file (here) --/          ingest_parsed_record() --> Company

This file does not reimplement parsing, normalization, entity resolution, or
evidence computation -- it calls the exact same functions
app/source_adapters/government_dataset_adapter.py and
app/ingestion/pipeline.py already expose, so there is exactly one place any
of that logic lives.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.compliance.source_policy import SourcePolicy
from app.core.config import get_settings
from app.db.base import SessionLocal
from app.ingestion.pipeline import IngestResult, ingest_parsed_record
from app.models.company import Company
from app.models.evidence import Evidence
from app.models.ingestion_job import IngestionJob
from app.models.source import Source
from app.source_adapters.base import FetchResult
from app.source_adapters.government_dataset_adapter import (
    DATASET_LICENSE,
    DATASET_NAME,
    DATASET_PUBLISHER,
    GovernmentDatasetAdapter,
    clean_numeric_string,
    records_from_rows,
    sniff_and_parse_rows,
)
from app.source_adapters.mca_field_mapping import map_external_fields

IMPORTER_VERSION = "import_mca/1.0.0"
FILE_IMPORT_SOURCE_NAME = "mca_company_master_data_file_import"


@dataclass
class ImportStats:
    rows_read: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    missing_cin: int = 0
    malformed_cin: int = 0
    duplicate_cins: int = 0
    duplicate_normalized_names: int = 0
    missing_incorporation_date: int = 0
    missing_state: int = 0
    malformed_monetary_values: int = 0
    parsing_failures: int = 0
    new_companies: int = 0
    existing_companies: int = 0
    ambiguous_matches: int = 0
    no_match: int = 0
    touched_company_ids: list[uuid.UUID] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_or_create_file_import_source(db, *, license_text: str) -> Source:
    """A distinct Source row from the live-API 'mca_company_master_data'
    source. This one represents a human-supervised local file import: no
    network fetch happens, so the automated-collection compliance gate
    (Source.collection_enabled) is a different question here than it is for
    the live API path -- a person already obtained this file through an
    official channel before running this command. collection_enabled=True
    here does NOT mean the live API is enabled; that stays a separate row,
    separately False, per docs/mca_data_access.md.
    """
    source = db.scalar(select(Source).where(Source.name == FILE_IMPORT_SOURCE_NAME))
    if source is not None:
        return source
    source = Source(
        name=FILE_IMPORT_SOURCE_NAME,
        source_type="government_dataset",
        collection_enabled=True,
        rate_limit_per_minute=10_000,  # not meaningfully rate-limited -- no network call happens
        max_concurrency=1,
        reliability_weight=95,
        license_notes=license_text,
        robots_notes="Not applicable: local file import, no network fetch is performed by this source.",
    )
    db.add(source)
    db.commit()
    return source


def _monetary_is_malformed(raw: str) -> bool:
    return bool(raw) and clean_numeric_string(raw) is None


def run(args: argparse.Namespace) -> int:
    """CLI entrypoint: run the import and return a process exit code. See
    _execute() for the version that returns ImportStats directly, used by
    tests that need to assert on the actual counts rather than parse stdout."""
    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1
    try:
        _execute(args)
    except _ImportAborted as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


class _ImportAborted(Exception):
    pass


def _execute(args: argparse.Namespace) -> ImportStats:
    file_path = Path(args.file)
    if not file_path.is_file():
        raise _ImportAborted(f"File not found: {file_path}")

    raw_bytes = file_path.read_bytes()
    file_sha256 = _sha256_of(file_path)
    import_timestamp = datetime.now(timezone.utc)

    try:
        text = raw_bytes.decode("utf-8-sig")
        all_rows = sniff_and_parse_rows(text)
    except Exception as exc:  # noqa: BLE001 - report cleanly, this is a CLI entrypoint
        raise _ImportAborted(f"Failed to parse {file_path}: {exc}") from exc

    offset = args.offset
    windowed_rows = all_rows[offset : offset + args.limit] if args.limit is not None else all_rows[offset:]

    stats = ImportStats(rows_read=len(windowed_rows))

    content_type = "application/json" if file_path.suffix.lower() == ".json" else "text/csv"
    fetch_result = FetchResult(
        url=args.source_url,
        status_code=200,
        content=raw_bytes,
        content_type=content_type,
        fetched_at=import_timestamp,
        metadata={"local_file_path": str(file_path.resolve())},
    )

    file_provenance = {
        "import_provenance_status": "file_import_user_declared",
        "original_filename": file_path.name,
        "file_sha256": file_sha256,
        "import_timestamp": import_timestamp.isoformat(),
        "dataset_name": args.dataset_name,
        "dataset_publisher": DATASET_PUBLISHER,
        "dataset_license": args.license,
        "dataset_publication_date": args.dataset_publication_date,
        "official_source_url": args.source_url,
        "importer_version": IMPORTER_VERSION,
    }

    print(f"File: {file_path}  ({len(raw_bytes)} bytes, sha256={file_sha256})")
    print(f"Declared source: {args.source_url}")
    print(f"Rows in file: {len(all_rows)}  |  window: offset={offset} limit={args.limit or '(all)'}  |  rows in window: {len(windowed_rows)}")
    print(f"Mode: {'DRY RUN (no company data will be written)' if args.dry_run else 'REAL IMPORT'}")
    print()

    db = SessionLocal()
    started_at = datetime.now(timezone.utc)
    try:
        source = _get_or_create_file_import_source(db, license_text=args.license)
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
            license_notes=source.license_notes or "",
            robots_notes=source.robots_notes or "",
        )
        adapter = GovernmentDatasetAdapter(source_name=source.name)

        job = IngestionJob(
            source_id=source.id,
            status="running",
            idempotency_key=f"cli-import-{import_timestamp.isoformat()}",
            started_at=started_at,
        )
        db.add(job)
        db.flush()

        seen_cins: Counter[str] = Counter()
        seen_normalized_names: Counter[str] = Counter()

        records = records_from_rows(windowed_rows, source_url=args.source_url)

        # records_from_rows() silently drops rows with no usable CIN (see its
        # docstring) -- they never become a ParsedRecord, so they'd otherwise
        # be invisible to this report. Count them here, against the raw rows,
        # before the loop below (which only sees `records`).
        for row in windowed_rows:
            mapped = map_external_fields(row)
            if not str(mapped.get("cin") or "").strip():
                stats.missing_cin += 1
        stats.invalid_rows += stats.missing_cin

        for record in records:
            seen_cins[record.external_ref] += 1
            if seen_cins[record.external_ref] > 1:
                stats.duplicate_cins += 1

            f = record.fields
            if not f.get("date_of_registration"):
                stats.missing_incorporation_date += 1
            if not f.get("registered_state"):
                stats.missing_state += 1
            if _monetary_is_malformed(f.get("authorized_capital", "")) or _monetary_is_malformed(
                f.get("paidup_capital", "")
            ):
                stats.malformed_monetary_values += 1

            normalized_name = (f.get("company_name") or "").strip().lower()
            if normalized_name:
                seen_normalized_names[normalized_name] += 1
                if seen_normalized_names[normalized_name] > 1:
                    stats.duplicate_normalized_names += 1

            if not adapter.validate(record):
                stats.invalid_rows += 1
                if len(f.get("cin", "")) != 21:
                    stats.malformed_cin += 1
                continue
            stats.valid_rows += 1

            savepoint = db.begin_nested()
            try:
                result: IngestResult = ingest_parsed_record(
                    db, adapter, source, policy, record, extra_observation_metadata=file_provenance
                )
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
                savepoint.rollback()
                stats.parsing_failures += 1
                stats.errors.append(f"{record.external_ref}: {exc}")
                continue

            if result.decision == "new_company":
                stats.new_companies += 1
            elif result.decision == "auto_match":
                stats.existing_companies += 1
            elif result.decision == "review":
                stats.ambiguous_matches += 1
            else:
                stats.no_match += 1
            if result.company_id:
                stats.touched_company_ids.append(result.company_id)

        finished_at = datetime.now(timezone.utc)
        job.status = "success" if stats.parsing_failures == 0 else "partial"
        job.records_discovered = stats.rows_read
        job.records_updated = stats.new_companies + stats.existing_companies
        job.records_failed = stats.parsing_failures
        job.finished_at = finished_at

        duration_seconds = (finished_at - started_at).total_seconds()

        _print_report(stats, duration_seconds=duration_seconds, dry_run=args.dry_run)

        if args.dry_run:
            db.rollback()
            print("\nDRY RUN complete -- all company/observation/job writes were rolled back.")
        else:
            db.commit()
            print(f"\nImport committed. IngestionJob id={job.id}")
            if stats.touched_company_ids:
                _print_post_import_quality_report(db, stats.touched_company_ids)
    finally:
        db.close()

    return stats


def _print_report(stats: ImportStats, *, duration_seconds: float, dry_run: bool) -> None:
    print("=" * 70)
    print("IMPORT REPORT" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 70)
    print(f"  rows read:                    {stats.rows_read}")
    print(f"  valid rows:                   {stats.valid_rows}")
    print(f"  invalid rows:                 {stats.invalid_rows}")
    print(f"    missing CIN:                {stats.missing_cin}")
    print(f"    malformed CIN:              {stats.malformed_cin}")
    print(f"  duplicate CINs (within file): {stats.duplicate_cins}")
    print(f"  duplicate normalized names:   {stats.duplicate_normalized_names}")
    print(f"  missing incorporation date:   {stats.missing_incorporation_date}")
    print(f"  missing state:                {stats.missing_state}")
    print(f"  malformed monetary values:    {stats.malformed_monetary_values}")
    print(f"  parsing/ingestion failures:   {stats.parsing_failures}")
    print(f"  new companies:                {stats.new_companies}")
    print(f"  existing companies (matched): {stats.existing_companies}")
    print(f"  ambiguous matches (review):   {stats.ambiguous_matches}")
    print(f"  no match / not ingested:      {stats.no_match}")
    print(f"  ingestion duration:           {duration_seconds:.2f}s")
    if stats.errors:
        print(f"\n  First {min(10, len(stats.errors))} error(s):")
        for err in stats.errors[:10]:
            print(f"    - {err}")


def _print_post_import_quality_report(db, touched_company_ids: list[uuid.UUID]) -> None:
    """Phase-3-style post-import summary: confidence distribution and up to
    20 representative canonical Company records with their evidence. Only
    meaningful after a real (non-dry-run) import, since it queries what was
    actually committed."""
    unique_ids = list(dict.fromkeys(touched_company_ids))
    companies = list(db.scalars(select(Company).where(Company.id.in_(unique_ids))))

    print()
    print("=" * 70)
    print("DATA QUALITY: CONFIDENCE DISTRIBUTION")
    print("=" * 70)
    buckets: dict[str, int] = defaultdict(int)
    for c in companies:
        conf = float(c.confidence or 0)
        bucket = f"{int(conf * 5) * 20}-{int(conf * 5) * 20 + 20}%"
        buckets[bucket] += 1
    for bucket in sorted(buckets, key=lambda b: int(b.split("-")[0])):
        print(f"  {bucket}: {buckets[bucket]} companies")

    print()
    print("=" * 70)
    print(f"REPRESENTATIVE COMPANIES (up to 20 of {len(companies)} touched)")
    print("=" * 70)
    for company in companies[:20]:
        print(f"\n- {company.canonical_name}  (cin={company.cin}, confidence={company.confidence})")
        evidence_rows = list(db.scalars(select(Evidence).where(Evidence.company_id == company.id)))
        for e in evidence_rows:
            print(f"    {e.field:30s} = {e.value!r:40s} conf={e.confidence} [{e.verification_type}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="Path to the MCA CSV or JSON export file")
    parser.add_argument(
        "--source-url",
        required=True,
        help="Where this file was officially obtained from (required -- a file is never treated "
        "as verified MCA data just because it was passed to this importer)",
    )
    parser.add_argument(
        "--license", default=DATASET_LICENSE, help=f"License covering this file (default: {DATASET_LICENSE!r})"
    )
    parser.add_argument("--dataset-name", default=DATASET_NAME, help=f"Dataset name (default: {DATASET_NAME!r})")
    parser.add_argument(
        "--dataset-publication-date", default=None, help="Dataset's own publication/update date, if known (ISO date)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Import at most this many rows from the window")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many rows before the window starts")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would happen; roll back all writes (no company data persisted)"
    )
    args = parser.parse_args()

    get_settings()  # fail fast on config errors before opening a DB session
    sys.exit(run(args))


if __name__ == "__main__":
    main()
