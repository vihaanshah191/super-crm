// Revenue/capital are stored as plain INR numerics end-to-end (see
// app/ingestion/normalization/revenue.py) -- lakh/crore formatting is
// presentation-only, applied here and nowhere upstream.
export function formatInr(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  if (value >= 1_00_00_000) return `₹${(value / 1_00_00_000).toFixed(2)} Cr`;
  if (value >= 1_00_000) return `₹${(value / 1_00_000).toFixed(2)} L`;
  return `₹${value.toLocaleString("en-IN")}`;
}

export function formatEmployeeRange(
  count: number | null | undefined,
  min: number | null | undefined,
  max: number | null | undefined
): string {
  if (count !== null && count !== undefined) return String(count);
  if (min !== null && min !== undefined && max !== null && max !== undefined) return `${min}–${max}`;
  if (min !== null && min !== undefined) return `${min}+`;
  return "Unknown";
}

export function confidenceLabel(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.75) return "high";
  if (confidence >= 0.4) return "medium";
  return "low";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "Unknown";
  return new Date(value).toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "numeric" });
}
