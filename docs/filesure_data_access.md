# FileSure API: research findings (2026-08-04, live-verified 2026-08-06)

Direct findings from FileSure's own site/docs -- not guessed. Every claim
below is either quoted/paraphrased from a fetched page or extracted from
FileSure's own developer-portal JavaScript bundles (which embed a real,
complete example request/response used in their own "getting started" copy).

## Key finding: no API key is present in this environment (as of 2026-08-04)

Checked: all environment variables, every `.env*` file in this repo, common
secret-manager locations, shell profile/rc files. **No `FILESURE_API_KEY` or
`fsk_test_...`/`fsk_live_...` value exists anywhere in this session.**
`FILESURE_API_KEY` is added to config as an empty default (see below) and
`FILESURE_COLLECTION_ENABLED` stays `false`. The "LIVE SANDBOX VERIFICATION"
step in this task requires a real key that isn't available here -- see the
final report for what's needed to complete it.

**Update 2026-08-06**: a real `fsk_test_...` sandbox key was provided
out-of-band and written directly to the local, git-ignored `.env` (never
printed, logged, or committed -- see "Live sandbox verification" below for
what was confirmed with it). `docs`/code below that predate this note
describe what was knowable *without* a key; the "Live sandbox verification"
section documents what changed once real calls were possible.

## Official source

`https://api.filesure.in/` -- FileSure API, MCA (Ministry of Corporate
Affairs) registry data reseller. Not a government source itself; it resells/
republishes MCA V2/V3 portal data (company master data, directors, charges,
filings, and "structured extractions from statutory forms").

## Base URL

`https://api.filesure.in/v1/` -- confirmed directly: an unauthenticated GET
to `https://api.filesure.in/v1/openapi.json` (a guessed path, expecting a
spec file) returned `{"error":{"code":"MISSING_API_KEY","message":"API key
is required. Pass it via the x-api-key header."},"meta":{"requestId":"..."}}`,
proving `/v1/` is live, requires auth, and confirming the real error-response
shape and the `x-api-key` header name directly from the API itself (not from
marketing copy).

There is no separate sandbox hostname -- sandbox is the same base URL, using
a test-prefixed key.

## Authentication

Two documented forms:
- `x-api-key: <key>` header -- confirmed directly from the live API's own
  401 error message.
- `Authorization: Bearer <key>` -- shown in FileSure's own docs code sample
  (see below). Both are implemented; `x-api-key` is used as the primary
  since it's the one the API itself named when rejecting an unauthenticated
  request.

Key formats: `fsk_test_...` (sandbox/free) and `fsk_live_...` (production,
billed). The docs' own example uses the literal placeholder
`fsk_test_demo_xxxxxxxxxx` -- not a usable key, just illustrative.

## Sandbox test CINs

FileSure states (pricing/about pages): test keys "call the same endpoints
against **a fixed whitelist of real sample CINs and DINs** at zero cost."
The exact whitelist is not published on any marketing page reachable without
a portal login. The one CIN directly confirmed as FileSure's own documented
example is:

    L74110KA2013PLC096530  (Swiggy Limited)

This CIN appears in a real curl example embedded in FileSure's developer
portal JS bundle (`/portal/assets/auth-snippets-*.js`), used to demonstrate
calling `/v1/companies/{cin}` with a test key, together with a complete,
realistic sample response (reproduced below). It is the only CIN this
research can respons‌ibly recommend using against the sandbox -- per this
task's own instruction not to attempt random CINs. Whether it is definitely
in the live test-key whitelist is not 100% confirmed without an actual key;
`app/cli/filesure_lookup.py` defaults to it but accepts `--cin` override.

## Company master-data endpoint

`GET /v1/companies/{cin}` -- confirmed both from the homepage's endpoint
table and from the real example below.

### Confirmed real response shape (from FileSure's own docs sample)

```json
{
  "data": {
    "cin": "L74110KA2013PLC096530",
    "company": "SWIGGY LIMITED",
    "masterData": {
      "companyData": {
        "cin": "L74110KA2013PLC096530",
        "companyName": "SWIGGY LIMITED",
        "pan": "AAFCB7707N",
        "rocCode": "RoC-Bangalore",
        "registrationNumber": "096530",
        "companyCategory": "Company limited by shares",
        "companySubcategory": "Indian Non-Government Company",
        "classOfCompany": "Public",
        "companyType": "Public Company",
        "companyOrigin": "Indian",
        "whetherListedOrNot": "Listed",
        "companyStatus": "Active",
        "activeCompliance": "ACTIVE compliant",
        "dateOfIncorporation": "09/12/2013",
        "dateOfBalanceSheet": "31/03/2024",
        "dateOfLastAGM": "30/09/2024",
        "authorisedCapital": "250000000000",
        "paidupCapital": "22390000000",
        "MCAMDSCompanyAddress": [
          {
            "addressType": "Registered Address",
            "addressLine1": "NO. 55, I&II FLOOR, 17TH CROSS",
            "addressLine2": "8TH MAIN ROAD, N.S. PALYA, BTM LAYOUT",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pinCode": "560076"
          }
        ]
      },
      "directorData": [
        {"DIN": "07799277", "firstName": "SRIHARSHA", "lastName": "MAJETY",
         "designation": "Managing Director", "dateOfAppointment": "14/12/2017"}
      ],
      "indexChargesData": []
    }
  },
  "meta": {}
}
```

**Important finding: no revenue/turnover/profit field appears anywhere in
this master-data response.** Only `authorisedCapital` and `paidupCapital`
(both statutory registry figures, not trading revenue) are present. This
directly confirms the task's warning not to conflate capital with revenue --
FileSure's own master-data endpoint doesn't expose revenue at all.

## Financial/extraction endpoints

The homepage documents these additional endpoints (table on `api.filesure.in`):

| Endpoint | Method | Notes |
|---|---|---|
| `/v1/companies/{cin}/charges` | GET | Open/satisfied charge records |
| `/v1/companies/{cin}/filings` | GET | Full filing document history |
| `/v1/companies/{cin}/extractions` | GET | "Structured extractions from statutory forms (MGT-7, PAS-3, AOC-4)" |
| `/v1/companies/{cin}/unlock` | POST | On-demand registry refresh (₹330/year, billed) |
| `/v1/filings/{id}/download` | GET | Raw filed document |
| `/v1/directors/{din}` | GET | Director profile by DIN |

**AOC-4 is the statutory financial-statements form** -- if FileSure exposes
revenue/turnover/profit anywhere, `/v1/companies/{cin}/extractions` is where
it would be, since that's the endpoint documented as extracting structured
data from AOC-4 filings specifically.

**No example response for `/extractions` was found** in any page or bundle
reachable without a real API key -- exhaustively searched (page content,
five separate JS bundles from the developer portal) for a sample, for field
names containing "revenue"/"turnover"/"profit"/"income", and for an
OpenAPI/Scalar spec file (the portal's API reference at `/portal/docs` is a
client-rendered SPA that loads its OpenAPI spec at runtime from an
authenticated call this research could not reach).

**Consequence for this implementation**: `FileSureAdapter` calls
`/extractions` and preserves the raw response for provenance (so a human
reviewing `RawObservation.metadata_json` can see exactly what came back),
but does **not** normalize any field from it into `company_financials` yet
-- there is nothing here to responsibly map without guessing field names,
which the task explicitly prohibits. See `app/source_adapters/filesure_adapter.py`
for exactly where this is marked, and the final report for what's needed
once a real sandbox response is seen.

## Rate limits

Not documented on any page this research could reach. Test-key calls are
stated to be free and not billed; no numeric requests-per-minute/day limit
was found. `FileSureAdapter` still goes through the existing
`app/compliance/source_policy.RateLimiter` (same as every other source) with
a conservative default, since the absence of a published limit is not the
same as no limit.

## Pricing (for context, not used by this implementation)

Company master-data reads: ₹5.00/call (one secondary search-summary source
said ₹1.50 -- the ₹5.00 figure is from FileSure's own homepage table via
direct WebFetch and is treated as authoritative). Extraction/document calls:
₹0.05/call. Unlock: ₹330/year. Test keys are free regardless.

## Live sandbox verification (2026-08-06)

A small number of real, authenticated calls (2-3, during initial debugging
of two unrelated infrastructure bugs described below, plus one final
confirmatory run) were made against `https://api.filesure.in/v1` with the
provided `fsk_test_...` key, all for CIN `L74110KA2013PLC096530` (Swiggy
Limited) -- the one officially-documented sandbox CIN identified above. No
other CIN was probed. The key was never printed, logged, or written outside
the local `.env`.

### `GET /v1/companies/{cin}` -- real response shape differs from the docs example

The **outer envelope matches the docs example exactly**: `{"data": {"cin",
"company", "cinHistory", "nameHistory", "masterData": {...}}, "meta": {}}`.

Inside `masterData`, the live response has **schema drift from the docs
sample** in three ways that mattered for correctness:

1. **`cin`, `companyName`, `companyStatus` are NOT inside `companyData`
   live**, unlike the docs example. `cin` and the company name are only at
   the top level (`data.cin`, `data.company`); `companyStatus` lives in a
   sibling `masterData.commonData` object instead. The adapter's original
   implementation assumed the docs example's nesting and silently produced
   zero observations against the live response until this was found (via
   the parse() tests, not a live call -- see "Errors and fixes" note below).
2. **`masterData.commonData` is a second, real object not shown in the docs
   example at all.** Live `companyData` keys observed:
   `companyType, companyOrigin, registrationNumber, dateOfIncorporation,
   emailAddress, whetherListedOrNot, companyCategory, companySubcategory,
   classOfCompany, authorisedCapital, paidUpCapital, dateOfLastAGM,
   strikeOff_amalgamated_transferredDate, llpStatus, statusUnderCIRP,
   numberOfPartners, numberOfDesignatedPartners,
   totalObligationOfContribution, mainDivision, mainDivisionDescription,
   BSDefaulter2Yrs, suspendedAtStockExchange, MCAMDSCompanyAddress,
   balanceSheet3years, annualReturns3years, rocName, shareCapitalFlag,
   maximumNumberOfMembers, subscribedCapital, rdName, rdRegion,
   balanceSheetDate, inc22Aflag`. Live `commonData` keys observed:
   `ucin, obligatedContribution, unclassifiedAuthShareCap,
   maximumNumberOfMembers, registrationNumber, companiesINC22Flag,
   inc22AFlag, companyIncorporationName, companyStatus, status, ROCName,
   emailAddress, mobile, type, businessActivity, smallCompanyFlag,
   shareCapitalFlag, inc20AFlag, numberOfDirectors, companyAddress,
   holdingCompanyCIN, managementDisputeFlag, fax, ROCCode, listed,
   NICCode1, NICCode1Desc, NICCode2, NICCode2Desc, NICCode3, NICCode3Desc`.
   The sanitized full response is saved at
   `tests/fixtures/data/filesure_master_data_response_live.json` (all email
   addresses replaced with `***@***.redacted` before this file was written
   to disk -- see "PII handling" below).
3. **Field-name casing/naming variants**: `paidUpCapital` (live, camelCase)
   vs. `paidupCapital` (docs example); `rocName`/`ROCName` (live, two
   casings across the two objects) vs. `rocCode` (docs example). `pan` was
   **absent from both live objects entirely** -- not a drift, just not
   present for this company in this response.

`app/source_adapters/filesure_field_mapping.py` and
`app/source_adapters/filesure_adapter.py::parse()` were updated to read
`cin`/`company` from the top level, merge `commonData` and `companyData`
(companyData wins on key collision), and accept both the docs-sample and
live field-name variants as aliases -- see the field map's own comments for
exactly which alias came from which generation of evidence. A second, real
address shape was also found: `commonData.companyAddress` (lowercase
`addressline1`/`addressline2`/`pincode`) in addition to
`companyData.MCAMDSCompanyAddress` (docs-example-style
`streetAddress`/`streetAddress2`/`postalCode`, itself different from the
docs sample's `addressLine1`/`addressLine2`/`pinCode`) -- `normalize()`
checks all three shapes for each address component.

**One item deliberately left unresolved, not guessed**: the live
`dateOfIncorporation` value for this response was `"12/26/2013"`, which
cannot be DD/MM/YYYY (there is no 26th month) and is therefore MM/DD/YYYY --
but the docs example used `"09/12/2013"` (ambiguous between the two
formats) and `parse_flexible_date()` was written to try `%d-%m-%Y` /
`%d/%m/%Y` before ever seeing this. Adding an MM/DD/YYYY fallback now would
mean guessing which format applies to a given value with no reliable
signal, which risks silently parsing a genuinely ambiguous date wrong. This
field currently fails to parse for this live sample (no `incorporation_date`
observation is produced for it) rather than risk an incorrect one; flagged
here as a known limitation rather than worked around.

### `GET /v1/companies/{cin}/extractions` -- confirmed to be a *discovery*
endpoint, not financial figures

A live call succeeded and returned data, but it is **not** the AOC-4/MGT-7/
PAS-3 structured figures assumed possible from the homepage's endpoint
description. The real response lists which statutory-form extractions are
*available to unlock* for this CIN (form types, filing dates, an
unlock/pricing signal per form) -- it does not itself contain
revenue/turnover/profit/paid-up-capital-history figures. Getting actual
financial-statement figures out of FileSure would require a further,
per-form call (implied by the `/v1/companies/{cin}/unlock` endpoint, billed
per FileSure's pricing table) that was not attempted, consistent with this
task's instruction not to add production/bulk ingestion yet.

**Consequence**: `FileSureAdapter._normalize_financials()` continues to
deliberately return `[]` -- this is now a confirmed decision (extractions
genuinely has no figures to map), not merely an absence-of-evidence one.
The raw discovery-endpoint response is still preserved via
`RawObservation`/`ParsedRecord.fields["_extractions_raw"]` for provenance.

### PII handling for the captured live fixture

The raw live `companies/{cin}` response contained an **unmasked** email
address in `companyData.emailAddress`, while the same logical field
elsewhere in the same response (`commonData.emailAddress`) was masked by
FileSure itself. Before writing any live response to a committed test
fixture, all email-shaped strings in the captured JSON were replaced with
`***@***.redacted` (verified afterward with a full-file regex sweep showing
zero real `@`-containing values remain), and the original unredacted
temporary file was securely deleted (`shred -u`). The result is
`tests/fixtures/data/filesure_master_data_response_live.json`, used by
`tests/test_filesure_adapter.py::TestParseAgainstLiveConfirmedSchema`.

### Two unrelated infrastructure bugs found only because this was the first
real network call attempted in this project's history

Both are documented in code comments at their fix sites and are unrelated
to FileSure's API itself:

1. **`app/ingestion/collectors/scrapling_collector.py`**: `fetch_static()`
   was passing `retries=0` to Scrapling's `Fetcher.get()`. Scrapling treats
   this as a total-attempts count (`for attempt in range(retries)`), so
   `retries=0` meant the request body never ran at all, falling through to
   a defensive `RuntimeError("No active session available.")` -- every
   previous real-network attempt in this project (including an earlier,
   unrelated `data.gov.in` test) silently failed this way before ever
   reaching curl_cffi. Fixed to `retries=1`.
2. **TLS impersonation vs. this session's TLS-intercepting proxy**:
   Scrapling's default `impersonate="chrome"` (anti-bot TLS fingerprint
   spoofing) is incompatible with the agent proxy this sandboxed session
   runs behind, causing `SSLError: Recv failure`. Root-caused by comparing
   a direct `curl_cffi.Session()` call (worked) against `Fetcher.get()`
   (failed only with impersonation on). Not changed for production (the
   proxy issue is specific to this sandboxed environment, and disabling
   anti-bot fingerprinting by default would weaken real deployments) --
   instead added a `SCRAPLING_IMPERSONATE` setting (default unchanged,
   `"chrome"`), overridden to empty only in the local, uncommitted `.env`
   used for this session's live verification calls.

### Summary: what Super CRM gets from FileSure master data today

Confirmed-mappable fields: CIN, legal/canonical name, company status,
company type (class + category), incorporation date (when unambiguous),
ROC, authorized capital, paid-up capital, PAN (when present), registered
address (city/state/postal code + full string). **No revenue, turnover, or
profit field exists anywhere in the master-data or extractions responses
seen** -- financial-year figures remain unmapped by design, not omission.
