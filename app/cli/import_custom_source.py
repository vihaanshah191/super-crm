"""Import a CSV or JSON file through an arbitrary, admin-declared field
mapping (Phase 7, docs/multi_source_architecture.md) -- the generic
counterpart to app/cli/import_mca.py's MCA-specific importer. Goes through
the exact same pipeline (CustomFileAdapter.parse/normalize ->
ingest_parsed_record()) as every other source; nothing here bypasses
entity resolution, confidence, or evidence.

    python -m app.cli.import_custom_source \\
        --file ./data/my_leads.csv \\
        --source-name acme_sales_export \\
        --mapping-file ./data/my_leads_mapping.json \\
        --declared-origin "Internal CRM export, 2026-08-10" \\
        --dry-run

Mapping file format (JSON object, source column name -> canonical field --
see app/source_adapters/custom_field_mapping.py for the full supported
canonical-field list):

    {"Company Name": "legal_name", "CIN Number": "cin", "State": "state",
     "Turnover": "annual_revenue_inr"}

A file is never treated as verified data merely because it was imported
here: every resulting observation is VerificationType.OBSERVED at a
below-registry confidence weight (see custom_file_adapter.py), and tagged
import_provenance_status=custom_source_user_declared. The mapping itself is
validated before anything is read from the file (unknown canonical fields,
no name field mapped, ambiguous double-mappings) -- see
custom_field_mapping.validate_field_mapping(); a mapping with errors
refuses to run at all, matching this task's "do not blindly trust custom
mappings" requirement.

Re-running against the same --source-name reuses that Source row and
overwrites its stored field_mapping/declared_origin with what THIS run
declares (Source.metadata_json) -- a custom source's mapping is expected to
evolve as an admin refines it, unlike a registry adapter's fixed field
names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.compliance.source_policy import SourcePolicy
from app.core.config import get_settings
from app.db.base import SessionLocal
from app.ingestion.pipeline import IngestResult, ingest_parsed_record
from app.models.ingestion_job import IngestionJob
from app.models.source import Source
from app.source_adapters.custom_field_mapping import validate_field_mapping
from app.source_adapters.custom_file_adapter import CustomFileAdapter

IMPORTER_VERSION = "import_custom_source/1.0.0"


@dataclass
class ImportStats:
    rows_read: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    new_companies: int = 0
    existing_companies: int = 0
    ambiguous_matches: int = 0
    no_match: int = 0
    errors: list[str] = field(default_factory=list)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mapping(args: argparse.Namespace) -> dict[str, str]:
    try:
        if args.mapping_file:
            return json.loads(Path(args.mapping_file).read_text())
        if args.mapping:
            return json.loads(args.mapping)
    except (json.JSONDecodeError, OSError) as exc:
        raise _ImportAborted(f"Could not read mapping: {exc}") from exc
    raise _ImportAborted("Either --mapping-file or --mapping is required.")


class _ImportAborted(Exception):
    pass


def _get_or_create_source(db, name: str, *, field_mapping: dict[str, str], declared_origin: str) -> Source:
    source = db.scalar(select(Source).where(Source.name == name))
    if source is not None:
        source.metadata_json = {
            **(source.metadata_json or {}),
            "field_mapping": field_mapping,
            "declared_origin": declared_origin,
        }
        db.commit()
        return source
    source = Source(
        name=name,
        source_type="user_file",
        # Running this CLI with an explicit mapping IS the human
        # authorization step, same convention as import_mca.py's file
        # importer and filesure_lookup.py's Source bootstrap.
        collection_enabled=True,
        rate_limit_per_minute=10_000,  # not meaningfully rate-limited -- no network call happens
        max_concurrency=1,
        reliability_weight=30,  # below every registry/API source -- unverified user-supplied data
        license_notes=f"User-uploaded custom file source. Declared origin: {declared_origin}",
        robots_notes="Not applicable: local file import, no network fetch is performed by this source.",
        metadata_json={"field_mapping": field_mapping, "declared_origin": declared_origin},
    )
    db.add(source)
    db.commit()
    return source


def run(args: argparse.Namespace) -> int:
    """CLI entrypoint: run the import and return a process exit code. See
    _execute() for the version that returns ImportStats directly, used by
    tests that need to assert on the actual counts rather than parse stdout
    (same split as app/cli/import_mca.py)."""
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


def _execute(args: argparse.Namespace) -> ImportStats:
    file_path = Path(args.file)
    if not file_path.is_file():
        raise _ImportAborted(f"File not found: {file_path}")

    mapping = _load_mapping(args)

    issues = validate_field_mapping(mapping)
    has_errors = any(i.severity == "error" for i in issues)
    for issue in issues:
        stream = sys.stderr if issue.severity == "error" else sys.stdout
        print(f"  [{issue.severity}] {issue.message}", file=stream)
    if has_errors:
        raise _ImportAborted("Mapping has errors -- refusing to import. Fix the mapping and retry.")

    raw_bytes = file_path.read_bytes()
    file_sha256 = _sha256_of(file_path)
    import_timestamp = datetime.now(timezone.utc)

    provenance = {
        "import_provenance_status": "custom_source_user_declared",
        "original_filename": file_path.name,
        "file_sha256": file_sha256,
        "import_timestamp": import_timestamp.isoformat(),
        "declared_origin": args.declared_origin,
        "importer_version": IMPORTER_VERSION,
    }

    print(f"File: {file_path}  ({len(raw_bytes)} bytes, sha256={file_sha256})")
    print(f"Field mapping: {mapping}")
    print(f"Mode: {'DRY RUN (no writes)' if args.dry_run else 'REAL IMPORT'}")
    print()

    stats = ImportStats()
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)
    try:
        source = _get_or_create_source(
            db, args.source_name, field_mapping=mapping, declared_origin=args.declared_origin
        )
        policy = SourcePolicy(
            source_name=source.name,
            collection_enabled=source.collection_enabled,
            rate_limit_per_minute=source.rate_limit_per_minute,
            max_concurrency=source.max_concurrency,
            license_notes=source.license_notes or "",
            robots_notes=source.robots_notes or "",
        )
        adapter = CustomFileAdapter(source_name=source.name, field_mapping=mapping)

        job = IngestionJob(
            source_id=source.id,
            status="running",
            idempotency_key=f"cli-custom-import-{import_timestamp.isoformat()}",
            started_at=started_at,
        )
        db.add(job)
        db.flush()

        fetch_result = adapter.fetch(str(file_path))
        records = adapter.parse(fetch_result)
        stats.rows_read = len(records)

        for record in records:
            if not adapter.validate(record):
                stats.invalid_rows += 1
                continue
            stats.valid_rows += 1

            savepoint = db.begin_nested()
            try:
                result: IngestResult = ingest_parsed_record(
                    db, adapter, source, policy, record, extra_observation_metadata=provenance
                )
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
                savepoint.rollback()
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

        finished_at = datetime.now(timezone.utc)
        job.status = "success" if not stats.errors else "partial"
        job.records_discovered = stats.rows_read
        job.records_updated = stats.new_companies + stats.existing_companies
        job.records_failed = len(stats.errors)
        job.finished_at = finished_at

        print("=" * 70)
        print("IMPORT REPORT" + (" (DRY RUN)" if args.dry_run else ""))
        print("=" * 70)
        print(f"  rows read:            {stats.rows_read}")
        print(f"  valid rows:           {stats.valid_rows}")
        print(f"  invalid rows:         {stats.invalid_rows}  (missing a mapped legal_name)")
        print(f"  new companies:        {stats.new_companies}")
        print(f"  existing (matched):   {stats.existing_companies}")
        print(f"  ambiguous (review):   {stats.ambiguous_matches}")
        print(f"  no match:             {stats.no_match}")
        if stats.errors:
            print(f"\n  First {min(10, len(stats.errors))} error(s):")
            for err in stats.errors[:10]:
                print(f"    - {err}")

        if args.dry_run:
            db.rollback()
            print("\nDRY RUN complete -- all writes rolled back (Source-row bootstrap aside).")
        else:
            db.commit()
            print(f"\nImport committed. IngestionJob id={job.id}")
    finally:
        db.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="Path to the CSV or JSON file to import")
    parser.add_argument(
        "--source-name", required=True, help="Internal Source.name for this custom source (reused across runs)"
    )
    parser.add_argument("--mapping", help="Field mapping as a JSON object string")
    parser.add_argument("--mapping-file", help="Path to a JSON file containing the field mapping")
    parser.add_argument(
        "--declared-origin",
        required=True,
        help="Where this file came from (e.g. 'Internal CRM export', 'Trade association member list 2026')",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would happen; roll back all writes"
    )
    args = parser.parse_args()

    get_settings()  # fail fast on config errors before opening a DB session
    sys.exit(run(args))


if __name__ == "__main__":
    main()
