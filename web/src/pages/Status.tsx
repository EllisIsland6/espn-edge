import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  EXPORT_ENDPOINTS,
  getAccounts,
  getAiStatus,
  getHealth,
  getPortfolio,
  type AccountOut,
  type AiStatus,
  type Health,
  type PortfolioRow,
} from "../api";
import { EmptyState, ErrorNote, Panel, Spinner } from "../components/ui";

// System Status is read-only and secret-free: it only *composes* what the API already
// exposes (health, AI status, accounts, portfolio). No ESPN calls, no math, no cookies.
export default function Status() {
  const [health, setHealth] = useState<Health | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [accounts, setAccounts] = useState<AccountOut[] | null>(null);
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setError(null);
      try {
        const [h, a, acc, p] = await Promise.all([
          getHealth(),
          getAiStatus(),
          getAccounts(),
          getPortfolio(),
        ]);
        setHealth(h);
        setAi(a);
        setAccounts(acc);
        setRows(p);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  const loading = !error && (!health || !ai || !accounts || !rows);

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

      {!loading && !error && health && ai && accounts && rows && (
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
