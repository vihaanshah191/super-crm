"""Structured search filter schema.

An LLM may translate a natural-language query ("Manufacturers in Maharashtra
with 20+ employees and ₹10cr+ revenue") into a CompanySearchFilters instance,
but it never decides which companies qualify -- query.py executes filters
deterministically against indexed columns. Keep this schema flat and typed so
that translation step has a small, validated target to fill in.
"""

from datetime import date

from pydantic import BaseModel, Field


class CompanySearchFilters(BaseModel):
    industry: str | None = None
    product: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None

    employee_min: int | None = Field(default=None, ge=0)
    employee_max: int | None = Field(default=None, ge=0)

    revenue_min_inr: float | None = Field(default=None, ge=0)
    revenue_max_inr: float | None = Field(default=None, ge=0)

    incorporated_before: date | None = None
    incorporated_after: date | None = None

    company_category: str | None = None  # manufacturer / distributor / service_provider / ...
    export_status: bool | None = None

    min_confidence: float | None = Field(default=None, ge=0, le=1)
    verification_type: str | None = None  # verified / observed / estimated / unknown
    last_verified_after: date | None = None

    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
