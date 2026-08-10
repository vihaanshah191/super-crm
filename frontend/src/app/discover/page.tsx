"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ApiError, searchCompaniesAdvanced } from "@/lib/api";
import type { AdvancedSearchResultOut, CompanyOut, FilterCondition, FilterNode, MatchStrength, UnknownHandling } from "@/lib/types";
import { fieldOption, LIST_VALUE_OPERATORS, NO_VALUE_OPERATORS } from "@/lib/filter-fields";
import { FilterRowEditor, newFilterRow, type FilterRowState } from "@/components/filter-row-editor";
import { formatEmployeeRange, formatInr } from "@/lib/format";
import { ConfidenceBadge } from "@/components/confidence-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const UNKNOWN_HANDLING_OPTIONS: { value: UnknownHandling; label: string }[] = [
  { value: "definite_and_possible", label: "Definite + possible (default)" },
  { value: "definite_only", label: "Definite only" },
  { value: "include_unknown_separately", label: "Definite + possible, show unknown separately" },
];

const MATCH_STRENGTH_BADGE: Record<MatchStrength, { label: string; variant: "secondary" | "outline" | "muted" }> = {
  definite: { label: "Definite", variant: "secondary" },
  possible: { label: "Possible", variant: "outline" },
  unknown: { label: "Unknown", variant: "muted" },
};

let rowIdCounter = 0;
function nextRowId(): string {
  rowIdCounter += 1;
  return `row-${rowIdCounter}`;
}

/** UI-only text input -> the typed value a FilterCondition needs, per the
 * field's data_type. The backend independently re-validates every
 * condition (see FilterCondition._check_operator_valid_for_type in
 * app/search/filter_types.py) -- this is only responsible for producing a
 * well-formed request, not for being the source of truth on validity. */
function toConditionValue(row: FilterRowState, dataType: string): unknown {
  if (NO_VALUE_OPERATORS.includes(row.operator)) return undefined;
  if (row.operator === "BETWEEN") {
    return dataType === "number" ? [Number(row.value), Number(row.value2)] : [row.value, row.value2];
  }
  if (LIST_VALUE_OPERATORS.includes(row.operator)) {
    const parts = row.value.split(",").map((v) => v.trim()).filter(Boolean);
    return dataType === "number" ? parts.map(Number) : parts;
  }
  if (dataType === "number") return Number(row.value);
  if (dataType === "boolean") return row.value === "true";
  return row.value;
}

function rowToCondition(row: FilterRowState): FilterCondition {
  const option = fieldOption(row.field);
  return {
    field: row.field,
    operator: row.operator,
    data_type: option.dataType,
    value: toConditionValue(row, option.dataType),
  };
}

function rowIsFilled(row: FilterRowState): boolean {
  if (NO_VALUE_OPERATORS.includes(row.operator)) return true;
  if (row.operator === "BETWEEN") return row.value.trim() !== "" && row.value2.trim() !== "";
  return row.value.trim() !== "";
}

export default function DiscoverPage() {
  const [rows, setRows] = useState<FilterRowState[]>([newFilterRow(nextRowId())]);
  const [combineMode, setCombineMode] = useState<"AND" | "OR">("AND");
  const [unknownHandling, setUnknownHandling] = useState<UnknownHandling>("definite_and_possible");

  const [results, setResults] = useState<AdvancedSearchResultOut[] | null>(null);
  const [unknownResults, setUnknownResults] = useState<CompanyOut[]>([]);
  const [totalReturned, setTotalReturned] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function updateRow(id: string, next: FilterRowState) {
    setRows((prev) => prev.map((r) => (r.id === id ? next : r)));
  }

  function removeRow(id: string) {
    setRows((prev) => (prev.length <= 1 ? prev : prev.filter((r) => r.id !== id)));
  }

  function addRow() {
    setRows((prev) => [...prev, newFilterRow(nextRowId())]);
  }

  const filledRows = rows.filter(rowIsFilled);

  async function runSearch(e?: FormEvent) {
    e?.preventDefault();
    if (filledRows.length === 0) {
      setError("Add at least one filter with a value before searching.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const conditions = filledRows.map(rowToCondition);
      const filter: FilterNode = conditions.length === 1 ? conditions[0] : { op: combineMode, conditions };
      const response = await searchCompaniesAdvanced({ filter, unknown_handling: unknownHandling, limit: 50 });
      setResults(response.results);
      setUnknownResults(response.unknown_results);
      setTotalReturned(response.total_returned);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API. Is the backend running?");
      setResults(null);
      setUnknownResults([]);
    } finally {
      setLoading(false);
    }
  }

  function renderCompanyRow(company: CompanyOut, matchStrength: MatchStrength | null) {
    const badge = matchStrength ? MATCH_STRENGTH_BADGE[matchStrength] : null;
    return (
      <TableRow key={company.id}>
        <TableCell className="font-medium">
          <Link href={`/companies/${company.id}`} className="hover:underline">
            {company.canonical_name}
          </Link>
        </TableCell>
        <TableCell>{[company.city, company.state].filter(Boolean).join(", ") || "Unknown"}</TableCell>
        <TableCell>{company.industry ?? "Unknown"}</TableCell>
        <TableCell>{formatEmployeeRange(company.employee_count, company.employee_range_min, company.employee_range_max)}</TableCell>
        <TableCell>{formatInr(company.annual_revenue_inr)}</TableCell>
        <TableCell>{company.source_count}</TableCell>
        <TableCell>
          {badge ? (
            <Badge variant={badge.variant === "muted" ? "outline" : badge.variant} className={badge.variant === "muted" ? "text-muted-foreground" : undefined}>
              {badge.label}
            </Badge>
          ) : (
            <span className="text-xs text-muted-foreground">n/a</span>
          )}
        </TableCell>
        <TableCell>
          <ConfidenceBadge confidence={company.confidence} />
        </TableCell>
      </TableRow>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="text-sm text-muted-foreground">
          Build any combination of field/operator/value filters -- executed deterministically against indexed
          company fields, no free-text matching. Unknown (missing) data is never treated as a non-match.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={runSearch} className="flex flex-col gap-3">
            {rows.length > 1 && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Match</span>
                <select
                  className="h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  value={combineMode}
                  onChange={(e) => setCombineMode(e.target.value as "AND" | "OR")}
                >
                  <option value="AND">all filters (AND)</option>
                  <option value="OR">any filter (OR)</option>
                </select>
              </div>
            )}

            {rows.map((row) => (
              <FilterRowEditor
                key={row.id}
                row={row}
                onChange={(next) => updateRow(row.id, next)}
                onRemove={() => removeRow(row.id)}
                canRemove={rows.length > 1}
              />
            ))}

            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" variant="outline" size="sm" onClick={addRow}>
                + Add filter
              </Button>

              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Unknown data</span>
                <select
                  className="h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  value={unknownHandling}
                  onChange={(e) => setUnknownHandling(e.target.value as UnknownHandling)}
                >
                  {UNKNOWN_HANDLING_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <Button type="submit" disabled={loading} className="ml-auto">
                {loading ? "Searching…" : "Search"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Search failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {!loading && results !== null && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{totalReturned} result{totalReturned === 1 ? "" : "s"}</CardTitle>
          </CardHeader>
          <CardContent>
            {results.length === 0 ? (
              <p className="text-sm text-muted-foreground">No companies match these filters.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Company</TableHead>
                    <TableHead>Location</TableHead>
                    <TableHead>Industry</TableHead>
                    <TableHead>Employees</TableHead>
                    <TableHead>Revenue</TableHead>
                    <TableHead>Sources</TableHead>
                    <TableHead>Match</TableHead>
                    <TableHead>Confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>{results.map((r) => renderCompanyRow(r.company, r.match_strength))}</TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {!loading && unknownResults.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {unknownResults.length} with unknown data on a filtered field
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              Excluded from the results above only because we don&apos;t have data for a filtered field -- not
              because a known value contradicts the filter.
            </p>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Company</TableHead>
                  <TableHead>Location</TableHead>
                  <TableHead>Industry</TableHead>
                  <TableHead>Employees</TableHead>
                  <TableHead>Revenue</TableHead>
                  <TableHead>Sources</TableHead>
                  <TableHead>Match</TableHead>
                  <TableHead>Confidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>{unknownResults.map((c) => renderCompanyRow(c, "unknown"))}</TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
