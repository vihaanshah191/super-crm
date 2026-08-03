# Compliance controls

Public accessibility does not imply unrestricted permission for automated
collection or commercial reuse. Every source goes through
`app/compliance/source_policy.py` before it can be fetched at all.

## The `Source` registry

Every source is a row in `sources` (`app/models/source.py`) with:

- `collection_enabled` -- hard off switch. `False` by default; a source only
  becomes fetchable after someone has actually confirmed the access method
  is permitted.
- `rate_limit_per_minute` / `max_concurrency` -- enforced by
  `app/compliance/source_policy.RateLimiter` before every job run.
- `robots_notes`, `license_notes`, `retention_notes` -- an audit trail,
  filled in during the review that flips `collection_enabled` to `True`.
- `reliability_weight` -- feeds the confidence engine (`docs/confidence_engine.md`).

`app.compliance.source_policy.SourcePolicy.assert_collection_allowed()` is
called at the top of `ingest_parsed_record()` (`app/ingestion/pipeline.py`)
and again in the Celery task (`app/ingestion/jobs/tasks.py`) -- collection is
blocked in two independent places, not just one.

## What is enabled in this codebase today

| Source | Type | Status | Why |
|---|---|---|---|
| MCA Company Master Data (via data.gov.in) | `government_dataset` | Adapter implemented, targets an official CSV download (GODL-licensed open data) | Prefer-official-downloads-over-scraping requirement is directly satisfied |
| Example company website | `website` | Adapter implemented, **fixture-only** (`tests/fixtures/html/example_company_website.html`, a `.example` RFC 2606 domain) | No real company website has been reviewed for robots.txt/ToS permission yet -- see below |

Neither source's demo/test `Source` row is pointed at a real live network
target from this codebase. The website adapter is exercised entirely against
a synthetic fixture we authored ourselves, specifically so this PoC does not
imply collection from, or dependency on, any real site whose terms have not
been reviewed.

**Before pointing `WebsiteAdapter` at a real site:** confirm robots.txt
allows the paths you intend to fetch, review the site's Terms of Service for
automated-access and reuse restrictions, and record the findings in that
source's `robots_notes`/`license_notes` before setting
`collection_enabled=True`.

## IndiaMART

Treated as a source candidate, not a dependency. **No IndiaMART adapter
exists in this codebase.** Before one is written:

1. An official/authorized API or data-access method for our intended use
   case must be identified.
2. Its access restrictions and terms must be reviewed.
3. The resulting `Source` row must stay `collection_enabled=False` until
   that permitted access method is confirmed and documented.

Super CRM's architecture does not depend on IndiaMART or any single source
-- `docs/ingestion.md` describes why a source going down (or staying
permanently disabled, as IndiaMART currently is) does not affect the rest of
the pipeline.

## What this codebase explicitly does not do

- No CAPTCHA bypassing, credential theft, or attempts to defeat access
  controls. Scrapling's stealth fetchers (`StealthyFetcher`,
  Cloudflare-challenge handling) are part of the upstream library but are
  not wired into any adapter here -- `ScraplingCollector.fetch_dynamic()`
  only uses `DynamicFetcher` (plain Playwright rendering), and nothing in
  this codebase calls `StealthyFetcher`.
- No execution of code obtained from scraped pages.
- SSRF/unsafe-URL guarding (`app/compliance/url_safety.py`) blocks
  non-http(s) schemes, credentials-in-URL, and private/loopback/link-local
  targets (including the cloud metadata endpoint) before any fetch. This
  is defense-in-depth, not a compliance decision -- it applies regardless
  of whether a source is otherwise permitted.
- Response size is capped (`SCRAPLING_MAX_RESPONSE_BYTES`) against
  oversized-response abuse.

## Known gap: redirect-chain validation

`ScraplingCollector.normalize_response()` checks each hop in
`response.history` against `assert_safe_url()` *after* the fetch completes,
because Scrapling's `Fetcher.get()` does not expose a pre-redirect hook to
validate each hop before it's followed. This is a real limitation, not a
claim of complete redirect-abuse protection -- documented here rather than
silently assumed away. `max_redirects=5` bounds the blast radius in the
meantime.
