# SEC EDGAR: access status

This document records what was actually verified against SEC's live
official documentation *and* a real, live API response before
`app/source_adapters/sec_edgar_adapter.py` was written -- not assumptions,
not what `public-apis/public-apis` claimed.

## What was directly verified (2026-08-14)

- **Authentication**: none. Confirmed from
  `sec.gov/search-filings/edgar-application-programming-interfaces`:
  > "These APIs do not require any authentication or API keys to access."

- **Required `User-Agent` header**: confirmed from `sec.gov/os/webmaster-faq`
  (SEC's fair-access policy):
  > "declare your user agent in request headers" as
  > `User-Agent: Sample Company Name AdminContact@<sample company domain>.com`

  Requests without a compliant User-Agent are rejected with an "Undeclared
  Automated Tool" error. This is config (`SEC_EDGAR_USER_AGENT`), not a
  secret, but `SecEdgarAdapter.fetch()` still refuses to run without it --
  see `app/source_adapters/sec_edgar_client.py`.

- **Rate limit**: 10 requests per second. Same page:
  > "carefully monitored to preserve equitable access for all users"

- **Base URL**: `https://data.sec.gov`.

- **Endpoints** (both confirmed by a real live call against a real filer --
  Apple Inc., CIK 0000320193, fetched 2026-08-14, with a compliant
  User-Agent -- see "Live verification" below):
  - `GET /submissions/CIK{10-digit-padded-cik}.json` -- entity profile +
    recent filings list. Confirmed present fields: `cik`, `entityType`,
    `sic`, `sicDescription`, `name`, `tickers`, `exchanges`,
    `stateOfIncorporation`, `fiscalYearEnd`, `phone`, `website`,
    `addresses.business`/`addresses.mailing` (`street1`, `street2`, `city`,
    `stateOrCountry`, `zipCode`), `filings.recent.{accessionNumber, form,
    filingDate, reportDate}`.
  - `GET /api/xbrl/companyfacts/CIK{10-digit-padded-cik}.json` -- XBRL
    financial facts. Confirmed shape: `facts.us-gaap.<concept>.units.USD[]`,
    each entry with `val`, `start`, `end`, `fy`, `fp` (fiscal period, `"FY"`
    for full-year), `form`, `filed`, `accn`.

- **Bulk data**: two nightly ZIP files exist --
  `companyfacts.zip`/`submissions.zip` under
  `www.sec.gov/Archives/edgar/daily-index/`. **Not implemented** in this
  batch (only single-company lookup); a genuine future option for
  bulk-loading every SEC filer without per-company API calls.

- **Data licensing**: SEC filings and derived data are US federal
  government work product, which is generally not subject to copyright in
  the US and is treated as public domain / freely reusable, including
  commercially. No explicit "commercial use permitted" statement was found
  on the specific pages fetched during this verification, but this is
  standard, well-established status for SEC EDGAR data broadly.

## Live verification (not just documentation reading)

Since SEC EDGAR needs no credential and explicitly permits scripted access
with a compliant User-Agent, this adapter's target schema was verified with
a **real live call**, not only against documentation:

```
curl -H "User-Agent: <redacted research contact>" \
  https://data.sec.gov/submissions/CIK0000320193.json
curl -H "User-Agent: <redacted research contact>" \
  https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json
```

Both returned real 200 responses matching the schema documented above.
Real revenue data points were observed for both
`RevenueFromContractWithCustomerExcludingAssessedTax` and `Revenues`
concepts. This is the strongest verification level any source in this
codebase's docs uses -- stronger than "documentation review," short of
"production ingestion run."

## What was confirmed ABSENT (not guessed, not silently skipped)

- **Employee count**: checked directly in the live `companyfacts.json`
  response -- no `dei:EntityNumberOfEmployees` (or any `*employ*` key) is
  present in either the `dei` or `us-gaap` taxonomies for the company
  checked. Not every filer may match this exactly, but the concept is not
  reliably present, so `SecEdgarAdapter.normalize()` never emits an
  `employee_count` observation. Confirming this by not-finding-it is
  exactly the "do not claim a field the schema doesn't confirm" discipline
  this task required.
- **Incorporation/founding date**: not present anywhere in `submissions.json`
  (only `stateOfIncorporation`, which is a jurisdiction, not a date).
  `SecEdgarAdapter.normalize()` never emits `incorporation_date`. This
  means the "Founded after 2015" example filter will never match on
  SEC-EDGAR-sourced evidence alone.

## Scope limitation: this is NOT US company-universe coverage

**SEC EDGAR only contains companies that file with the SEC** -- i.e.
publicly traded (or otherwise SEC-registered) US companies. The overwhelming
majority of US businesses Super CRM would actually search for -- private
manufacturers, distributors, service providers, the entire private-company
universe MCA gives for India -- never appear in EDGAR at all. This adapter
is an **enrichment source for public companies**, not a US company
directory. `source_type = "public_filing"` (not `"government_dataset"`)
deliberately distinguishes it from a bulk-universe source in this
codebase's terms. See `app/models/enums.py::SourceType` and the task that
authorized this batch, which required this distinction be stated plainly
rather than implied.

## Revenue: currency handling

Revenue is reported in USD. `Company.annual_revenue_inr` is an
India-currency-specific column (see the multi-source architecture audit in
this project's history) -- writing a USD figure into it would silently
mislabel the currency, a real correctness bug, not a shortcut. This adapter
instead records revenue as an Evidence-only observation
(`field="annual_revenue_usd"`, full fiscal-year/XBRL-concept/accession
provenance preserved in `ObservationDraft.metadata`), **not** projected onto
any Company column. It is visible on a company's evidence tab but not
currently reachable via the existing `revenue_inr` search filter. Making
USD (or any non-INR currency) revenue searchable requires the
currency-generic `revenue_amount`/`revenue_currency` schema change already
flagged as a required decision in this project's architecture audit --
explicitly out of scope for this adapter.

## Compliance gates

Only the standard `Source.collection_enabled` / `SourcePolicy` DB-level
gate applies -- there is no API key to gate on a second, independent
config flag the way FileSure/Companies House need (no secret exists to
protect), matching `GovernmentDatasetAdapter`'s precedent. `Settings.sec_edgar_user_agent`
must still be non-empty (checked in `fetch()`) since SEC will reject
non-compliant requests regardless of any DB state.

`python -m app.cli.sec_edgar_lookup` is the only place the `sec_edgar`
Source row gets created, with `collection_enabled=True` set at that point
-- running the CLI with an explicit CIK *is* the human authorization step.

**Scheduled-collection note**: same structural situation as FileSure and
Companies House -- `_adapter_for()` would dispatch a `SecEdgarAdapter` for
this source_type, but Celery Beat's `dispatch_enabled_source_collections()`
always passes `source_target=None`, so `run_source_collection()` refuses to
call `adapter.fetch()` before ever reaching the adapter. Incidental
protection, not purpose-built, same as documented for FileSure in
`docs/source_strategy.md`.
