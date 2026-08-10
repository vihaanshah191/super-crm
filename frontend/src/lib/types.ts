// Mirrors app/api/schemas.py. Kept hand-written (not generated) since the
// backend has no OpenAPI-client codegen step wired up yet -- if these two
// ever drift, the fix is either to add codegen or to update both by hand,
// not to guess field names at the call site.

export interface EvidenceOut {
  field: string;
  value: string | null;
  numeric_value: number | null;
  range_min: number | null;
  range_max: number | null;
  unit: string | null;
  financial_year: number | null;
  confidence: number;
  verification_type: string;
  explanation: Record<string, unknown>;
  computed_at: string;
}

export interface CompanyOut {
  id: string;
  canonical_name: string;
  legal_name: string | null;
  website: string | null;
  website_domain: string | null;
  cin: string | null;
  gstin: string | null;
  incorporation_date: string | null;
  company_type: string | null;

  city: string | null;
  state: string | null;
  country: string | null;
  postal_code: string | null;

  industry: string | null;
  sub_industry: string | null;
  products: string[] | null;
  company_category: string | null;
  export_status: boolean | null;

  employee_count: number | null;
  employee_range_min: number | null;
  employee_range_max: number | null;
  annual_revenue_inr: number | null;
  revenue_range_min_inr: number | null;
  revenue_range_max_inr: number | null;
  revenue_year: number | null;

  public_phone: string | null;
  public_email: string | null;

  confidence: number;
  last_verified_at: string | null;
  source_count: number;
}

export interface CompanyDetailOut extends CompanyOut {
  evidence: EvidenceOut[];
}

export interface CompanySearchResultOut extends CompanyOut {
  match_is_definite: boolean | null;
}

export interface CompanySearchResponse {
  total_returned: number;
  results: CompanySearchResultOut[];
}

export interface CompanySearchFilters {
  industry?: string;
  product?: string;
  city?: string;
  state?: string;
  country?: string;
  employee_min?: number;
  employee_max?: number;
  revenue_min_inr?: number;
  revenue_max_inr?: number;
  incorporated_before?: string;
  incorporated_after?: string;
  company_category?: string;
  export_status?: boolean;
  min_confidence?: number;
  verification_type?: string;
  last_verified_after?: string;
  limit?: number;
  offset?: number;
}

export interface CompanyFinancialsOut {
  id: string;
  financial_year: string;
  annual_revenue_inr: number | null;
  revenue_range_min_inr: number | null;
  revenue_range_max_inr: number | null;
  authorized_capital_inr: number | null;
  paidup_capital_inr: number | null;
  verification_type: string;
  collected_at: string | null;
}

export interface CompanyGSTRegistrationOut {
  id: string;
  gstin: string;
  registered_state: string | null;
  registration_date: string | null;
  cancellation_date: string | null;
  is_primary: boolean;
}

export interface SourceOut {
  id: string;
  name: string;
  source_type: string;
  collection_enabled: boolean;
  rate_limit_per_minute: number;
  reliability_weight: number;
  license_notes: string | null;
}

export interface SourceHealthOut {
  source: SourceOut;
  last_successful_run: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_error: string | null;
  records_collected_total: number;
  total_jobs: number;
}

export interface IngestionJobOut {
  id: string;
  source_id: string;
  status: string;
  idempotency_key: string;
  started_at: string | null;
  finished_at: string | null;
  records_discovered: number;
  records_updated: number;
  records_failed: number;
  retry_count: number;
  error_summary: string | null;
}

export interface EntityMatchCandidateOut {
  id: string;
  observation_id: string;
  candidate_company_id: string | null;
  incoming_payload: Record<string, unknown>;
  match_score: number;
  matched_signals: Record<string, unknown>;
  status: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface EntityMatchCandidateDetailOut extends EntityMatchCandidateOut {
  candidate_company: CompanyOut | null;
}

// Mirrors app/search/filter_types.py -- the generic filter engine behind
// POST /api/search/companies/advanced. See lib/filter-fields.ts for the
// field registry (mirrors app/search/filter_registry.py) that drives the
// Discover page's dynamic filter builder.

export type FilterDataType = "string" | "number" | "date" | "boolean" | "enum";

export type FilterOperator =
  | "="
  | "!="
  | ">"
  | ">="
  | "<"
  | "<="
  | "IN"
  | "NOT_IN"
  | "CONTAINS"
  | "STARTS_WITH"
  | "EXISTS"
  | "NOT_EXISTS"
  | "BETWEEN";

export interface FilterCondition {
  field: string;
  operator: FilterOperator;
  data_type: FilterDataType;
  value?: unknown;
}

export interface FilterGroup {
  op: "AND" | "OR" | "NOT";
  conditions: FilterNode[];
}

export type FilterNode = FilterCondition | FilterGroup;

export type MatchStrength = "definite" | "possible" | "unknown";

export type UnknownHandling = "definite_only" | "definite_and_possible" | "include_unknown_separately";

export interface AdvancedSearchRequest {
  filter: FilterNode;
  unknown_handling?: UnknownHandling;
  limit?: number;
  offset?: number;
}

export interface AdvancedSearchResultOut {
  company: CompanyOut;
  match_strength: MatchStrength;
}

export interface AdvancedSearchResponse {
  total_returned: number;
  results: AdvancedSearchResultOut[];
  unknown_results: CompanyOut[];
}
