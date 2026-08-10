import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class CompanyGSTRegistration(UUIDPKMixin, TimestampMixin, Base):
    """One row per GST registration held by a company.

    A company registers for GST separately in each state it operates in, so
    "one Company -> one GSTIN" does not hold in general. `companies.gstin`
    stays as a denormalized copy of the primary registration's GSTIN (kept in
    sync by the ingestion pipeline) so simple lookups/search don't need a
    join; this table is the source of truth for the full set.

    Provenance fields (source_id/collected_at) mirror RawObservation instead
    of pointing back at it 1:1, because a registration can be corroborated by
    multiple observations over time -- same append-only-evidence posture as
    the rest of the schema, just narrower in scope.
    """

    __tablename__ = "company_gst_registrations"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gstin: Mapped[str] = mapped_column(String(15), nullable=False, unique=True, index=True)
    registered_state: Mapped[str | None] = mapped_column(String(128))
    registration_date: Mapped[date | None] = mapped_column(Date)
    cancellation_date: Mapped[date | None] = mapped_column(Date)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_company_gst_registrations_company_primary", "company_id", "is_primary"),
    )
