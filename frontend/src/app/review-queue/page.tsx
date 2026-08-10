"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiError, confirmMatch, listReviewQueue, rejectMatch } from "@/lib/api";
import type { EntityMatchCandidateDetailOut } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const REVIEWER = "frontend-operator"; // placeholder until auth/identity is wired in

export default function ReviewQueuePage() {
  const [candidates, setCandidates] = useState<EntityMatchCandidateDetailOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const mounted = useRef(true);

  // Fetches on mount and whenever reloadToken changes (bumped after a
  // confirm/reject action) -- the effect owns the fetch-on-mount/refresh
  // lifecycle directly rather than calling an externally-defined async
  // function, per react-hooks/set-state-in-effect.
  useEffect(() => {
    mounted.current = true;
    listReviewQueue()
      .then((data) => {
        if (mounted.current) setCandidates(data);
      })
      .catch((err) => {
        if (mounted.current) {
          setError(err instanceof ApiError ? err.message : "Could not reach the API. Is the backend running?");
        }
      });
    return () => {
      mounted.current = false;
    };
  }, [reloadToken]);

  async function handleConfirm(id: string) {
    setActingOn(id);
    try {
      await confirmMatch(id, REVIEWER);
      setReloadToken((t) => t + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Confirm failed.");
    } finally {
      setActingOn(null);
    }
  }

  async function handleReject(id: string) {
    setActingOn(id);
    try {
      await rejectMatch(id, REVIEWER);
      setReloadToken((t) => t + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reject failed.");
    } finally {
      setActingOn(null);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Entity-Resolution Review Queue</h1>
        <p className="text-sm text-muted-foreground">
          These matches scored above the &quot;possible match&quot; threshold but below auto-match -- see
          docs/entity_resolution.md. Nothing here was merged automatically.
        </p>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {candidates === null && !error && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {candidates !== null && candidates.length === 0 && (
        <p className="text-sm text-muted-foreground">No pending matches to review.</p>
      )}

      <div className="flex flex-col gap-4">
        {candidates?.map((c) => (
          <Card key={c.id}>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="text-base">
                  Incoming: {String(c.incoming_payload.canonical_name ?? "(no name)")}
                </CardTitle>
                <Badge variant="secondary">score {c.match_score.toFixed(2)}</Badge>
              </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Incoming payload</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
                    {JSON.stringify(c.incoming_payload, null, 2)}
                  </pre>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground">Matched signals</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
                    {JSON.stringify(c.matched_signals, null, 2)}
                  </pre>
                </div>
              </div>

              {c.candidate_company && (
                <div className="rounded-md border p-3">
                  <p className="text-xs font-medium text-muted-foreground">Candidate existing company</p>
                  <Link href={`/companies/${c.candidate_company.id}`} className="font-medium hover:underline">
                    {c.candidate_company.canonical_name}
                  </Link>
                  <p className="text-xs text-muted-foreground">
                    {[c.candidate_company.city, c.candidate_company.state].filter(Boolean).join(", ")} · CIN{" "}
                    {c.candidate_company.cin ?? "unknown"}
                  </p>
                </div>
              )}

              <div className="flex gap-2">
                <Button size="sm" disabled={actingOn === c.id} onClick={() => handleConfirm(c.id)}>
                  Confirm match
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={actingOn === c.id}
                  onClick={() => handleReject(c.id)}
                >
                  Reject
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
