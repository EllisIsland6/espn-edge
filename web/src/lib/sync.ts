import type { SyncSummary } from "../api";

export type SyncOutcome =
  | "success"
  | "issues"
  | "team_missing"
  | "needs_reauth"
  | "failed"
  | "skipped";

export interface SyncResult {
  status: SyncOutcome;
  message: string;
}

export function classifySyncSummary(
  summary: SyncSummary,
  { accountLinked }: { accountLinked: boolean },
): SyncResult {
  if (summary.needs_reauth) {
    return {
      status: "needs_reauth",
      message: "Account needs re-authentication before syncing can continue.",
    };
  }
  if (summary.errors.length > 0) {
    return {
      status: "issues",
      message: `Completed with issues: ${summary.errors.join("; ")}`,
    };
  }
  if (accountLinked && summary.my_team_espn_id == null) {
    return {
      status: "team_missing",
      message: "Synced, but this account's team was not identified.",
    };
  }
  return { status: "success", message: "Synced successfully." };
}

export function syncFailureMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const concise = message.replace(/\s+/g, " ").trim();
  return concise.length > 240 ? `${concise.slice(0, 237)}...` : concise;
}

// Turn a SyncSummary into a user-facing warning, or null when the sync was clean.
// The backend often returns failures/partial failures with HTTP 200 (in
// needs_reauth / errors), so callers must inspect the summary, not just the status.
// Never surfaces secrets — the backend already redacts cookies/SWID/espn_s2.
export function syncSummaryMessage(
  summary: SyncSummary,
  options: { accountLinked?: boolean } = {},
): string | null {
  const league = summary.name ?? `League ${summary.league_id}`;
  const result = classifySyncSummary(summary, {
    accountLinked: options.accountLinked ?? false,
  });
  return result.status === "success" ? null : `${league}: ${result.message}`;
}
