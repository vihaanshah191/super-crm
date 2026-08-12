import { notFound } from "next/navigation";
import { ApiError, getCompany, getCompanyFinancials, getCompanyGstRegistrations } from "@/lib/api";
import { formatDate, formatEmployeeRange, formatInr } from "@/lib/format";
import { ConfidenceBadge, VerificationBadge } from "@/components/confidence-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default async function CompanyProfilePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let company;
  try {
    company = await getCompany(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [financials, gstRegistrations] = await Promise.all([
    getCompanyFinancials(id),
    getCompanyGstRegistrations(id),
  ]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{company.canonical_name}</h1>
          <p className="text-sm text-muted-foreground">{company.legal_name}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {company.company_category && <Badge variant="secondary">{company.company_category}</Badge>}
            {company.industry && <Badge variant="outline">{company.industry}</Badge>}
            {company.export_status && <Badge variant="outline">Exporter</Badge>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <ConfidenceBadge confidence={company.confidence} />
          <span className="text-xs text-muted-foreground">
            {company.source_count} source{company.source_count === 1 ? "" : "s"} · last verified{" "}
            {formatDate(company.last_verified_at)}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryStat label="Location" value={[company.city, company.state].filter(Boolean).join(", ") || "Unknown"} />
        <SummaryStat
          label="Employees"
          value={formatEmployeeRange(company.employee_count, company.employee_range_min, company.employee_range_max)}
        />
        <SummaryStat label="Revenue (latest)" value={formatInr(company.annual_revenue_inr)} />
        <SummaryStat label="Incorporated" value={formatDate(company.incorporation_date)} />
      </div>

      <ContactCard phone={company.public_phone} email={company.public_email} website={company.website} />

      <Tabs defaultValue="evidence">
        <TabsList>
          <TabsTrigger value="evidence">Evidence &amp; Provenance</TabsTrigger>
          <TabsTrigger value="financials">Financial History</TabsTrigger>
          <TabsTrigger value="gst">GST Registrations</TabsTrigger>
        </TabsList>

        <TabsContent value="evidence">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Per-field evidence ({company.evidence.length} field{company.evidence.length === 1 ? "" : "s"})
              </CardTitle>
            </CardHeader>
            <CardContent>
              {company.evidence.length === 0 ? (
                <p className="text-sm text-muted-foreground">No evidence recorded for this company yet.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Field</TableHead>
                      <TableHead>Value</TableHead>
                      <TableHead>Verification</TableHead>
                      <TableHead>Confidence</TableHead>
                      <TableHead>Computed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {company.evidence.map((e) => (
                      <TableRow key={e.field}>
                        <TableCell className="font-mono text-xs">{e.field}</TableCell>
                        <TableCell className="max-w-xs truncate">{e.value ?? "—"}</TableCell>
                        <TableCell>
                          <VerificationBadge verificationType={e.verification_type} />
                        </TableCell>
                        <TableCell>{(e.confidence * 100).toFixed(0)}%</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{formatDate(e.computed_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="financials">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Financial year history</CardTitle>
            </CardHeader>
            <CardContent>
              {financials.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No financial-year evidence recorded. This is distinct from statutory authorized/paid-up capital,
                  which never populates revenue fields.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Financial year</TableHead>
                      <TableHead>Revenue</TableHead>
                      <TableHead>Authorized capital</TableHead>
                      <TableHead>Paid-up capital</TableHead>
                      <TableHead>Verification</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {financials.map((f) => (
                      <TableRow key={f.id}>
                        <TableCell className="font-medium">{f.financial_year}</TableCell>
                        <TableCell>{formatInr(f.annual_revenue_inr)}</TableCell>
                        <TableCell>{formatInr(f.authorized_capital_inr)}</TableCell>
                        <TableCell>{formatInr(f.paidup_capital_inr)}</TableCell>
                        <TableCell>
                          <VerificationBadge verificationType={f.verification_type} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="gst">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">GST registrations</CardTitle>
            </CardHeader>
            <CardContent>
              {gstRegistrations.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No GST registrations on record. A company may hold more than one -- see docs/ingestion.md.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>GSTIN</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>Registered</TableHead>
                      <TableHead>Cancelled</TableHead>
                      <TableHead>Primary</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {gstRegistrations.map((g) => (
                      <TableRow key={g.id}>
                        <TableCell className="font-mono text-xs">{g.gstin}</TableCell>
                        <TableCell>{g.registered_state ?? "Unknown"}</TableCell>
                        <TableCell>{formatDate(g.registration_date)}</TableCell>
                        <TableCell>{g.cancellation_date ? formatDate(g.cancellation_date) : "—"}</TableCell>
                        <TableCell>{g.is_primary ? <Badge variant="secondary">Primary</Badge> : null}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Separator />
      <p className="text-xs text-muted-foreground">
        Identity: CIN {company.cin ?? "unknown"} · GSTIN (primary) {company.gstin ?? "unknown"}
      </p>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="py-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold">{value}</p>
      </CardContent>
    </Card>
  );
}

// Surfaces contact fields already captured on Company (public_phone/
// public_email/website -- populated today by WebsiteAdapter) but never
// previously shown anywhere in the frontend. No new backend field or
// source: this only makes existing, already-collected data reachable.
function ContactCard({ phone, email, website }: { phone: string | null; email: string | null; website: string | null }) {
  const websiteHref = website ? (website.startsWith("http://") || website.startsWith("https://") ? website : `https://${website}`) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Contact</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <ContactField label="Phone" value={phone} href={phone ? `tel:${phone}` : null} />
          <ContactField label="Email" value={email} href={email ? `mailto:${email}` : null} />
          <ContactField label="Website" value={website} href={websiteHref} />
        </div>
      </CardContent>
    </Card>
  );
}

function ContactField({ label, value, href }: { label: string; value: string | null; href: string | null }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      {value && href ? (
        <a href={href} className="text-sm font-medium text-primary hover:underline" target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noopener noreferrer" : undefined}>
          {value}
        </a>
      ) : (
        <p className="text-sm text-muted-foreground">Not available</p>
      )}
    </div>
  );
}
