"""One-command client-demo setup for Super CRM.

Generates a synthetic demo dataset (app.cli.generate_demo_dataset), imports
it through the existing custom-source pipeline (app.cli.import_custom_source
-- normalization, entity resolution, evidence/provenance, exactly like every
other source) as a clearly-labeled "Super CRM Demo Dataset" source, then
seeds a handful of saved searches built from the real filter engine
(app.search.filter_types) so the demo has something to click on immediately.

NOT REAL DATA. See app/cli/generate_demo_dataset.py's module docstring.
Every observation this produces is OBSERVED verification_type at a
below-registry confidence weight (app.source_adapters.custom_file_adapter's
_CUSTOM_SOURCE_CONFIDENCE=0.4) -- same as any other user-uploaded file, never
silently promoted to VERIFIED.

    python -m app.cli.seed_demo
    python -m app.cli.seed_demo --count 900 --reset

--reset deletes any previously-imported demo data (companies/observations/
evidence/jobs tied to the demo Source, plus prior demo saved searches)
before re-importing, so re-running this command is idempotent rather than
accumulating duplicates.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.cli.generate_demo_dataset import DEFAULT_COUNT, DEFAULT_OUT_PATH, DEFAULT_SEED, FIELD_MAPPING, generate_rows, write_csv
from app.cli.import_custom_source import _execute
from app.db.base import SessionLocal
from app.models.company import Company
from app.models.evidence import Evidence, EvidenceObservation
from app.models.ingestion_job import IngestionJob
from app.models.match_candidate import EntityMatchCandidate
from app.models.observation import RawObservation
from app.models.saved_search import SavedSearch
from app.models.source import Source
from app.search.filter_types import FilterCondition, FilterDataType, FilterGroup, FilterOperator

DEMO_SOURCE_NAME = "super_crm_demo_dataset"
# Exactly "Super CRM Demo Dataset" -- this is the literal, user-facing name
# the frontend matches on to render DEMO badges (see frontend/src/lib/demo.ts).
DEMO_SOURCE_DISPLAY_NAME = "Super CRM Demo Dataset"
DEMO_DECLARED_ORIGIN = (
    "Synthetically generated for client demonstrations -- see "
    "app/cli/generate_demo_dataset.py. NOT real company data."
)
# Matches the frontend's CREATED_BY placeholder constant exactly
# (frontend/src/app/discover/page.tsx) -- the Discover page's saved-searches
# list is filtered by created_by, so a seeded search under any other name
# would be invisible in the UI even though it exists in the database.
DEMO_SAVED_SEARCH_CREATED_BY = "frontend-operator"


def _cond(field: str, operator: FilterOperator, value, data_type: FilterDataType) -> dict:
    return FilterCondition(field=field, operator=operator, value=value, data_type=data_type).model_dump(mode="json")


def _group(op: str, conditions: list[dict]) -> dict:
    return FilterGroup(op=op, conditions=conditions).model_dump(mode="json")


def _demo_saved_searches() -> list[dict]:
    maharashtra_manufacturing = [
        FilterCondition(field="industry", operator=FilterOperator.EQ, value="Manufacturing", data_type=FilterDataType.STRING),
        FilterCondition(field="state", operator=FilterOperator.EQ, value="Maharashtra", data_type=FilterDataType.STRING),
    ]
    return [
        dict(
            name="Maharashtra Manufacturers",
            filter_definition=FilterGroup(op="AND", conditions=maharashtra_manufacturing).model_dump(mode="json"),
        ),
        dict(
            name="Large Maharashtra Manufacturers",
            filter_definition=FilterGroup(
                op="AND",
                conditions=[
                    *maharashtra_manufacturing,
                    FilterCondition(field="employees", operator=FilterOperator.GTE, value=20, data_type=FilterDataType.NUMBER),
                    FilterCondition(field="revenue_inr", operator=FilterOperator.GTE, value=100_000_000, data_type=FilterDataType.NUMBER),
                ],
            ).model_dump(mode="json"),
        ),
        dict(
            name="High Revenue Indian Companies",
            filter_definition=FilterCondition(
                field="revenue_inr", operator=FilterOperator.GTE, value=250_000_000, data_type=FilterDataType.NUMBER
            ).model_dump(mode="json"),
        ),
        dict(
            name="Export Manufacturers",
            filter_definition=FilterGroup(
                op="AND",
                conditions=[
                    FilterCondition(field="industry", operator=FilterOperator.EQ, value="Manufacturing", data_type=FilterDataType.STRING),
                    FilterCondition(field="export_status", operator=FilterOperator.EQ, value=True, data_type=FilterDataType.BOOLEAN),
                ],
            ).model_dump(mode="json"),
        ),
    ]


def _reset_demo_data(db) -> None:
    source = db.scalar(select(Source).where(Source.name == DEMO_SOURCE_NAME))
    if source is not None:
        company_ids = [
            row[0]
            for row in db.execute(
                select(RawObservation.company_id).where(RawObservation.source_id == source.id).distinct()
            ).all()
            if row[0] is not None
        ]
        obs_ids = select(RawObservation.id).where(RawObservation.source_id == source.id)
        db.query(EvidenceObservation).filter(EvidenceObservation.observation_id.in_(obs_ids)).delete(synchronize_session=False)
        db.query(EntityMatchCandidate).filter(
            EntityMatchCandidate.observation_id.in_(obs_ids)
        ).delete(synchronize_session=False)
        db.query(RawObservation).filter(RawObservation.source_id == source.id).delete(synchronize_session=False)
        if company_ids:
            db.query(Evidence).filter(Evidence.company_id.in_(company_ids)).delete(synchronize_session=False)
            db.query(Company).filter(Company.id.in_(company_ids)).delete(synchronize_session=False)
        db.query(IngestionJob).filter(IngestionJob.source_id == source.id).delete(synchronize_session=False)
        db.delete(source)
    db.query(SavedSearch).filter(SavedSearch.created_by == DEMO_SAVED_SEARCH_CREATED_BY).delete(synchronize_session=False)
    db.commit()


def _mark_source_as_demo(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == DEMO_SOURCE_NAME))
    if source is None:
        raise RuntimeError(f"Expected import to have created Source '{DEMO_SOURCE_NAME}'")
    source.display_name = DEMO_SOURCE_DISPLAY_NAME
    source.countries = ["IN"]
    source.license_notes = (
        "SYNTHETIC DEMONSTRATION DATA -- not a real data provider, not verified, not licensed content. "
        "Generated by app/cli/generate_demo_dataset.py for client/product demos. "
        f"{DEMO_DECLARED_ORIGIN}"
    )
    db.commit()
    return source


def _seed_saved_searches(db) -> list[SavedSearch]:
    created = []
    for spec in _demo_saved_searches():
        existing = db.scalar(
            select(SavedSearch).where(
                SavedSearch.name == spec["name"], SavedSearch.created_by == DEMO_SAVED_SEARCH_CREATED_BY
            )
        )
        if existing is not None:
            created.append(existing)
            continue
        saved = SavedSearch(
            name=spec["name"],
            created_by=DEMO_SAVED_SEARCH_CREATED_BY,
            country_scope=["IN"],
            filter_definition=spec["filter_definition"],
            sort=[],
            selected_fields=[],
        )
        db.add(saved)
        created.append(saved)
    db.commit()
    return created


def run(*, count: int, seed: int, reset: bool) -> dict:
    db = SessionLocal()
    try:
        if reset:
            print("Removing any previously-imported demo data...")
            _reset_demo_data(db)

        print(f"Generating {count} synthetic demo companies (seed={seed})...")
        rows = generate_rows(count=count, seed=seed)
        write_csv(rows, DEFAULT_OUT_PATH)
        print(f"  wrote {DEFAULT_OUT_PATH}")

        args = argparse.Namespace(
            file=str(DEFAULT_OUT_PATH),
            source_name=DEMO_SOURCE_NAME,
            mapping=json.dumps(FIELD_MAPPING),
            mapping_file=None,
            declared_origin=DEMO_DECLARED_ORIGIN,
            dry_run=False,
        )
        print(f"Importing through the custom-source pipeline as '{DEMO_SOURCE_NAME}'...")
        stats = _execute(args)

        source = _mark_source_as_demo(db)
        saved_searches = _seed_saved_searches(db)

        summary = {
            "source_id": str(source.id),
            "source_name": source.name,
            "rows_read": stats.rows_read,
            "valid_rows": stats.valid_rows,
            "invalid_rows": stats.invalid_rows,
            "new_companies": stats.new_companies,
            "existing_companies": stats.existing_companies,
            "ambiguous_matches": stats.ambiguous_matches,
            "no_match": stats.no_match,
            "errors": len(stats.errors),
            "saved_searches": [s.name for s in saved_searches],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        print()
        print("=" * 70)
        print("DEMO SETUP COMPLETE")
        print("=" * 70)
        for k, v in summary.items():
            print(f"  {k:20s} {v}")
        return summary
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"Number of demo companies (default {DEFAULT_COUNT})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Random seed (default {DEFAULT_SEED})")
    parser.add_argument("--reset", action="store_true", help="Remove any previously-imported demo data first")
    args = parser.parse_args()
    run(count=args.count, seed=args.seed, reset=args.reset)


if __name__ == "__main__":
    main()
