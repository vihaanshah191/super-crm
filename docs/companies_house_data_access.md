# UK Companies House: access status

This document records what was actually verified against Companies House's
live official documentation before `app/source_adapters/companies_house_adapter.py`
was written -- not assumptions, not what `public-apis/public-apis` claimed
(that repository was used only as a discovery pointer, per
`docs/multi_source_architecture.md`'s Phase-19-style discipline of not
trusting a discovery catalog's own field/terms claims).

## What was directly verified (2026-08-14)

- **Authentication**: HTTP Basic Access Authentication, with the API key as
  the username and the password left blank. Confirmed from
  `developer.company-information.service.gov.uk/authentication`:
  > "The Companies House API takes the username as the API or stream key
  > and ignores the password, so it can be left blank."

  Example: `curl -XGET -u my_api_key: https://api.company-information.service.gov.uk/company/00000006`

- **Base URL**: `https://api.company-information.service.gov.uk` (from the
  same page's curl example).

- **Rate limit**: 600 requests per 5-minute window, per API key. Confirmed
  from `developer.company-information.service.gov.uk/developer-guidelines`:
  > "You can make up to 600 requests within a 5 minute period... you will
  > receive a `429 Too Many Requests` HTTP status code for each request
  > made within the rest of the 5 minute time frame. At the end of the
  > period, your rate limit will reset back to 600 requests."

- **Registration**: free. Sign in, create an application (test or live
  environment), create API client credentials. No cost mentioned anywhere
  in the developer docs.

- **Commercial use / licensing**: the register itself is public and the
  developer docs reference Crown copyright. Strong circumstantial evidence
  (Crown-copyright government data of this kind is standardly published
  under the Open Government Licence, and this is corroborated by
  third-party developer discussion) supports commercial use being
  permitted -- but no single official page fetched during this
  verification stated "commercial use permitted" in as many words. Anyone
  running this in production should confirm current ToS at
  `developer.company-information.service.gov.uk` before relying on it
  commercially at scale.

- **Endpoint used**: `GET /company/{company_number}` -- confirmed field
  names via the official API reference
  (`developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference`):
  `company_name`, `company_number`, `company_status`, `type`,
  `date_of_creation`, `sic_codes`, `jurisdiction`, `registered_office_address`
  (`address_line_1`, `address_line_2`, `locality`, `region`, `postal_code`,
  `country`), `has_charges`, `has_insolvency_history`, `accounts.next_due`,
  `confirmation_statement.next_due`.

- **Bulk data**: Companies House separately publishes a free monthly "Free
  Company Data Product" -- a CSV snapshot of the full register, updated
  within 5 working days of month end, downloadable at
  `download.companieshouse.gov.uk` with no API key required. **Not yet
  implemented** in this codebase (only the live single-company lookup is
  built, per this batch's scope) -- this is the natural next step if
  Companies House becomes a P0/bulk-ingestion priority the way MCA is for
  India, and would follow `GovernmentDatasetAdapter`'s CSV-import pattern
  exactly.

## What was NOT found / confirmed absent

- **Filed financial figures (revenue/turnover)**: the company-profile
  endpoint (`GET /company/{company_number}`) does not return them --
  confirmed by reading the documented response shape. Only filing
  *metadata* is available (`accounts.next_due`, whether accounts are
  overdue). Getting actual filed revenue requires either downloading and
  parsing the company's iXBRL/PDF accounts documents directly, or a
  separate structured-financials provider (e.g. Registrum, found during
  discovery -- **explicitly not implemented in this batch** per the task
  that authorized this work). `CompaniesHouseAdapter` never claims
  revenue/employee data it doesn't have.
- **Employee count**: not present anywhere in the company-profile response.
- **SIC descriptions**: Companies House returns only numeric SIC 2007
  codes, not human-readable text. This adapter buckets the first SIC code
  to its official ONS SIC-2007 *section* (a small, stable, publicly
  documented set of ~21 top-level divisions) for `industry`, and preserves
  the raw code(s) on `sub_industry` -- see
  `app/source_adapters/companies_house_field_mapping.py` for the exact
  section boundaries and why this is the honest middle ground between
  "no industry data" and inventing per-code descriptions we have no source
  for.

## Compliance gates

Two independent gates, mirroring FileSure (`docs/filesure_data_access.md`):

1. `Settings.companies_house_collection_enabled` (`COMPANIES_HOUSE_COLLECTION_ENABLED`)
   -- config-level, checked directly in `CompaniesHouseAdapter.fetch()`.
2. `Source.collection_enabled` -- the standard DB-level `SourcePolicy` gate.

Both default to disabled/False. `python -m app.cli.companies_house_lookup`
is the only place the `companies_house` Source row gets created, with
`collection_enabled=True` set at that point -- running the CLI with an
explicit company number *is* the human authorization step, same convention
as `filesure_lookup.py` and `import_mca.py`.

Companies House data is free (no per-call cost, unlike FileSure), so the
config-level gate here isn't cost protection -- it exists purely for
architectural consistency with the "collection is blocked in two
independent places, not just one" principle (`docs/compliance.md`), and so
the source can be killed via an env var without a DB write.

**Scheduled-collection note** (same structural situation as FileSure, see
`docs/source_strategy.md`): `_adapter_for()` in `app/ingestion/jobs/tasks.py`
would return a `CompaniesHouseAdapter` for a `companies_house` Source row if
Celery Beat ever dispatched it, but `dispatch_enabled_source_collections()`
always passes `source_target=None`, and `run_source_collection()` refuses
to call `adapter.fetch()` without a target -- so this source can never be
scheduled automatically today. That protection is incidental (Beat has
literally no company number to send), not a purpose-built exclusion the way
`user_file` sources get in `dispatch_enabled_source_collections()`.

## Live verification status

The adapter's request-building, response-parsing, and error-handling code
paths are covered by tests against **mocked** responses built from the
real, documented schema above (see `tests/test_companies_house_*.py`) --
**no live API key was available during this work**, so `CompaniesHouseAdapter.fetch()`
against the real API has not itself been exercised end-to-end. The correct
verification phrase for this source is:

> Companies House: IMPLEMENTED / DISABLED PENDING CREDENTIALS

not "verified live" -- matching the same discipline `docs/mca_data_access.md`
established for MCA's live API path.
