# Confidence & verification

## Verification types

- **VERIFIED** -- supported directly by an authoritative/reliable source
  (e.g. MCA's Company Master Data -- the statutory registrar of companies).
- **OBSERVED** -- explicitly stated by a company, marketplace, directory, or
  website (e.g. a company's own "About" page).
- **ESTIMATED** -- derived from multiple signals rather than stated directly
  by any one source.
- **UNKNOWN** -- insufficient evidence.

An adapter decides the verification type of each `ObservationDraft` it
produces (see `docs/adding_a_source.md`) -- this is a property of *how
trustworthy the source's own claim is*, not of the confidence engine.

**Invariant:** the confidence engine never lets a value get labeled
`VERIFIED` unless it is actually backed by a `VERIFIED` observation. If a
government source says `state=Maharashtra` (verified) and a directory says
`state=Gujarat` (observed), the winning value (`Maharashtra`, the majority)
is correctly labeled `verified` -- but if the *only* observations for a
field are `OBSERVED`, the rollup stays `OBSERVED`, however many of them
agree. See `compute_field_confidence()` in
`app/ingestion/confidence/engine.py` and
`tests/test_confidence.py::test_estimated_value_is_never_labeled_verified`.

## How confidence is computed

Deterministic and explainable -- **not** an ML model. Every observation's
individual score is:

```
reliability (Source.reliability_weight / 100)
  x verification_weight (verified=1.0, observed=0.7, estimated=0.5, unknown=0.1)
  x freshness (exp(-age_days / 365))
```

Then, among observations that agree on the winning value:

```
base_score = max(individual scores)
independent_source_bonus = min(0.15, 0.05 x (agreeing_sources - 1))
conflict_penalty = (1 - agreement_ratio) x 0.30
confidence = clamp(base_score + independent_source_bonus - conflict_penalty, 0, 1)
```

`compute_field_confidence()` returns the full breakdown (`explanation` dict)
alongside the score, so an API/UI can show exactly why a number exists --
e.g. `{"base_score": 0.95, "independent_source_bonus": 0.05,
"conflict_penalty": 0.0, "agreement_ratio": 1.0, ...}`. This is intentionally
a replaceable interface: a future learned model could sit behind the same
function signature without touching any caller.

## Company-level confidence

`app/ingestion/pipeline.recompute_company_evidence()` recomputes one
`Evidence` row per field from that company's `RawObservation`s, then sets
`Company.confidence` to the mean of all field confidences and
`Company.last_verified_at` to the most recent `collected_at` among any
`VERIFIED` observation (never among `OBSERVED`/`ESTIMATED` ones -- a company
should not appear "verified" because someone scraped its own claims about
itself).

## Revenue/numeric normalization

Confidence and evidence always operate on standardized numeric values, never
formatted strings: `"₹10 crore"` is stored as `100000000.0` INR
(`app/ingestion/normalization/revenue.py`); lakh/crore formatting is a
presentation-layer concern, not something evidence/confidence ever compute
against.
