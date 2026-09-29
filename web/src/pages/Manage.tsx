import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  addAccount,
  addLeague,
  deleteAccount,
  discoverLeagues,
  getAccounts,
  getLeagues,
  reauthAccount,
  syncLeague,
  triggerRecoveryBackup,
  type AccountOut,
  type DiscoveredLeague,
  type LeagueOut,
} from "../api";
import { AchievementRow } from "../components/Gamification";
import { TeamIdentity } from "../components/TeamIdentity";
import { Button, EmptyState, ErrorNote, LifecycleBadge, Panel, Spinner } from "../components/ui";
import { LIFECYCLE_LABEL, relTime } from "../lib/format";
import {
  classifySyncSummary,
  syncFailureMessage,
  syncSummaryMessage,
  type SyncOutcome,
} from "../lib/sync";

type SyncOperation =
  | { kind: "individual"; leagueId: number }
  | { kind: "import"; accountId: number }
  | { kind: "bulk" };

interface ImportResult {
  key: string;
  league: string;
  status: SyncOutcome;
  message: string;
}

interface LeagueSyncResult {
  leagueId: number;
  league: string;
  status: SyncOutcome;
  message: string;
}

const SYNC_STATUS_LABEL: Record<SyncOutcome, string> = {
  success: "Success",
  issues: "Completed with issues",
  team_missing: "Team not identified",
  needs_reauth: "Needs re-auth",
  failed: "Failed",
  skipped: "Skipped",
};

const SYNC_STATUS_TONE: Record<SyncOutcome, string> = {
  success: "text-green",
  issues: "text-gold",
  team_missing: "text-gold",
  needs_reauth: "text-red",
  failed: "text-red",
  skipped: "text-muted",
};

export default function Manage() {
  const [accounts, setAccounts] = useState<AccountOut[] | null>(null);
  const [leagues, setLeagues] = useState<LeagueOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncOperation, setSyncOperation] = useState<SyncOperation | null>(null);
  const syncOperationRef = useRef<SyncOperation | null>(null);

  const loadData = useCallback(async () => {
    const [nextAccounts, nextLeagues] = await Promise.all([getAccounts(), getLeagues()]);
    setAccounts(nextAccounts);
    setLeagues(nextLeagues);
  }, []);

  const reload = useCallback(async () => {
    setError(null);
    try {
      await loadData();
    } catch (e) {
      setError(String(e));
    }
  }, [loadData]);

  const reloadAfterBulk = useCallback(async (): Promise<string | null> => {
    try {
      await loadData();
      setError(null);
      return null;
    } catch (e) {
      return syncFailureMessage(e);
    }
  }, [loadData]);

  const beginSyncOperation = useCallback((operation: SyncOperation): boolean => {
    if (syncOperationRef.current !== null) return false;
    syncOperationRef.current = operation;
    setSyncOperation(operation);
    return true;
  }, []);

  const endSyncOperation = useCallback(() => {
    syncOperationRef.current = null;
    setSyncOperation(null);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-semibold">Manage accounts & leagues</h1>
      <p className="mt-1 text-sm text-secondary">
        Cookies are stored encrypted and never leave your machine. All ESPN traffic goes
        through the backend.
      </p>
      {error && <div className="mt-4"><ErrorNote message={error} /></div>}

      <AddAccountForm onDone={reload} onError={setError} />
      {!accounts ? (
        <Spinner />
      ) : (
        <AccountList
          accounts={accounts}
          leagues={leagues ?? []}
          onChange={reload}
          onError={setError}
          syncOperation={syncOperation}
          beginSyncOperation={beginSyncOperation}
          endSyncOperation={endSyncOperation}
        />
      )}

      <AddLeagueForm accounts={accounts ?? []} onDone={reload} onError={setError} />
      {!leagues ? (
        <Spinner />
      ) : (
        <LeagueList
          leagues={leagues}
          accounts={accounts ?? []}
          onChange={reload}
          onBulkReload={reloadAfterBulk}
          onError={setError}
          syncOperation={syncOperation}
          beginSyncOperation={beginSyncOperation}
          endSyncOperation={endSyncOperation}
        />
      )}
    </div>
  );
}

function AddAccountForm({ onDone, onError }: { onDone: () => void; onError: (e: string) => void }) {
  const [label, setLabel] = useState("");
  const [swid, setSwid] = useState("");
  const [s2, setS2] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await addAccount(label, swid, s2);
      setLabel("");
      setSwid("");
      setS2("");
      onDone();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel className="mt-5 p-4">
      <h2 className="text-sm font-semibold">Add ESPN account</h2>
      <p className="mt-1 text-xs text-muted">
        In Chrome at fantasy.espn.com → DevTools → Application → Cookies: copy{" "}
        <code className="mono">SWID</code> (keep the braces) and <code className="mono">espn_s2</code>.
      </p>
      <form onSubmit={submit} className="mt-3 grid gap-2 sm:grid-cols-[160px_1fr_1fr_auto]">
        <Field value={label} onChange={setLabel} placeholder="Label (e.g. Main)" />
        <Field value={swid} onChange={setSwid} placeholder="SWID {…}" />
        <Field value={s2} onChange={setS2} placeholder="espn_s2" type="password" />
        <Button type="submit" variant="sync" disabled={busy || !label || !swid || !s2}>
          {busy ? "Adding…" : "Add"}
        </Button>
      </form>
    </Panel>
  );
}

function AccountList({
  accounts,
  leagues,
  onChange,
  onError,
  syncOperation,
  beginSyncOperation,
  endSyncOperation,
}: {
  accounts: AccountOut[];
  leagues: LeagueOut[];
  onChange: () => void;
  onError: (e: string) => void;
  syncOperation: SyncOperation | null;
  beginSyncOperation: (operation: SyncOperation) => boolean;
  endSyncOperation: () => void;
}) {
  if (accounts.length === 0)
    return (
      <div className="mt-3">
        <EmptyState
          title="No accounts yet"
          hint={
            <>
              Add one above using the <b>SWID</b> and <b>espn_s2</b> cookies from a
              logged-in fantasy.espn.com session (DevTools → Application → Cookies).
              Public leagues need no account.
            </>
          }
        />
      </div>
    );
  return (
    <div className="mt-3 space-y-2">
      {accounts.map((a) => (
        <AccountRow
          key={a.id}
          account={a}
          leagues={leagues}
          onChange={onChange}
          onError={onError}
          syncOperation={syncOperation}
          beginSyncOperation={beginSyncOperation}
          endSyncOperation={endSyncOperation}
        />
      ))}
    </div>
  );
}

function AccountRow({
  account: a,
  leagues,
  onChange,
  onError,
  syncOperation,
  beginSyncOperation,
  endSyncOperation,
}: {
  account: AccountOut;
  leagues: LeagueOut[];
  onChange: () => void;
  onError: (e: string) => void;
  syncOperation: SyncOperation | null;
  beginSyncOperation: (operation: SyncOperation) => boolean;
  endSyncOperation: () => void;
}) {
  const needsReauth = a.status === "needs_reauth";
  // Auto-expand the re-auth form when the account's session expired.
  const [showReauth, setShowReauth] = useState(needsReauth);
  const [showDiscovery, setShowDiscovery] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [importing, setImporting] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredLeague[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [discoveryNote, setDiscoveryNote] = useState<string | null>(null);
  const [importResults, setImportResults] = useState<ImportResult[]>([]);

  useEffect(() => {
    if (needsReauth) setShowReauth(true);
  }, [needsReauth]);

  const discoveryKey = (league: DiscoveredLeague) =>
    `${league.season ?? "current"}:${league.espn_league_id}`;
  const existingLeague = (league: DiscoveredLeague) =>
    leagues.find(
      (saved) =>
        saved.espn_league_id === league.espn_league_id &&
        (league.season == null || saved.season === league.season),
    );
  const isAlreadyAttached = (league: DiscoveredLeague) =>
    existingLeague(league)?.account_id === a.id;

  async function runDiscovery() {
    setDiscovering(true);
    setDiscoveryNote(null);
    setImportResults([]);
    try {
      const found = await discoverLeagues(a.id);
      const unique = Array.from(
        new Map(found.map((league) => [discoveryKey(league), league])).values(),
      );
      setDiscovered(unique);
      setSelected(
        new Set(unique.filter((league) => !isAlreadyAttached(league)).map(discoveryKey)),
      );
    } catch (err) {
      await onChange();
      onError(String(err));
    } finally {
      setDiscovering(false);
    }
  }

  async function toggleDiscovery() {
    if (showDiscovery) {
      setShowDiscovery(false);
      return;
    }
    setShowDiscovery(true);
    if (discovered === null) await runDiscovery();
  }

  async function importSelected() {
    if (!discovered) return;
    if (!beginSyncOperation({ kind: "import", accountId: a.id })) return;
    const choices = discovered.filter((league) => selected.has(discoveryKey(league)));
    setImporting(true);
    setDiscoveryNote(null);
    setImportResults([]);
    const results: ImportResult[] = [];
    const record = (result: ImportResult) => {
      results.push(result);
      setImportResults([...results]);
    };
    try {
      for (const league of choices) {
        const key = discoveryKey(league);
        const label = league.name ?? `League ${league.espn_league_id}`;
        let saved: LeagueOut;
        try {
          saved = await addLeague(league.espn_league_id, a.id, league.season ?? undefined);
        } catch (err) {
          record({
            key,
            league: label,
            status: "failed",
            message: `Import failed: ${syncFailureMessage(err)}`,
          });
          continue;
        }
        try {
          const summary = await syncLeague(saved.id);
          const result = classifySyncSummary(summary, { accountLinked: true });
          record({ key, league: label, ...result });
        } catch (err) {
          record({
            key,
            league: label,
            status: "failed",
            message: `Imported, but sync failed: ${syncFailureMessage(err)}`,
          });
        }
      }
      setSelected(new Set());
      setDiscoveryNote(`Processed ${results.length} ${results.length === 1 ? "league" : "leagues"}.`);
      await onChange();
    } finally {
      setImporting(false);
      endSyncOperation();
    }
  }

  const selectedCount = selected.size;
  return (
    <Panel className={`px-4 py-2 ${needsReauth ? "border-red/50" : ""}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{a.label}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-[11px] ${
            a.status === "active" ? "bg-greenchip text-green" : "bg-red/15 text-red"
          }`}
          data-testid={`account-status-${a.id}`}
        >
          {a.status}
        </span>
        <span className="mono ml-auto text-[11px] text-muted">added {relTime(a.created_at)}</span>
        <Button
          variant="discover"
          onClick={toggleDiscovery}
          disabled={needsReauth || discovering || importing}
          title={needsReauth ? "Re-authenticate this account before discovering leagues" : undefined}
        >
          {discovering ? "Discovering…" : showDiscovery ? "Hide leagues" : "Discover leagues"}
        </Button>
        <Button variant="sync" onClick={() => setShowReauth((v) => !v)}>
          {showReauth ? "Cancel" : "Re-auth"}
        </Button>
        <Button
          variant="danger"
          onClick={async () => {
            try {
              await deleteAccount(a.id);
              onChange();
            } catch (e) {
              onError(String(e));
            }
          }}
        >
          Delete
        </Button>
      </div>
      {needsReauth && (
        <p className="mt-2 text-xs text-red">
          This account’s ESPN session expired. Paste fresh cookies below to resume syncing.
        </p>
      )}
      {showReauth && (
        <ReauthForm
          accountId={a.id}
          onDone={() => {
            setShowReauth(false);
            onChange();
          }}
          onError={onError}
        />
      )}
      {showDiscovery && (
        <div className="mt-3 border-t border-line pt-3" data-testid={`league-discovery-${a.id}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-secondary">Leagues ESPN associates with this account</span>
            <Button
              variant="sync"
              onClick={runDiscovery}
              disabled={discovering || importing}
              title="Refresh league discovery"
            >
              Refresh
            </Button>
          </div>
          {discovered?.length === 0 && (
            <p className="mt-3 text-xs text-muted">
              ESPN returned no discoverable leagues for the current season. Discovery is best-effort;
              you can still add any league by ID or URL below.
            </p>
          )}
          {discovered && discovered.length > 0 && (
            <div className="mt-3 space-y-1">
              {discovered.map((league) => {
                const key = discoveryKey(league);
                const existing = existingLeague(league);
                const attached = existing?.account_id === a.id;
                return (
                  <label
                    key={key}
                    className={`flex min-h-11 items-center gap-3 rounded-md px-3 py-2 ${
                      attached ? "bg-row text-muted" : "bg-row hover:bg-rowhover"
                    }`}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select ${league.name ?? `league ${league.espn_league_id}`}`}
                      checked={selected.has(key)}
                      disabled={attached || importing}
                      onChange={(event) => {
                        setSelected((current) => {
                          const next = new Set(current);
                          if (event.target.checked) next.add(key);
                          else next.delete(key);
                          return next;
                        });
                      }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-primary">
                        {league.name ?? `League ${league.espn_league_id}`}
                      </span>
                      <span className="mono block text-[11px] text-muted">
                        ID {league.espn_league_id} · {league.season ?? "current season"}
                      </span>
                    </span>
                    <span className="mono text-[11px] uppercase text-muted">
                      {attached ? "Added" : existing ? "Relink" : "New"}
                    </span>
                  </label>
                );
              })}
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <Button
                  variant="sync"
                  onClick={importSelected}
                  disabled={syncOperation !== null || selectedCount === 0}
                >
                  {importing ? "Importing & syncing…" : `Import & sync ${selectedCount}`}
                </Button>
                <span className="text-xs text-muted">Selected leagues sync one at a time.</span>
              </div>
            </div>
          )}
          {discoveryNote && <p className="mt-3 text-xs text-secondary">{discoveryNote}</p>}
          {importResults.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid={`league-import-results-${a.id}`}>
              {importResults.map((result) => (
                <li key={result.key} className="flex flex-wrap items-baseline gap-x-2 text-xs">
                  <span className={`mono uppercase ${SYNC_STATUS_TONE[result.status]}`}>
                    {SYNC_STATUS_LABEL[result.status]}
                  </span>
                  <span className="font-medium text-primary">{result.league}</span>
                  <span className="text-muted">{result.message}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Panel>
  );
}

function ReauthForm({
  accountId,
  onDone,
  onError,
}: {
  accountId: number;
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [swid, setSwid] = useState("");
  const [s2, setS2] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await reauthAccount(accountId, swid, s2);
      // Clear the secrets from state (and the DOM) as soon as they're accepted.
      setSwid("");
      setS2("");
      onDone();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
      data-testid={`reauth-form-${accountId}`}
    >
      <Field value={swid} onChange={setSwid} placeholder="SWID {…}" />
      <Field value={s2} onChange={setS2} placeholder="espn_s2" type="password" />
      <Button type="submit" variant="sync" disabled={busy || !swid || !s2}>
        {busy ? "Saving…" : "Save cookies"}
      </Button>
    </form>
  );
}

function AddLeagueForm({
  accounts,
  onDone,
  onError,
}: {
  accounts: AccountOut[];
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [ref, setRef] = useState("");
  const [accountId, setAccountId] = useState<string>("");
  const [season, setSeason] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await addLeague(ref, accountId ? Number(accountId) : null, season ? Number(season) : undefined);
      setRef("");
      onDone();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel className="mt-6 p-4">
      <h2 className="text-sm font-semibold">Add league</h2>
      <p className="mt-1 text-xs text-muted">Paste a league ID or a league URL. Leave account empty for public leagues.</p>
      <form onSubmit={submit} className="mt-3 grid gap-2 sm:grid-cols-[1fr_180px_120px_auto]">
        <Field value={ref} onChange={setRef} placeholder="League ID or URL" />
        <select
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
          className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-primary"
        >
          <option value="">Public (no account)</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        <Field value={season} onChange={setSeason} placeholder="Season" />
        <Button type="submit" variant="sync" disabled={busy || !ref}>
          {busy ? "Adding…" : "Add"}
        </Button>
      </form>
    </Panel>
  );
}

function LeagueList({
  leagues,
  accounts,
  onChange,
  onBulkReload,
  onError,
  syncOperation,
  beginSyncOperation,
  endSyncOperation,
}: {
  leagues: LeagueOut[];
  accounts: AccountOut[];
  onChange: () => void;
  onBulkReload: () => Promise<string | null>;
  onError: (e: string) => void;
  syncOperation: SyncOperation | null;
  beginSyncOperation: (operation: SyncOperation) => boolean;
  endSyncOperation: () => void;
}) {
  const [bulkSyncingId, setBulkSyncingId] = useState<number | null>(null);
  const [bulkProgress, setBulkProgress] = useState({ current: 0, total: 0 });
  const [bulkResults, setBulkResults] = useState<LeagueSyncResult[]>([]);
  const [bulkReloadError, setBulkReloadError] = useState<string | null>(null);
  const acctLabel = (id: number | null) => accounts.find((a) => a.id === id)?.label ?? "public";
  const leagueLabel = (league: LeagueOut) => league.name ?? `League ${league.espn_league_id}`;

  async function syncOne(league: LeagueOut) {
    if (!beginSyncOperation({ kind: "individual", leagueId: league.id })) return;
    try {
      const summary = await syncLeague(league.id);
      const message = syncSummaryMessage(summary, { accountLinked: league.account_id != null });
      await onChange();
      if (message) onError(message);
    } catch (e) {
      onError(syncFailureMessage(e));
    } finally {
      endSyncOperation();
    }
  }

  async function syncAll() {
    if (!beginSyncOperation({ kind: "bulk" })) return;
    const queue = [...leagues];
    const blockedAccounts = new Set(
      accounts.filter((account) => account.status === "needs_reauth").map((account) => account.id),
    );
    const results: LeagueSyncResult[] = [];
    const record = (result: LeagueSyncResult) => {
      results.push(result);
      setBulkResults([...results]);
    };

    setBulkResults([]);
    setBulkReloadError(null);
    setBulkProgress({ current: 0, total: queue.length });

    try {
      for (const [index, league] of queue.entries()) {
        setBulkSyncingId(league.id);
        setBulkProgress({ current: index + 1, total: queue.length });
        const label = leagueLabel(league);
        if (league.account_id != null && blockedAccounts.has(league.account_id)) {
          record({
            leagueId: league.id,
            league: label,
            status: "skipped",
            message: "Skipped because this account needs re-authentication.",
          });
          continue;
        }

        try {
          const summary = await syncLeague(league.id);
          const result = classifySyncSummary(summary, {
            accountLinked: league.account_id != null,
          });
          record({ leagueId: league.id, league: label, ...result });
          if (result.status === "needs_reauth" && league.account_id != null) {
            blockedAccounts.add(league.account_id);
          }
        } catch (e) {
          record({
            leagueId: league.id,
            league: label,
            status: "failed",
            message: `Sync request failed: ${syncFailureMessage(e)}`,
          });
        }
      }
      if (
        queue.length > 0
        && results.length === queue.length
        && results.every((result) => result.status === "success")
      ) {
        try {
          await triggerRecoveryBackup("post-clean-portfolio-sync");
        } catch (e) {
          record({
            leagueId: 0,
            league: "Recovery point",
            status: "failed",
            message: `Sync data updated, but the recovery point failed: ${syncFailureMessage(e)}`,
          });
        }
      }
      setBulkReloadError(await onBulkReload());
    } finally {
      setBulkSyncingId(null);
      endSyncOperation();
    }
  }

  const isBulkSyncing = syncOperation?.kind === "bulk";
  const resultCounts = bulkResults.reduce<Partial<Record<SyncOutcome, number>>>((counts, result) => {
    counts[result.status] = (counts[result.status] ?? 0) + 1;
    return counts;
  }, {});
  const summaryParts = [
    `${resultCounts.success ?? 0} synced`,
    resultCounts.issues ? `${resultCounts.issues} with issues` : null,
    resultCounts.team_missing ? `${resultCounts.team_missing} team not identified` : null,
    resultCounts.needs_reauth ? `${resultCounts.needs_reauth} need re-auth` : null,
    resultCounts.failed ? `${resultCounts.failed} failed` : null,
    resultCounts.skipped ? `${resultCounts.skipped} skipped` : null,
  ].filter(Boolean);
  const bulkSummary =
    bulkResults.length === bulkProgress.total && bulkProgress.total > 0 && !isBulkSyncing
      ? `Processed ${bulkProgress.total} ${
          bulkProgress.total === 1 ? "league" : "leagues"
        }: ${summaryParts.join(", ")}.`
      : null;

  if (leagues.length === 0)
    return (
      <div className="mt-3">
        <EmptyState
          title="No leagues yet"
          hint={<>Add a league above by its ID or URL, pick the owning account (or Public), then Sync.</>}
        />
      </div>
    );
  return (
    <div className="mt-3 space-y-2">
      {leagues.map((l) => (
        <Panel key={l.id} className="flex min-h-20 items-center gap-3 px-4 py-2">
          <Link to={`/league/${l.id}`} className="min-w-0 flex-1 hover:text-red">
            <TeamIdentity
              team={{
                name: l.my_team_name,
                logo_url: l.my_team_logo_url,
                is_me: l.my_team_id != null,
              }}
              size="sm"
              fallback="Team not detected"
            />
            <span className="mt-1 block truncate pl-11 text-sm text-secondary">
              {l.name ?? `League ${l.espn_league_id}`}
            </span>
          </Link>
          <LifecycleBadge lifecycle={l.lifecycle} label={LIFECYCLE_LABEL[l.lifecycle] ?? l.lifecycle} />
          <span className="mono text-[11px] text-muted">
            {acctLabel(l.account_id)} · {l.season}
          </span>
          <span className="mono ml-auto text-[11px] text-muted">synced {relTime(l.last_synced_at)}</span>
          {l.last_sync_ok === false && (
            <span
              className="rounded bg-red/15 px-1.5 py-0.5 text-[11px] text-red"
              title={l.last_sync_error ?? undefined}
              data-testid={`league-sync-failed-${l.id}`}
            >
              last sync failed
            </span>
          )}
          {l.last_sync_ok === true && (
            <AchievementRow
              achievements={[{
                key: "sync_healthy",
                label: "Sync healthy",
                detail: "Most recent sync completed",
              }]}
              testId={`league-achievements-${l.id}`}
            />
          )}
          <Button
            variant="sync"
            onClick={() => void syncOne(l)}
            disabled={syncOperation !== null}
          >
            {(syncOperation?.kind === "individual" && syncOperation.leagueId === l.id) ||
            (isBulkSyncing && bulkSyncingId === l.id)
              ? "Syncing…"
              : "Sync"}
          </Button>
        </Panel>
      ))}
      <div className="flex flex-wrap items-center gap-3 pt-3">
        <Button
          variant="sync"
          onClick={() => void syncAll()}
          disabled={syncOperation !== null}
        >
          {isBulkSyncing
            ? `Syncing ${bulkProgress.current} of ${bulkProgress.total}...`
            : `Sync all ${leagues.length} ${leagues.length === 1 ? "league" : "leagues"}`}
        </Button>
        <span className="text-xs text-muted">
          Leagues sync one at a time. Keep this page open until the run finishes.
        </span>
      </div>
      <div aria-live="polite" aria-atomic="false" data-testid="bulk-sync-status">
        {isBulkSyncing && (
          <p className="mt-2 text-xs text-secondary">
            Syncing {bulkProgress.current} of {bulkProgress.total}:{" "}
            {bulkSyncingId == null
              ? "Preparing..."
              : leagueLabel(leagues.find((league) => league.id === bulkSyncingId) ?? leagues[0])}
          </p>
        )}
        {bulkSummary && <p className="mt-2 text-xs text-secondary">{bulkSummary}</p>}
        {bulkReloadError && (
          <p className="mt-2 text-xs text-red">
            Sync results were saved, but Manage data could not be refreshed: {bulkReloadError}
          </p>
        )}
        {bulkResults.length > 0 && (
          <ul className="mt-2 space-y-1" data-testid="bulk-sync-results">
            {bulkResults.map((result) => (
              <li
                key={result.leagueId}
                className="flex flex-wrap items-baseline gap-x-2 text-xs"
                data-testid={`bulk-sync-result-${result.leagueId}`}
              >
                <span className={`mono uppercase ${SYNC_STATUS_TONE[result.status]}`}>
                  {SYNC_STATUS_LABEL[result.status]}
                </span>
                <span className="font-medium text-primary">{result.league}</span>
                <span className="text-muted">{result.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Field({
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-primary placeholder:text-muted focus:border-red/60"
    />
  );
}
