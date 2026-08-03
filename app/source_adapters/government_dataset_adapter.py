"""Government/open-data source adapter.

Per the project's data-collection policy: for government and open datasets,
an official API/CSV/JSON/XML download is always preferred over HTML scraping.
This adapter targets the "Company Master Data" dataset published by India's
Ministry of Corporate Affairs (MCA) via data.gov.in under the Government Open
Data License - India (GODL) -- a downloadable CSV resource, not a page to
scrape. It still goes through ScraplingCollector.fetch_static() for the HTTP
GET itself (uniform retry/backoff/SSRF-guard/logging across every HTTP-based
source), but never touches Scrapling's HTML parser -- CSV is parsed with the
standard library.

MCA/RoC data (CIN, legal name, incorporation date, registered address,
company class/category) is treated as VERIFIED: it comes directly from the
statutory registrar of companies, the highest-reliability source type this
system defines. Financial figures (authorized/paid-up capital) are also
VERIFIED but are registry filings, not trading revenue -- they are stored as
distinct evidence fields, never conflated with a company's operating
"annual_revenue".
"""

import csv
import io
from datetime import date, datetime

from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import extract_postal_code, normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.models.enums import VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter

# MCA/data.gov.in publication metadata, retained for provenance (see
# RawObservation.metadata / docs/compliance.md). Not fetched dynamically --
# these describe the dataset itself, not a single record.
DATASET_LICENSE = "Government Open Data License - India (GODL)"
DATASET_PUBLISHER = "Ministry of Corporate Affairs (via data.gov.in)"

_GOVT_CONFIDENCE = 0.95

# Known alternate column names in different data.gov.in MCA dataset exports.
# The actual CSV header may include suffixes like "(for efiling)" or use
# older abbreviations -- we normalize before reading any field.
_COLUMN_ALIASES: dict[str, str] = {
    "AUTHORIZED_CAP": "AUTHORIZED_CAPITAL",
    "REGISTRAR_OF_COMPANIES": "ROC",
    "ROC_CODE": "ROC",
}


def _normalize_column_names(row: dict[str, str]) -> dict[str, str]:
    """Strip '(for efiling)'-style suffixes and apply known column aliases."""
    normalized: dict[str, str] = {}
    for key, value in row.items():
        clean = key.split("(")[0].strip()
        clean = _COLUMN_ALIASES.get(clean, clean)
        normalized[clean] = value
    return normalized


class GovernmentDatasetAdapter(SourceAdapter):
    source_type = "government_dataset"
    collector_version = "government-dataset-adapter/1.0.0"

    def __init__(self, source_name: str, collector: ScraplingCollector | None = None) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()

    def fetch(self, target: str) -> FetchResult:
        """`target` is the data.gov.in resource download URL (CSV). Requires
        DATA_GOV_IN_MCA_RESOURCE_URL / an API key to be configured -- see
        .env.example. Not used by tests/the demo, which parse a saved CSV
        fixture directly instead of hitting the live dataset."""
        return self._collector.fetch_static(target)

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        text = fetch_result.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        records: list[ParsedRecord] = []
        for row in reader:
            row = _normalize_column_names(row)
            cin = (row.get("CIN") or "").strip()
            if not cin:
                continue
            fields = {k: (v or "").strip() for k, v in row.items()}
            records.append(
                ParsedRecord(
                    external_ref=cin,
                    fields=fields,
                    source_url=fetch_result.url,
                    source_published_at=None,
                )
            )
        return records

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        cin = record.fields.get("CIN", "")
        # CIN is a fixed 21-character alphanumeric identifier assigned by MCA.
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

        if cin := f.get("CIN"):
            add("cin", cin, cin.upper())

        if name := f.get("COMPANY_NAME"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        if status := f.get("COMPANY_STATUS"):
            add("company_status", status, status.strip().lower())

        company_class = f.get("COMPANY_CLASS", "")
        company_category = f.get("COMPANY_CATEGORY", "")
        if company_class or company_category:
            combined = normalize_whitespace(f"{company_class} {company_category}")
            add("company_type", combined, combined.lower())

        if reg_date := f.get("DATE_OF_REGISTRATION"):
            parsed_date = _parse_date(reg_date)
            if parsed_date:
                add("incorporation_date", reg_date, parsed_date.isoformat())

        if state := f.get("REGISTERED_STATE"):
            add("state", state, state.strip().lower())

        if address := f.get("REGISTERED_OFFICE_ADDRESS"):
            add("registered_address", address, address)
            if postal_code := extract_postal_code(address):
                add("postal_code", address, postal_code)

        if auth_cap := f.get("AUTHORIZED_CAPITAL"):
            add("authorized_capital_inr", auth_cap, _clean_numeric(auth_cap))

        if paidup_cap := f.get("PAIDUP_CAPITAL"):
            add("paidup_capital_inr", paidup_cap, _clean_numeric(paidup_cap))

        return drafts


def _parse_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _clean_numeric(raw: str) -> str | None:
    cleaned = raw.replace(",", "").strip()
    return cleaned if cleaned.replace(".", "", 1).isdigit() else None
