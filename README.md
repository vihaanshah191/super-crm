# Super CRM

B2B company intelligence platform: aggregates company information from
multiple permitted public/licensed sources into normalized, evidence-backed
canonical company profiles, searchable via structured filters.

This repository currently implements the **ingestion vertical slice**
described in `docs/ingestion.md` -- two sources (a company website fixture
and India's MCA Company Master Data), through raw observation storage,
normalization, entity resolution, confidence/evidence, and structured search.
See `docs/` for the full design and `docs/compliance.md` for what is (and is
not) enabled for live collection.

## Stack

Python 3.11+, FastAPI, PostgreSQL (+ `pg_trgm`), SQLAlchemy 2.0 + Alembic,
Celery + Redis, [Scrapling](https://github.com/D4Vinci/Scrapling) (wrapped,
never called directly outside `app/ingestion/collectors/scrapling_collector.py`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Postgres: create a database and enable pg_trgm (used for fuzzy name candidate generation)
createdb super_crm
psql super_crm -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

cp .env.example .env   # edit DATABASE_URL etc. if needed

alembic upgrade head
```

Redis is required for Celery (`redis-server` or a hosted instance) -- point
`CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` at it in `.env`.

## Running things

```bash
# API
uvicorn app.main:app --reload

# Celery worker (for background ingestion jobs)
celery -A app.ingestion.jobs.celery_app worker --loglevel=info

# Celery beat (scheduled collection)
celery -A app.ingestion.jobs.celery_app beat --loglevel=info

# Vertical-slice demo: two sources -> one canonical company profile
python scripts/demo_vertical_slice.py

# Tests
pytest
```

## Documentation

- `docs/ingestion.md` -- pipeline architecture, how to run collectors locally
- `docs/adding_a_source.md` -- how to add a new SourceAdapter
- `docs/entity_resolution.md` -- matching rules, review queue
- `docs/confidence_engine.md` -- how confidence/verification_type are computed
- `docs/compliance.md` -- per-source compliance controls, what's enabled today
