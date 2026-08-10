# Ingestion pipeline

## Architecture

```
Source -> Collector (SourceAdapter) -> Raw Observation Storage -> Normalization
        -> Entity Resolution -> Evidence/Confidence Engine -> Canonical Company
        -> Search Index -> Super CRM
```

Each stage is independently replaceable:

| Stage | Module | Notes |
|---|---|---|
| Collector | `app/source_adapters/*`, `app/ingestion/collectors/scrapling_collector.py` | Only place `scrapling` is imported |
| Raw observation storage | `app/models/observation.py` (`raw_observations` table) | Append-only, never overwritten |
| Normalization | `app/ingestion/normalization/*` | Pure functions: name, revenue, employee range, address |
| Entity resolution | `app/ingestion/entity_resolution/*` | Deterministic-first matching, see `docs/entity_resolution.md` |
| Evidence/confidence | `app/ingestion/confidence/engine.py`, `app/models/evidence.py` | Rules/weights, explainable, see `docs/confidence_engine.md` |
| Canonical company | `app/models/company.py` (`companies` table) | Only ever written by `app/ingestion/pipeline.py`, never directly by an adapter |
| Search | `app/search/*`, `app/api/routes/search.py` | Structured filters -> deterministic SQL |
| Orchestration | `app/ingestion/pipeline.py` | Shared by both the demo script and Celery tasks |
| Scheduling/retries | `app/ingestion/jobs/*` | Celery + Beat |

## Multi-valued identifiers and time-series financials

Two fields on `Company` are denormalized *snapshots*, not the sole record:

- **`Company.gstin`** -- a company can hold a separate GST registration in
  each state it operates in ("one Company -> one GSTIN" does not hold).
  `company_gst_registrations` (`app/models/gst_registration.py`) is the
  source of truth: one row per registration, `gstin` globally unique
  (statutory identifier), `is_primary` flags which one `Company.gstin`
  mirrors. Entity resolution's GSTIN exact-match signal should query this
  table, not `companies.gstin`, once GST collection exists.
- **`Company.annual_revenue_inr` / `revenue_range_*_inr` / `revenue_year`**
  -- a single-value field would silently overwrite FY2024 with FY2025 on
  re-ingestion. `company_financials` (`app/models/financials.py`) retains one
  row per `(company_id, financial_year)`; `Company`'s revenue columns mirror
  the most recent year's figures for cheap single-column search filtering,
  while `company_financials` holds the full history.

Both tables are wired into the schema (migration `4920c24524c9`) but **not
yet populated by any adapter** -- no source in this codebase currently
collects multiple GSTINs or per-year financials, so there's nothing to
insert. Wiring `GovernmentDatasetAdapter`/future adapters to actually write
these rows, and updating `recompute_company_evidence()` to keep the
`Company`-level snapshot columns in sync, is follow-up work.

## Why raw observations are never applied directly

`RawObservation` rows are immutable and untyped-per-source: a company's
`state` field might be reported by five different sources with three
different values. `app/ingestion/pipeline.recompute_company_evidence()` is
the *only* code path allowed to write to `Company` columns, and it always
goes through the confidence engine first -- so no adapter, no matter how
trusted its source, writes to the canonical table directly.

## Running collectors locally

The two sources currently implemented target **fixtures**, not live
endpoints -- see `docs/compliance.md` for why. To exercise the pipeline:

```bash
python scripts/demo_vertical_slice.py
```

This ingests `tests/fixtures/html/example_company_website.html` and
`tests/fixtures/data/mca_company_master_maharashtra.csv`, resolves them to
one canonical company, and prints the evidence/confidence/provenance
breakdown plus a structured search query against the result.

To run a source through the actual Celery task (still against fixtures, via
a monkeypatched adapter) see `tests/test_jobs.py`. To run it for real once a
source has a live, compliance-approved target:

```python
from app.ingestion.jobs.tasks import run_source_collection
run_source_collection.delay(str(source.id), "<real fetch target>", "<idempotency key, e.g. today's date>")
```

## Testing collectors

- Adapter `parse()`/`normalize()` logic: fixture-based, no network
  (`tests/test_adapters.py`).
- `ScraplingCollector`: mocked Scrapling `Response` objects for
  retry/normalize/extract, and literal-IP targets for the SSRF guard --
  no live network dependency (`tests/test_scrapling_collector.py`).
- Full pipeline (ingest -> resolve -> evidence): real Postgres, fixture data
  (`tests/test_pipeline.py`).
- Job layer (idempotency, disabled-source skip, failure isolation): Celery
  eager mode with a fake adapter (`tests/test_jobs.py`).

Run everything with `pytest`. Tests use the same local Postgres database
configured via `DATABASE_URL`/`.env` (a `pg_trgm`-backed database is
required, since entity-resolution candidate generation uses trigram
similarity) -- see `README.md` for setup.
