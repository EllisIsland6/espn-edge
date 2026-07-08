import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getLeagueActivity,
  getLeagueDraft,
  getLeagueMatchups,
  getLeagueOverview,
  syncLeague,
  type DraftPickOut,
  type LeagueOverview,
  type MatchupOut,
  type TeamOut,
  type TransactionOut,
} from "../api";
import { DataTable } from "../components/DataTable";
import {
  Button,
  EmptyState,
  ErrorNote,
  GradePill,
  LifecycleBadge,
  Panel,
  SizePill,
  Spinner,
  ValueChip,
  WarningNote,
} from "../components/ui";
import { DASH, LIFECYCLE_LABEL, num, ordinal, record, relTime } from "../lib/format";
import { syncSummaryMessage } from "../lib/sync";

type Tab = "overview" | "draft" | "teams" | "matchups" | "activity" | "ai";
const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "draft", label: "Draft Board" },
  { key: "teams", label: "Teams" },
  { key: "matchups", label: "Matchups" },
  { key: "activity", label: "Activity" },
  { key: "ai", label: "AI Brief" },
];

export default function LeagueDetail() {
  const { id } = useParams();
  const leagueId = Number(id);
  const [ov, setOv] = useState<LeagueOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [syncing, setSyncing] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setOv(null);
    setError(null);
    getLeagueOverview(leagueId).then(setOv).catch((e) => setError(String(e)));
  }, [leagueId, reloadKey]);

  const teamName = useMemo(() => {
    const m = new Map<number, TeamOut>();
    ov?.teams.forEach((t) => m.set(t.id, t));
    return m;
  }, [ov]);

  async function sync() {
    setSyncing(true);
    setWarning(null);
    try {
      const summary = await syncLeague(leagueId);
      setWarning(syncSummaryMessage(summary)); // null when clean
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSyncing(false);
    }
  }

  if (error) return <ErrorNote message={error} />;
  if (!ov) return <Spinner label="Loading league…" />;

  const lg = ov.league;
  return (
    <div>
      <div className="flex items-center gap-3">
        <Link to="/" className="text-sm text-secondary hover:text-primary">
          ← Portfolio
        </Link>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{lg.name ?? `League ${lg.espn_league_id}`}</h1>
        <SizePill size={lg.size} />
        <LifecycleBadge lifecycle={lg.lifecycle} label={LIFECYCLE_LABEL[lg.lifecycle] ?? lg.lifecycle} />
        {ov.scoring && (
          <span className="mono rounded bg-rowhover px-1.5 py-0.5 text-[11px] text-secondary">
            {ov.scoring}
          </span>
        )}
        <div className="mono ml-auto flex items-center gap-3 text-[11px] text-muted">
          <span>{ov.account_label ?? "public"}</span>
          <span>· {lg.season}</span>
          <span>· synced {relTime(lg.last_synced_at)}</span>
          <Button variant="primary" onClick={sync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync now"}
          </Button>
        </div>
      </div>

      {warning && (
        <div className="mt-4">
          <WarningNote title="Last sync had an issue" messages={[warning]} onDismiss={() => setWarning(null)} />
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors duration-150 ${
              tab === t.key
                ? "border-red text-primary"
                : "border-transparent text-secondary hover:text-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-5">
        {tab === "overview" && <OverviewTab ov={ov} />}
        {tab === "draft" && <DraftTab leagueId={leagueId} teamName={teamName} myTeamId={lg.my_team_id} />}
        {tab === "teams" && <TeamsTab teams={ov.teams} />}
        {tab === "matchups" && <MatchupsTab leagueId={leagueId} teamName={teamName} />}
        {tab === "activity" && <ActivityTab leagueId={leagueId} teamName={teamName} />}
        {tab === "ai" && <AiTab />}
      </div>
    </div>
  );
}

function teamLabel(m: Map<number, TeamOut>, id: number | null): string {
  if (id == null) return DASH;
  return m.get(id)?.name ?? `#${id}`;
}

// --- Overview: standings + Edge breakdown placeholder ----------------------
function OverviewTab({ ov }: { ov: LeagueOverview }) {
  const cols: ColumnDef<TeamOut, any>[] = [
    { accessorKey: "standing", header: "#", cell: (c) => <span className="mono text-secondary">{ordinal(c.getValue<number | null>())}</span> },
    {
      accessorKey: "name",
      header: "Team",
      cell: (c) => (
        <span className={c.row.original.is_me ? "font-semibold text-primary" : "text-primary"}>
          {c.getValue<string>()} {c.row.original.is_me && <span className="text-red">·me</span>}
          {c.row.original.autodrafted && <span className="ml-1 text-[10px] text-muted">auto</span>}
        </span>
      ),
    },
    { id: "record", header: "W-L-T", accessorFn: (t) => t.wins, cell: (c) => <span className="mono">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "points_for", header: "PF", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "points_against", header: "PA", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
  ];
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
      <Panel className="overflow-hidden">
        <div className="border-b border-line px-4 py-2 text-xs uppercase tracking-wide text-muted">
          Standings
        </div>
        <div className="p-1">
          <DataTable
            data={ov.teams}
            columns={cols}
            initialSort={[{ id: "standing", desc: false }]}
            rowClassName={(t) => (t.is_me ? "bg-greenchip/40" : "")}
          />
        </div>
      </Panel>
      <Panel className="p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Edge breakdown</h3>
          <GradePill grade={ov.grade} />
        </div>
        <div className="mt-3 flex items-center gap-3">
          <ValueChip
            primary={ov.edge_score == null ? DASH : num(ov.edge_score, 0)}
            secondary="Edge Score"
            muted={ov.edge_score == null}
          />
          <ValueChip
            primary={ov.playoff_odds == null ? DASH : `${Math.round(ov.playoff_odds * 100)}%`}
            secondary="Playoff odds"
            muted={ov.playoff_odds == null}
          />
          {ov.verdict && (
            <span className="mono text-xs uppercase tracking-wide text-secondary">{ov.verdict}</span>
          )}
        </div>
        <div className="mt-4">
          {ov.edge_score == null ? (
            <EmptyState
              title={
                ov.league.lifecycle === "pre_draft"
                  ? "Edge Score pending — league hasn't drafted"
                  : "Edge Score pending — not enough data yet"
              }
              hint="Within-league v1 score; full LeagueSoftness + MyEdge breakdown (SPEC §6) is future."
            />
          ) : (
            <p className="text-[11px] leading-relaxed text-muted">
              v1 within-league score from record, points, and roster projections. The full
              component breakdown (SPEC §6) lands in a later phase.
            </p>
          )}
        </div>
      </Panel>
    </div>
  );
}

// --- Draft Board ------------------------------------------------------------
function DraftTab({
  leagueId,
  teamName,
  myTeamId,
}: {
  leagueId: number;
  teamName: Map<number, TeamOut>;
  myTeamId: number | null;
}) {
  const [picks, setPicks] = useState<DraftPickOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueDraft(leagueId).then(setPicks).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!picks) return <Spinner />;
  if (picks.length === 0)
    return <EmptyState title="No draft yet" hint="This league is pre-draft — the board unlocks once it drafts." />;

  const cols: ColumnDef<DraftPickOut, any>[] = [
    { accessorKey: "overall", header: "Overall", cell: (c) => <span className="mono text-secondary">{c.getValue<number>()}</span> },
    { id: "rp", header: "Rd.Pick", accessorFn: (p) => (p.round ?? 0) * 100 + (p.round_pick ?? 0), cell: (c) => <span className="mono text-muted">{c.row.original.round}.{c.row.original.round_pick}</span> },
    { id: "team", header: "Team", accessorFn: (p) => teamLabel(teamName, p.team_id), cell: (c) => <span className="text-primary">{teamLabel(teamName, c.row.original.team_id)}</span> },
    { accessorKey: "espn_player_id", header: "Player ID", cell: (c) => <span className="mono text-secondary">{c.getValue<number | null>() ?? DASH}</span> },
    { accessorKey: "keeper", header: "Keeper", cell: (c) => (c.getValue<boolean>() ? "K" : "") },
    { accessorKey: "autodraft", header: "Auto", cell: (c) => (c.getValue<boolean>() ? <span className="text-muted">auto</span> : "") },
    { id: "value", header: "Δ vs ADP", accessorFn: (p) => p.value_delta ?? 0, cell: (c) => <span className="mono text-muted">{c.row.original.value_delta == null ? DASH : num(c.row.original.value_delta)}</span> },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <DataTable
        data={picks}
        columns={cols}
        initialSort={[{ id: "overall", desc: false }]}
        rowClassName={(p) => (myTeamId != null && p.team_id === myTeamId ? "bg-greenchip/40" : "")}
      />
      <p className="px-3 py-2 text-[11px] text-muted">
        Player names + ADP value deltas resolve once player mapping/analytics land (Phase 3).
      </p>
    </Panel>
  );
}

// --- Teams ------------------------------------------------------------------
function TeamsTab({ teams }: { teams: TeamOut[] }) {
  const cols: ColumnDef<TeamOut, any>[] = [
    { accessorKey: "name", header: "Team", cell: (c) => <span className={c.row.original.is_me ? "font-semibold text-primary" : "text-primary"}>{c.getValue<string>()}{c.row.original.is_me && <span className="text-red"> ·me</span>}</span> },
    { accessorKey: "abbrev", header: "Abbr", cell: (c) => <span className="mono text-muted">{c.getValue<string | null>() ?? DASH}</span> },
    { id: "record", header: "W-L-T", accessorFn: (t) => t.wins, cell: (c) => <span className="mono">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "points_for", header: "PF", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "standing", header: "Standing", cell: (c) => <span className="mono">{ordinal(c.getValue<number | null>())}</span> },
    { id: "strength", header: "Roster str.", cell: () => <span className="mono text-muted">{DASH}</span> },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <DataTable data={teams} columns={cols} rowClassName={(t) => (t.is_me ? "bg-greenchip/40" : "")} />
      <p className="px-3 py-2 text-[11px] text-muted">Roster strength (ADP/projection based) computes in Phase 3.</p>
    </Panel>
  );
}

// --- Matchups ---------------------------------------------------------------
function MatchupsTab({ leagueId, teamName }: { leagueId: number; teamName: Map<number, TeamOut> }) {
  const [ms, setMs] = useState<MatchupOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueMatchups(leagueId).then(setMs).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!ms) return <Spinner />;
  const played = ms.filter((m) => (m.home_points ?? 0) > 0 || (m.away_points ?? 0) > 0);
  if (played.length === 0)
    return <EmptyState title="No completed matchups" hint="Weekly results appear here once games are played." />;

  const cols: ColumnDef<MatchupOut, any>[] = [
    { accessorKey: "week", header: "Wk", cell: (c) => <span className="mono text-secondary">{c.getValue<number>()}</span> },
    { id: "home", header: "Home", accessorFn: (m) => teamLabel(teamName, m.home_team_id), cell: (c) => teamLabel(teamName, c.row.original.home_team_id) },
    { accessorKey: "home_points", header: "HPts", cell: (c) => <span className="mono">{num(c.getValue<number | null>())}</span> },
    { accessorKey: "away_points", header: "APts", cell: (c) => <span className="mono">{num(c.getValue<number | null>())}</span> },
    { id: "away", header: "Away", accessorFn: (m) => teamLabel(teamName, m.away_team_id), cell: (c) => teamLabel(teamName, c.row.original.away_team_id) },
    { accessorKey: "is_playoff", header: "PO", cell: (c) => (c.getValue<boolean>() ? <span className="text-gold">●</span> : "") },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <DataTable data={played} columns={cols} initialSort={[{ id: "week", desc: false }]} />
      <p className="px-3 py-2 text-[11px] text-muted">All-play records + luck delta arrive in Phase 3.</p>
    </Panel>
  );
}

// --- Activity ---------------------------------------------------------------
function ActivityTab({ leagueId, teamName }: { leagueId: number; teamName: Map<number, TeamOut> }) {
  const [tx, setTx] = useState<TransactionOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueActivity(leagueId).then(setTx).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!tx) return <Spinner />;
  if (tx.length === 0)
    return (
      <EmptyState
        title="No transactions recorded"
        hint="ESPN's mTransactions2 returned none for this league (see SPEC §2.4 open note)."
      />
    );

  const cols: ColumnDef<TransactionOut, any>[] = [
    { accessorKey: "week", header: "Wk", cell: (c) => <span className="mono text-secondary">{c.getValue<number | null>() ?? DASH}</span> },
    { id: "team", header: "Team", accessorFn: (t) => teamLabel(teamName, t.team_id), cell: (c) => teamLabel(teamName, c.row.original.team_id) },
    { accessorKey: "type", header: "Type", cell: (c) => <span className="mono text-secondary">{c.getValue<string | null>() ?? DASH}</span> },
    { accessorKey: "player_in", header: "In", cell: (c) => <span className="mono text-green">{c.getValue<number | null>() ?? ""}</span> },
    { accessorKey: "player_out", header: "Out", cell: (c) => <span className="mono text-muted">{c.getValue<number | null>() ?? ""}</span> },
    { accessorKey: "bid", header: "Bid", cell: (c) => <span className="mono">{c.getValue<number | null>() ?? DASH}</span> },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <DataTable data={tx} columns={cols} />
    </Panel>
  );
}

// --- AI Brief ---------------------------------------------------------------
function AiTab() {
  return (
    <EmptyState
      title="AI analysis — Phase 4"
      hint="League difficulty brief, exploit plan, and advantage verdict generate here once the Anthropic layer is wired."
    />
  );
}
