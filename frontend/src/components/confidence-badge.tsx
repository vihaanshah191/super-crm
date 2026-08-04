import { Badge } from "@/components/ui/badge";
import { confidenceLabel } from "@/lib/format";

const STYLES: Record<ReturnType<typeof confidenceLabel>, string> = {
  high: "bg-green-100 text-green-800 hover:bg-green-100 dark:bg-green-950 dark:text-green-300",
  medium: "bg-amber-100 text-amber-800 hover:bg-amber-100 dark:bg-amber-950 dark:text-amber-300",
  low: "bg-red-100 text-red-800 hover:bg-red-100 dark:bg-red-950 dark:text-red-300",
};

export function ConfidenceBadge({ confidence }: { confidence: number }) {
  const label = confidenceLabel(confidence);
  return (
    <Badge className={STYLES[label]} variant="secondary">
      {(confidence * 100).toFixed(0)}% confidence
    </Badge>
  );
}

export function VerificationBadge({ verificationType }: { verificationType: string }) {
  const styles: Record<string, string> = {
    verified: "bg-blue-100 text-blue-800 hover:bg-blue-100 dark:bg-blue-950 dark:text-blue-300",
    observed: "bg-slate-100 text-slate-800 hover:bg-slate-100 dark:bg-slate-800 dark:text-slate-300",
    estimated: "bg-amber-100 text-amber-800 hover:bg-amber-100 dark:bg-amber-950 dark:text-amber-300",
    unknown: "bg-gray-100 text-gray-600 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400",
  };
  return (
    <Badge className={styles[verificationType] ?? styles.unknown} variant="secondary">
      {verificationType}
    </Badge>
  );
}
