// Identifies the synthetic client-demo dataset (app/cli/seed_demo.py) so the
// UI can visibly label it as demonstration data rather than letting it look
// indistinguishable from a real, verified source. Matches on the exact
// display_name that CLI sets -- see DEMO_SOURCE_DISPLAY_NAME in
// app/cli/seed_demo.py.

export const DEMO_SOURCE_DISPLAY_NAME = "Super CRM Demo Dataset";

export function isDemoSourceName(name: string | null | undefined): boolean {
  return name === DEMO_SOURCE_DISPLAY_NAME;
}

export function hasDemoSource(sourceNames: string[]): boolean {
  return sourceNames.some(isDemoSourceName);
}
