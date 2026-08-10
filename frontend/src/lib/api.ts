import type {
  CompanyDetailOut,
  CompanyFinancialsOut,
  CompanyGSTRegistrationOut,
  CompanySearchFilters,
  CompanySearchResponse,
  EntityMatchCandidateDetailOut,
  IngestionJobOut,
  SourceOut,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function searchCompanies(filters: CompanySearchFilters): Promise<CompanySearchResponse> {
  return request<CompanySearchResponse>("/api/search/companies", {
    method: "POST",
    body: JSON.stringify(filters),
  });
}

export function getCompany(id: string): Promise<CompanyDetailOut> {
  return request<CompanyDetailOut>(`/api/companies/${id}`);
}

export function getCompanyFinancials(id: string): Promise<CompanyFinancialsOut[]> {
  return request<CompanyFinancialsOut[]>(`/api/companies/${id}/financials`);
}

export function getCompanyGstRegistrations(id: string): Promise<CompanyGSTRegistrationOut[]> {
  return request<CompanyGSTRegistrationOut[]>(`/api/companies/${id}/gst-registrations`);
}

export function listSources(): Promise<SourceOut[]> {
  return request<SourceOut[]>("/api/ingestion/sources");
}

export function listIngestionJobs(status?: string): Promise<IngestionJobOut[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<IngestionJobOut[]>(`/api/ingestion/jobs${query}`);
}

export function listReviewQueue(): Promise<EntityMatchCandidateDetailOut[]> {
  return request<EntityMatchCandidateDetailOut[]>("/api/review-queue");
}

export function confirmMatch(candidateId: string, reviewedBy: string): Promise<CompanyDetailOut> {
  return request(`/api/review-queue/${candidateId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ reviewed_by: reviewedBy }),
  });
}

export function rejectMatch(candidateId: string, reviewedBy: string): Promise<void> {
  return request(`/api/review-queue/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reviewed_by: reviewedBy }),
  });
}
