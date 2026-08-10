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


class CompanySearchResultOut(CompanyOut):
    match_is_definite: bool | None = None


class CompanySearchResponse(BaseModel):
    total_returned: int
    results: list[CompanySearchResultOut]


class CompanyFinancialsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    financial_year: str
    annual_revenue_inr: float | None
    revenue_range_min_inr: float | None
    revenue_range_max_inr: float | None
    authorized_capital_inr: float | None
    paidup_capital_inr: float | None
    verification_type: str
    collected_at: datetime | None


class CompanyGSTRegistrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gstin: str
    registered_state: str | None
    registration_date: date | None
    cancellation_date: date | None
    is_primary: bool


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    source_type: str
    collection_enabled: bool
    rate_limit_per_minute: int
    reliability_weight: int
    license_notes: str | None


class IngestionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    idempotency_key: str
    started_at: datetime | None
    finished_at: datetime | None
    records_discovered: int
    records_updated: int
    records_failed: int
    retry_count: int
    error_summary: str | None


class EntityMatchCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    observation_id: uuid.UUID
    candidate_company_id: uuid.UUID | None
    incoming_payload: dict
    match_score: float
    matched_signals: dict
    status: str
    resolved_at: datetime | None
    resolved_by: str | None


class EntityMatchCandidateDetailOut(EntityMatchCandidateOut):
    candidate_company: CompanyOut | None = None


class ReviewDecisionIn(BaseModel):
    reviewed_by: str
