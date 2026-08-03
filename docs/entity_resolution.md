# Entity resolution

Different sources describe the same company differently ("ABC Industries" /
"ABC Industries Pvt Ltd" / "ABC INDUSTRIES PRIVATE LIMITED"). Entity
resolution decides whether an incoming observation belongs to an existing
`Company`, is ambiguous and needs human review, or seeds a new company.

## Modules

- `app/ingestion/entity_resolution/matcher.py` -- pure, DB-independent
  scoring. `score_match(incoming, candidate) -> MatchResult`.
- `app/ingestion/entity_resolution/fuzzy.py` -- name similarity
  (`rapidfuzz`), used only as one signal inside `matcher.py`.
- `app/ingestion/entity_resolution/resolver.py` -- DB-touching candidate
  generation (`find_candidates`) and orchestration (`resolve`).

## Candidate generation

`find_candidates()` never scans the whole `companies` table:

1. Exact-equality lookups on indexed columns: `cin`, `gstin`, `website_domain`.
2. A bounded `pg_trgm` similarity query on `normalized_name` (backed by the
   GIN trigram index from the initial migration), ordered by similarity,
   limited to 10 rows.

## Scoring rules (deterministic, explainable)

| Signal | Score if matched | Decision band |
|---|---|---|
| CIN match | 1.00 | `auto_match` |
| GSTIN match | 0.95 | `auto_match` |
| Website domain match | 0.75 | `review` |
| Email domain match | 0.60 | `review` |
| Exact name + location corroboration (state or postal code) | 0.75 | `review` |
| Exact name alone | 0.55 | `review` |
| Fuzzy name (>=0.85 similarity) + location corroboration | 0.65 | `review` |
| Fuzzy name alone | 0.30 | `no_match` |

Thresholds: `score >= 0.90` -> `auto_match`, `score >= 0.50` -> `review`,
else `no_match`.

**Invariant, enforced by construction:** name similarity can never reach
`auto_match` on its own, or even combined with a postal code (Indian PIN
codes cover entire industrial estates -- hundreds of unrelated companies can
share one). Only CIN or GSTIN -- unique statutory identifiers -- score high
enough to auto-match alone. Everything else lands in the `review` band and
produces an `EntityMatchCandidate` row instead of a silent merge.

## The review queue

An ambiguous match creates an `EntityMatchCandidate` (`status="pending"`)
recording the incoming payload, the candidate company, the score, and the
exact signals that matched (`matched_signals`, JSON, directly explainable in
a UI). The underlying `RawObservation` rows stay unattached
(`company_id IS NULL`) until a reviewer acts:

- `app.ingestion.pipeline.confirm_match(db, candidate_id, reviewed_by)` --
  attaches the observations to the candidate company and recomputes its
  evidence.
- `app.ingestion.pipeline.reject_match(db, candidate_id, reviewed_by)` --
  leaves the observations unattached for later reconsideration.

## New companies

If nothing matches (`no_match`) and the incoming observation has at least a
normalized name, a new `Company` stub is created and the observations attach
to it immediately -- there's no "existing thing" it could have been silently
merged into, so there's no ambiguity to review.

## Testing

`tests/test_entity_resolution.py` covers the scoring table directly (pure,
no DB) plus candidate generation and full `resolve()` behavior against
Postgres. `tests/test_pipeline.py`'s `TestMultiSourceResolution` class is the
end-to-end demonstration: MCA creates a company, the website's observation is
correctly held for review (not auto-merged), and confirming the match merges
evidence from both sources while each `RawObservation` keeps its own
`source_id`/provenance.
