import { Badge } from "@/components/ui/badge";
import { isDemoSourceName } from "@/lib/demo";

// Renders the real Source name(s) behind a company/evidence row -- never
// just a bare count -- with the synthetic demo source visibly called out
// so it's never mistaken for a verified production source.
export function SourceBadges({ sources }: { sources: string[] }) {
  if (sources.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {sources.map((name) =>
        isDemoSourceName(name) ? (
          <Badge
            key={name}
            variant="outline"
            className="border-amber-400 text-amber-800 dark:border-amber-700 dark:text-amber-300"
            title={name}
          >
            DEMO
          </Badge>
        ) : (
          <Badge key={name} variant="secondary">
            {name}
          </Badge>
        )
      )}
    </div>
  );
}
