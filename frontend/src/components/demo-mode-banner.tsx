import { listSourceHealth } from "@/lib/api";
import { DEMO_SOURCE_DISPLAY_NAME } from "@/lib/demo";

// Global "DEMO MODE" indicator -- rendered once in the root layout so it's
// visible on every page, not just Discover. Never hides that the demo
// dataset is synthetic: shows the demo source's own name and how many
// companies it produced (derived from real IngestionJob history, same
// projection the Ingestion Status page uses -- not a separate fabricated
// count). Renders nothing if the demo dataset hasn't been seeded, and fails
// silent (not a page-breaking error) if the API is unreachable, since this
// banner must never be the reason the rest of the app fails to render.
export async function DemoModeBanner() {
  let demoHealth;
  try {
    const health = await listSourceHealth();
    demoHealth = health.find((h) => h.source.display_name === DEMO_SOURCE_DISPLAY_NAME);
  } catch {
    return null;
  }
  if (!demoHealth) return null;

  return (
    <div className="border-b border-amber-300 bg-amber-50 px-6 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded bg-amber-200 px-1.5 py-0.5 text-xs font-bold uppercase tracking-wide text-amber-900 dark:bg-amber-800 dark:text-amber-100">
          Demo Mode
        </span>
        <span>
          {demoHealth.records_collected_total} companies from a synthetic demonstration dataset (
          <span className="font-medium">{DEMO_SOURCE_DISPLAY_NAME}</span>) -- not real, verified company data.
        </span>
      </div>
    </div>
  );
}
