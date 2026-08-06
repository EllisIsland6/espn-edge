import type { ColumnDef } from "@tanstack/react-table";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
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
  getLeagueEdgeIndex,
  getLeagueLineupEfficiency,
  getLeagueMatchups,
  getLeagueMyEdge,
  getLeagueOverview,
  getLeagueSoftness,
  getLeagueTeams,
  getWeeklyRecap,
  generateWeeklyRecap,
  getTradeFinder,
  generateTradeFinder,
  syncLeague,
  type AdvantageVerdictContent,
  type AiReportEnvelope,
  type AiStatus,
  type AllPlayOut,
  type DraftPickOut,
  type EdgeIndexOut,
  type LeagueSoftnessOut,
  type LineupEfficiencyOut,
  type MyEdgeOut,
  type DraftRecapContent,
  type LeagueBriefContent,
  type LeagueOverview,
  type MatchupOut,
  type PlayerReference,
  type TeamOut,
  type TradeFinderContent,
  type TransactionOut,
  type WeeklyRecapContent,
} from "../api";
import { DataTable } from "../components/DataTable";
import {
  AchievementRow,
  ContentReveal,
  MomentumBadges,
  ScoreRing,
} from "../components/Gamification";
import { PlayerIdentity, PlayerMentionText } from "../components/PlayerIdentity";
import { TeamDetailLink } from "../components/TeamDetailLink";
import { TeamIdentity, TeamSelect } from "../components/TeamIdentity";
import {
  Button,
  EmptyState,
  ErrorNote,
  GradePill,
  LifecycleBadge,
  Panel,
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
const TAB_KEYS = new Set<Tab>(TABS.map((tab) => tab.key));

function tabFromSearch(value: string | null): Tab {
  return value && TAB_KEYS.has(value as Tab) ? (value as Tab) : "overview";
}

function leagueReturnTo(leagueId: number, tab: Tab): string {
  return `/league/${leagueId}?tab=${tab}`;
}

function LinkedTeamIdentity({
  leagueId,
  tab,
  team,
  size = "xs",
  showMe = true,
  fallback = "Unknown team",
}: {
  leagueId: number;
  tab: Tab;
  team: TeamOut | null;
  size?: "xs" | "sm" | "md" | "lg";
  showMe?: boolean;
  fallback?: string;
}) {
  return team ? (
    <TeamDetailLink
      leagueId={leagueId}
      team={team}
      returnTo={leagueReturnTo(leagueId, tab)}
      size={size}
      showMe={showMe}
      fallback={fallback}
    />
  ) : (
    <TeamIdentity team={null} size={size} showMe={showMe} fallback={fallback} />
  );
}

function playerReference(
  espnPlayerId: number | null,
  name: string | null,
  position: string | null,
): PlayerReference {
  return {
    espn_player_id: espnPlayerId,
    name: name ?? (espnPlayerId != null ? `#${espnPlayerId}` : "Unknown player"),
    position,
  };
}

function uniquePlayerReferences(players: PlayerReference[]): PlayerReference[] {
  return Array.from(
    new Map(
      players.map((player) => [
        player.espn_player_id != null ? `id:${player.espn_player_id}` : `name:${player.name}`,
        player,
      ]),
    ).values(),
  );
}

function transactionPlayerReferences(transactions: TransactionOut[]): PlayerReference[] {
  return uniquePlayerReferences(
    transactions.flatMap((transaction) => {
      const players: PlayerReference[] = [];
      if (transaction.player_in != null || transaction.player_in_name) {
        players.push(
          playerReference(
            transaction.player_in,
            transaction.player_in_name,
            transaction.player_in_position,
          ),
        );
      }
      if (transaction.player_out != null || transaction.player_out_name) {
        players.push(
          playerReference(
            transaction.player_out,
            transaction.player_out_name,
            transaction.player_out_position,
          ),
        );
      }
      return players;
    }),
  );
}

export default function LeagueDetail() {
  const { id } = useParams();
  const leagueId = Number(id);
  const [searchParams, setSearchParams] = useSearchParams();
  const [ov, setOv] = useState<LeagueOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tab = tabFromSearch(searchParams.get("tab"));
  const [syncing, setSyncing] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const seenAiRevealTokens = useRef(new Set<string>());

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

  function selectTab(next: Tab) {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set("tab", next);
    setSearchParams(nextParams);
  }

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
  const myTeam = ov.teams.find((team) => team.id === lg.my_team_id || team.is_me) ?? null;
  return (
    <div>
      <div className="flex items-center gap-3">
        <Link to="/" className="mono text-[10px] text-secondary transition-colors duration-150 hover:text-ice">
          ← Portfolio
        </Link>
      </div>
      <div className="mt-4">
        <h1 className="display-face text-2xl font-bold tracking-tight text-frost">
          <LinkedTeamIdentity
            leagueId={leagueId}
            tab={tab}
            team={myTeam}
            size="lg"
            fallback="Team not detected"
          />
        </h1>
        <div className="mt-1 flex flex-wrap items-center gap-2 pl-16">
          <span className="text-sm text-icesoft">{lg.name ?? `League ${lg.espn_league_id}`}</span>
          <SizePill size={lg.size} />
          <LifecycleBadge lifecycle={lg.lifecycle} label={LIFECYCLE_LABEL[lg.lifecycle] ?? lg.lifecycle} />
          {ov.scoring && (
            <span className="mono rounded bg-rowhover px-1.5 py-0.5 text-[11px] text-secondary">
              {ov.scoring}
            </span>
          )}
        </div>
        <div className="mono mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 pl-16 text-[9px] uppercase tracking-wider text-muted">
          <span>{ov.account_label ?? "public"}</span>
          <span>· {lg.season}</span>
          <span>· synced {relTime(lg.last_synced_at)}</span>
          <Button variant="sync" onClick={sync} disabled={syncing}>
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

      <div className="mt-6 flex flex-nowrap gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => selectTab(t.key)}
            className={`display-face -mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-semibold tracking-wide transition-colors duration-150 ${
              tab === t.key
                ? "border-red text-primary"
                : "border-transparent text-secondary hover:text-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-4">
        {tab === "overview" && <OverviewTab ov={ov} leagueId={leagueId} />}
        {tab === "draft" && <DraftTab leagueId={leagueId} teamName={teamName} myTeamId={lg.my_team_id} />}
        {tab === "teams" && <TeamsTab teams={ov.teams} leagueId={leagueId} myTeamId={lg.my_team_id} />}
        {tab === "matchups" && <MatchupsTab leagueId={leagueId} teamName={teamName} myTeamId={lg.my_team_id} />}
        {tab === "activity" && <ActivityTab leagueId={leagueId} teamName={teamName} />}
        {tab === "ai" && (
          <AiTab leagueId={leagueId} seenRevealTokens={seenAiRevealTokens.current} />
        )}
      </div>
    </div>
  );
}

function teamLabel(m: Map<number, TeamOut>, id: number | null): string {
  if (id == null) return DASH;
  return m.get(id)?.name ?? `#${id}`;
}

function teamForId(m: Map<number, TeamOut>, id: number | null): TeamOut | null {
  return id == null ? null : (m.get(id) ?? null);
}

function teamForEspnId(m: Map<number, TeamOut>, espnTeamId: number): TeamOut | null {
  return [...m.values()].find((team) => team.espn_team_id === espnTeamId) ?? null;
}

// --- Overview: standings + Edge breakdown + MyEdge panel --------------------
function OverviewTab({ ov, leagueId }: { ov: LeagueOverview; leagueId: number }) {
  const cols: ColumnDef<TeamOut, any>[] = [
    { accessorKey: "standing", header: "#", cell: (c) => <span className="mono text-secondary">{ordinal(c.getValue<number | null>())}</span> },
    {
      accessorKey: "name",
      header: "Team",
      cell: (c) => (
        <div>
          <LinkedTeamIdentity
            leagueId={leagueId}
            tab="overview"
            team={c.row.original}
          />
          {c.row.original.autodrafted && <span className="ml-9 text-[10px] text-muted">auto</span>}
        </div>
      ),
    },
    { id: "record", header: "W-L-T", accessorFn: (t) => t.wins, cell: (c) => <span className="mono whitespace-nowrap">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "points_for", header: "PF", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "points_against", header: "PA", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
  ];
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-4">
        <Panel className="overflow-hidden">
          <div className="mono border-b border-line px-4 py-3 text-[9px] uppercase tracking-[0.16em] text-muted">
            Standings
          </div>
          <div className="p-1">
            <DataTable
              data={ov.teams}
              columns={cols}
              ariaLabel="League standings"
              initialSort={[{ id: "standing", desc: false }]}
              rowClassName={(t) => (t.is_me ? "bg-highlight" : "")}
              columnClassName={(columnId) =>
                columnId === "points_for" || columnId === "points_against"
                  ? "hidden sm:table-cell"
                  : ""
              }
            />
          </div>
          <div className="flex items-center gap-2 border-t border-line bg-icechip/45 px-4 py-2">
            <span className="mono text-[8px] uppercase tracking-[0.16em] text-ice">Next up</span>
            <span className="truncate text-[10px] text-icesoft">
              Standings and scoring context update on the next successful sync.
            </span>
          </div>
        </Panel>
        <div className="grid gap-4 sm:grid-cols-2">
          <MyEdgePanel leagueId={leagueId} />
          <LeagueSoftnessPanel leagueId={leagueId} />
        </div>
      </div>
      <aside className="space-y-4">
        <EdgeIndexPanel leagueId={leagueId} />
        <LegacyEdgePanel ov={ov} />
      </aside>
    </div>
  );
}

function LegacyEdgePanel({ ov }: { ov: LeagueOverview }) {
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between">
        <h3 className="display-face text-sm font-bold">
          Edge Score <span className="mono text-[8px] uppercase tracking-wide text-muted">legacy</span>
        </h3>
        {ov.verdict && (
          <span className="mono text-[8px] uppercase tracking-[0.14em] text-secondary">{ov.verdict}</span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-4">
        <ScoreRing
          value={ov.edge_score}
          grade={ov.grade}
          label="Edge Score"
          size="sm"
          testId="overview-score-ring"
        />
        <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
          <MomentumBadges momentum={ov.momentum} testId="overview-momentum" />
          <AchievementRow achievements={ov.achievements} testId="overview-achievements" />
          <ValueChip
            primary={ov.playoff_odds == null ? DASH : `${Math.round(ov.playoff_odds * 100)}%`}
            secondary="Playoff odds"
            muted={ov.playoff_odds == null}
          />
        </div>
      </div>
      <div className="mt-4">
        {ov.components.length > 0 ? (
          <>
            <div className="mono text-[8px] uppercase tracking-[0.14em] text-muted">
              Components (within-league percentile)
            </div>
            <div className="mt-2 space-y-2.5">
              {ov.components.map((c) => (
                <div key={c.key}>
                  <div className="flex items-baseline justify-between text-[10px]">
                    <span className="text-secondary">{c.label}</span>
                    <span className="mono text-muted">weight {Math.round(c.weight * 100)}%</span>
                  </div>
                  <ValueBar
                    value={c.percentile}
                    max={100}
                    label={`${num(c.percentile, 0)} pct`}
                    tone="ice"
                  />
                </div>
              ))}
            </div>
          </>
        ) : (
          <EmptyState
            title={
              ov.league.lifecycle === "pre_draft"
                ? "Edge Score pending — league hasn't drafted"
                : "Edge Score pending — not enough data yet"
            }
            hint="Appears when within-league scoring has enough data."
          />
        )}
      </div>
    </Panel>
  );
}

// Full Edge Index v1 (Phase 16): my team's 0.5·MyEdge + 0.5·LeagueSoftness composite.
function EdgeIndexPanel({ leagueId }: { leagueId: number }) {
  const [rows, setRows] = useState<EdgeIndexOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueEdgeIndex(leagueId).then(setRows).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!rows) return <Spinner />;
  const mine = rows.find((r) => r.is_me) ?? null;
  return (
    <Panel className="overflow-hidden border-mint/35 bg-coldpanel/55">
      <div className="flex items-center justify-between border-b border-coldline px-4 py-3">
        <h3 className="display-face text-sm font-bold text-frost">
          Edge Index <span className="mono text-[8px] uppercase tracking-wide text-muted">v1</span>
        </h3>
        <span className="mono rounded-full border border-mint/35 bg-mintchip px-2 py-1 text-[8px] uppercase tracking-[0.14em] text-mint">
          advanced
        </span>
      </div>
      {mine ? (
        <div className="p-4">
          <div className="flex items-center gap-4">
            <ScoreRing
              value={mine.edge_index_score}
              grade={mine.grade}
              label="Composite"
            />
            <div className="flex min-w-0 flex-1 flex-col items-start gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <GradePill grade={mine.grade} />
                {mine.verdict && (
                  <span className="mono text-[9px] uppercase tracking-[0.12em] text-mint">{mine.verdict}</span>
                )}
              </div>
              <span className="mono rounded border border-mint/30 bg-mintchip px-2 py-1 text-[8px] uppercase tracking-[0.14em] text-mint">
                Edge Index
              </span>
            </div>
          </div>
          <div className="mt-4 space-y-2.5">
            {mine.components.map((c) => (
              <div key={c.key}>
                <div className="flex items-baseline justify-between text-[10px]">
                  <span className="text-secondary">{c.label}</span>
                  <span className="mono text-muted">weight {Math.round(c.weight * 100)}%</span>
                </div>
                <ValueBar value={c.value} max={100} label={`${num(c.value, 0)} pct`} tone="mint" />
              </div>
            ))}
          </div>
          <p className="mt-3 border-t border-coldline pt-3 text-[10px] leading-relaxed text-muted">
            0.5 × MyEdge + 0.5 × LeagueSoftness. This is the primary Portfolio ranking.
          </p>
        </div>
      ) : (
        <div className="p-4">
          <EmptyState
            title="Edge Index pending"
            hint="Appears once MyEdge or LeagueSoftness has enough data."
          />
        </div>
      )}
    </Panel>
  );
}

// MyEdge v1 (Phase 14): my team's blended score + component bars. Backend-computed.
function MyEdgePanel({ leagueId }: { leagueId: number }) {
  const [rows, setRows] = useState<MyEdgeOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueMyEdge(leagueId).then(setRows).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!rows) return <Spinner />;
  const mine = rows.find((r) => r.is_me) ?? null;
  return (
    <Panel className="min-h-full p-4">
      <div className="flex items-center justify-between">
        <h3 className="display-face text-sm font-bold">
          MyEdge <span className="mono text-[8px] uppercase tracking-wide text-muted">v1</span>
        </h3>
        {mine && <ValueChip primary={num(mine.my_edge_score, 0)} secondary="MyEdge" />}
      </div>
      {mine ? (
        <div className="mt-3 space-y-2">
          {mine.components.map((c) => (
            <div key={c.key}>
              <div className="flex items-baseline justify-between text-[10px]">
                <span className="text-secondary">{c.label}</span>
                <span className="mono text-muted">weight {Math.round(c.weight * 100)}%</span>
              </div>
              <ValueBar value={c.percentile} max={100} label={`${num(c.percentile, 0)} pct`} tone="ice" />
            </div>
          ))}
          <p className="mt-3 text-[11px] leading-relaxed text-muted">
            MyEdge blends roster strength, draft surplus, lineup efficiency, and luck-adjusted
            record (SPEC §6.2). A separate v1 score — it doesn&apos;t change the Edge Score above.
          </p>
        </div>
      ) : (
        <div className="mt-3">
          <EmptyState
            title="MyEdge pending"
            hint="Appears once your team has enough drafted/played data for its components."
          />
        </div>
      )}
    </Panel>
  );
}

// LeagueSoftness v1 (Phase 15): how exploitable my team's opponents are. Backend-computed.
function LeagueSoftnessPanel({ leagueId }: { leagueId: number }) {
  const [rows, setRows] = useState<LeagueSoftnessOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    getLeagueSoftness(leagueId).then(setRows).catch((e) => setErr(String(e)));
  }, [leagueId]);
  if (err) return <ErrorNote message={err} />;
  if (!rows) return <Spinner />;
  const mine = rows.find((r) => r.is_me) ?? null;
  return (
    <Panel className="min-h-full p-4">
      <div className="flex items-center justify-between">
        <h3 className="display-face text-sm font-bold">
          LeagueSoftness <span className="mono text-[8px] uppercase tracking-wide text-muted">v1</span>
        </h3>
        {mine && <ValueChip primary={num(mine.league_softness_score, 0)} secondary="Softness" />}
      </div>
      {mine ? (
        <div className="mt-3 space-y-2">
          {mine.components.map((c) => (
            <div key={c.key}>
              <div className="flex items-baseline justify-between text-[10px]">
                <span className="text-secondary">{c.label}</span>
                <span className="mono text-muted">weight {Math.round(c.weight * 100)}%</span>
              </div>
              <ValueBar value={c.percentile} max={100} label={`${num(c.percentile, 0)} pct`} tone="ash" />
            </div>
          ))}
          <p className="mt-3 text-[11px] leading-relaxed text-muted">
            How exploitable your opponents are (SPEC §6.1) — higher is softer. A separate v1
            score; it doesn&apos;t change the Edge Score above.
          </p>
        </div>
      ) : (
        <div className="mt-3">
          <EmptyState
            title="LeagueSoftness pending"
            hint="Appears once opponents have enough drafted/played data for its components."
          />
        </div>
      )}
    </Panel>
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
    {
      id: "team",
      header: "Team",
      accessorFn: (p) => teamLabel(teamName, p.team_id),
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="draft"
          team={teamForId(teamName, c.row.original.team_id)}
        />
      ),
    },
    {
      id: "player",
      header: "Player",
      accessorFn: (p) => p.player_name ?? (p.espn_player_id != null ? `#${p.espn_player_id}` : ""),
      cell: (c) => {
        const p = c.row.original;
        return (
          <PlayerIdentity
            player={playerReference(p.espn_player_id, p.player_name, p.player_position)}
          />
        );
      },
    },
    { accessorKey: "adp_at_draft", header: "ADP", cell: (c) => <span className="mono text-secondary">{c.getValue<number | null>() == null ? DASH : num(c.getValue<number>())}</span> },
    { accessorKey: "keeper", header: "Keeper", cell: (c) => (c.getValue<boolean>() ? "K" : "") },
    { accessorKey: "autodraft", header: "Auto", cell: (c) => (c.getValue<boolean>() ? <span className="text-muted">auto</span> : "") },
    { id: "value", header: "Δ vs ADP", accessorFn: (p) => p.value_delta ?? 0, cell: (c) => <span className="mono text-muted">{c.row.original.value_delta == null ? DASH : num(c.row.original.value_delta)}</span> },
  ];
  const players = uniquePlayerReferences(
    picks.map((pick) => playerReference(pick.espn_player_id, pick.player_name, pick.player_position)),
  );
  return (
    <div className="space-y-5">
      <DraftRecaps leagueId={leagueId} players={players} teams={teamName} />
      <Panel className="overflow-hidden p-1">
        <DataTable
          data={picks}
          columns={cols}
          ariaLabel="Draft board"
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
function DraftRecaps({
  leagueId,
  players,
  teams,
}: {
  leagueId: number;
  players: PlayerReference[];
  teams: Map<number, TeamOut>;
}) {
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
        <div className="mt-3">
          <div
            className="flex gap-2 overflow-x-auto border-y border-line py-2"
            data-testid="draft-grade-strip"
            aria-label="Draft grade summary"
          >
            {reports.map((r) => (
              <div
                key={r.espn_team_id}
                className="flex shrink-0 items-center gap-2 border-r border-line pr-2 last:border-r-0"
                data-testid={`draft-grade-strip-${r.espn_team_id}`}
              >
                <LinkedTeamIdentity
                  leagueId={leagueId}
                  tab="draft"
                  team={teamForEspnId(teams, r.espn_team_id)}
                  size="xs"
                  showMe={false}
                  fallback={r.team_name ?? `Team ${r.espn_team_id}`}
                />
                <GradePill grade={r.grade} />
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {reports.map((r) => (
              <div
                key={r.espn_team_id}
                className="rounded-lg border border-line bg-row p-3"
                data-testid={`draft-recap-card-${r.espn_team_id}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <LinkedTeamIdentity
                    leagueId={leagueId}
                    tab="draft"
                    team={teamForEspnId(teams, r.espn_team_id)}
                    size="xs"
                    fallback={r.team_name ?? `Team ${r.espn_team_id}`}
                  />
                  <GradePill grade={r.grade} />
                </div>
                <div className="mono mt-1 flex flex-wrap items-center gap-1 text-[11px] text-secondary">
                  <span className="rounded bg-rowhover px-1.5 py-0.5">{r.strategy_label}</span>
                  {r.secondary_label && (
                    <span className="rounded bg-rowhover px-1.5 py-0.5 text-muted">{r.secondary_label}</span>
                  )}
                  <span className="text-muted">· {r.confidence} confidence</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-secondary">
                  <PlayerMentionText text={r.summary} players={players} />
                </p>
              </div>
            ))}
          </div>
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
  const teamName = new Map(teams.map((team) => [team.id, team]));
  const cols: ColumnDef<TeamOut, any>[] = [
    {
      accessorKey: "name",
      header: "Team",
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="teams"
          team={c.row.original}
        />
      ),
    },
    { accessorKey: "abbrev", header: "Abbr", cell: (c) => <span className="mono text-muted">{c.getValue<string | null>() ?? DASH}</span> },
    { id: "record", header: "W-L-T", accessorFn: (t) => t.wins, cell: (c) => <span className="mono">{record(c.row.original.wins, c.row.original.losses, c.row.original.ties)}</span> },
    { accessorKey: "points_for", header: "PF", cell: (c) => <span className="mono">{num(c.getValue<number>())}</span> },
    { accessorKey: "standing", header: "Standing", cell: (c) => <span className="mono">{ordinal(c.getValue<number | null>())}</span> },
    { id: "strength", header: "Roster str.", cell: () => <span className="mono text-muted">{DASH}</span> },
  ];
  return (
    <div className="space-y-5">
      <Panel className="overflow-hidden p-1">
        <DataTable
          data={teams}
          columns={cols}
          ariaLabel="League teams"
          rowClassName={(t) => (t.is_me ? "bg-greenchip/40" : "")}
        />
        <p className="px-3 py-2 text-[11px] text-muted">Roster strength (ADP/projection based) computes in Phase 3.</p>
      </Panel>
      <LineupEfficiencyTable leagueId={leagueId} myTeamId={myTeamId} teamName={teamName} />
    </div>
  );
}

// Lineup efficiency: started vs optimal points (Phase 13). Backend-computed; React formats.
function LineupEfficiencyTable({
  leagueId,
  myTeamId,
  teamName,
}: {
  leagueId: number;
  myTeamId: number | null;
  teamName: Map<number, TeamOut>;
}) {
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
    {
      accessorKey: "team_name",
      header: "Team",
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="teams"
          team={teamForId(teamName, c.row.original.team_id)}
          fallback={c.getValue<string | null>() ?? DASH}
        />
      ),
    },
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
        ariaLabel="Lineup efficiency"
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
    {
      id: "home",
      header: "Home",
      accessorFn: (m) => teamLabel(teamName, m.home_team_id),
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="matchups"
          team={teamForId(teamName, c.row.original.home_team_id)}
        />
      ),
    },
    { accessorKey: "home_points", header: "HPts", cell: (c) => <span className="mono">{num(c.getValue<number | null>())}</span> },
    { accessorKey: "away_points", header: "APts", cell: (c) => <span className="mono">{num(c.getValue<number | null>())}</span> },
    {
      id: "away",
      header: "Away",
      accessorFn: (m) => teamLabel(teamName, m.away_team_id),
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="matchups"
          team={teamForId(teamName, c.row.original.away_team_id)}
        />
      ),
    },
    { accessorKey: "is_playoff", header: "PO", cell: (c) => (c.getValue<boolean>() ? <span className="text-gold">●</span> : "") },
  ];
  return (
    <div className="space-y-5">
      <AllPlayTable leagueId={leagueId} myTeamId={myTeamId} teamName={teamName} />
      <Panel className="overflow-hidden p-1">
        <div className="border-b border-line px-3 py-2 text-xs uppercase tracking-wide text-muted">
          Matchup schedule
        </div>
        <DataTable
          data={played}
          columns={cols}
          ariaLabel="Matchup schedule"
          initialSort={[{ id: "week", desc: false }]}
        />
      </Panel>
    </div>
  );
}

// All-play record + luck delta (Phase 12). All numbers are backend-computed; the component
// only formats them (percent, signed luck) — no analytics math here.
function AllPlayTable({
  leagueId,
  myTeamId,
  teamName,
}: {
  leagueId: number;
  myTeamId: number | null;
  teamName: Map<number, TeamOut>;
}) {
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
    {
      accessorKey: "team_name",
      header: "Team",
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="matchups"
          team={teamForId(teamName, c.row.original.team_id)}
          fallback={c.getValue<string | null>() ?? DASH}
        />
      ),
    },
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
        ariaLabel="All-play standings"
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
    {
      id: "team",
      header: "Team",
      accessorFn: (t) => teamLabel(teamName, t.team_id),
      cell: (c) => (
        <LinkedTeamIdentity
          leagueId={leagueId}
          tab="activity"
          team={teamForId(teamName, c.row.original.team_id)}
        />
      ),
    },
    { accessorKey: "type", header: "Type", cell: (c) => <span className="mono text-secondary">{c.getValue<string | null>() ?? DASH}</span> },
    {
      id: "player_in",
      header: "In",
      accessorFn: (transaction) => transaction.player_in_name ?? transaction.player_in ?? "",
      cell: (c) => {
        const transaction = c.row.original;
        if (transaction.player_in == null && !transaction.player_in_name) return null;
        return (
          <PlayerIdentity
            player={playerReference(
              transaction.player_in,
              transaction.player_in_name,
              transaction.player_in_position,
            )}
            tone="text-green"
          />
        );
      },
    },
    {
      id: "player_out",
      header: "Out",
      accessorFn: (transaction) => transaction.player_out_name ?? transaction.player_out ?? "",
      cell: (c) => {
        const transaction = c.row.original;
        if (transaction.player_out == null && !transaction.player_out_name) return null;
        return (
          <PlayerIdentity
            player={playerReference(
              transaction.player_out,
              transaction.player_out_name,
              transaction.player_out_position,
            )}
            tone="text-muted"
          />
        );
      },
    },
    { accessorKey: "bid", header: "Bid", cell: (c) => <span className="mono">{c.getValue<number | null>() ?? DASH}</span> },
  ];
  return (
    <Panel className="overflow-hidden p-1">
      <DataTable data={tx} columns={cols} ariaLabel="League activity" />
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

function AiTab({
  leagueId,
  seenRevealTokens,
}: {
  leagueId: number;
  seenRevealTokens: Set<string>;
}) {
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
        revealScope={`${leagueId}:brief`}
        seenRevealTokens={seenRevealTokens}
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
        revealScope={`${leagueId}:verdict`}
        seenRevealTokens={seenRevealTokens}
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

      <WeeklyRecapCard leagueId={leagueId} />
      <TradeFinderCard leagueId={leagueId} />
    </div>
  );
}

// Roster-snapshot provenance chip for the Trade Finder (Phase 22). Backend-provided values.
function TradeProvenance({ c }: { c: TradeFinderContent }) {
  const chips: { text: string; tone: string }[] = [];
  if (c.grounding_source === "lineup_snapshot") {
    chips.push({
      text: c.snapshot_week != null ? `Week ${c.snapshot_week} roster snapshot` : "Roster snapshot",
      tone: "text-secondary",
    });
    if (c.snapshot_stale) chips.push({ text: "older snapshot", tone: "text-gold" });
  } else if (c.grounding_source === "drafted_roster") {
    chips.push({ text: "Drafted-roster fallback", tone: "text-gold" });
  } else if (c.grounding_source === "none") {
    chips.push({ text: "No roster data", tone: "text-red" });
  }
  if (c.projections_stale) chips.push({ text: "projections unconfirmed", tone: "text-gold" });
  if (typeof c.my_projection_coverage === "number" && c.my_projection_coverage < 0.5) {
    chips.push({ text: "low projection coverage", tone: "text-muted" });
  }
  return (
    <>
      {chips.map((ch, i) => (
        <span key={i} className={`mono rounded bg-rowhover px-1.5 py-0.5 text-[10px] ${ch.tone}`}>
          {ch.text}
        </span>
      ))}
    </>
  );
}

// Trade finder (Phase 21/22): pick an opponent, generate/regenerate, render trade proposals.
// Advisory only — the app never executes trades. React formats backend content only.
function TradeFinderCard({ leagueId }: { leagueId: number }) {
  const [teams, setTeams] = useState<TeamOut[] | null>(null);
  const [opp, setOpp] = useState<number | null>(null);
  const [env, setEnv] = useState<AiReportEnvelope<TradeFinderContent> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getLeagueTeams(leagueId)
      .then((ts) => {
        const opponents = ts.filter((t) => !t.is_me);
        setTeams(ts);
        setOpp(opponents.length ? opponents[0].id : null);
      })
      .catch((e) => setErr(String(e)));
  }, [leagueId]);

  useEffect(() => {
    if (opp == null) {
      setEnv(null);
      return;
    }
    // Clear the previous opponent's result while the new one loads (no stale flash).
    setEnv(null);
    let active = true;
    getTradeFinder(leagueId, opp)
      .then((e) => { if (active) setEnv(e); })
      .catch(() => { if (active) setEnv(null); });
    return () => { active = false; };
  }, [leagueId, opp]);

  async function generate() {
    if (opp == null) return;
    setBusy(true);
    setErr(null);
    try {
      setEnv(await generateTradeFinder(leagueId, opp, !!env?.content));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (teams == null) return <Panel className="p-4"><Spinner /></Panel>;
  const opponents = teams.filter((t) => !t.is_me);
  const selectedOpponent = opponents.find((team) => team.id === opp) ?? null;
  const hasMe = teams.some((t) => t.is_me);
  const has = !!env?.content;
  const c = env?.content;
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Trade finder</h3>
        <div className="flex items-center gap-2">
          {hasMe && opponents.length > 0 && (
            <TeamSelect
              teams={opponents}
              value={opp}
              onChange={setOpp}
              label="Trade opponent"
            />
          )}
          {has && env?.stale && (
            <span className="mono text-[10px] uppercase tracking-wide text-gold">inputs changed</span>
          )}
          <Button
            variant={has ? "secondary" : "primary"}
            onClick={generate}
            disabled={busy || opp == null}
          >
            {busy ? "Generating…" : has ? "Regenerate" : "Generate"}
          </Button>
        </div>
      </div>
      {err && <div className="mt-2"><ErrorNote message={err} /></div>}
      {env?.error && <div className="mt-2"><ErrorNote message={env.error} /></div>}
      {!hasMe ? (
        <div className="mt-3">
          <EmptyState
            title="No 'my team' detected"
            hint="Trade ideas need your team in this league (add the owning account and re-sync)."
          />
        </div>
      ) : opponents.length === 0 ? (
        <div className="mt-3"><EmptyState title="No opponents to trade with yet" /></div>
      ) : (
        <div className="mt-3">
          {has && c ? (
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono text-[11px] uppercase tracking-wide text-muted">vs</span>
                <LinkedTeamIdentity
                  leagueId={leagueId}
                  tab="ai"
                  team={selectedOpponent}
                  size="xs"
                  showMe={false}
                  fallback={c.opponent_name ?? "opponent"}
                />
                <TradeProvenance c={c} />
              </div>
              {c.proposals.length === 0 ? (
                <p className="mt-2 text-sm text-muted">No trade proposed.</p>
              ) : (
                <ul className="mt-2 space-y-3">
                  {c.proposals.map((p, i) => (
                    <li key={i} className="rounded-md border border-line p-3">
                      {(() => {
                        const give = p.i_give_players?.length
                          ? p.i_give_players
                          : p.i_give.map((name) => playerReference(null, name, null));
                        const get = p.i_get_players?.length
                          ? p.i_get_players
                          : p.i_get.map((name) => playerReference(null, name, null));
                        const proposalPlayers = uniquePlayerReferences([...give, ...get]);
                        return (
                          <>
                            <div className="grid gap-3 sm:grid-cols-2">
                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-muted">I give</div>
                                <div className="mt-1 space-y-2">
                                  {give.map((player) => (
                                    <PlayerIdentity
                                      key={player.espn_player_id ?? player.name}
                                      player={player}
                                      tone="text-red"
                                    />
                                  ))}
                                </div>
                              </div>
                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-muted">I get</div>
                                <div className="mt-1 space-y-2">
                                  {get.map((player) => (
                                    <PlayerIdentity
                                      key={player.espn_player_id ?? player.name}
                                      player={player}
                                      tone="text-green"
                                    />
                                  ))}
                                </div>
                              </div>
                            </div>
                            <p className="mt-3 text-sm leading-relaxed text-secondary">
                              <PlayerMentionText text={p.rationale} players={proposalPlayers} />
                            </p>
                          </>
                        );
                      })()}
                    </li>
                  ))}
                </ul>
              )}
              {c.note && (
                <p className="mt-2 text-[11px] italic text-muted">
                  <PlayerMentionText
                    text={c.note}
                    players={uniquePlayerReferences(
                      c.proposals.flatMap((proposal) => [
                        ...(proposal.i_give_players ?? []),
                        ...(proposal.i_get_players ?? []),
                      ]),
                    )}
                  />
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted">Not generated yet for this opponent.</p>
          )}
          <p className="mt-3 text-[11px] leading-relaxed text-muted">
            Advisory only — the app never executes trades on ESPN.
            {has && c?.grounding_source === "lineup_snapshot"
              ? " Grounded on each team's latest shared roster snapshot and projections."
              : " This falls back to drafted-roster grounding, which may not reflect current rosters."}
          </p>
        </div>
      )}
      {has && env?.model && (
        <div className="mono mt-3 text-[10px] text-muted">
          {env.model}
          {env.created_at ? ` · ${relTime(env.created_at)}` : ""}
        </div>
      )}
    </Panel>
  );
}

// Weekly recap (Phase 20): pick a completed week, generate/regenerate, render the recap.
// React only formats backend content + the existing matchup weeks — no analytics here.
function WeeklyRecapCard({ leagueId }: { leagueId: number }) {
  const [weeks, setWeeks] = useState<number[] | null>(null);
  const [week, setWeek] = useState<number | null>(null);
  const [env, setEnv] = useState<AiReportEnvelope<WeeklyRecapContent> | null>(null);
  const [players, setPlayers] = useState<PlayerReference[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    getLeagueActivity(leagueId)
      .then((transactions) => setPlayers(transactionPlayerReferences(transactions)))
      .catch(() => setPlayers([]));
    getLeagueMatchups(leagueId)
      .then((ms) => {
        const done = [
          ...new Set(
            ms
              .filter((m) => !m.is_playoff && (m.home_points ?? 0) > 0 && (m.away_points ?? 0) > 0)
              .map((m) => m.week),
          ),
        ].sort((a, b) => a - b);
        setWeeks(done);
        setWeek(done.length ? done[done.length - 1] : null);
      })
      .catch((e) => setErr(String(e)));
  }, [leagueId]);

  useEffect(() => {
    if (week == null) {
      setEnv(null);
      return;
    }
    getWeeklyRecap(leagueId, week).then(setEnv).catch(() => setEnv(null));
  }, [leagueId, week]);

  async function generate() {
    if (week == null) return;
    setBusy(true);
    setErr(null);
    try {
      setEnv(await generateWeeklyRecap(leagueId, week, !!env?.content));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (weeks == null) return <Panel className="p-4"><Spinner /></Panel>;
  const has = !!env?.content;
  const c = env?.content;
  return (
    <Panel className="p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Weekly recap</h3>
        <div className="flex items-center gap-2">
          {weeks.length > 0 && (
            <select
              value={week ?? ""}
              onChange={(e) => setWeek(Number(e.target.value))}
              className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-primary"
            >
              {weeks.map((w) => (
                <option key={w} value={w}>Week {w}</option>
              ))}
            </select>
          )}
          {has && env?.stale && (
            <span className="mono text-[10px] uppercase tracking-wide text-gold">inputs changed</span>
          )}
          <Button
            variant={has ? "secondary" : "primary"}
            onClick={generate}
            disabled={busy || week == null}
          >
            {busy ? "Generating…" : has ? "Regenerate" : "Generate"}
          </Button>
        </div>
      </div>
      {err && <div className="mt-2"><ErrorNote message={err} /></div>}
      {env?.error && <div className="mt-2"><ErrorNote message={env.error} /></div>}
      {weeks.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title="No completed weeks yet"
            hint="Weekly recaps unlock once a regular-season week has final scores."
          />
        </div>
      ) : (
        <div className="mt-3">
          {has && c ? (
            <div>
              <div className="text-sm font-semibold text-primary">
                <PlayerMentionText text={c.headline} players={players} />
              </div>
              <p className="mt-1 text-sm leading-relaxed text-secondary">
                <PlayerMentionText text={c.body} players={players} />
              </p>
              {c.luck_notes.length > 0 && (
                <>
                  <div className="mt-2 text-xs uppercase tracking-wide text-muted">Luck notes</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-secondary">
                    {c.luck_notes.map((n, i) => (
                      <li key={i}><PlayerMentionText text={n} players={players} /></li>
                    ))}
                  </ul>
                </>
              )}
              {c.waiver_highlights.length > 0 && (
                <>
                  <div className="mt-2 text-xs uppercase tracking-wide text-muted">Waiver highlights</div>
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-secondary">
                    {c.waiver_highlights.map((n, i) => (
                      <li key={i}><PlayerMentionText text={n} players={players} /></li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted">Not generated yet for this week.</p>
          )}
        </div>
      )}
      {has && env?.model && (
        <div className="mono mt-3 text-[10px] text-muted">
          {env.model}
          {env.created_at ? ` · ${relTime(env.created_at)}` : ""}
        </div>
      )}
    </Panel>
  );
}

function AiCard({
  title,
  env,
  busy,
  onGenerate,
  revealScope,
  seenRevealTokens,
  children,
}: {
  title: string;
  env: AiReportEnvelope<unknown> | null;
  busy: boolean;
  onGenerate: () => void;
  revealScope: string;
  seenRevealTokens: Set<string>;
  children?: ReactNode;
}) {
  const has = !!env?.content;
  const revealToken = `${revealScope}:${env?.created_at ?? ""}:${JSON.stringify(env?.content)}`;
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
      <div className="mt-3">
        {has ? (
          <ContentReveal
            key={revealToken}
            token={revealToken}
            seenTokens={seenRevealTokens}
            testId="ai-content-reveal"
          >
            {children}
          </ContentReveal>
        ) : (
          <p className="text-sm text-muted">Not generated yet.</p>
        )}
      </div>
      {has && env?.model && (
        <div className="mono mt-3 text-[10px] text-muted">
          {env.model}
          {env.created_at ? ` · ${relTime(env.created_at)}` : ""}
        </div>
      )}
    </Panel>
  );
}
