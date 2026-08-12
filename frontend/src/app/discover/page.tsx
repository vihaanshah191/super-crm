"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  ApiError,
  createSavedSearch,
  deleteSavedSearch,
  executeSavedSearch,
  listSavedSearches,
  listSources,
  searchCompaniesAdvanced,
} from "@/lib/api";
import type {
  AdvancedSearchResultOut,
  CompanyOut,
  FilterCondition,
  FilterNode,
  MatchStrength,
  SavedSearchOut,
  SourceOut,
  UnknownHandling,
} from "@/lib/types";
import { fieldOption, LIST_VALUE_OPERATORS, NO_VALUE_OPERATORS } from "@/lib/filter-fields";
import { FilterRowEditor, newFilterRow, type FilterRowState } from "@/components/filter-row-editor";
import { formatDate, formatEmployeeRange, formatInr } from "@/lib/format";
import { ConfidenceBadge } from "@/components/confidence-badge";
import { SourceBadges } from "@/components/source-badges";
import { isDemoSourceName } from "@/lib/demo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// Placeholder until auth/identity is wired in -- same convention as
// review-queue/page.tsx's REVIEWER constant.
const CREATED_BY = "frontend-operator";

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

function buildFilterNode(filledRows: FilterRowState[], combineMode: "AND" | "OR"): FilterNode {
  const conditions = filledRows.map(rowToCondition);
  return conditions.length === 1 ? conditions[0] : { op: combineMode, conditions };
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

  const [savedSearches, setSavedSearches] = useState<SavedSearchOut[]>([]);
  const [savedSearchesError, setSavedSearchesError] = useState<string | null>(null);
  const [savedSearchesReloadToken, setSavedSearchesReloadToken] = useState(0);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);
  const [activeSavedSearchId, setActiveSavedSearchId] = useState<string | null>(null);
  const savedSearchesMounted = useRef(true);

  const [sources, setSources] = useState<SourceOut[]>([]);
  const [selectedCountry, setSelectedCountry] = useState<string>("");
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set());
  const sourcesMounted = useRef(true);

  // Fetches on mount and whenever savedSearchesReloadToken changes (bumped
  // after save/delete) -- the effect owns the fetch lifecycle directly
  // rather than calling an externally-defined async function, per
  // react-hooks/set-state-in-effect (same pattern as review-queue/page.tsx).
  useEffect(() => {
    savedSearchesMounted.current = true;
    listSavedSearches(CREATED_BY)
      .then((data) => {
        if (savedSearchesMounted.current) {
          setSavedSearches(data);
          setSavedSearchesError(null);
        }
      })
      .catch((err) => {
        if (savedSearchesMounted.current) {
          setSavedSearchesError(err instanceof ApiError ? err.message : "Could not load saved searches.");
        }
      });
    return () => {
      savedSearchesMounted.current = false;
    };
  }, [savedSearchesReloadToken]);

  // Sources drive both the top-level "Sources" checklist and the Country
  // dropdown's options (every distinct country any source declares
  // coverage for) -- fetched once on mount. All sources start selected
  // (unscoped == every source), matching "no source_scope" search semantics.
  useEffect(() => {
    sourcesMounted.current = true;
    listSources()
      .then((data) => {
        if (sourcesMounted.current) {
          setSources(data);
          setSelectedSourceIds(new Set(data.map((s) => s.id)));
        }
      })
      .catch(() => {
        /* Source list is a convenience filter, not required for search to work -- fail silent. */
      });
    return () => {
      sourcesMounted.current = false;
    };
  }, []);

  const availableCountries = Array.from(new Set(sources.flatMap((s) => s.countries))).sort();

  function toggleSource(id: string) {
    setSelectedSourceIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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
    setActiveSavedSearchId(null);
    setLoading(true);
    setError(null);
    try {
      const filter = buildFilterNode(filledRows, combineMode);
      const response = await searchCompaniesAdvanced({
        filter,
        unknown_handling: unknownHandling,
        country_scope: selectedCountry ? [selectedCountry] : undefined,
        source_scope:
          selectedSourceIds.size > 0 && selectedSourceIds.size < sources.length
            ? Array.from(selectedSourceIds)
            : undefined,
        limit: 50,
      });
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

  // Deliberately not a <form onSubmit> -- this dialog's content is a React
  // (though not DOM) descendant of the page's outer <form onSubmit={runSearch}>
  // (Dialog content renders into a portal, but React re-simulates event
  // bubbling along the React tree, not the DOM tree, for portaled content).
  // A nested <form>'s submit would therefore also fire the outer form's
  // onSubmit. Plain onClick avoids the whole nested-form ambiguity.
  async function saveCurrentSearch() {
    if (filledRows.length === 0 || !saveName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const filter = buildFilterNode(filledRows, combineMode);
      await createSavedSearch({ name: saveName.trim(), created_by: CREATED_BY, filter_definition: filter });
      setSaveDialogOpen(false);
      setSaveName("");
      setSavedSearchesReloadToken((t) => t + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save this search.");
    } finally {
      setSaving(false);
    }
  }

  async function runSavedSearch(saved: SavedSearchOut) {
    setActiveSavedSearchId(saved.id);
    setLoading(true);
    setError(null);
    try {
      const response = await executeSavedSearch(saved.id, { unknown_handling: unknownHandling });
      setResults(response.results);
      setUnknownResults(response.unknown_results);
      setTotalReturned(response.total_returned);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not run this saved search.");
      setResults(null);
      setUnknownResults([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteSavedSearch(id: string) {
    try {
      await deleteSavedSearch(id);
      if (activeSavedSearchId === id) setActiveSavedSearchId(null);
      setSavedSearchesReloadToken((t) => t + 1);
    } catch (err) {
      setSavedSearchesError(err instanceof ApiError ? err.message : "Could not delete this saved search.");
    }
  }

  function renderCompanyRow(company: CompanyOut, matchStrength: MatchStrength | null, sources: string[]) {
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
        <TableCell>
          <SourceBadges sources={sources} />
        </TableCell>
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
          <CardTitle className="text-base">Country &amp; sources</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-8">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="countrySelect">Country</Label>
              <select
                id="countrySelect"
                className="h-9 w-48 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                value={selectedCountry}
                onChange={(e) => setSelectedCountry(e.target.value)}
              >
                <option value="">All countries</option>
                {availableCountries.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Sources</Label>
              {sources.length === 0 ? (
                <p className="text-sm text-muted-foreground">No sources registered yet.</p>
              ) : (
                <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                  {sources.map((s) => {
                    const isDemo = isDemoSourceName(s.display_name);
                    return (
                      <label key={s.id} className="flex items-center gap-1.5 text-sm">
                        <input
                          type="checkbox"
                          checked={selectedSourceIds.has(s.id)}
                          onChange={() => toggleSource(s.id)}
                          className="size-4 rounded border-input"
                        />
                        <span>{s.display_name ?? s.name}</span>
                        {isDemo && (
                          <Badge
                            variant="outline"
                            className="border-amber-400 text-amber-800 dark:border-amber-700 dark:text-amber-300"
                          >
                            DEMO
                          </Badge>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

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

              <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
                <DialogTrigger
                  render={
                    <Button type="button" variant="outline" className="ml-auto" disabled={filledRows.length === 0} />
                  }
                >
                  Save this search
                </DialogTrigger>
                <DialogContent>
                  <div className="flex flex-col gap-4">
                    <DialogHeader>
                      <DialogTitle>Save this search</DialogTitle>
                    </DialogHeader>
                    <div className="flex flex-col gap-1.5">
                      <Label htmlFor="saveName">Name</Label>
                      <Input
                        id="saveName"
                        placeholder="e.g. My Maharashtra manufacturers"
                        value={saveName}
                        onChange={(e) => setSaveName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            saveCurrentSearch();
                          }
                        }}
                        autoFocus
                      />
                    </div>
                    <DialogFooter>
                      <Button type="button" disabled={saving || !saveName.trim()} onClick={saveCurrentSearch}>
                        {saving ? "Saving…" : "Save"}
                      </Button>
                    </DialogFooter>
                  </div>
                </DialogContent>
              </Dialog>

              <Button type="submit" disabled={loading}>
                {loading ? "Searching…" : "Search"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Saved searches ({savedSearches.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {savedSearchesError && (
            <Alert variant="destructive" className="mb-3">
              <AlertDescription>{savedSearchesError}</AlertDescription>
            </Alert>
          )}
          {savedSearches.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No saved searches yet -- build a filter above and click &quot;Save this search&quot;.
            </p>
          ) : (
            <ul className="flex flex-col divide-y divide-border">
              {savedSearches.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{s.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {s.created_by} · {formatDate(s.created_at)}
                      {s.country_scope.length > 0 ? ` · ${s.country_scope.join(", ")}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant={activeSavedSearchId === s.id ? "secondary" : "outline"}
                      disabled={loading}
                      onClick={() => runSavedSearch(s)}
                    >
                      Run
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => handleDeleteSavedSearch(s.id)}>
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
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
                <TableBody>{results.map((r) => renderCompanyRow(r.company, r.match_strength, r.sources))}</TableBody>
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
              <TableBody>{unknownResults.map((c) => renderCompanyRow(c, "unknown", []))}</TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
