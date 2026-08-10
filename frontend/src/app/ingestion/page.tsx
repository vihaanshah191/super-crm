import { listIngestionJobs, listSourceHealth } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const STATUS_STYLES: Record<string, string> = {
  success: "bg-green-100 text-green-800 hover:bg-green-100 dark:bg-green-950 dark:text-green-300",
  running: "bg-blue-100 text-blue-800 hover:bg-blue-100 dark:bg-blue-950 dark:text-blue-300",
  pending: "bg-slate-100 text-slate-800 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300",
  partial: "bg-amber-100 text-amber-800 hover:bg-amber-100 dark:bg-amber-950 dark:text-amber-300",
  failed: "bg-red-100 text-red-800 hover:bg-red-100 dark:bg-red-950 dark:text-red-300",
};

export default async function IngestionStatusPage() {
  const [sourceHealth, jobs] = await Promise.all([listSourceHealth(), listIngestionJobs()]);
  const sourceById = new Map(sourceHealth.map((h) => [h.source.id, h.source]));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ingestion Status</h1>
        <p className="text-sm text-muted-foreground">
          Every source is gated by <code className="text-xs">collection_enabled</code> before it can be fetched --
          see docs/compliance.md. Last run / last error / records collected are derived from ingestion job
          history, not separately stored -- a source that has never run shows &quot;Never run&quot;, not a
          fabricated status.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sources ({sourceHealth.length})</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Collection enabled</TableHead>
                <TableHead>Last successful run</TableHead>
                <TableHead>Last error</TableHead>
                <TableHead>Records collected</TableHead>
                <TableHead>Rate limit / min</TableHead>
                <TableHead>Reliability</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sourceHealth.map((h) => (
                <TableRow key={h.source.id}>
                  <TableCell className="font-mono text-xs">{h.source.name}</TableCell>
                  <TableCell>{h.source.source_type}</TableCell>
                  <TableCell>
                    {h.source.collection_enabled ? (
                      <Badge variant="secondary">Enabled</Badge>
                    ) : (
                      <Badge variant="outline">Disabled</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {h.last_successful_run ? formatDate(h.last_successful_run) : "Never run"}
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                    {h.last_error ?? "—"}
                  </TableCell>
                  <TableCell>{h.records_collected_total}</TableCell>
                  <TableCell>{h.source.rate_limit_per_minute}</TableCell>
                  <TableCell>{h.source.reliability_weight}/100</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent jobs ({jobs.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No ingestion jobs recorded yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Finished</TableHead>
                  <TableHead>Discovered</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead>Failed</TableHead>
                  <TableHead>Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobs.map((j) => (
                  <TableRow key={j.id}>
                    <TableCell className="font-mono text-xs">{sourceById.get(j.source_id)?.name ?? j.source_id}</TableCell>
                    <TableCell>
                      <Badge className={STATUS_STYLES[j.status] ?? ""} variant="secondary">
                        {j.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">{formatDate(j.started_at)}</TableCell>
                    <TableCell className="text-xs">{j.finished_at ? formatDate(j.finished_at) : "—"}</TableCell>
                    <TableCell>{j.records_discovered}</TableCell>
                    <TableCell>{j.records_updated}</TableCell>
                    <TableCell>{j.records_failed}</TableCell>
                    <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                      {j.error_summary ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
