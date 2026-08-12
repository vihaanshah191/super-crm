// Client-side mirror of app/search/filter_registry.py's FIELD_REGISTRY, for
// driving the Discover page's dynamic filter builder (field dropdown ->
// operator dropdown, restricted to what's valid for that field). The
// backend re-validates every condition independently (see
// FilterCondition._check_operator_valid_for_type in filter_types.py) --
// this list only controls what the UI *offers*, it isn't itself a trust
// boundary.

import type { FilterDataType, FilterOperator } from "./types";

export type ValueInputKind = "text" | "number" | "date" | "boolean" | "select";

export interface FilterFieldOption {
  value: string;
  label: string;
  dataType: FilterDataType;
  operators: FilterOperator[];
  inputKind: ValueInputKind;
  selectOptions?: string[];
  /** True for numeric fields with a companion estimated-range pair
   * (employees, revenue) -- these are the fields that can produce a
   * "possible" (not just definite/unknown) match strength. */
  rangeCapable?: boolean;
}

const STRING_OPS: FilterOperator[] = ["=", "!=", "CONTAINS", "STARTS_WITH", "IN", "NOT_IN", "EXISTS", "NOT_EXISTS"];
const ENUM_OPS: FilterOperator[] = ["=", "!=", "IN", "NOT_IN", "EXISTS", "NOT_EXISTS"];
const BOOLEAN_OPS: FilterOperator[] = ["=", "!=", "EXISTS", "NOT_EXISTS"];
const DATE_OPS: FilterOperator[] = ["=", "!=", ">", ">=", "<", "<=", "BETWEEN", "EXISTS", "NOT_EXISTS"];
const NUMBER_OPS: FilterOperator[] = ["=", "!=", ">", ">=", "<", "<=", "IN", "NOT_IN", "BETWEEN", "EXISTS", "NOT_EXISTS"];
const NUMBER_ORDER_ONLY_OPS: FilterOperator[] = ["=", "!=", ">", ">=", "<", "<="];
const DATE_ORDER_ONLY_OPS: FilterOperator[] = [">", ">=", "<", "<=", "EXISTS", "NOT_EXISTS"];

export const FILTER_FIELDS: FilterFieldOption[] = [
  { value: "country", label: "Country", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  {
    value: "country_code",
    label: "Country code (ISO)",
    dataType: "string",
    operators: STRING_OPS,
    inputKind: "text",
  },
  { value: "state", label: "State", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "city", label: "City", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "postal_code", label: "Postal code", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "industry", label: "Industry", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "sub_industry", label: "Sub-industry", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "legal_name", label: "Legal name", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "website", label: "Website", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "cin", label: "CIN", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "gstin", label: "GSTIN", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  { value: "company_type", label: "Company type", dataType: "string", operators: STRING_OPS, inputKind: "text" },
  {
    value: "company_category",
    label: "Company category",
    dataType: "enum",
    operators: ENUM_OPS,
    inputKind: "select",
    selectOptions: ["manufacturer", "distributor", "service_provider", "retailer", "unknown"],
  },
  { value: "export_status", label: "Exports", dataType: "boolean", operators: BOOLEAN_OPS, inputKind: "boolean" },
  { value: "incorporation_date", label: "Incorporated", dataType: "date", operators: DATE_OPS, inputKind: "date" },
  {
    value: "last_verified_at",
    label: "Last verified",
    dataType: "date",
    operators: DATE_ORDER_ONLY_OPS,
    inputKind: "date",
  },
  {
    value: "confidence",
    label: "Confidence",
    dataType: "number",
    operators: NUMBER_ORDER_ONLY_OPS,
    inputKind: "number",
  },
  {
    value: "employees",
    label: "Employees",
    dataType: "number",
    operators: NUMBER_OPS,
    inputKind: "number",
    rangeCapable: true,
  },
  {
    value: "revenue_inr",
    label: "Revenue (INR)",
    dataType: "number",
    operators: NUMBER_OPS,
    inputKind: "number",
    rangeCapable: true,
  },
  { value: "revenue_year", label: "Revenue year", dataType: "number", operators: NUMBER_OPS, inputKind: "number" },
];

export function fieldOption(fieldName: string): FilterFieldOption {
  const found = FILTER_FIELDS.find((f) => f.value === fieldName);
  if (!found) {
    throw new Error(`Unknown filter field: ${fieldName}`);
  }
  return found;
}

export const OPERATOR_LABELS: Record<FilterOperator, string> = {
  "=": "equals",
  "!=": "does not equal",
  ">": "greater than",
  ">=": "at least",
  "<": "less than",
  "<=": "at most",
  IN: "is one of",
  NOT_IN: "is not one of",
  CONTAINS: "contains",
  STARTS_WITH: "starts with",
  EXISTS: "is known",
  NOT_EXISTS: "is unknown",
  BETWEEN: "is between",
};

export const NO_VALUE_OPERATORS: FilterOperator[] = ["EXISTS", "NOT_EXISTS"];
export const LIST_VALUE_OPERATORS: FilterOperator[] = ["IN", "NOT_IN"];
