import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  addAccount,
  addLeague,
  deleteAccount,
  getAccounts,
  getLeagues,
  reauthAccount,
  syncLeague,
  type AccountOut,
  type LeagueOut,
} from "../api";
import { Button, EmptyState, ErrorNote, LifecycleBadge, Panel, Spinner } from "../components/ui";
import { LIFECYCLE_LABEL, relTime } from "../lib/format";
import { syncSummaryMessage } from "../lib/sync";

export default function Manage() {
  const [accounts, setAccounts] = useState<AccountOut[] | null>(null);
  const [leagues, setLeagues] = useState<LeagueOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    setError(null);
    try {
      const [a, l] = await Promise.all([getAccounts(), getLeagues()]);
      setAccounts(a);
      setLeagues(l);
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    reload();
  }, []);

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
        <AccountList accounts={accounts} onChange={reload} onError={setError} />
      )}

      <AddLeagueForm accounts={accounts ?? []} onDone={reload} onError={setError} />
      {!leagues ? (
        <Spinner />
      ) : (
        <LeagueList leagues={leagues} accounts={accounts ?? []} onChange={reload} onError={setError} />
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
        <Button type="submit" variant="primary" disabled={busy || !label || !swid || !s2}>
          {busy ? "Adding…" : "Add"}
        </Button>
      </form>
    </Panel>
  );
}

function AccountList({
  accounts,
  onChange,
  onError,
}: {
  accounts: AccountOut[];
  onChange: () => void;
  onError: (e: string) => void;
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
        <AccountRow key={a.id} account={a} onChange={onChange} onError={onError} />
      ))}
    </div>
  );
}

function AccountRow({
  account: a,
  onChange,
  onError,
}: {
  account: AccountOut;
  onChange: () => void;
  onError: (e: string) => void;
}) {
  const needsReauth = a.status === "needs_reauth";
  // Auto-expand the re-auth form when the account's session expired.
  const [showReauth, setShowReauth] = useState(needsReauth);
  return (
    <Panel className={`px-4 py-2 ${needsReauth ? "border-red/50" : ""}`}>
      <div className="flex items-center gap-3">
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
        <Button onClick={() => setShowReauth((v) => !v)}>
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
      <Button type="submit" variant="primary" disabled={busy || !swid || !s2}>
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
        <Button type="submit" variant="primary" disabled={busy || !ref}>
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
  onError,
}: {
  leagues: LeagueOut[];
  accounts: AccountOut[];
  onChange: () => void;
  onError: (e: string) => void;
}) {
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const acctLabel = (id: number | null) => accounts.find((a) => a.id === id)?.label ?? "public";
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
        <Panel key={l.id} className="flex items-center gap-3 px-4 py-2">
          <Link to={`/league/${l.id}`} className="font-medium hover:text-red">
            {l.name ?? `League ${l.espn_league_id}`}
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
          <Button
            onClick={async () => {
              setSyncingId(l.id);
              try {
                const s = await syncLeague(l.id);
                const msg = syncSummaryMessage(s); // covers needs_reauth AND errors
                if (msg) onError(msg);
                onChange();
              } catch (e) {
                onError(String(e));
              } finally {
                setSyncingId(null);
              }
            }}
            disabled={syncingId === l.id}
          >
            {syncingId === l.id ? "Syncing…" : "Sync"}
          </Button>
        </Panel>
      ))}
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
