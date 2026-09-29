import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  EXPORT_ENDPOINTS,
  getAccounts,
  getAiStatus,
  getHealth,
  getOpportunityStatus,
  getPortfolio,
  getRecoveryStatus,
  triggerRecoveryBackup,
  type AccountOut,
  type AiStatus,
  type Health,
  type OpportunityStatus,
  type PortfolioRow,
  type RecoveryStatus,
} from "../api";
import { Button, EmptyState, ErrorNote, Panel, Spinner } from "../components/ui";

// System Status is read-only and secret-free: it only *composes* what the API already
// exposes (health, AI status, accounts, portfolio). No ESPN calls, no math, no cookies.
export default function Status() {
  const [health, setHealth] = useState<Health | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [accounts, setAccounts] = useState<AccountOut[] | null>(null);
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [opportunity, setOpportunity] = useState<OpportunityStatus | null>(null);
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null);
  const [backingUp, setBackingUp] = useState(false);
  const [recoveryNote, setRecoveryNote] = useState<string | null>(null);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setError(null);
      try {
        const [h, a, acc, p, o] = await Promise.all([
          getHealth(),
          getAiStatus(),
          getAccounts(),
          getPortfolio(),
          getOpportunityStatus(),
        ]);
        setHealth(h);
        setAi(a);
        setAccounts(acc);
        setRows(p);
        setOpportunity(o);
      } catch (e) {
        setError(String(e));
      }
    })();
    (async () => {
      setRecoveryError(null);
      try {
        setRecovery(await getRecoveryStatus());
      } catch (e) {
        setRecoveryError(String(e));
      }
    })();
  }, []);

  const loading = !error && (!health || !ai || !accounts || !rows || !opportunity);

  async function manualBackup() {
    setBackingUp(true);
    setRecoveryNote(null);
    try {
      const result = await triggerRecoveryBackup("manual");
      setRecoveryNote(
        result.snapshot_created ? "New recovery point verified." : "Existing recovery point reverified.",
      );
      setRecovery(await getRecoveryStatus());
    } catch (e) {
      setRecoveryNote(`Recovery point failed: ${String(e)}`);
    } finally {
      setBackingUp(false);
    }
  }

  const needsReauth = accounts?.filter((a) => a.status === "needs_reauth").length ?? 0;
  const failedSync = rows?.filter((r) => r.last_sync_ok === false).length ?? 0;

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-semibold">System status</h1>
      <p className="mt-1 text-sm text-secondary">
        A read-only health check of your local ESPN Edge instance. Nothing here calls ESPN,
        and no cookies or secrets are ever shown.
      </p>
      {error && <div className="mt-4"><ErrorNote message={error} /></div>}
      {loading && <Spinner label="Checking status…" />}

      {!loading && !error && health && ai && accounts && rows && opportunity && (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <StatusCard title="API">
            <Row label="Health" value={health.status} ok={health.status === "ok"} />
            <Row label="Season" value={String(health.season)} />
            <Row label="Database" value={health.db_path} mono />
          </StatusCard>

          <StatusCard title="AI layer">
            <Row
              label="Anthropic key"
              value={ai.enabled ? "connected" : "not set"}
              ok={ai.enabled}
              warn={!ai.enabled}
            />
            <Row label="Standard model" value={ai.standard_model} mono />
            <Row label="Bulk model" value={ai.bulk_model} mono />
            {!ai.enabled && (
              <p className="mt-2 text-[11px] text-muted">
                AI panels are optional. Set <code className="mono">ANTHROPIC_API_KEY</code> in{" "}
                <code className="mono">.env</code> and restart the API to enable them.
              </p>
            )}
          </StatusCard>

          <StatusCard title="Accounts">
            <Row label="Total" value={String(accounts.length)} />
            <Row
              label="Need re-auth"
              value={String(needsReauth)}
              ok={needsReauth === 0}
              warn={needsReauth > 0}
            />
            {needsReauth > 0 && (
              <p className="mt-2 text-[11px] text-muted">
                Paste fresh cookies on the <Link to="/manage" className="text-red hover:underline">Manage</Link> tab
                to resume syncing.
              </p>
            )}
          </StatusCard>

          <StatusCard title="Leagues">
            <Row label="Tracked" value={String(rows.length)} />
            <Row
              label="Last sync failed"
              value={String(failedSync)}
              ok={failedSync === 0}
              warn={failedSync > 0}
            />
          </StatusCard>

          <StatusCard title="Opportunity data">
            <Row
              label="State"
              value={opportunity.state}
              ok={["ready", "partial", "skipped", "empty"].includes(opportunity.state)}
              warn={opportunity.state === "failed" || opportunity.stale}
            />
            <Row
              label="Latest NFL week"
              value={opportunity.latest_week == null ? "not published" : String(opportunity.latest_week)}
            />
            <Row label="Stored player-games" value={String(opportunity.stored_rows)} />
            <Row
              label="Player mapping"
              value={`${opportunity.matched_players} matched · ${opportunity.unmatched_players} missing`}
              warn={opportunity.unmatched_players > 0}
            />
            <Row
              label="Last good import"
              value={opportunity.last_good_at ? new Date(opportunity.last_good_at).toLocaleString() : "none yet"}
            />
            {opportunity.error_code && (
              <Row label="Diagnostic" value={opportunity.error_code} warn mono />
            )}
            <Row label="Run ID" value={opportunity.run_id ?? "none"} mono />
            <Row
              label="Adapter / schema"
              value={`${opportunity.package_version ?? "unknown"} / ${opportunity.schema_fingerprint ?? "none"}`}
              mono
            />
            {opportunity.error_message && (
              <p className="mt-2 text-[11px] text-muted">{opportunity.error_message}</p>
            )}
            <p className="mt-2 text-[11px] text-muted">
              Troubleshoot with <code className="mono">python -m api.opportunity doctor --json</code>.
            </p>
          </StatusCard>

          <StatusCard title="Private recovery">
            {recoveryError && <ErrorNote message={`Recovery status unavailable: ${recoveryError}`} />}
            {!recovery && !recoveryError && <Spinner label="Checking recovery…" />}
            {recovery && <>
            <Row
              label="State"
              value={recovery.state.replace(/_/g, " ")}
              ok={recovery.state === "ready" || recovery.state === "not_required"}
              warn={!['ready', 'not_required'].includes(recovery.state)}
            />
            <Row
              label="Latest coverage"
              value={
                recovery.last_coverage_at
                  ? new Date(recovery.last_coverage_at).toLocaleString()
                  : "none"
              }
            />
            <Row
              label="Age"
              value={
                recovery.age_seconds == null
                  ? "unknown"
                  : `${Math.floor(recovery.age_seconds / 3600)}h ${Math.floor((recovery.age_seconds % 3600) / 60)}m`
              }
              warn={
                recovery.age_seconds != null
                && recovery.age_seconds >= recovery.stale_after_seconds
              }
            />
            <Row
              label="Retention"
              value={recovery.retention_enforced ? "enforced" : "not proven"}
              ok={recovery.retention_enforced}
              warn={recovery.required && !recovery.retention_enforced}
            />
            <div className="pt-2" aria-live="polite">
              <Button
                type="button"
                variant="sync"
                disabled={backingUp || !recovery.supported_topology || !recovery.configured}
                onClick={() => void manualBackup()}
              >
                {backingUp ? "Backing up…" : "Create recovery point"}
              </Button>
              {recoveryNote && <p className="mt-2 text-[11px] text-muted">{recoveryNote}</p>}
            </div>
            </>}
          </StatusCard>

          <StatusCard title="Exports">
            <Row label="CSV" value={EXPORT_ENDPOINTS.csv} mono />
            <Row label="JSON" value={EXPORT_ENDPOINTS.json} mono />
            <Row label="XLSX" value={EXPORT_ENDPOINTS.xlsx} mono />
            <p className="mt-2 text-[11px] text-muted">
              Download from the Export control on the <Link to="/" className="text-red hover:underline">Portfolio</Link> board.
            </p>
          </StatusCard>

          <StatusCard title="Beta checklist">
            {accounts.length === 0 ? (
              <ChecklistItem done={false}>
                Add your first ESPN account on <Link to="/manage" className="text-red hover:underline">Manage</Link>
              </ChecklistItem>
            ) : (
              <ChecklistItem done>Account added ({accounts.length})</ChecklistItem>
            )}
            {rows.length === 0 ? (
              <ChecklistItem done={false}>Add a league and Sync it</ChecklistItem>
            ) : (
              <ChecklistItem done>League synced ({rows.length})</ChecklistItem>
            )}
            <ChecklistItem done={needsReauth === 0 && failedSync === 0}>
              No accounts need re-auth and no syncs failed
            </ChecklistItem>
            <p className="mt-2 text-[11px] text-muted">
              Validating against a real league? Follow <code className="mono">docs/live-smoke.md</code>.
            </p>
          </StatusCard>
        </div>
      )}

      {!loading && !error && rows && rows.length === 0 && (
        <div className="mt-4">
          <EmptyState
            title="No leagues tracked yet"
            hint={
              <>
                Add an ESPN account and a league on the <b>Manage</b> tab, then Sync to
                populate your Portfolio Board.
              </>
            }
          />
        </div>
      )}
    </div>
  );
}

function StatusCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel className="p-4">
      <h2 className="text-sm font-semibold text-primary">{title}</h2>
      <dl className="mt-3 space-y-2">{children}</dl>
    </Panel>
  );
}

function Row({
  label,
  value,
  ok,
  warn,
  mono,
}: {
  label: string;
  value: string;
  ok?: boolean;
  warn?: boolean;
  mono?: boolean;
}) {
  const tone = warn ? "text-red" : ok ? "text-green" : "text-primary";
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-xs text-secondary">{label}</dt>
      <dd className={`${mono ? "mono" : ""} min-w-0 truncate text-right text-sm ${tone}`} title={value}>
        {value}
      </dd>
    </div>
  );
}

function ChecklistItem({ done, children }: { done: boolean; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`mono ${done ? "text-green" : "text-muted"}`}>{done ? "✓" : "○"}</span>
      <span className={done ? "text-secondary" : "text-primary"}>{children}</span>
    </div>
  );
}
