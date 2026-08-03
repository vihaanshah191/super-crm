"""Generic company-website source adapter.

Extracts a fixed set of fields from a company's own website using CSS
selectors, via the ScraplingCollector wrapper. Selectors are per-instance
configuration (`field_selectors`) because every company website's markup
differs -- there is no universal "company website" schema. In practice each
real deployed website source is its own Source row + adapter instance with
selectors tuned for that site (or for a directory template shared by many
sites of the same platform).

The default selectors below match the synthetic fixture at
tests/fixtures/html/example_company_website.html, used for tests and the
vertical-slice demo. This adapter has not been pointed at any real live
website -- do that only after confirming robots.txt/ToS permit automated
collection for the target site (see docs/compliance.md).
"""

from app.ingestion.collectors.scrapling_collector import ScraplingCollector
from app.ingestion.normalization.address import (
    extract_postal_code,
    normalize_domain,
    normalize_whitespace,
)
from app.ingestion.normalization.company_name import normalize_company_name
from app.ingestion.normalization.employee_range import parse_employee_range
from app.models.enums import VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter

DEFAULT_FIELD_SELECTORS = {
    "canonical_name": "h1.company-name::text",
    "industry": ".company-industry::text",
    "website": ".company-website::text",
    "public_phone": ".company-phone::text",
    "public_email": ".company-email::text",
    "address": "address.company-address::text",
    "employee_count": ".employee-count::text",
}

_MULTI_VALUE_SELECTORS = {
    "products": ".company-product::text",
}

# A website stating a fact about itself is OBSERVED, not VERIFIED -- it hasn't
# been corroborated by an independent authoritative source.
_WEBSITE_CONFIDENCE = 0.55


class WebsiteAdapter(SourceAdapter):
    source_type = "website"
    collector_version = "website-adapter/1.0.0"

    def __init__(
        self,
        source_name: str,
        collector: ScraplingCollector | None = None,
        field_selectors: dict[str, str] | None = None,
    ) -> None:
        self.source_name = source_name
        self._collector = collector or ScraplingCollector()
        self.field_selectors = field_selectors or DEFAULT_FIELD_SELECTORS

    def fetch(self, target: str) -> FetchResult:
        return self._collector.fetch_static(target)

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        from scrapling.parser import Selector

        selector = Selector(fetch_result.content, url=fetch_result.url)

        fields: dict[str, str | list[str]] = {}
        for field_name, css in self.field_selectors.items():
            values = [v for v in selector.css(css).getall() if v and v.strip()]
            if values:
                fields[field_name] = normalize_whitespace(" ".join(values))

        for field_name, css in _MULTI_VALUE_SELECTORS.items():
            values = [normalize_whitespace(v) for v in selector.css(css).getall() if v and v.strip()]
            if values:
                fields[field_name] = values

        if not fields.get("canonical_name"):
            return []

        return [
            ParsedRecord(
                external_ref=fetch_result.url,
                fields=fields,
                source_url=fetch_result.url,
                source_published_at=None,  # company websites rarely expose a publish date
            )
        ]

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        drafts: list[ObservationDraft] = []

        def add(field: str, raw: str, normalized: str | None, confidence: float = _WEBSITE_CONFIDENCE) -> None:
            drafts.append(
                ObservationDraft(
                    field=field,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=confidence,
                    verification_type=VerificationType.OBSERVED.value,
                )
            )

        if name := record.fields.get("canonical_name"):
            add("canonical_name", name, normalize_company_name(name))

        if industry := record.fields.get("industry"):
            add("industry", industry, industry.strip().lower())

        if website := record.fields.get("website"):
            add("website", website, normalize_domain(website))

        if phone := record.fields.get("public_phone"):
            add("public_phone", phone, normalize_whitespace(phone))

        if email := record.fields.get("public_email"):
            add("public_email", email, email.strip().lower())

        if address := record.fields.get("address"):
            add("address", address, address)
            if postal_code := extract_postal_code(address):
                add("postal_code", address, postal_code)

        if employee_text := record.fields.get("employee_count"):
            parsed = parse_employee_range(employee_text)
            if parsed:
                if parsed.count is not None:
                    add("employee_count", employee_text, str(parsed.count))
                if parsed.range_min is not None:
                    add("employee_range_min", employee_text, str(parsed.range_min))
                if parsed.range_max is not None:
                    add("employee_range_max", employee_text, str(parsed.range_max))

        if products := record.fields.get("products"):
            add("products", ", ".join(products), ", ".join(products))

        return drafts
