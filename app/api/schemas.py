import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.search.filter_types import FilterNode, MatchStrength, SortSpec, UnknownHandling


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
    country_code: str | None
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


class AdvancedSearchRequest(BaseModel):
    """Request body for POST /api/search/companies/advanced -- the generic,
    field/operator/value filter engine (app.search.filter_types). Kept as a
    separate endpoint from POST /api/search/companies rather than replacing
    it, so existing callers of the flat CompanySearchFilters shape keep
    working unchanged."""

    filter: FilterNode
    unknown_handling: UnknownHandling = UnknownHandling.DEFINITE_AND_POSSIBLE
    sort: list[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AdvancedSearchResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company: CompanyOut
    match_strength: MatchStrength


class AdvancedSearchResponse(BaseModel):
    total_returned: int
    results: list[AdvancedSearchResultOut]
    # Only populated when unknown_handling=include_unknown_separately -- see
    # app.search.advanced_query.find_unknown_bucket for what "unknown" means
    # here (never a definite non-match, never coerced to zero/false).
    unknown_results: list[CompanyOut] = []


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
    display_name: str | None
    source_type: str
    countries: list[str]
    access_method: str
    compliance_status: str
    collection_enabled: bool
    rate_limit_per_minute: int
    reliability_weight: int
    license_notes: str | None


class SourceHealthOut(BaseModel):
    """Per-source health, derived read-only from IngestionJob history --
    see app.ingestion.source_health. Not itself a new stored fact about a
    source (no migration involved)."""

    model_config = ConfigDict(from_attributes=True)

    source: SourceOut
    last_successful_run: datetime | None
    last_run_status: str | None
    last_run_at: datetime | None
    last_error: str | None
    records_collected_total: int
    total_jobs: int


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


# Fields a saved search's `selected_fields` may reference -- kept to what
# CompanyOut actually exposes, so a garbage/typo'd column name is rejected
# at creation time rather than silently stored (see app/models/saved_search.py).
_SELECTABLE_COMPANY_FIELDS = frozenset(CompanyOut.model_fields)


def _validate_selected_fields(value: list[str]) -> list[str]:
    unknown = [f for f in value if f not in _SELECTABLE_COMPANY_FIELDS]
    if unknown:
        raise ValueError(f"Unknown selected_fields: {unknown}. Known fields: {sorted(_SELECTABLE_COMPANY_FIELDS)}")
    return value


class SavedSearchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    created_by: str = Field(min_length=1, max_length=255)
    country_scope: list[str] = Field(default_factory=list)
    source_scope: list[uuid.UUID] = Field(default_factory=list)
    filter_definition: FilterNode
    sort: list[SortSpec] = Field(default_factory=list)
    selected_fields: list[str] = Field(default_factory=list)

    @field_validator("selected_fields")
    @classmethod
    def _check_selected_fields(cls, value: list[str]) -> list[str]:
        return _validate_selected_fields(value)


class SavedSearchUpdate(BaseModel):
    """All fields optional -- PATCH semantics: only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    country_scope: list[str] | None = None
    source_scope: list[uuid.UUID] | None = None
    filter_definition: FilterNode | None = None
    sort: list[SortSpec] | None = None
    selected_fields: list[str] | None = None

    @field_validator("selected_fields")
    @classmethod
    def _check_selected_fields(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return _validate_selected_fields(value)


class SavedSearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_by: str
    country_scope: list[str]
    source_scope: list[uuid.UUID]
    filter_definition: dict
    sort: list[dict]
    selected_fields: list[str]
    created_at: datetime
    updated_at: datetime


class SavedSearchExecuteRequest(BaseModel):
    unknown_handling: UnknownHandling = UnknownHandling.DEFINITE_AND_POSSIBLE
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
