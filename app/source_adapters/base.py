"""Generic source/collector abstraction.

Every collection mechanism -- Scrapling-backed HTTP fetches, government open-data
downloads, future directory/marketplace/filing integrations -- implements this
interface. Nothing outside `app/source_adapters/` and `app/ingestion/collectors/`
should import a specific collection library (e.g. `scrapling`) directly: business
logic (normalization, entity resolution, confidence, search) only ever sees
`ParsedRecord` / `ObservationDraft`, so swapping the underlying fetch mechanism
never touches those layers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FetchResult:
    """Raw bytes retrieved from a source, before any parsing."""

    url: str
    status_code: int
    content: bytes
    content_type: str
    fetched_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedRecord:
    """One real-world entity's worth of raw fields extracted from a fetch,
    before normalization or entity resolution."""

    external_ref: str
    fields: dict[str, Any]
    source_url: str | None = None
    source_published_at: datetime | None = None


@dataclass(frozen=True)
class ObservationDraft:
    """A single field observation, ready to be persisted as a RawObservation
    once entity resolution has determined which company (if any) it belongs to."""

    field: str
    raw_value: str
    normalized_value: str | None
    confidence: float
    verification_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    """Interface every collection mechanism must implement.

    fetch() and parse() are the only stages allowed to know about the wire
    format of the source (HTML, CSV, JSON API, ...). normalize() converts raw
    field values into standardized units (see app.ingestion.normalization).
    validate() is a last line of defense against malformed/unsafe input before
    anything is written to raw_observations.
    """

    source_name: str
    source_type: str
    collector_version: str = "1.0.0"

    @abstractmethod
    def fetch(self, target: str) -> FetchResult:
        """Retrieve raw content for `target` (a URL, resource id, or file path
        depending on the adapter). Must not raise for ordinary HTTP error
        statuses -- callers inspect FetchResult.status_code."""

    @abstractmethod
    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]:
        """Extract zero or more entity records from a fetched payload."""

    @abstractmethod
    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]:
        """Convert a parsed record's raw fields into ObservationDrafts with
        standardized normalized_value representations."""

    def validate(self, record: ParsedRecord) -> bool:
        """Baseline sanity check run before normalize(). Subclasses should
        extend, not replace, this (call super().validate(record) too)."""
        if not record.fields:
            return False
        if not record.external_ref or not record.external_ref.strip():
            return False
        return True
