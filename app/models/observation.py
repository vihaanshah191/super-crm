import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class RawObservation(UUIDPKMixin, TimestampMixin, Base):
    """A single fact as reported by a single source at a single point in time.

    Immutable once written (append-only). Never overwrites canonical Company
    fields directly -- normalization/entity-resolution/confidence read these
    and produce Evidence + Company updates.
    """

    __tablename__ = "raw_observations"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))

    field: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)

    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    verification_type: Mapped[str] = mapped_column(String(16), nullable=False)
    collector_version: Mapped[str] = mapped_column(String(64), nullable=False)

    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_raw_observations_company_field", "company_id", "field"),
        Index("ix_raw_observations_source_collected", "source_id", "collected_at"),
    )
