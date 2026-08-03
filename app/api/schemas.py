import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field: str
    value: str | None
    numeric_value: float | None
    range_min: float | None
    range_max: float | None
    unit: str | None
    financial_year: int | None
    confidence: float
    verification_type: str
    explanation: dict
    computed_at: datetime


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_name: str
    legal_name: str | None
    website: str | None
    website_domain: str | None
    cin: str | None
    gstin: str | None
    incorporation_date: date | None
    company_type: str | None

    city: str | None
    state: str | None
    country: str | None
    postal_code: str | None

    industry: str | None
    sub_industry: str | None
    products: list | None
    company_category: str | None
    export_status: bool | None

    employee_count: int | None
    employee_range_min: int | None
    employee_range_max: int | None
    annual_revenue_inr: float | None
    revenue_range_min_inr: float | None
    revenue_range_max_inr: float | None
    revenue_year: int | None

    public_phone: str | None
    public_email: str | None

    confidence: float
    last_verified_at: datetime | None
    source_count: int


class CompanyDetailOut(CompanyOut):
    evidence: list[EvidenceOut] = []


class CompanySearchResponse(BaseModel):
    total_returned: int
    results: list[CompanyOut]
