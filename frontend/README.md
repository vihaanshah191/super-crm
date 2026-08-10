# Super CRM frontend

Next.js (App Router) + TypeScript + Tailwind CSS v4 + shadcn/ui, talking to
the FastAPI backend in `../app`.

## Setup

```bash
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_API_BASE_URL if the backend isn't on :8000
npm run dev
```

The backend must be running separately (`uvicorn app.main:app --reload` from
the repo root) and have `CORS_ALLOW_ORIGINS` covering wherever this dev
server is reachable from (`http://localhost:3000` and `http://127.0.0.1:3000`
are allowed by default -- see `app/core/config.py`).

For anything to show up, seed development data first (from the repo root,
with the backend's venv):

```bash
python -m app.cli.seed_dev --yes
```

This creates obviously-synthetic companies (Acme Industrial Systems Pvt Ltd,
etc.) -- see `app/cli/seed_dev.py`. **Never point this frontend at a
database seeded only with synthetic data and call it production.**

## Pages

| Route | Purpose |
|---|---|
| `/discover` | Structured filter form + company results (industry, location, employee/revenue thresholds, confidence). Calls `POST /api/search/companies`. |
| `/companies/[id]` | Company profile: summary stats, per-field evidence/provenance, financial-year history, GST registrations. |
| `/ingestion` | Source registry + recent ingestion job history. |
| `/review-queue` | Pending entity-resolution matches awaiting human confirm/reject. |

## API client

`src/lib/api.ts` wraps `fetch` against `NEXT_PUBLIC_API_BASE_URL`.
`src/lib/types.ts` hand-mirrors `app/api/schemas.py` -- there's no codegen
step yet, so if the backend's Pydantic schemas change, update both by hand.
