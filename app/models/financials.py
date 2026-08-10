import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPKMixin


class CompanyFinancials(UUIDPKMixin, TimestampMixin, Base):
    """One row per (company, financial_year): retains a full history of
    revenue/capital figures instead of overwriting a single Company field
    every time a newer filing or estimate arrives.

    `financial_year` is a string ("FY2024", "2023-24") rather than an int so
    it can hold whatever format a source actually publishes without lossy
    normalization; adapters normalize to a single convention before writing.

    `companies.annual_revenue_inr` / `revenue_range_min_inr` /
    `revenue_range_max_inr` / `revenue_year` stay as a denormalized snapshot
    of the most recent financial_year's figures here, kept in sync by
    `recompute_company_evidence()` -- so existing search-by-revenue filters
    keep working against a single indexed column without a join, while this
    table is the source of truth for the full history.
    """

    __tablename__ = "company_financials"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    financial_year: Mapped[str] = mapped_column(String(9), nullable=False)

    annual_revenue_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_range_min_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    revenue_range_max_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    authorized_capital_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))
    paidup_capital_inr: Mapped[float | None] = mapped_column(Numeric(20, 2))

    verification_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")

    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("company_id", "financial_year", name="uq_company_financials_company_year"),
    )
