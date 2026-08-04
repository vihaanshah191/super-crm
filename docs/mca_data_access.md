# MCA Company Master Data: access status

This document distinguishes four separate milestones that are easy to
conflate. As of this writing, **only the first is done**:

| Milestone | Status |
|---|---|
| 1. Adapter implemented (parses CSV/JSON, maps fields, normalizes, validates) | **DONE** |
| 2. API connectivity verified (a real request to api.data.gov.in succeeded) | **NOT DONE** -- no API key |
| 3. Live schema verified (real column names/types confirmed against the adapter's mapping) | **NOT DONE** -- depends on #2 |
| 4. Production ingestion verified (a real, non-trivial batch of real MCA records ingested end-to-end) | **NOT DONE** -- depends on #3 |

**Do not describe MCA as "verified live" anywhere in this codebase or in
conversation about it until milestone 3 has actually happened.** The correct
status phrase is:

> MCA: IMPLEMENTED / DISABLED PENDING CREDENTIALS

not "verified live" or "production ready."

## What was actually investigated (2026-08-04)

Direct, tool-verified findings -- not claims copied from a search summary:

- **Official source**: "Company Master Data" (also surfaced as "Registrars
  of Companies (RoC)-wise Company Master Data"), published by the Ministry
  of Corporate Affairs via the Open Government Data (OGD) Platform India
  (data.gov.in). Confirmed live at
  `https://www.data.gov.in/catalog/company-master-data` (catalog id
  `ec58dab7-d891-4abb-936e-d5d274a6ce9b`) and mirrored per-state, e.g.
  `https://tn.data.gov.in/resource/registrars-companies-roc-wise-company-master-data`.
  A resource UUID, `4dbe5667-7b6b-41d7-82af-211562424d9a`, appears embedded
  in that live page's HTML -- this is the default for
  `DATA_GOV_IN_MCA_RESOURCE_ID` (see `.env.example`), but it has **not**
  been confirmed against an actual API response.
- **License**: Government Open Data License - India (GODL), per the catalog
  page.
- **Access method**: `api.data.gov.in/resource/<id>` requires a registered
  API key. Directly tested (not assumed): requests with `format=json` and
  `format=csv`, with no key, both return HTTP 400
  `{"error": "Authorization field missing"}`. A bulk-download route that
  doesn't require a key was searched for and not found -- the old
  Drupal-era `/node/<id>/download` URL pattern 404s on the current platform.
  **A registered data.gov.in API key is required for any live-data path.**
- **MCA's own portal** (mca.gov.in) returns an Akamai WAF 403 on a plain
  request. This was not investigated further -- attempting to get past a
  WAF block is out of scope regardless of the outcome; see
  `docs/compliance.md`.
- **Column names / record count**: NOT independently verified. The catalog
  page's prose description lists CIN, Company Name, Company Status, Company
  Class, Company Category, Authorized Capital, Paid-up Capital, Date of
  Registration, Registered State, Registrar of Companies, Principal
  Business Activity, Registered Office Address, Sub Category -- but this is
  a human-written description, not literal column headers. A "~3.67 million
  companies" figure appeared in one search result but could not be
  confirmed and should not be repeated as fact.

## What this means for the codebase

`DATA_GOV_IN_API_KEY` stays empty. `Source.collection_enabled` for the live
API path stays `False`. Neither is changed by anything in this document --
see `app/compliance/source_policy.py`. We are **not blocked** on getting a
key, though: two things exist so real access can be verified and used the
moment a key (or an officially-obtained file) is available.

### 1. `python -m app.cli.inspect_mca_schema`

Read-only. Requires `DATA_GOV_IN_API_KEY`. Requests a small sample (default
5 rows) from the live API and prints:

- resource metadata (whatever the API response includes)
- actual field names and representative value types
- a diff against `app/source_adapters/mca_field_mapping.py`: unknown fields
  we don't recognize, and fields we expect that aren't present

Does not touch the database. This is the command to run the moment a key
exists, before trusting anything else in this document or the adapter
against real data.

### 2. `python -m app.cli.import_mca`

Imports an officially-obtained CSV/JSON export from disk through the exact
same adapter/pipeline the live API would use (see
`app/source_adapters/government_dataset_adapter.py` and
`app/ingestion/pipeline.py`). Does not require an API key or network
access -- a human obtains the file through an official channel first. See
its module docstring and `docs/adding_a_source.md` for details. Requires
`--source-url` (no default): a file is never treated as verified MCA data
merely because it was handed to this importer -- every resulting
observation is tagged `import_provenance_status=file_import_user_declared`,
never `platform_verified`.

## Field mapping

`app/source_adapters/mca_field_mapping.py` holds the external-column-name
-> canonical-field mapping as data, not inline string literals scattered
through the adapter. Every alias currently in it is either from the
project's own fixture or a documented historical variant -- **none of it
has been checked against a live API response**. When `inspect_mca_schema`
does run against real data, add any newly observed column-name variant
there; nothing else should need to change.
