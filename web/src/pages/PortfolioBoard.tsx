import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getPortfolio,
  getLeagues,
  syncLeague,
  type PortfolioRow,
} from "../api";
import {
  DASH,
  LIFECYCLE_LABEL,
  num,
  ordinal,
  record,
  relTime,
  verdictOf,
  type Verdict,
} from "../lib/format";
import {
  Button,
  EmptyState,
  ErrorNote,
  GradePill,
  LifecycleBadge,
  Panel,
  SizePill,
  Spinner,
  TierDivider,
  ValueChip,
} from "../components/ui";

type Filter = "all" | "advantaged" | "neutral" | "disadvantaged" | "by_account";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "ALL" },
  { key: "advantaged", label: "ADVANTAGED" },
  { key: "neutral", label: "NEUTRAL" },
  { key: "disadvantaged", label: "DISADVANTAGED" },
  { key: "by_account", label: "BY ACCOUNT" },
];

const VERDICT_ACCENT: Record<Verdict, string> = {
  advantaged: "bg-grade-a",
  neutral: "bg-grade-c",
  disadvantaged: "bg-grade-df",
  pending: "bg-line",
};

export default function PortfolioBoard() {
  const [rows, setRows] = useState<PortfolioRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [syncing, setSyncing] = useState(false);
  const navigate = useNavigate();

  async function load() {
    setError(null);
    try {
      setRows(await getPortfolio());
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !(r.league_name ?? "").toLowerCase().includes(q) &&
          !(r.account_label ?? "").toLowerCase().includes(q)) {
        return false;
      }
      if (filter === "all" || filter === "by_account") return true;
      return verdictOf(r.verdict) === filter;
    });
  }, [rows, query, filter]);

  // Group into sections: by verdict tier (default) or by account.
  const groups = useMemo(() => groupRows(filtered, filter), [filtered, filter]);

  async function syncAll() {
    setSyncing(true);
    try {
      const leagues = await getLeagues();
      for (const lg of leagues) {
        try {
          await syncLeague(lg.id);
        } catch {
          /* keep going; per-league errors surface on next load */
        }
      }
      await load();
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="flex gap-6">
      <div className="min-w-0 flex-1">
        <ControlBar filter={filter} setFilter={setFilter} query={query} setQuery={setQuery} />
        {error && <div className="mt-4"><ErrorNote message={error} /></div>}
        {!rows && !error && <Spinner label="Loading portfolio…" />}
        {rows && rows.length === 0 && (
          <div className="mt-4">
            <EmptyState
              title="No leagues yet"
              hint={<>Add an account and a league on the <b>Manage</b> tab, then Sync.</>}
            />
          </div>
        )}
        {rows && rows.length > 0 && (
          <Panel className="mt-4 overflow-hidden">
            {groups.length === 0 ? (
              <div className="p-4">
                <EmptyState title="No leagues match this filter." />
              </div>
            ) : (
              groups.map((g) => (
                <div key={g.key}>
                  {g.kind === "tier" ? (
                    <TierDivider verdict={g.verdict} count={g.rows.length} />
                  ) : (
                    <AccountDivider label={g.label} count={g.rows.length} />
                  )}
                  {g.rows.map((r) => (
                    <BoardRow key={r.league_id} row={r} onClick={() => navigate(`/league/${r.league_id}`)} />
                  ))}
                </div>
              ))
            )}
          </Panel>
        )}
      </div>
      <RightRail rows={rows ?? []} syncing={syncing} onSyncAll={syncAll} />
    </div>
  );
}

function ControlBar({
  filter,
  setFilter,
  query,
  setQuery,
}: {
  filter: Filter;
  setFilter: (f: Filter) => void;
  query: string;
  setQuery: (q: string) => void;
}) {
  return (
    <div className="sticky top-14 z-10 -mx-1 flex flex-wrap items-center gap-2 bg-page/80 px-1 py-2 backdrop-blur">
      <div className="flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`mono rounded-md px-2.5 py-1 text-[11px] tracking-wide transition-colors duration-150 ${
              filter === f.key
                ? "bg-red text-white"
                : "border border-line bg-panel text-secondary hover:text-primary"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search leagues / accounts"
          className="w-56 rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-primary placeholder:text-muted focus:border-red/60"
        />
        <Button variant="secondary" disabled title="CSV export — Phase 5">
          CSV
        </Button>
      </div>
    </div>
  );
}

function BoardRow({ row, onClick }: { row: PortfolioRow; onClick: () => void }) {
  const verdict = verdictOf(row.verdict);
  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" ? onClick() : undefined)}
      className="relative flex h-16 cursor-pointer items-center gap-4 border-t border-line pl-5 pr-4 first:border-t-0 hover:bg-rowhover"
    >
      <span className={`absolute inset-y-0 left-0 w-[3px] ${VERDICT_ACCENT[verdict]}`} />
      {/* League + account */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2">
          <span className="truncate font-semibold text-primary">{row.league_name ?? `League ${row.espn_league_id}`}</span>
          <SizePill size={row.size} />
          <LifecycleBadge lifecycle={row.lifecycle} label={LIFECYCLE_LABEL[row.lifecycle] ?? row.lifecycle} />
        </div>
        <div className="mono mt-0.5 flex items-center gap-2 text-[11px] text-muted">
          <span>{row.account_label ?? "—"}</span>
          <span>·</span>
          <span>{row.my_team_name ?? "team not detected"}</span>
          <span>·</span>
          <span>{row.season}</span>
        </div>
      </div>
      {/* Record */}
      <Stat label="W-L-T" value={record(row.wins, row.losses, row.ties)} />
      {/* PF / PA */}
      <Stat label="PF" value={num(row.points_for)} />
      <Stat label="PA" value={num(row.points_against)} />
      {/* Standing */}
      <Stat label="STANDING" value={ordinal(row.standing)} />
      {/* Playoff odds (Phase 3) */}
      <Stat label="PLAYOFF" value={row.playoff_odds == null ? DASH : `${Math.round(row.playoff_odds * 100)}%`} />
      {/* Edge score chip + grade (Phase 3 → pending) */}
      <div className="flex w-24 items-center justify-end gap-2">
        <ValueChip primary={row.edge_score == null ? DASH : num(row.edge_score, 0)} muted={row.edge_score == null} />
        <GradePill grade={row.grade} />
      </div>
      <div className="mono w-16 shrink-0 text-right text-[11px] text-muted">{relTime(row.last_synced_at)}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex w-14 shrink-0 flex-col items-end">
      <span className="mono text-sm text-primary">{value}</span>
      <span className="text-[9px] uppercase tracking-wide text-muted">{label}</span>
    </div>
  );
}

function AccountDivider({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-3 px-4 pt-5 pb-2">
      <span className="mono text-[11px] uppercase tracking-[0.18em] text-secondary">{label}</span>
      <span className="h-px flex-1 bg-line" />
      <span className="mono text-[11px] text-muted">{count}</span>
    </div>
  );
}

function RightRail({
  rows,
  syncing,
  onSyncAll,
}: {
  rows: PortfolioRow[];
  syncing: boolean;
  onSyncAll: () => void;
}) {
  const total = rows.length;
  const advantaged = rows.filter((r) => verdictOf(r.verdict) === "advantaged").length;
  const scored = rows.filter((r) => r.edge_score != null);
  const agg = rows.reduce(
    (a, r) => ({
      w: a.w + (r.wins ?? 0),
      l: a.l + (r.losses ?? 0),
      t: a.t + (r.ties ?? 0),
    }),
    { w: 0, l: 0, t: 0 },
  );
  return (
    <div className="hidden w-[320px] shrink-0 lg:block">
      <Panel className="sticky top-[4.75rem] p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-primary">Portfolio</h2>
          <span className="mono rounded-full bg-red/15 px-2 py-0.5 text-[11px] text-red">
            {advantaged} of {total} advantaged
          </span>
        </div>

        <dl className="mt-4 space-y-3">
          <RailStat label="Leagues tracked" value={String(total)} />
          <RailStat label="Aggregate record" value={record(agg.w, agg.l, agg.t)} />
          <RailStat
            label="Edge Index"
            value={scored.length ? `${scored.length} scored` : "pending — Phase 3"}
            muted={!scored.length}
          />
          <RailStat label="Best / worst edge" value={DASH} muted />
        </dl>

        <div className="mt-5">
          <Button variant="primary" onClick={onSyncAll} disabled={syncing || total === 0}>
            {syncing ? "Syncing…" : "Sync all"}
          </Button>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          Edge Score, grade, and playoff odds populate once the analytics engine lands
          (Phase 3). Records, points, and standings are live from ESPN.
        </p>
      </Panel>
    </div>
  );
}

function RailStat({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-xs text-secondary">{label}</dt>
      <dd className={`mono text-sm ${muted ? "text-muted" : "text-primary"}`}>{value}</dd>
    </div>
  );
}

// --- grouping ---------------------------------------------------------------
type Group =
  | { kind: "tier"; key: string; verdict: Verdict; rows: PortfolioRow[] }
  | { kind: "account"; key: string; label: string; rows: PortfolioRow[] };

const TIER_ORDER: Verdict[] = ["advantaged", "neutral", "disadvantaged", "pending"];

function groupRows(rows: PortfolioRow[], filter: Filter): Group[] {
  if (filter === "by_account") {
    const by = new Map<string, PortfolioRow[]>();
    for (const r of rows) {
      const label = r.account_label ?? "Unassigned";
      (by.get(label) ?? by.set(label, []).get(label)!).push(r);
    }
    return [...by.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([label, rs]) => ({ kind: "account", key: label, label, rows: rs }));
  }
  const by = new Map<Verdict, PortfolioRow[]>();
  for (const r of rows) {
    const v = verdictOf(r.verdict);
    (by.get(v) ?? by.set(v, []).get(v)!).push(r);
  }
  return TIER_ORDER.filter((v) => by.has(v)).map((v) => ({
    kind: "tier",
    key: v,
    verdict: v,
    rows: by.get(v)!,
  }));
}
