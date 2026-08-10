# Multi-source, multi-country architecture: assessment and plan

This document is the required "inspect before building" report for the
multi-source/multi-country expansion, plus the phased plan that follows from
it. Written before any schema change. See `docs/adding_a_source.md`,
`docs/entity_resolution.md`, `docs/confidence_engine.md`, `docs/compliance.md`
for the systems this builds on -- this doc doesn't repeat what's already
documented there except where it's directly relevant to what's changing.

## A. Current source adapter architecture

`app/source_adapters/base.py` defines the interface every collector
implements: `fetch(target) -> FetchResult`, `parse(FetchResult) ->
list[ParsedRecord]`, `normalize(ParsedRecord) -> list[ObservationDraft]`,
`validate(ParsedRecord) -> bool`. Business logic (normalization, entity
resolution, confidence, search) never imports a specific collection library --
only `ParsedRecord`/`ObservationDraft` cross that boundary. Three concrete
adapters exist today: `WebsiteAdapter` (fixture-based HTML),
`GovernmentDatasetAdapter` (MCA/data.gov.in), `FileSureAdapter` (MCA reseller
API). All three funnel into the same `app.ingestion.pipeline.
ingest_parsed_record()`.

**This abstraction is already generic** -- it has no India-specific types in
its interface. What's India-specific lives in individual adapters' field
mappings (`mca_field_mapping.py`, `filesure_field_mapping.py`) and in the
downstream schema (see C, E, F). Section 1's requirements are almost entirely
satisfied by the interface as it stands; what's missing is generic *source
metadata* (country, access method, compliance status as a typed enum) on the
`Source` model itself, not a new adapter interface.

## B. Current Company schema

`app/models/company.py`: `Company` has `country: str | None` (free text,
default `"India"`), plus India-specific top-level columns baked in directly:
`cin`, `llpin` (both `unique=True` constraints), `gstin`. Revenue columns are
suffixed `_inr` throughout (`annual_revenue_inr`, `revenue_range_min_inr`,
`revenue_range_max_inr`) with no `currency` column anywhere. `state`/`city`
are free-text indexed strings (not normalized to any subdivision standard).
Two companion tables carry India-specific data outright:
`CompanyFinancials` (financial-year history, INR-suffixed) and
`CompanyGSTRegistration` (India's GST regime specifically -- not a generic
"tax registration" concept).

`app/ingestion/entity_resolution/matcher.py`'s `IdentitySignals` dataclass
hardcodes `cin: str | None` and `gstin: str | None` as named fields (not a
generic identifiers map), and `_CIN_SCORE = 1.0` / `_GSTIN_SCORE = 0.95` are
the only identifiers strong enough to auto-match alone.

## C. Current search/filter architecture

`app/search/filters.py` (`CompanySearchFilters`, a flat Pydantic model) →
`app/search/query.py` (`build_company_query()`, deterministic SQL
translation) → `app/api/routes/search.py` (`POST /api/search/companies`).
This is already a clean three-layer separation (typed filter object →
SQL builder → HTTP endpoint) and Section 7's "extend rather than replace"
instruction is very achievable: the existing flat-filter model can stay as a
supported shorthand while a new generic `FilterGroup`/`FilterCondition`
representation is added alongside it and compiled to the same kind of SQL
`Select`.

**`range_match_is_definite()` in `query.py` already implements exactly the
DEFINITE/POSSIBLE distinction Section 8 asks for**, currently scoped to
employee/revenue range filters only: a company's stored value/range is
compared against the filter bound, returning `True` (definite -- the
company's own least-favorable data point still clears the bar), `False`
(possible -- ranges overlap but aren't strictly contained), or `None` (no
range filter active). This is the pattern to generalize to every
evidence-backed field, not a new concept to invent (see G/Phase 3).

## D. Current frontend search UI

`frontend/src/app/discover/page.tsx`: a fixed form (industry/state/city/
category/employee-min/revenue-min/min-confidence as literal `<input>`
elements bound to one `useState` object), calling `searchCompanies()` (a
thin fetch wrapper in `frontend/src/lib/api.ts`) with a `CompanySearchFilters`
object matching the backend Pydantic model field-for-field. It already
renders a `Definite`/`Possible`/`n/a` badge per result row (from
`match_is_definite`) and a confidence badge -- the visual vocabulary Section
8/12 asks for is already partly built, just not generalized past
employee/revenue. There is no country selector, source selector, or dynamic
filter-row builder; the form is hardcoded to the seven fields above.

## E. Existing country assumptions

- `Company.country` defaults to the string `"India"` (not an ISO code, and
  not enforced against any code list).
- `cin`, `llpin`, `gstin` are unconditional top-level `Company` columns.
- `CompanyGSTRegistration` is an entire table for an India-only tax regime.
- Revenue/capital columns are `_inr`-suffixed everywhere: `Company.
  annual_revenue_inr`/`revenue_range_min_inr`/`revenue_range_max_inr`,
  `CompanyFinancials.annual_revenue_inr`/`authorized_capital_inr`/
  `paidup_capital_inr`. No currency column exists anywhere in the schema.
- `IdentitySignals` (entity resolution) hardcodes `cin`/`gstin` as named
  fields rather than a generic identifier-type/value pair.
- `state`/`city` filters do `ILIKE` substring matches against free text --
  workable for any country, but with no concept of "this is an Indian state"
  vs. "this is a US state" vs. "this is a UK county."

## F. Existing source assumptions

- `SourceType` enum (`app/models/enums.py`) has five values, none of which
  assume India specifically, but none of which cover `SEARCH_PROVIDER`,
  `BUSINESS_DIRECTORY`, `SOCIAL_PLATFORM`, or `USER_FILE` either -- it needs
  extending (additively; existing values `WEBSITE`, `GOVERNMENT_DATASET`,
  `DIRECTORY`, `MARKETPLACE`, `PUBLIC_FILING`, `REGISTRY_DATA_PROVIDER` all
  stay valid and mostly map onto Section 1's requested type list already:
  `DIRECTORY`≈`BUSINESS_DIRECTORY`, `PUBLIC_FILING`/`REGISTRY_DATA_PROVIDER`
  already cover the government/reseller registry space).
- `Source` (`app/models/source.py`) has no `country`/`countries` column, no
  `display_name` (only the internal `name`), no `access_method` field, no
  structured `compliance_status` (only free-text `license_notes`/
  `robots_notes`), no `last_successful_run`/`last_error` columns (these are
  currently only reconstructable by joining `IngestionJob`, which already
  has `status`/`finished_at`/`error_summary` -- see G).
- `Source.metadata_json` (JSONB, already exists, currently unused by any
  adapter) is exactly the kind of free-form `configuration` column Section 1
  asks for -- it doesn't need to be added, just adopted.
- No adapter or model currently distinguishes "not enabled yet" from
  "cannot legally be automated at all" -- `collection_enabled=False` today
  means both "an intern hasn't reviewed it yet" and "this is LinkedIn and
  there is no permitted mechanism," which is exactly the ambiguity Section 3
  /13 asks to resolve with a real `NOT_AVAILABLE`/`REQUIRES_LICENSE` status.

## G. Minimal schema changes required

Two migrations, both additive (no column drops/renames, no backfill risk to
existing FileSure/MCA data written in this project so far):

**Migration 1 -- generic source metadata** (`sources` table):
```
display_name           VARCHAR(255) NULL
countries               VARCHAR(2)[] NOT NULL DEFAULT '{}'   -- ISO 3166-1 alpha-2, empty = global/country-agnostic
access_method           VARCHAR(32)  NOT NULL DEFAULT 'unknown'  -- new enum, see below
compliance_status       VARCHAR(32)  NOT NULL DEFAULT 'under_review'  -- new enum, see below
```
`last_successful_run`/`last_error`/`records_collected` are deliberately
**not** new columns -- `IngestionJob` already has everything needed
(`status`, `finished_at`, `records_updated`, `error_summary`); Phase 8 adds a
read-only query that derives these per-source from the existing table
instead of duplicating state that could drift out of sync. `configuration`
is deliberately not a new column either -- `Source.metadata_json` (JSONB,
already exists) is reused. Secrets are never stored in this table or its
`metadata_json` (see F; this only strengthens the existing FileSure
precedent of secrets living in `.env`/settings only).

**Migration 2 -- country code normalization** (`companies` table):
```
country_code   VARCHAR(2) NULL   -- ISO 3166-1 alpha-2, indexed
```
Added *alongside* the existing `country` free-text column, not replacing it
(existing `country="India"` values stay valid and readable; new ingestion
paths populate `country_code`; a follow-up backfill script, not part of this
migration, can set `country_code='IN'` wherever `country` matches known
India spellings). `cin`/`llpin`/`gstin` stay as-is -- they're real,
already-used India-specific identifiers with live data behind them (the
FileSure/MCA adapters). Generalizing identity signals to
non-India countries is a matcher-layer change (`IdentitySignals` gets a
generic `country_identifiers: dict[str, str]` field alongside the existing
named `cin`/`gstin` fields, additive, see Phase 2), not a schema rename.

**Currency**: flagged, not fixed in this pass. The `_inr` suffix throughout
`Company`/`CompanyFinancials` is a real multi-country blocker (a US company's
revenue has no home in these columns without lying about the unit), but
renaming `annual_revenue_inr` → `annual_revenue` + a new `currency` column
touches the pipeline, query builder, API schema, frontend formatter, and
every existing test -- a real migration with real risk to the FileSure data
already committed. Recommending this as a named, separate migration
(**Migration 3**, not attempted in this pass) once a second country's
revenue data actually needs to be stored, rather than doing it speculatively
now. Until then, non-INR sources should omit revenue rather than write a
wrong-currency number into an `_inr` column.

New enums (pure Python, `app/models/enums.py` -- no migration by themselves,
only the two `sources` columns referencing them are):
```python
class SourceAccessMethod(str, enum.Enum):
    OFFICIAL_API = "official_api"
    SCRAPED_PUBLIC_PAGE = "scraped_public_page"
    GOVERNMENT_OPEN_DATA = "government_open_data"
    USER_UPLOADED_FILE = "user_uploaded_file"
    UNKNOWN = "unknown"

class SourceComplianceStatus(str, enum.Enum):
    ACTIVE = "active"                # permitted mechanism confirmed, collection_enabled may be True
    UNDER_REVIEW = "under_review"     # default; not yet confirmed either way
    REQUIRES_LICENSE = "requires_license"  # a permitted mechanism exists but needs a paid/approved license Super CRM doesn't have yet
    NOT_AVAILABLE = "not_available"   # no permitted automated mechanism exists at all; adapter must not be built
```

## H. Which proposed features can be implemented without migrations

- **Phase 3 (dynamic filter representation)**: entirely new Pydantic/
  dataclass types (`FilterCondition`, `FilterGroup`, operators, `MatchStrength`
  DEFINITE/POSSIBLE/UNKNOWN) compiled against the *existing* `companies`/
  `evidence` tables. Zero schema change -- this generalizes `query.py`'s
  existing `range_match_is_definite()` pattern to arbitrary fields/operators.
- **Phase 4 (filter API)**: a new endpoint (or an extended existing one)
  accepting the Phase 3 representation, reusing `build_company_query`'s
  column-mapping knowledge. Zero schema change.
- **Phase 5 (frontend filter builder)**: a dynamic add/remove filter-row UI
  driven by a field registry (name, type, allowed operators), replacing the
  fixed form. Zero schema/backend-model change beyond calling the new
  endpoint.
- **Phase 7 (custom CSV/JSON source), partially**: the adapter, field-mapping
  validation, and CLI can all be built now against `Source.metadata_json`
  for mapping config storage -- no new columns needed for a first version
  (an admin/UI to edit that config more nicely can come later as a schema
  change if JSONB editing proves too rough).
- **Phase 8 (source health), partially**: the `IngestionJob`-derived
  last-run/last-error view needs no schema change (see G). A dedicated admin
  UI page consuming it is also schema-free.
- **Phase 9 (permitted adapters)**: any adapter for a source with a genuine
  official API doesn't require schema changes beyond Migration 1's new
  `Source` columns to describe it accurately.

Everything above is implemented in this pass, following Section 17's
"implement the non-schema portions first." **Migrations 1 and 2 are proposed
here but not yet applied** -- Phases 1 and 2 (generic source metadata,
country support at the schema level) wait for a explicit go-ahead given
they touch the `sources`/`companies` tables directly.

## I. Compliance/access status of Google, Justdial, Facebook, and LinkedIn

Verified via web search against current (2026) developer documentation and
reporting, not fabricated or assumed from training data alone. Every
determination below defaults to the most restrictive reading when evidence
is ambiguous, per this project's compliance posture (`docs/compliance.md`).

**LinkedIn -- `REQUIRES_LICENSE`.** No self-serve API for company search or
directory-style enrichment exists. LinkedIn's own API surface (Marketing
Developer Platform / partner programs) is enterprise-partnership-gated,
commonly cited as a 3-6 month approval process reserved for large partners,
not a general-purpose company-data endpoint. LinkedIn's User Agreement
(Section 8.2) explicitly prohibits scraping/automated collection, and
*hiQ Labs v. LinkedIn* ultimately confirmed scraping is a contract
(ToS) violation even where it isn't a CFAA violation. **No adapter should be
built** against LinkedIn without a confirmed, named partnership agreement.
[LinkedIn API 2026: Access, Endpoints, Limits & Alternatives](https://connectsafely.ai/articles/linkedin-api-complete-guide-2026),
[Is LinkedIn Automation Safe in 2026? ToS & Scraping Rules](https://connectsafely.ai/articles/is-linkedin-automation-safe-tos-scraping-guide-2026)

**Facebook/Meta -- `REQUIRES_LICENSE`.** The Graph API exists and is
"official," but reading public Page data belonging to pages you don't own
requires Meta's **Page Public Content Access** feature, which itself
requires App Review *and* Business Verification -- not self-serve, and Meta
grants it selectively. There is no general "search all business pages"
endpoint available by default. **No adapter should be built** until Page
Public Content Access is actually granted to a real Meta developer app tied
to this project. [Facebook Data API: The Complete 2026 Developer Guide](https://www.socialcrawl.dev/blog/facebook-data-api-2026)

**Justdial -- `NOT_AVAILABLE` (pending direct confirmation).** No publicly
documented self-serve developer API was found; what exists is third-party
scraper products (Apify, Piloterr, Decodo, etc.) built by scraping
justdial.com directly, which is exactly the "technically accessible but not
permitted" case Section 3 explicitly prohibits building. A confirmed,
named commercial-partnership path with Justdial itself was not found in
this search. Recommendation: mark `NOT_AVAILABLE` until someone on the team
gets a direct answer from Justdial's business-development contact --
**do not build a scraper against justdial.com** regardless of technical
feasibility. [Justdial Business Scraper - India Listings, Reviews, Phone API](https://apify.com/thirdwatch/justdial-business-scraper/api)

**Google -- `REQUIRES_LICENSE` for the only legitimate mechanism (Places
API); `NOT_AVAILABLE` for scraping google.com search results.** Google
Places API is a real, official, paid API (place search/details, 100M+
business listings) that could plausibly back a future `SEARCH_PROVIDER`
adapter for basic business-existence/address/website enrichment -- but it's
metered/billed per call, so per Section 14 ("normal search must not trigger
paid API calls") it can only ever be an explicit, separately-controlled
enrichment action, never part of ordinary `/api/search/companies` query
execution. Scraping Google's search-results pages is separately and
unconditionally out of scope (CAPTCHA/WAF-protected, ToS-prohibited, and
explicitly forbidden by this task's own rules). [Overview | Places API | Google for Developers](https://developers.google.com/maps/documentation/places/web-service/overview)

**Net effect on `SourceComplianceStatus`**: all four launch as
`REQUIRES_LICENSE` (Google, LinkedIn, Facebook) or `NOT_AVAILABLE`
(Justdial, pending direct confirmation) -- none as `ACTIVE`. This is a
correctness feature, not a limitation to work around: Section 13 explicitly
says "do not pretend unsupported sources are active."

## J. Recommended implementation sequence

Follows Section 15 with H's non-schema-first reordering:

1. **Now, this pass**: Phase 3 (generic filter representation) → Phase 4
   (filter API, additive alongside the existing `CompanySearchFilters`
   endpoint, which keeps working unchanged) → Phase 5 (frontend dynamic
   filter builder) → Phase 7 first cut (custom CSV/JSON source adapter,
   `Source.metadata_json`-backed field mapping, validation) → Phase 8 first
   cut (`IngestionJob`-derived source health view + admin page). Tests for
   all of the above, full existing suite re-run at the end.
2. **Next, pending go-ahead**: apply Migration 1 (generic source metadata)
   and Migration 2 (`country_code`) exactly as specified in G, then Phase 1/2
   proper (populate the new `Source` columns for MCA/FileSure/website
   adapters retroactively, wire `country_code` through ingestion).
3. **After that**: Phase 6 (saved searches -- needs its own small schema
   addition, a `saved_searches` table storing the Phase 3 filter
   representation as JSONB; proposed at that time, not before).
4. **Only once a named partnership/license is actually in hand for a given
   platform**: Phase 9 per-platform adapters (Google Places API first, since
   it's the only one of the four with a genuine self-serve paid tier;
   LinkedIn/Facebook/Justdial stay `REQUIRES_LICENSE`/`NOT_AVAILABLE` stubs
   until someone completes that platform's actual approval process).
