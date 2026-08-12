from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SourceAccessMethod, SourceComplianceStatus
from app.models.mixins import TimestampMixin, UUIDPKMixin


class Source(UUIDPKMixin, TimestampMixin, Base):
    """Registry of collection sources with compliance controls.

    A source with collection_enabled=False must never be scheduled or fetched --
    this is the switch that lets us disable a source (e.g. IndiaMART) until its
    permitted access method is confirmed. `compliance_status` records *why*
    (see SourceComplianceStatus) -- collection_enabled is the operational
    switch, compliance_status is the human-reviewed reason behind it; they
    are deliberately two separate fields so "not yet reviewed" and
    "reviewed and blocked" are never conflated.
    """

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # Internal, stable identifier (existing column, unchanged). Distinct
    # from display_name, which is what a UI shows a human -- `name` values
    # like "mca_company_master_data_file_import" are deliberately not
    # user-facing.
    display_name: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(512))

    # ISO 3166-1 alpha-2 codes this source covers (e.g. ["IN"]). Empty list
    # means global/country-agnostic (e.g. a source that itself spans many
    # countries, or one not yet classified) -- never inferred from
    # source_type or name, only set explicitly.
    countries: Mapped[list[str]] = mapped_column(ARRAY(String(2)), nullable=False, default=list)
    access_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceAccessMethod.UNKNOWN.value
    )
    compliance_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceComplianceStatus.UNDER_REVIEW.value
    )

    collection_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)

    robots_notes: Mapped[str | None] = mapped_column(Text)
    license_notes: Mapped[str | None] = mapped_column(Text)
    retention_notes: Mapped[str | None] = mapped_column(Text)
    reliability_weight: Mapped[int] = mapped_column(Integer, default=50)  # 0-100, used by confidence engine

    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
