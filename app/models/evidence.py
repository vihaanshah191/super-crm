import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class Evidence(UUIDPKMixin, TimestampMixin, Base):
    """Per-(company, field) rollup of the current best-known value plus the
    confidence/verification-type that justifies it. Recomputed whenever a new
    observation for that field arrives -- never edited by hand.
    """

    __tablename__ = "evidence"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(128), nullable=False)

    value: Mapped[str | None] = mapped_column(String(1024))
    numeric_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    range_min: Mapped[float | None] = mapped_column(Numeric(20, 4))
    range_max: Mapped[float | None] = mapped_column(Numeric(20, 4))
    unit: Mapped[str | None] = mapped_column(String(32))
    financial_year: Mapped[int | None] = mapped_column(Integer)

    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    verification_type: Mapped[str] = mapped_column(String(16), nullable=False)
    explanation: Mapped[dict] = mapped_column(JSONB, default=dict)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "field", name="uq_evidence_company_field"),
        Index("ix_evidence_company_field", "company_id", "field"),
    )


# Association table: which raw observations back a given evidence rollup.
class EvidenceObservation(Base):
    __tablename__ = "evidence_observations"

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_observations.id", ondelete="CASCADE"), primary_key=True
    )
