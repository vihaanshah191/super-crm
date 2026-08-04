"""Government/open-data source adapter.

Per the project's data-collection policy: for government and open datasets,
an official API/CSV/JSON/XML download is always preferred over HTML scraping.
This adapter targets the "Company Master Data" dataset published by India's
Ministry of Corporate Affairs (MCA) via data.gov.in under the Government Open
Data License - India (GODL). It still goes through ScraplingCollector for the
HTTP GET itself when fetching live (uniform retry/backoff/SSRF-guard/logging
across every HTTP-based source), but never touches Scrapling's HTML parser --
both CSV and the data.gov.in JSON API response shape are parsed with the
standard library.

Two independent transports feed this same adapter (see docs/mca_data_access.md
and app/cli/import_mca.py):
  - the live data.gov.in REST API (JSON), gated behind DATA_GOV_IN_API_KEY and
    the collection_enabled compliance flag -- currently disabled;
  - an officially-obtained CSV/JSON file imported from disk via
    `python -m app.cli.import_mca`, which does not require network access or
    an API key, but requires the operator to explicitly state where the file
    came from (see import_mca.py) rather than silently trusting it.
Both paths run parse() -> validate() -> normalize() identically; only fetch()
differs, and the file-import CLI doesn't call fetch() at all (it builds a
FetchResult directly from the file's bytes).

MCA/RoC data (CIN, legal name, incorporation date, registered address,
company class/category, ROC) is treated as VERIFIED: it comes directly from
the statutory registrar of companies, the highest-reliability source type
this system defines. Financial figures (authorized/paid-up capital) are also
VERIFIED but are registry filings, not trading revenue -- they are stored as
distinct evidence fields (authorized_capital_inr / paidup_capital_inr), never
conflated with a company's operating annual_revenue. See
docs/mca_data_access.md for the important distinction between "adapter
implemented" and "live schema verified" -- the latter has NOT happened yet.

Column names: this adapter does not hardcode exact external column-name
strings inline. See app/source_adapters/mca_field_mapping.py for the
documented external-name -> canonical-field mapping and why it exists.
"""

import csv
import io
import json
from datetime import date, datetime

from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import extract_postal_code, normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.models.enums import VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.mca_field_mapping import map_external_fields

# MCA/data.gov.in publication metadata, retained for provenance (see
# RawObservation.metadata / docs/compliance.md). Not fetched dynamically --
# these describe the dataset itself, not a single record.
DATASET_LICENSE = "Government Open Data License - India (GODL)"
DATASET_PUBLISHER = "Ministry of Corporate Affairs (via data.gov.in)"
DATASET_NAME = "MCA Company Master Data"
# Discovered from the live data.gov.in catalog/resource page HTML on
# 2026-08-04 (docs/mca_data_access.md) -- NOT confirmed by an actual API
# response, since that requires DATA_GOV_IN_API_KEY.
DATASET_CATALOG_URL = "https://www.data.gov.in/catalog/company-master-data"

_GOVT_CONFIDENCE = 0.95


def clean_numeric_string(raw: str) -> str | None:
    """Strip thousands separators from a monetary string; return None (not a
    fabricated 0) if what's left isn't a valid non-negative number. Exported
    (not module-private) so callers reporting on malformed-value counts --
    e.g. the import_mca dry-run report -- can reuse the exact same check the
    adapter uses, rather than re-implementing it."""
    cleaned = raw.replace(",", "").strip()
    return cleaned if cleaned.replace(".", "", 1).isdigit() else None


class GovernmentDatasetAdapter(SourceAdapter):
    source_type = "government_dataset"
    collector_version = "government-dataset-adapter/2.0.0"

    def __init__(self, source_name: str, collector: ScraplingCollector | None = None) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()

    def fetch(self, target: str) -> FetchResult:
        """`target` is the data.gov.in resource URL (with api-key and format
        query params already attached -- see app/cli/inspect_mca_schema.py
        and docs/mca_data_access.md for how that URL is built). Requires
        DATA_GOV_IN_API_KEY to be configured; collection stays disabled via
        the Source.collection_enabled compliance gate until then regardless.
        Not used by tests/the demo, which parse a saved CSV fixture directly
        instead of hitting the live dataset."""
        return self._collector.fetch_static(target)

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        """Handles both wire formats data.gov.in publishes this dataset in:
        CSV (the traditional bulk-export format) and the JSON shape returned
        by the api.data.gov.in REST API (`{"records": [...], ...}`, or a bare
        JSON array for a pre-extracted records file). Format is sniffed from
        content, not trusted from a caller-supplied content_type, since a
        locally imported file's content_type is often just "text/csv" or
        unset regardless of its actual contents."""
        text = fetch_result.content.decode("utf-8-sig")
        rows = sniff_and_parse_rows(text)
        return records_from_rows(rows, source_url=fetch_result.url)

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        cin = record.fields.get("cin", "")
        # CIN is a fixed 21-character alphanumeric identifier assigned by MCA.
        # A record without a well-formed CIN fails validation for that row --
        # we never fabricate or guess a CIN to let a row through.
        return len(cin) == 21

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        drafts: list[ObservationDraft] = []

        def add(field: str, raw: str, normalized: str | None, confidence: float = _GOVT_CONFIDENCE) -> None:
            drafts.append(
                ObservationDraft(
                    field=field,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=confidence,
                    verification_type=VerificationType.VERIFIED.value,
                    metadata={"dataset_license": DATASET_LICENSE, "dataset_publisher": DATASET_PUBLISHER},
                )
            )

        f = record.fields

        if cin := f.get("cin"):
            add("cin", cin, cin.upper())

        if name := f.get("company_name"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        if status := f.get("company_status"):
            add("company_status", status, status.strip().lower())

        company_class = f.get("company_class", "")
        company_category = f.get("company_category", "")
        if company_class or company_category:
            combined = normalize_whitespace(f"{company_class} {company_category}")
            add("company_type", combined, combined.lower())

        if reg_date := f.get("date_of_registration"):
            parsed_date = _parse_date(reg_date)
            if parsed_date:
                add("incorporation_date", reg_date, parsed_date.isoformat())

        if state := f.get("registered_state"):
            add("state", state, state.strip().lower())

        if address := f.get("registered_office_address"):
            add("registered_address", address, address)
            if postal_code := extract_postal_code(address):
                add("postal_code", address, postal_code)

        if roc := f.get("roc"):
            add("roc", roc, roc.strip())

        if auth_cap := f.get("authorized_capital"):
            add("authorized_capital_inr", auth_cap, clean_numeric_string(auth_cap))

        if paidup_cap := f.get("paidup_capital"):
            add("paidup_capital_inr", paidup_cap, clean_numeric_string(paidup_cap))

        return drafts


def records_from_rows(rows: list[dict[str, object]], *, source_url: str | None) -> list[ParsedRecord]:
    """Map already-parsed raw rows (dicts keyed by external column name) into
    ParsedRecords, dropping any row without a usable CIN. Exposed (not
    module-private) so callers that need row-level visibility BEFORE this
    drop happens -- e.g. app/cli/import_mca.py's dry-run report, which counts
    "missing CIN" rows as a distinct stat -- can call sniff_and_parse_rows()
    themselves, inspect the raw rows, then still funnel the same rows through
    this exact function for the records that actually get ingested. There is
    only one place CIN-presence filtering happens; nothing re-implements it."""
    records: list[ParsedRecord] = []
    for row in rows:
        mapped = map_external_fields(row)
        cin = str(mapped.get("cin") or "").strip()
        if not cin:
            continue
        fields = {k: ("" if v is None else str(v)).strip() for k, v in mapped.items()}
        records.append(
            ParsedRecord(external_ref=cin, fields=fields, source_url=source_url, source_published_at=None)
        )
    return records


def sniff_and_parse_rows(text: str) -> list[dict[str, object]]:
    """Detect CSV vs JSON and return the raw row dicts (external column names
    as keys, before mapping to canonical fields). Public for the same reason
    as records_from_rows() above."""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _parse_json_rows(stripped)
    return list(csv.DictReader(io.StringIO(text)))


def _parse_json_rows(text: str) -> list[dict[str, object]]:
    """Handle both the api.data.gov.in response envelope
    (`{"records": [...], "field": [...], "total": N, ...}`) and a bare JSON
    array of row objects (e.g. a records-only export saved to disk)."""
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        records = parsed.get("records")
        if isinstance(records, list):
            return records
    raise ValueError("Unrecognized JSON shape for MCA dataset: expected a list or a {'records': [...]} object")


def _parse_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None
