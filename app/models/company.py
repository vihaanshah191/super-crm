import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class Company(UUIDPKMixin, TimestampMixin, Base):
    """Canonical company profile. Never written to directly from scraped/collected
    values -- populated only by the entity-resolution + evidence pipeline."""

    __tablename__ = "companies"

    # Identity
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(512))
    website_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    cin: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    llpin: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    gstin: Mapped[str | None] = mapped_column(String(32), index=True)
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    company_type: Mapped[str | None] = mapped_column(String(64))

    # Location
    registered_address: Mapped[str | None] = mapped_column(Text)
    operating_locations: Mapped[list | None] = mapped_column(JSONB, default=list)
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    state: Mapped[str | None] = mapped_column(String(128), index=True)
    # Free-text, pre-existing -- kept as-is (see docs/multi_source_architecture.md
    # Section G) so existing "India"-valued rows stay readable; new ingestion
    # paths should prefer setting country_code below where possible.
    country: Mapped[str | None] = mapped_column(String(128), index=True, default="India")
    # ISO 3166-1 alpha-2, added alongside `country` rather than replacing it.
    # Nullable and never backfilled automatically -- inferring "India" ->
    # "IN" for every existing free-text value would be a guess this project's
    # data-collection policy prohibits; a deliberate, reviewed backfill
    # script is a separate, later step.
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), index=True)

    # Business
    industry: Mapped[str | None] = mapped_column(String(128), index=True)
    sub_industry: Mapped[str | None] = mapped_column(String(128), index=True)
    products: Mapped[list | None] = mapped_column(JSONB, default=list)
    services: Mapped[list | None] = mapped_column(JSONB, default=list)
    business_model: Mapped[str | None] = mapped_column(String(16))  # b2b / b2c / both
    company_category: Mapped[str | None] = mapped_column(String(32), index=True)
    export_status: Mapped[bool | None] = mapped_column(Boolean)
    markets_served: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Metrics (standardized numeric values; formatting happens at presentation layer)
    employee_count: Mapped[int | None] = mapped_column(Integer)
    employee_range_min: Mapped[int | None] = mapped_column(Integer, index=True)
    employee_range_max: Mapped[int | None] = mapped_column(Integer, index=True)
    annual_revenue_inr: Mapped[float | None] = mapped_column(Numeric(20, 2), index=True)
    revenue_range_min_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_range_max_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_year: Mapped[int | None] = mapped_column(Integer, index=True)
    growth_signals: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    # Contacts
    public_phone: Mapped[str | None] = mapped_column(String(32))
    public_email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(512))

    # Metadata
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_count: Mapped[int] = mapped_column(Integer, default=0)

    aliases: Mapped[list["CompanyAlias"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_companies_state_city_industry", "state", "city", "industry"),
        Index("ix_companies_employee_range", "employee_range_min", "employee_range_max"),
    )


class CompanyAlias(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "company_aliases"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), nullable=False, index=True)

    company: Mapped[Company] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("ix_company_aliases_company_normalized", "company_id", "normalized_alias", unique=True),
    )
