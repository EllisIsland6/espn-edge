import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import {
  downloadExport,
  getPortfolio,
  getPortfolioSummary,
  getLeagues,
  syncLeague,
  type PortfolioRow,
  type PortfolioSummary,
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
import { syncSummaryMessage } from "../lib/sync";
import { AchievementRow, MomentumBadges, ScoreRing } from "../components/Gamification";
import { TeamIdentity } from "../components/TeamIdentity";
import {
  Button,
  EmptyState,
  ErrorNote,
  LifecycleBadge,
  Panel,
  SizePill,
  Spinner,
  WarningNote,
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
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [syncing, setSyncing] = useState(false);

  async function load() {
    setError(null);
    try {
      const [r, s] = await Promise.all([getPortfolio(), getPortfolioSummary()]);
      setRows(r);
      setSummary(s);
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
          !(r.account_label ?? "").toLowerCase().includes(q) &&
          !(r.my_team_name ?? "").toLowerCase().includes(q)) {
        return false;
      }
      if (filter === "all" || filter === "by_account") return true;
      return verdictOf(r.edge_index_verdict) === filter;
    });
  }, [rows, query, filter]);

  // Group into sections: by verdict tier (default) or by account.
  const groups = useMemo(() => groupRows(filtered, filter), [filtered, filter]);

  async function syncAll() {
    setSyncing(true);
    setWarnings([]);
    const msgs: string[] = [];
    try {
      const leagues = await getLeagues();
      for (const lg of leagues) {
        // Keep going on failure, but record which leagues failed/partially failed.
        try {
          const s = await syncLeague(lg.id);
          const m = syncSummaryMessage(s);
          if (m) msgs.push(m);
        } catch (e) {
          msgs.push(`${lg.name ?? `League ${lg.espn_league_id}`}: sync request failed — ${e}`);
        }
      }
      await load();
    } finally {
      setWarnings(msgs);
      setSyncing(false);
    }
  }

  return (
    <div className="flex gap-6">
      <div className="min-w-0 flex-1">
        <ControlBar filter={filter} setFilter={setFilter} query={query} setQuery={setQuery} />
        {error && <div className="mt-4"><ErrorNote message={error} /></div>}
        {warnings.length > 0 && (
          <div className="mt-4">
            <WarningNote
              title={`Sync finished with ${warnings.length} issue${warnings.length > 1 ? "s" : ""}`}
              messages={warnings}
              onDismiss={() => setWarnings([])}
            />
          </div>
        )}
        {!rows && !error && <Spinner label="Loading portfolio…" />}
        {rows && rows.length === 0 && (
          <div className="mt-4">
            <EmptyState
              title="No leagues yet"
              hint={
                <>
                  Get started on the <Link to="/manage" className="text-red hover:underline">Manage</Link> tab:
                  add an ESPN account (label + SWID + espn_s2 cookies), add a league by ID or
                  URL, then Sync. Your teams appear here tiered by Edge.
                </>
              }
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
                    <GradeTierDivider grade={g.grade} count={g.rows.length} />
                  ) : (
                    <AccountDivider label={g.label} count={g.rows.length} />
                  )}
                  {g.rows.map((r) => (
                    <BoardRow key={r.league_id} row={r} />
                  ))}
                </div>
              ))
            )}
          </Panel>
        )}
      </div>
      <RightRail summary={summary} syncing={syncing} onSyncAll={syncAll} />
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
      <div className="ml-auto flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search teams / leagues / accounts"
          className="min-w-0 flex-1 basis-56 rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-primary placeholder:text-muted focus:border-red/60 sm:flex-none"
        />
        <div className="flex items-center overflow-hidden rounded-md border border-line">
          <span className="mono px-2 text-[10px] uppercase tracking-wide text-muted">Export</span>
          <button
            onClick={() => downloadExport("csv")}
            title="Download portfolio as CSV"
            className="border-l border-line px-2.5 py-1.5 text-xs text-secondary transition-colors duration-150 hover:bg-rowhover hover:text-primary"
          >
            CSV
          </button>
          <button
            onClick={() => downloadExport("json")}
            title="Download master portfolio JSON"
            className="border-l border-line px-2.5 py-1.5 text-xs text-secondary transition-colors duration-150 hover:bg-rowhover hover:text-primary"
          >
            JSON
          </button>
          <button
            onClick={() => downloadExport("xlsx")}
            title="Download Excel workbook (summary + per-league sheets)"
            className="border-l border-line px-2.5 py-1.5 text-xs text-secondary transition-colors duration-150 hover:bg-rowhover hover:text-primary"
          >
            XLSX
          </button>
        </div>
      </div>
    </div>
  );
}

function BoardRow({ row }: { row: PortfolioRow }) {
  const verdict = verdictOf(row.edge_index_verdict);
  // Semantic navigation: a real <Link> (anchor) — keyboard-activatable via Enter,
  // focusable, and gets the global red :focus-visible ring. The row has no nested
  // interactive children, so wrapping the whole row is safe (no nested controls).
  return (
    <Link
      to={`/league/${row.league_id}`}
      className="relative flex min-h-24 items-center gap-3 border-t border-line py-3 pl-5 pr-4 no-underline first:border-t-0 hover:bg-rowhover focus-visible:bg-rowhover"
    >
      <span className={`absolute inset-y-0 left-0 w-[3px] ${VERDICT_ACCENT[verdict]}`} />
      {/* Owned team first, then its league context. */}
      <div className="flex min-w-0 flex-1 flex-col">
        <TeamIdentity
          team={{
            name: row.my_team_name,
            logo_url: row.my_team_logo_url,
            is_me: row.my_team_id != null,
          }}
          size="sm"
          fallback="Team not detected"
        />
        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 pl-11">
          <span className="truncate text-sm text-secondary">{row.league_name ?? `League ${row.espn_league_id}`}</span>
          <SizePill size={row.size} />
          <LifecycleBadge lifecycle={row.lifecycle} label={LIFECYCLE_LABEL[row.lifecycle] ?? row.lifecycle} />
        </div>
        <div className="mono mt-0.5 flex flex-wrap items-center gap-x-2 pl-11 text-[11px] text-muted">
          <span>{row.account_label ?? "—"}</span>
          <span>·</span>
          <span>{row.season}</span>
        </div>
        <div className="mt-1.5 flex min-h-5 flex-wrap items-center gap-2 pl-11">
          <AchievementRow achievements={row.achievements} testId={`achievements-${row.league_id}`} />
          {row.edge_score != null && (
            <span className="mono text-[9px] uppercase text-muted">
              Legacy Edge Score {num(row.edge_score, 0)}
            </span>
          )}
        </div>
      </div>
      {/* Record */}
      <div className="hidden sm:block"><Stat label="W-L-T" value={record(row.wins, row.losses, row.ties)} /></div>
      {/* PF / PA */}
      <div className="hidden xl:block"><Stat label="PF" value={num(row.points_for)} /></div>
      <div className="hidden xl:block"><Stat label="PA" value={num(row.points_against)} /></div>
      {/* Standing */}
      <div className="hidden lg:block"><Stat label="STANDING" value={ordinal(row.standing)} /></div>
      {/* Playoff odds */}
      <div className="hidden lg:block"><Stat label="PLAYOFF" value={row.playoff_odds == null ? DASH : `${Math.round(row.playoff_odds * 100)}%`} /></div>
      <div className="flex w-24 shrink-0 flex-col items-center gap-1">
        <ScoreRing
          value={row.edge_index_score}
          grade={row.edge_index_grade}
          label="Edge Index"
          size="sm"
          testId={`score-ring-${row.league_id}`}
        />
        <MomentumBadges momentum={row.edge_index_momentum} testId={`momentum-${row.league_id}`} />
      </div>
      <div className="mono hidden w-16 shrink-0 text-right text-[11px] text-muted sm:block">{relTime(row.last_synced_at)}</div>
    </Link>
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

const GRADE_TIER_TONE: Record<GradeTier, string> = {
  A: "text-grade-a",
  B: "text-grade-b",
  C: "text-grade-c",
  D: "text-grade-df",
  F: "text-grade-df",
  PENDING: "text-muted",
};

function GradeTierDivider({ grade, count }: { grade: GradeTier; count: number }) {
  return (
    <div className="flex items-center gap-3 px-4 pt-5 pb-2" data-testid={`grade-tier-${grade.toLowerCase()}`}>
      <span className={`mono text-[11px] uppercase tracking-[0.18em] ${GRADE_TIER_TONE[grade]}`}>
        {grade === "PENDING" ? "Pending" : `${grade} tier`}
      </span>
      <span className="h-px flex-1 bg-line" />
      <span className="mono text-[11px] text-muted">{count}</span>
    </div>
  );
}

// Renders the backend-computed summary — React only formats, never aggregates.
function RightRail({
  summary,
  syncing,
  onSyncAll,
}: {
  summary: PortfolioSummary | null;
  syncing: boolean;
  onSyncAll: () => void;
}) {
  const total = summary?.total_leagues ?? 0;
  const advantaged = summary?.edge_index_advantaged_count ?? 0;
  const scored = summary?.edge_index_scored_count ?? 0;
  const bestWorst =
    summary && summary.best_edge_index_score != null && summary.worst_edge_index_score != null
      ? `${num(summary.best_edge_index_score, 0)} / ${num(summary.worst_edge_index_score, 0)}`
      : DASH;
  const legacyScored = summary?.scored_count ?? 0;
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
          <RailStat
            label="Aggregate record"
            value={
              summary
                ? record(summary.aggregate_wins, summary.aggregate_losses, summary.aggregate_ties)
                : DASH
            }
          />
          <RailStat
            label="Edge Index"
            value={scored ? `${scored} scored` : "pending"}
            muted={!scored}
          />
          <RailStat
            label="Best / worst Edge Index"
            value={bestWorst}
            muted={bestWorst === DASH}
          />
          <RailStat
            label="Legacy Edge Score"
            value={legacyScored ? `${legacyScored} scored` : "—"}
            muted={!legacyScored}
          />
        </dl>

        <div className="mt-5">
          <Button variant="primary" onClick={onSyncAll} disabled={syncing || total === 0}>
            {syncing ? "Syncing…" : "Sync all"}
          </Button>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          Edge Index is the full SPEC §6 composite (0.5 × MyEdge + 0.5 × LeagueSoftness); it
          stays pending until a league has enough drafted/played data. The legacy within-league
          Edge Score is kept alongside for reference.
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
  | { kind: "tier"; key: string; grade: GradeTier; rows: PortfolioRow[] }
  | { kind: "account"; key: string; label: string; rows: PortfolioRow[] };

type GradeTier = "A" | "B" | "C" | "D" | "F" | "PENDING";
const TIER_ORDER: GradeTier[] = ["A", "B", "C", "D", "F", "PENDING"];

function gradeTier(grade: string | null): GradeTier {
  const letter = grade?.trim().toUpperCase().charAt(0);
  return letter === "A" || letter === "B" || letter === "C" || letter === "D" || letter === "F"
    ? letter
    : "PENDING";
}

function compareRows(a: PortfolioRow, b: PortfolioRow): number {
  const score = (b.edge_index_score ?? Number.NEGATIVE_INFINITY) -
    (a.edge_index_score ?? Number.NEGATIVE_INFINITY);
  if (score !== 0) return score;
  const name = (a.league_name ?? "").localeCompare(b.league_name ?? "");
  return name || a.league_id - b.league_id;
}

function groupRows(rows: PortfolioRow[], filter: Filter): Group[] {
  if (filter === "by_account") {
    const by = new Map<string, PortfolioRow[]>();
    for (const r of rows) {
      const label = r.account_label ?? "Unassigned";
      (by.get(label) ?? by.set(label, []).get(label)!).push(r);
    }
    return [...by.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([label, rs]) => ({ kind: "account", key: label, label, rows: rs.sort(compareRows) }));
  }
  const by = new Map<GradeTier, PortfolioRow[]>();
  for (const r of rows) {
    const grade = gradeTier(r.edge_index_grade);
    (by.get(grade) ?? by.set(grade, []).get(grade)!).push(r);
  }
  return TIER_ORDER.filter((grade) => by.has(grade)).map((grade) => ({
    kind: "tier",
    key: grade,
    grade,
    rows: by.get(grade)!.sort(compareRows),
  }));
}
