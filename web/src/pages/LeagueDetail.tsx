import type { ColumnDef } from "@tanstack/react-table";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  generateAdvantageVerdict,
  generateDraftRecaps,
  generateLeagueBrief,
  getAdvantageVerdict,
  getAiStatus,
  getDraftRecaps,
  getLeagueActivity,
  getLeagueAllPlay,
  getLeagueBrief,
  getLeagueDraft,
  getLeagueLineupEfficiency,
  getLeagueMatchups,
  getLeagueOverview,
  syncLeague,
  type AdvantageVerdictContent,
  type AiReportEnvelope,
  type AiStatus,
  type AllPlayOut,
  type DraftPickOut,
  type LineupEfficiencyOut,
  type DraftRecapContent,
  type LeagueBriefContent,
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
  PositionPill,
  SizePill,
  Spinner,
  ValueBar,
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

      {lg.last_sync_ok === false && (
        <div className="mt-4" data-testid="league-sync-failed-note">
          <WarningNote
            title="Last sync failed"
            messages={[lg.last_sync_error ?? "The most recent sync did not complete."]}
          />
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
        {tab === "teams" && <TeamsTab teams={ov.teams} leagueId={leagueId} myTeamId={lg.my_team_id} />}
        {tab === "matchups" && <MatchupsTab leagueId={leagueId} teamName={teamName} myTeamId={lg.my_team_id} />}
        {tab === "activity" && <ActivityTab leagueId={leagueId} teamName={teamName} />}
        {tab === "ai" && <AiTab leagueId={leagueId} />}
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
          {ov.components.length > 0 ? (
            <>
              <div className="text-[10px] uppercase tracking-wide text-muted">
                Components (within-league percentile)
              </div>
              <div className="mt-2 space-y-2">
                {ov.components.map((c) => (
                  <div key={c.key}>
                    <div className="flex items-baseline justify-between text-[11px]">
                      <span className="text-secondary">{c.label}</span>
                      <span className="mono text-muted">weight {Math.round(c.weight * 100)}%</span>
                    </div>
                    <ValueBar
                      value={c.percentile}
                      max={100}
                      label={`${num(c.percentile, 0)} pct`}
                    />
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-muted">
                Edge Score is the weighted mean of these percentiles. The fuller
                LeagueSoftness + MyEdge model (SPEC §6) is future work.
              </p>
            </>
          ) : (
            <EmptyState
              title={
                ov.league.lifecycle === "pre_draft"
                  ? "Edge Score pending — league hasn't drafted"
                  : "Edge Score pending — not enough data yet"
              }
              hint="Within-league v1 score; full LeagueSoftness + MyEdge breakdown (SPEC §6) is future."
            />
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
    {
      id: "player",
      header: "Player",
      accessorFn: (p) => p.player_name ?? (p.espn_player_id != null ? `#${p.espn_player_id}` : ""),
      cell: (c) => {
        const p = c.row.original;
        return (
          <span className="flex items-center gap-2">
            <PositionPill pos={p.player_position} />
            <span className="text-primary">
              {p.player_name ?? (p.espn_player_id != null ? `#${p.espn_player_id}` : DASH)}
            </span>
          </span>
        );
      },
    },
    { accessorKey: "adp_at_draft", header: "ADP", cell: (c) => <span className="mono text-secondary">{c.getValue<number | null>() == null ? DASH : num(c.getValue<number>())}</span> },
    { accessorKey: "keeper", header: "Keeper", cell: (c) => (c.getValue<boolean>() ? "K" : "") },
    { accessorKey: "autodraft", header: "Auto", cell: (c) => (c.getValue<boolean>() ? <span className="text-muted">auto</span> : "") },
    { id: "value", header: "Δ vs ADP", accessorFn: (p) => p.value_delta ?? 0, cell: (c) => <span className="mono text-muted">{c.row.original.value_delta == null ? DASH : num(c.row.original.value_delta)}</span> },
  ];
  return (
    <div className="space-y-5">
      <DraftRecaps leagueId={leagueId} />
      <Panel className="overflow-hidden p-1">
        <DataTable
          data={picks}
          columns={cols}
          initialSort={[{ id: "overall", desc: false }]}
          rowClassName={(p) => (myTeamId != null && p.team_id === myTeamId ? "bg-greenchip/40" : "")}
        />
        <p className="px-3 py-2 text-[11px] text-muted">
          ADP and Δ vs ADP (positive = drafted later than ADP) come from the ESPN player
          pool at last sync; blank when a player isn&apos;t in the pool.
        </p>
      </Panel>
    </div>
  );
}

// --- AI draft recaps (per team) --------------------------------------------
function DraftRecaps({ leagueId }: { leagueId: number }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [reports, setReports] = useState<DraftRecapContent[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getAiStatus().then(setStatus).catch(() => setStatus({ enabled: false, standard_model: "", bulk_model: "" }));
    getDraftRecaps(leagueId).then((r) => setReports(r.reports)).catch(() => setReports([]));
  }, [leagueId]);

  async function generate() {
    setBusy(true);
    setErr(null);
    try {
      const r = await generateDraftRecaps(leagueId, (reports?.length ?? 0) > 0);
      if (r.error) setErr(r.error);
      setReports(r.reports);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">AI draft recaps</h3>
        {status.enabled && (
          <Button variant={reports && reports.length ? "secondary" : "primary"} onClick={generate} disabled={busy}>
            {busy ? "Generating…" : reports && reports.length ? "Regenerate all" : "Generate all"}
          </Button>
        )}
      </div>
      {!status.enabled ? (
        <div className="mt-3"><AiKeyOff /></div>
      ) : err ? (
        <div className="mt-3"><ErrorNote message={err} /></div>
      ) : !reports || reports.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No recaps yet — click Generate all.</p>
      ) : (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {reports.map((r) => (
            <div key={r.espn_team_id} className="rounded-lg border border-line bg-row p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-primary">{r.team_name ?? `Team ${r.espn_team_id}`}</span>
                <GradePill grade={r.grade} />
              </div>
              <div className="mono mt-1 flex flex-wrap items-center gap-1 text-[11px] text-secondary">
                <span className="rounded bg-rowhover px-1.5 py-0.5">{r.strategy_label}</span>
                {r.secondary_label && (
                  <span className="rounded bg-rowhover px-1.5 py-0.5 text-muted">{r.secondary_label}</span>
                )}
                <span className="text-muted">· {r.confidence} confidence</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-secondary">{r.summary}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// --- Teams ------------------------------------------------------------------
function TeamsTab({
  teams,
  leagueId,
  myTeamId,
}: {
  teams: TeamOut[];
  leagueId: number;
  myTeamId: number | null;
}) {
  const cols: ColumnDef<TeamOut, any>[] = [
    { accessorKey: "name", header: "Team", cell: (c) => <span className={c.row.original.is_me ? "font-semibold text-primary" : "text-primary"}>{c.getValue<string>()}{c.row.original.is_me && <span className="text-red"> ·me</span>}</span> },
    { accessorKey: "abbrev", header: "Abbr", cell: (c) => <span className="mono text-muted">{c.getValue<string | null>() ?? DASH}</span> },
    { id: "record", header: "W-L-T", accessorFn: (t) => t.wins, cell: (c) => <span className="mono">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "points_for", header: "PF", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "standing", header: "Standing", cell: (c) => <span className="mono">{ordinal(c.getValue<number | null>())}</span> },
    { id: "strength", header: "Roster str.", cell: () => <span className="mono text-muted">{DASH}</span> },
  ];
  return (
    <div className="space-y-5">
      <Panel className="overflow-hidden p-1">
        <DataTable data={teams} columns={cols} rowClassName={(t) => (t.is_me ? "bg-greenchip/40" : "")} />
        <p className="px-3 py-2 text-[11px] text-muted">Roster strength (ADP/projection based) computes in Phase 3.</p>
      </Panel>
      <LineupEfficiencyTable leagueId={leagueId} myTeamId={myTeamId} />
    </div>
  );
}

// Lineup efficiency: started vs optimal points (Phase 13). Backend-computed; React formats.
function LineupEfficiencyTable({ leagueId, myTeamId }: { leagueId: number; myTeamId: number | null }) {
  const [rows, setRows] = useState<LineupEfficiencyOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueLineupEfficiency(leagueId).then(setRows).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!rows) return <Spinner />;
  if (rows.length === 0)
    return (
      <EmptyState
        title="No lineup efficiency yet"
        hint="Started-vs-optimal efficiency appears once a regular-season week completes."
      />
    );

  const cols: ColumnDef<LineupEfficiencyOut, any>[] = [
    { accessorKey: "team_name", header: "Team", cell: (c) => <span className="text-primary">{c.getValue<string | null>() ?? DASH}</span> },
    { accessorKey: "lineup_efficiency", header: "Efficiency", cell: (c) => <span className="mono text-green">{`${(c.getValue<number>() * 100).toFixed(1)}%`}</span> },
    { accessorKey: "started_points_avg", header: "Started/wk", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "optimal_points_avg", header: "Optimal/wk", cell: (c) => <span className="mono text-secondary">{num(c.getValue<number>())}</span> },
    { accessorKey: "points_left_on_bench_avg", header: "Left on bench/wk", cell: (c) => <span className="mono text-muted">{num(c.getValue<number>())}</span> },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <div className="border-b border-line px-3 py-2 text-xs uppercase tracking-wide text-muted">
        Lineup efficiency
      </div>
      <DataTable
        data={rows}
        columns={cols}
        initialSort={[{ id: "lineup_efficiency", desc: true }]}
        rowClassName={(r) => (myTeamId != null && r.team_id === myTeamId ? "bg-greenchip/40" : "")}
      />
      <p className="px-3 py-2 text-[11px] text-muted">
        Efficiency = started points ÷ best legal lineup from that week&apos;s roster, averaged
        (points-weighted) over completed weeks.
      </p>
    </Panel>
  );
}

// --- Matchups ---------------------------------------------------------------
function MatchupsTab({
  leagueId,
  teamName,
  myTeamId,
}: {
  leagueId: number;
  teamName: Map<number, TeamOut>;
  myTeamId: number | null;
}) {
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
    <div className="space-y-5">
      <AllPlayTable leagueId={leagueId} myTeamId={myTeamId} />
      <Panel className="overflow-hidden p-1">
        <div className="border-b border-line px-3 py-2 text-xs uppercase tracking-wide text-muted">
          Matchup schedule
        </div>
        <DataTable data={played} columns={cols} initialSort={[{ id: "week", desc: false }]} />
      </Panel>
    </div>
  );
}

// All-play record + luck delta (Phase 12). All numbers are backend-computed; the component
// only formats them (percent, signed luck) — no analytics math here.
function AllPlayTable({ leagueId, myTeamId }: { leagueId: number; myTeamId: number | null }) {
  const [rows, setRows] = useState<AllPlayOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueAllPlay(leagueId).then(setRows).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!rows) return <Spinner />;
  if (rows.length === 0)
    return (
      <EmptyState
        title="No all-play sample yet"
        hint="All-play records and luck deltas appear once a regular-season week completes."
      />
    );

  const pct = (v: number) => `${(v * 100).toFixed(0)}%`;
  const luck = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}`;
  const cols: ColumnDef<AllPlayOut, any>[] = [
    { accessorKey: "team_name", header: "Team", cell: (c) => <span className="text-primary">{c.getValue<string | null>() ?? DASH}</span> },
    { id: "record", header: "W-L-T", accessorFn: (r) => r.wins, cell: (c) => <span className="mono">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "win_pct", header: "Win%", cell: (c) => <span className="mono text-secondary">{pct(c.getValue<number>())}</span> },
    { id: "ap_record", header: "All-play", accessorFn: (r) => r.all_play_win_pct, cell: (c) => <span className="mono">{record(c.row.original.all_play_wins, c.row.original.all_play_losses, c.row.original.all_play_ties)}</span> },
    { accessorKey: "all_play_win_pct", header: "AP Win%", cell: (c) => <span className="mono text-secondary">{pct(c.getValue<number>())}</span> },
    {
      accessorKey: "luck_delta",
      header: "Luck",
      cell: (c) => {
        const v = c.getValue<number>();
        return <span className={`mono ${v > 0 ? "text-green" : v < 0 ? "text-red" : "text-muted"}`}>{luck(v)}</span>;
      },
    },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <div className="border-b border-line px-3 py-2 text-xs uppercase tracking-wide text-muted">
        All-play &amp; luck
      </div>
      <DataTable
        data={rows}
        columns={cols}
        initialSort={[{ id: "all_play_win_pct", desc: true }]}
        rowClassName={(r) => (myTeamId != null && r.team_id === myTeamId ? "bg-greenchip/40" : "")}
      />
      <p className="px-3 py-2 text-[11px] text-muted">
        All-play scores each team against every other team every completed week. Luck = all-play
        win% − actual win% (in points); positive means better scoring than the record shows.
      </p>
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
function AiKeyOff() {
  return (
    <EmptyState
      title="AI analysis is off"
      hint={
        <>
          Set <code className="mono">ANTHROPIC_API_KEY</code> in <code className="mono">.env</code> and
          restart the API to enable draft recaps, league briefs, and advantage verdicts.
        </>
      }
    />
  );
}

function AiTab({ leagueId }: { leagueId: number }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [brief, setBrief] = useState<AiReportEnvelope<LeagueBriefContent> | null>(null);
  const [verdict, setVerdict] = useState<AiReportEnvelope<AdvantageVerdictContent> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getAiStatus().then(setStatus).catch((e) => setErr(String(e)));
    getLeagueBrief(leagueId).then(setBrief).catch(() => {});
    getAdvantageVerdict(leagueId).then(setVerdict).catch(() => {});
  }, [leagueId]);

  if (err) return <ErrorNote message={err} />;
  if (!status) return <Spinner />;
  if (!status.enabled) return <AiKeyOff />;

  async function generate(which: "brief" | "verdict") {
    setBusy(which);
    setErr(null);
    try {
      if (which === "brief") setBrief(await generateLeagueBrief(leagueId, !!brief?.content));
      else setVerdict(await generateAdvantageVerdict(leagueId, !!verdict?.content));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <AiCard
        title="League difficulty brief"
        env={brief}
        busy={busy === "brief"}
        onGenerate={() => generate("brief")}
      >
        {brief?.content && (
          <div>
            <span className="mono rounded bg-rowhover px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-gold">
              {brief.content.difficulty_tier}
            </span>
            <p className="mt-2 text-sm leading-relaxed text-secondary">{brief.content.narrative}</p>
            <div className="mt-2 text-xs uppercase tracking-wide text-muted">Exploit plan</div>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-secondary">
              {brief.content.exploit_plan.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        )}
      </AiCard>

      <AiCard
        title="My advantage verdict"
        env={verdict}
        busy={busy === "verdict"}
        onGenerate={() => generate("verdict")}
      >
        {verdict?.content && (
          <div>
            <span className="mono rounded bg-rowhover px-2 py-0.5 text-[11px] uppercase tracking-wide text-primary">
              {verdict.content.verdict_label}
            </span>
            <p className="mt-2 text-sm leading-relaxed text-secondary">{verdict.content.paragraph}</p>
            <div className="mt-2 text-xs uppercase tracking-wide text-muted">Highest-leverage move</div>
            <p className="mt-1 text-sm text-green">{verdict.content.highest_leverage_move}</p>
          </div>
        )}
      </AiCard>
    </div>
  );
}

function AiCard({
  title,
  env,
  busy,
  onGenerate,
  children,
}: {
  title: string;
  env: AiReportEnvelope<unknown> | null;
  busy: boolean;
  onGenerate: () => void;
  children?: ReactNode;
}) {
  const has = !!env?.content;
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <div className="flex items-center gap-2">
          {has && env?.stale && (
            <span className="mono text-[10px] uppercase tracking-wide text-gold">inputs changed</span>
          )}
          <Button variant={has ? "secondary" : "primary"} onClick={onGenerate} disabled={busy}>
            {busy ? "Generating…" : has ? "Regenerate" : "Generate"}
          </Button>
        </div>
      </div>
      {env?.error && <div className="mt-2"><ErrorNote message={env.error} /></div>}
      <div className="mt-3">{has ? children : <p className="text-sm text-muted">Not generated yet.</p>}</div>
      {has && env?.model && (
        <div className="mono mt-3 text-[10px] text-muted">
          {env.model}
          {env.created_at ? ` · ${relTime(env.created_at)}` : ""}
        </div>
      )}
    </Panel>
  );
}
