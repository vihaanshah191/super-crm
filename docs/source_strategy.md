# Source strategy: no paid-per-call dependency

Super CRM must be able to know a company exists, and to answer structured
search queries against it, **without spending money per company**. Paid
per-call enrichment (e.g. a ₹-per-lookup company API) is never the
foundation of the database -- at most, it is an optional, explicitly
invoked upgrade for a hand-picked prospect.

This is enforced structurally, not by convention: `Company` rows are only
ever written by `app/ingestion/pipeline.py`, which only ever consumes
`RawObservation`s produced by a registered `SourceAdapter` (see
`docs/ingestion.md`). There is no code path from "user searches" or "user
opens a company profile" to any outbound API call, paid or otherwise --
`tests/test_no_paid_enrichment_dependency.py` asserts this directly (no
outbound network connection occurs during search, company-profile access,
or a background ingestion job) and statically greps `app/` to guarantee no
FileSure-style paid provider is wired in.

## Source tiers

| Tier | What | Cost model | Status here |
|---|---|---|---|
| 1 | Official/bulk/open government datasets (e.g. MCA Company Master Data via data.gov.in) | Free / one-time bulk download | Implemented: `GovernmentDatasetAdapter`. See `docs/mca_data_access.md`. |
| 2 | Permitted company websites and other public sources, collected per `docs/compliance.md` | Free (compute only) | Implemented: `WebsiteAdapter` (fixture-only pending a real compliance-reviewed target). |
| 3 | Derived classifications computed from Tier 1/2 evidence (e.g. industry inferred from products/registry category) | Free (compute only) | Not yet implemented; slots into the same `SourceAdapter` -> Evidence pipeline. |
| 4 | Optional future licensed enrichment providers (paid, per-call or per-seat) | Paid | **Not implemented, and intentionally optional.** Must be invoked only by a separate, explicitly-triggered enrichment service -- never automatically by search, company-profile loading, ingestion, or a scheduled job. |
| 5 | Deep research for a selected prospect (manual or LLM-assisted, human-in-the-loop) | Paid (time/compute), per selected company only | Not yet implemented; would be user-initiated, not automatic. |

Tier 4 must remain fully optional: Super CRM must start, ingest, search,
and serve company profiles with zero Tier 4 providers configured.

## Revenue specifically

Revenue is one of the fields most tempting to "solve" by calling a paid
API per company. Don't. Revenue is only ever set when a Tier 1/2 source
actually reports it (`Company.annual_revenue_inr` /
`revenue_range_min_inr`/`revenue_range_max_inr`, with a `verification_type`
of verified/observed/estimated -- see `docs/confidence_engine.md`).
`range_match_is_definite()` (`app/search/query.py`) distinguishes a
DEFINITE range match from a merely POSSIBLE one for range-based filters
like "revenue > ₹10cr". Paid-up/authorized capital (a registry filing) is
never conflated with operating revenue -- see
`app/source_adapters/government_dataset_adapter.py` and
`docs/ingestion.md`. Where no source has reported revenue, it stays
`None` -- UNKNOWN -- rather than being guessed.

## FileSure

FileSure (a paid, ~₹5-per-call MCA registry reseller API) **is implemented
as a Tier 4 example** -- `app/source_adapters/filesure_adapter.py` /
`filesure_client.py`, gated by `Settings.filesure_collection_enabled`
(default `False`) and `Settings.filesure_api_key` (default empty). See
`docs/filesure_data_access.md` for what was verified about the API and
`docs/multi_source_architecture.md` for how it fits the multi-source
architecture.

Its presence in the codebase does not weaken Tier 4's "intentionally
optional" rule above:

- Local startup, `alembic upgrade head`, `seed_dev`, search, and
  company-profile loading all work with zero FileSure configuration --
  `tests/test_no_paid_enrichment_dependency.py` asserts this directly.
- `FileSureAdapter.fetch()` refuses to run unless
  `FILESURE_COLLECTION_ENABLED=true` is explicitly set, independent of any
  other configuration (see `app/source_adapters/filesure_adapter.py`).
- The only invocation path in this codebase is the explicit
  `python -m app.cli.filesure_lookup` CLI (a human running it *is* the
  authorization step) -- nothing in search, company-profile access, or
  unscoped background ingestion calls it.
- FileSure data is never treated as more trustworthy than what it actually
  is: observations use the same `VerificationType.VERIFIED` a direct MCA
  feed uses (FileSure resells MCA registry data, it doesn't merely observe
  it), but at a confidence weight one notch below MCA's (0.85 vs. 0.95 --
  see `_MASTER_DATA_CONFIDENCE` in `app/source_adapters/filesure_adapter.py`),
  reflecting the added reseller provenance hop. Revenue is never conflated
  with authorized/paid-up capital (FileSure's master-data endpoint doesn't
  expose revenue at all).
