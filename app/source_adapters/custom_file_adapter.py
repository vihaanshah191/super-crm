"""CustomFileAdapter -- Phase 7 of the multi-source expansion
(docs/multi_source_architecture.md): imports a CSV or JSON file through an
arbitrary, self-declared field mapping (see custom_field_mapping.py)
instead of a hardcoded provider-specific one like mca_field_mapping.py or
filesure_field_mapping.py.

Like every other adapter, this NEVER writes to Company directly -- it
produces ObservationDrafts; app.ingestion.pipeline does entity resolution
and canonical projection from there, so a bad custom mapping can corrupt at
most this source's own observations/evidence, never bypass confidence-
weighted merging with other sources for the same company.

Confidence/verification posture: user-supplied file data is the weakest
provenance tier in this project -- OBSERVED (never VERIFIED, since nothing
here independently confirms the file's authenticity) at a below-registry
confidence weight. Compare: MCA live/file-import ~0.95, FileSure ~0.85,
WebsiteAdapter's un-vetted HTML is the closest precedent for "collected but
not authoritative."

Known limitation: this adapter's fetch() reads a *local* file path -- it
has no network-fetch/re-poll mechanism, so it cannot currently be driven by
the Celery scheduler's periodic re-collection loop the way an API-backed
source can (a "schedule" for a file source would need a watched-directory
or re-upload mechanism this pass doesn't add). app/cli/import_custom_source.py
is the intended entrypoint for now, matching app/cli/import_mca.py's local-
file precedent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.normalization.address import normalize_whitespace
from app.ingestion.normalization.company_name import normalize_company_name
from app.ingestion.normalization.dates import parse_flexible_date
from app.models.enums import SourceType, VerificationType
from app.source_adapters.base import FetchResult, ObservationDraft, ParsedRecord, SourceAdapter
from app.source_adapters.custom_field_mapping import CANONICAL_FIELD_TYPES, map_row, value_matches_type
from app.source_adapters.government_dataset_adapter import clean_numeric_string, sniff_and_parse_rows

PROVIDER_NAME = "custom_file_import"

# Below every registry/API-backed source's confidence input (MCA ~0.95,
# FileSure ~0.85) -- see module docstring for why.
_CUSTOM_SOURCE_CONFIDENCE = 0.4


class CustomFileAdapter(SourceAdapter):
    source_type = SourceType.USER_FILE.value
    collector_version = "custom-file-adapter/1.0.0"

    def __init__(self, source_name: str, field_mapping: dict[str, str]) -> None:
        self.source_name = source_name
        self.field_mapping = field_mapping
        # canonical field -> the source column name that produced it, for
        # per-observation provenance (see normalize()'s add()).
        self._reverse_mapping = {v: k for k, v in field_mapping.items()}

    def fetch(self, target: str) -> FetchResult:
        """`target` is a local file path -- no network call, matching
        app/cli/import_mca.py's local-file transport."""
        path = Path(target)
        raw_bytes = path.read_bytes()
        content_type = "application/json" if path.suffix.lower() == ".json" else "text/csv"
        return FetchResult(
            url=f"file://{path.resolve()}",
            status_code=200,
            content=raw_bytes,
            content_type=content_type,
            fetched_at=datetime.now(timezone.utc),
            metadata={"local_file_path": str(path.resolve())},
        )

    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        text = fetch_result.content.decode("utf-8-sig")
        rows = sniff_and_parse_rows(text)
        records: list[ParsedRecord] = []
        for i, row in enumerate(rows):
            mapped = map_row(row, self.field_mapping)
            if not mapped:
                continue
            # Prefer a real identifier for external_ref (stable across
            # re-imports); a positional fallback is still safe -- entity
            # resolution never trusts external_ref alone, only the
            # identity signals normalize() derives from the row's fields.
            external_ref = mapped.get("cin") or mapped.get("gstin") or mapped.get("website") or f"row-{i}"
            records.append(
                ParsedRecord(
                    external_ref=external_ref, fields=mapped, source_url=fetch_result.url, source_published_at=None
                )
            )
        return records

    def validate(self, record: ParsedRecord) -> bool:
        if not super().validate(record):
            return False
        return bool(record.fields.get("legal_name", "").strip())

    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        drafts: list[ObservationDraft] = []
        f = record.fields

        def add(field_name: str, raw: str, normalized: str | None) -> None:
            drafts.append(
                ObservationDraft(
                    field=field_name,
                    raw_value=raw,
                    normalized_value=normalized,
                    confidence=_CUSTOM_SOURCE_CONFIDENCE,
                    verification_type=VerificationType.OBSERVED.value,
                    metadata={
                        "provider": PROVIDER_NAME,
                        "source_column": self._reverse_mapping.get(field_name),
                    },
                )
            )

        if name := f.get("legal_name"):
            add("legal_name", name, name.strip())
            add("canonical_name", name, normalize_company_name(name))

        for field_name, raw in f.items():
            if field_name == "legal_name" or not raw:
                continue
            data_type = CANONICAL_FIELD_TYPES.get(field_name, "string")
            if not value_matches_type(raw, data_type):
                # Malformed cell for this one field -- skip just this
                # observation, never the whole row over one bad column.
                continue

            if data_type == "number":
                cleaned = clean_numeric_string(raw)
                if cleaned is not None:
                    add(field_name, raw, cleaned)
            elif data_type == "date":
                parsed_date = parse_flexible_date(raw)
                if parsed_date:
                    add(field_name, raw, parsed_date.isoformat())
            elif data_type == "boolean":
                add(field_name, raw, str(raw.strip().lower() in {"true", "yes", "1"}))
            elif field_name == "country_code":
                add(field_name, raw, raw.strip().upper())
            elif field_name in ("state", "city", "country", "postal_code", "cin", "gstin"):
                add(field_name, raw, raw.strip())
            else:
                add(field_name, raw, normalize_whitespace(raw))

        return drafts
