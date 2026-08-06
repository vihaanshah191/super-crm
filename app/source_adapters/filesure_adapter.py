"""FileSure adapter (api.filesure.in) -- an optional enrichment source that
resells/republishes MCA registry data plus (per its own docs) structured
extractions from statutory filings. See docs/filesure_data_access.md for
what was actually confirmed about the API before writing this file, and its
"Financial/extraction endpoints" section for why financial-year figures are
NOT normalized here yet.

Two independent, layered gates control whether this adapter's fetch() can
ever run:
  1. `Settings.filesure_collection_enabled` (FILESURE_COLLECTION_ENABLED) --
     a hard config-level switch, checked directly in fetch(), independent of
     any database state.
  2. The standard `Source.collection_enabled` / `SourcePolicy` compliance
     gate (see app/compliance/source_policy.py), checked by
     app.ingestion.pipeline.ingest_parsed_record() before fetch() is ever
     reached in the normal ingestion path.
Both must be true. This mirrors "collection is blocked in two independent
places, not just one" from docs/compliance.md.

Like GovernmentDatasetAdapter, this adapter never writes to Company
directly -- it produces ObservationDrafts; app.ingestion.pipeline does
everything from there (entity resolution by CIN, confidence-weighted
Evidence, canonical Company projection).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.ingestion.normalization.dates import parse_flexible_date
from app.models.enums import SourceType, VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.filesure_client import FileSureConfigurationError, fetch_company
from app.source_adapters.filesure_field_mapping import map_company_fields
from app.source_adapters.government_dataset_adapter import clean_numeric_string

PROVIDER_NAME = "filesure"
# What FileSure's own field naming implies about its master-data provenance
# (the field is literally named "MCAMDSCompanyAddress" -- MCA Master Data
# Services) -- not an independent confirmation from MCA itself, but FileSure's
# own documented claim about where the data comes from. Recorded as
# metadata, not asserted as fact beyond "this is what the provider states."
UNDERLYING_SOURCE_CLAIM = "MCA registry (via FileSure reseller)"

# Master-data confidence input: FileSure is a third-party reseller, not the
# registrar itself -- one notch below the 0.95 used for a direct MCA feed,
# reflecting the added provenance hop (see docs/confidence_engine.md).
_MASTER_DATA_CONFIDENCE = 0.85


class FileSureAdapter(SourceAdapter):
    source_type = SourceType.REGISTRY_DATA_PROVIDER.value
    collector_version = "filesure-adapter/1.0.0"

    def __init__(self, source_name: str, collector: ScraplingCollector | None = None) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()

    def fetch(self, target: str) -> FetchResult:
        """`target` is a CIN (not a URL) -- FileSureAdapter calls two
        FileSure endpoints per lookup (master data + best-effort
        extractions) and packages both into one JSON envelope, so parse()
        has a single self-describing payload, consistent with fetch()/
        parse() being the only stages that know the wire format."""
        settings = get_settings()
        if not settings.filesure_collection_enabled:
            raise FileSureConfigurationError(
                "FILESURE_COLLECTION_ENABLED is false -- FileSure collection is disabled "
                "regardless of any other configuration. See docs/filesure_data_access.md."
            )
        if not settings.filesure_api_key:
            raise FileSureConfigurationError("FILESURE_API_KEY is not configured.")

        response = fetch_company(
            target,
            api_key=settings.filesure_api_key,
            base_url=settings.filesure_base_url,
            collector=self._collector,
        )
        envelope = {
            "cin": response.cin,
            "master_data": response.master_data,
            "extractions_raw": response.extractions_raw,
            "extractions_error": response.extractions_error,
            "retrieved_at": response.retrieved_at_iso,
        }
        return FetchResult(
            url=response.source_url,
            status_code=200,
            content=json.dumps(envelope).encode("utf-8"),
            content_type="application/json",
            fetched_at=datetime.now(timezone.utc),
            metadata={"provider": PROVIDER_NAME, "filesure_env": settings.filesure_env},
        )

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        envelope = json.loads(fetch_result.content.decode("utf-8"))
        # envelope["master_data"] is the full FileSure `data` object (see
        # fetch()/filesure_client.fetch_company()). Confirmed live
        # (2026-08-06, see docs/filesure_data_access.md): `cin` and
        # `company` (the name) live at THIS top level, not inside
        # companyData -- FileSure's own docs example showed them
        # duplicated inside companyData too, but a live call found they
        # aren't there. companyStatus similarly lives in a sibling
        # "commonData" object, not companyData, in the live response.
        data = envelope.get("master_data") or {}
        master_data_section = data.get("masterData") or {}
        company_data = master_data_section.get("companyData") or {}
        common_data = master_data_section.get("commonData") or {}

        cin = str(data.get("cin") or "").strip().upper()
        if not cin:
            return []

        # commonData first, companyData second: on the one field name that
        # has appeared in both places across the two response samples seen
        # (docs example vs. live), companyData is treated as more specific
        # to this lookup and wins.
        merged_source = {**common_data, **company_data}
        mapped = map_company_fields(merged_source)
        mapped["cin"] = cin
        if company_name := data.get("company"):
            mapped["company_name"] = company_name

        fields: dict[str, Any] = {k: ("" if v is None else str(v)) for k, v in mapped.items()}
        # Structural/passthrough data that isn't a simple field rename --
        # kept out of the stringified dict above and handled explicitly in
        # normalize(). Address has been seen in two different shapes/field
        # names across the two response samples (see normalize() below);
        # both are captured here and normalize() handles either.
        fields["_address"] = company_data.get("MCAMDSCompanyAddress") or common_data.get("companyAddress") or []
        fields["_extractions_raw"] = envelope.get("extractions_raw")
        fields["_extractions_error"] = envelope.get("extractions_error")
        fields["_retrieved_at"] = envelope.get("retrieved_at")

        return [
            ParsedRecord(external_ref=cin, fields=fields, source_url=fetch_result.url, source_published_at=None)
        ]

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        cin = record.fields.get("cin", "")
        return len(cin) == 21

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        drafts: list[ObservationDraft] = []
        f = record.fields

        def add(
            field: str,
            raw: str,
            normalized: str | None,
            *,
            confidence: float = _MASTER_DATA_CONFIDENCE,
            verification_type: str = VerificationType.VERIFIED.value,
        ) -> None:
            drafts.append(
                ObservationDraft(
                    field=field,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=confidence,
                    verification_type=verification_type,
                    metadata={
                        "provider": PROVIDER_NAME,
                        "underlying_source": UNDERLYING_SOURCE_CLAIM,
                        "retrieved_at": f.get("_retrieved_at"),
                    },
                )
            )

        if cin := f.get("cin"):
            add("cin", cin, cin.upper())

        if name := f.get("company_name"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        if status := f.get("company_status"):
            add("company_status", status, status.strip().lower())

        class_of_company = f.get("class_of_company", "")
        company_category = f.get("company_category", "")
        if class_of_company or company_category:
            combined = normalize_whitespace(f"{class_of_company} {company_category}")
            add("company_type", combined, combined.lower())

        if inc_date := f.get("date_of_incorporation"):
            parsed_date = parse_flexible_date(inc_date)
            if parsed_date:
                add("incorporation_date", inc_date, parsed_date.isoformat())

        if roc := f.get("roc"):
            add("roc", roc, roc.strip())

        if auth_cap := f.get("authorized_capital"):
            add("authorized_capital_inr", auth_cap, clean_numeric_string(auth_cap))

        if paidup_cap := f.get("paidup_capital"):
            add("paidup_capital_inr", paidup_cap, clean_numeric_string(paidup_cap))

        if pan := f.get("pan"):
            add("pan", pan, pan.strip().upper())

        address_list = f.get("_address") or []
        registered_address = next(
            (a for a in address_list if a.get("addressType") == "Registered Address"),
            address_list[0] if address_list else None,
        )
        if registered_address:
            # Three address-object shapes have been observed across
            # FileSure's docs example and two address arrays in a single
            # live response (see docs/filesure_data_access.md):
            # addressLine1/addressLine2/pinCode (docs),
            # streetAddress/streetAddress2/postalCode
            # (companyData.MCAMDSCompanyAddress, live), and all-lowercase
            # addressline1/addressline2/pincode (commonData.companyAddress,
            # live). All three are read here; whichever is present wins.
            line1 = (
                registered_address.get("addressLine1")
                or registered_address.get("streetAddress")
                or registered_address.get("addressline1")
            )
            line2 = (
                registered_address.get("addressLine2")
                or registered_address.get("streetAddress2")
                or registered_address.get("addressline2")
            )
            pin_code = (
                registered_address.get("pinCode")
                or registered_address.get("postalCode")
                or registered_address.get("pincode")
            )

            full_address = normalize_whitespace(
                ", ".join(
                    filter(
                        None,
                        [
                            line1,
                            line2,
                            registered_address.get("city"),
                            registered_address.get("state"),
                            str(pin_code) if pin_code else None,
                        ],
                    )
                )
            )
            if full_address:
                add("registered_address", full_address, full_address)
            if city := registered_address.get("city"):
                add("city", city, city.strip())
            if state := registered_address.get("state"):
                add("state", state, state.strip().lower())
            if pin_code:
                add("postal_code", str(pin_code), str(pin_code).strip())

        drafts.extend(_normalize_financials(f.get("_extractions_raw"), f.get("_extractions_error")))

        return drafts


def _normalize_financials(extractions_raw: dict | None, extractions_error: str | None) -> list[ObservationDraft]:
    """FileSure's `/companies/{cin}/extractions` endpoint (built from
    AOC-4/MGT-7/PAS-3 statutory filings, per FileSure's own docs) is where
    financial-year revenue/turnover/profit data would live if FileSure
    exposes it -- but no confirmed response schema for this endpoint was
    found during research (see docs/filesure_data_access.md: every page and
    JS bundle reachable without a real API key was searched for a sample
    response or field names, with none found).

    Producing a company_financials mapping without having seen real field
    names would mean guessing -- which this project's data-collection
    policy explicitly prohibits (see docs/adding_a_source.md: "Do not
    invent mappings for fields we don't understand"). This function is the
    single place that mapping will go once a real extractions response has
    been observed (via `python -m app.cli.filesure_lookup`); until then it
    deliberately returns no financial observations. The raw response (or
    error) is still preserved via ParsedRecord.fields/RawObservation
    provenance so a human can inspect exactly what FileSure returned.
    """
    return []
