from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class Source(UUIDPKMixin, TimestampMixin, Base):
    """Registry of collection sources with compliance controls.

    A source with collection_enabled=False must never be scheduled or fetched --
    this is the switch that lets us disable a source (e.g. IndiaMART) until its
    permitted access method is confirmed.
    """

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(512))

    collection_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)

    robots_notes: Mapped[str | None] = mapped_column(Text)
    license_notes: Mapped[str | None] = mapped_column(Text)
    retention_notes: Mapped[str | None] = mapped_column(Text)
    reliability_weight: Mapped[int] = mapped_column(Integer, default=50)  # 0-100, used by confidence engine

    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, default=dict)
