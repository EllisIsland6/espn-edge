import type { SyncSummary } from "../api";

// Turn a SyncSummary into a user-facing warning, or null when the sync was clean.
// The backend often returns failures/partial failures with HTTP 200 (in
// needs_reauth / errors), so callers must inspect the summary, not just the status.
// Never surfaces secrets — the backend already redacts cookies/SWID/espn_s2.
export function syncSummaryMessage(summary: SyncSummary): string | null {
  const league = summary.name ?? `League ${summary.league_id}`;
  if (summary.needs_reauth) {
    return `${league}: account needs re-auth — refresh its ESPN cookies on Manage.`;
  }
  if (summary.errors && summary.errors.length > 0) {
    return `${league}: sync completed with issues — ${summary.errors.join("; ")}`;
  }
  return null;
}
