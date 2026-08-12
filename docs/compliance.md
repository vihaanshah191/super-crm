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
- `compliance_status` (`SourceComplianceStatus`: `active` / `under_review` /
  `requires_license` / `not_available`) -- the human-reviewed *reason*
  behind `collection_enabled`, added in the multi-source expansion (see
  `docs/multi_source_architecture.md` Section G/I). Distinct on purpose:
  `collection_enabled=False` alone doesn't say whether a source is merely
  unreviewed or has been reviewed and found to have no permitted access
  mechanism at all (e.g. LinkedIn/Facebook/Justdial, none of which have an
  adapter in this codebase -- see that doc's Section I for why).
- `access_method` (`SourceAccessMethod`: `official_api` /
  `scraped_public_page` / `government_open_data` / `user_uploaded_file` /
  `unknown`) -- how the source's data is actually obtained, independent of
  `source_type` (what kind of thing it is).
- `countries` -- ISO 3166-1 alpha-2 codes this source covers; empty means
  not yet classified, never inferred.

`app.compliance.source_policy.SourcePolicy.assert_collection_allowed()` is
called at the top of `ingest_parsed_record()` (`app/ingestion/pipeline.py`)
and again in the Celery task (`app/ingestion/jobs/tasks.py`) -- collection is
blocked in two independent places, not just one.

## What is enabled in this codebase today

| Source | Type | Status | Why |
|---|---|---|---|
| MCA Company Master Data (via data.gov.in) | `government_dataset` | **IMPLEMENTED / DISABLED PENDING CREDENTIALS** -- adapter implemented and tested against fixtures; live API connectivity, live schema, and production ingestion are all separate, not-yet-done milestones. See `docs/mca_data_access.md`. Do not describe this as "verified live." | Prefer-official-downloads-over-scraping requirement is directly satisfied; access is gated on `DATA_GOV_IN_API_KEY`, which stays empty |
| MCA Company Master Data (local file import) | `government_dataset` | Adapter implemented; `python -m app.cli.import_mca` ingests an officially-obtained file through the same pipeline, no API key needed. Requires an explicit `--source-url`; observations are tagged `file_import_user_declared`, never claimed as independently verified. | Lets real, officially-obtained MCA data be ingested without waiting on API credentials |
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

## Redirect-chain SSRF protection

`ScraplingCollector.fetch_static()` passes `follow_redirects="safe"` to
`Fetcher.get()`. This maps to curl_cffi's `CurlFollow.SAFE` mode
(`CURLOPT_FOLLOWLOCATION=4`), which instructs libcurl to validate each
redirect target against private/loopback/link-local ranges **before**
establishing the TCP connection to that target -- this is a pre-redirect
check at the C library level, not post-hoc.

`normalize_response()` also re-validates the completed redirect history
through `assert_safe_url()` as a second, independent layer.

### Known remaining gap: DNS rebinding (TOCTOU)

Both the libcurl `SAFE` check and `assert_safe_url()` resolve the redirect
target's hostname to an IP at check time. An attacker who controls DNS could
serve a public IP during the check and a private IP when the actual TCP
connection is established (time-of-check/time-of-use). This is a structural
limitation shared by any DNS-based redirect guard and cannot be fully closed
without a pinned-IP transport layer or per-connection IP verification -- both
beyond the scope of this PoC. `max_redirects=5` bounds the blast radius.

This gap is documented here rather than silently assumed away.
