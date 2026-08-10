"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ApiError, searchCompanies } from "@/lib/api";
import type { CompanySearchFilters, CompanySearchResultOut } from "@/lib/types";
import { formatEmployeeRange, formatInr } from "@/lib/format";
import { ConfidenceBadge } from "@/components/confidence-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

const CATEGORY_OPTIONS = ["", "manufacturer", "distributor", "service_provider", "retailer"];

export default function DiscoverPage() {
  const [form, setForm] = useState({
    industry: "",
    city: "",
    state: "",
    companyCategory: "",
    employeeMin: "",
    revenueMinInr: "",
    minConfidence: "",
  });
  const [results, setResults] = useState<CompanySearchResultOut[] | null>(null);
  const [totalReturned, setTotalReturned] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runSearch(e?: FormEvent) {
    e?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const filters: CompanySearchFilters = {
        industry: form.industry || undefined,
        city: form.city || undefined,
        state: form.state || undefined,
        company_category: form.companyCategory || undefined,
        employee_min: form.employeeMin ? Number(form.employeeMin) : undefined,
        revenue_min_inr: form.revenueMinInr ? Number(form.revenueMinInr) : undefined,
        min_confidence: form.minConfidence ? Number(form.minConfidence) : undefined,
        limit: 50,
      };
      const response = await searchCompanies(filters);
      setResults(response.results);
      setTotalReturned(response.total_returned);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the API. Is the backend running?");
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="text-sm text-muted-foreground">
          Structured filters execute deterministically against indexed company fields -- no free-text matching.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={runSearch} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="industry">Industry</Label>
              <Input
                id="industry"
                placeholder="e.g. Chemical"
                value={form.industry}
                onChange={(e) => setForm({ ...form, industry: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="state">State</Label>
              <Input
                id="state"
                placeholder="e.g. Maharashtra"
                value={form.state}
                onChange={(e) => setForm({ ...form, state: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="city">City</Label>
              <Input
                id="city"
                placeholder="e.g. Pune"
                value={form.city}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="category">Company category</Label>
              <select
                id="category"
                className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                value={form.companyCategory}
                onChange={(e) => setForm({ ...form, companyCategory: e.target.value })}
              >
                {CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt === "" ? "Any" : opt.replace("_", " ")}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="employeeMin">Min employees</Label>
              <Input
                id="employeeMin"
                type="number"
                min={0}
                placeholder="e.g. 20"
                value={form.employeeMin}
                onChange={(e) => setForm({ ...form, employeeMin: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="revenueMin">Min revenue (INR)</Label>
              <Input
                id="revenueMin"
                type="number"
                min={0}
                placeholder="e.g. 100000000 (10 Cr)"
                value={form.revenueMinInr}
                onChange={(e) => setForm({ ...form, revenueMinInr: e.target.value })}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="minConfidence">Min confidence</Label>
              <Input
                id="minConfidence"
                type="number"
                min={0}
                max={1}
                step={0.1}
                placeholder="0.0 - 1.0"
                value={form.minConfidence}
                onChange={(e) => setForm({ ...form, minConfidence: e.target.value })}
              />
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={loading} className="w-full">
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
                    <TableHead>Match</TableHead>
                    <TableHead>Confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="font-medium">
                        <Link href={`/companies/${c.id}`} className="hover:underline">
                          {c.canonical_name}
                        </Link>
                      </TableCell>
                      <TableCell>{[c.city, c.state].filter(Boolean).join(", ") || "Unknown"}</TableCell>
                      <TableCell>{c.industry ?? "Unknown"}</TableCell>
                      <TableCell>{formatEmployeeRange(c.employee_count, c.employee_range_min, c.employee_range_max)}</TableCell>
                      <TableCell>{formatInr(c.annual_revenue_inr)}</TableCell>
                      <TableCell>
                        {c.match_is_definite === null ? (
                          <span className="text-xs text-muted-foreground">n/a</span>
                        ) : c.match_is_definite ? (
                          <Badge variant="secondary">Definite</Badge>
                        ) : (
                          <Badge variant="outline">Possible</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <ConfidenceBadge confidence={c.confidence} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
