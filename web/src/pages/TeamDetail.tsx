import { type ColumnDef } from "@tanstack/react-table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import {
  getLeagueTeamDetail,
  syncLeague,
  type RosterSlotOut,
  type TeamDetailOut,
  type TeamMatchupSideOut,
} from "../api";
import { DataTable } from "../components/DataTable";
import { PlayerIdentity } from "../components/PlayerIdentity";
import {
  type TeamDetailLocationState,
  TeamDetailLink,
} from "../components/TeamDetailLink";
import { TeamIdentity } from "../components/TeamIdentity";
import {
  Button,
  LifecycleBadge,
  Panel,
  SizePill,
  WarningNote,
} from "../components/ui";
import { DASH, LIFECYCLE_LABEL, num, record, relTime } from "../lib/format";
import { syncSummaryMessage } from "../lib/sync";

function playerFor(slot: RosterSlotOut) {
  return {
    espn_player_id: slot.espn_player_id,
    name: slot.player_name ?? "Unknown player",
    position: slot.player_position,
  };
}

function injuryLabel(status: string | null): string | null {
  if (!status) return null;
  const normalized = status.toUpperCase().replace(/ /g, "_");
  if (normalized.includes("INJURY_RESERVE") || normalized === "IR") return "IR";
  if (normalized.includes("QUESTIONABLE")) return "Q";
  if (normalized.includes("DOUBTFUL")) return "D";
  if (normalized.includes("OUT")) return "O";
  return status;
}

function kickoffLabel(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function gameLabel(slot: RosterSlotOut): string {
  if (slot.game_status === "bye") return "Bye";
  if (slot.game_status === "in_progress") return "In progress";
  if (slot.game_status === "final") return "Final";
  const kickoff = kickoffLabel(slot.kickoff_at);
  return kickoff ?? DASH;
}

function TeamDetailSkeleton() {
  return (
    <div aria-label="Loading team roster" className="animate-pulse space-y-5">
      <div className="h-16 max-w-lg rounded-md bg-rowhover" />
      <div className="h-32 rounded-md border border-line bg-panel" />
      <div className="overflow-hidden rounded-md border border-line bg-panel">
        <div className="h-10 border-b border-line bg-rowhover" />
        {Array.from({ length: 8 }, (_, index) => (
          <div key={index} className="grid h-14 grid-cols-[60px_1fr_90px] gap-3 border-b border-line/60 px-3 py-2">
            <div className="rounded bg-rowhover" />
            <div className="rounded bg-rowhover" />
            <div className="rounded bg-rowhover" />
          </div>
        ))}
      </div>
    </div>
  );
}

function MatchupSide({
  side,
  leagueId,
  returnTo,
}: {
  side: TeamMatchupSideOut;
  leagueId: number;
  returnTo: string;
}) {
  if (!side.team) {
    return <div className="text-sm text-muted">Bye</div>;
  }
  const score =
    side.points != null && side.points !== 0
      ? num(side.points)
      : side.projected_points != null
        ? `${num(side.projected_points)} proj`
        : DASH;
  return (
    <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
      <div className="min-w-0">
        <TeamDetailLink
          leagueId={leagueId}
          team={side.team}
          returnTo={returnTo}
          size="sm"
        />
        <div className="mono mt-1 pl-11 text-[11px] text-muted">
          {record(side.team.wins, side.team.losses, side.team.ties)}
        </div>
      </div>
      <span className="mono shrink-0 text-base text-primary">{score}</span>
    </div>
  );
}

function MatchupPanel({
  detail,
  leagueId,
  returnTo,
}: {
  detail: TeamDetailOut;
  leagueId: number;
  returnTo: string;
}) {
  const matchup = detail.matchup;
  if (!matchup) {
    return (
      <Panel className="p-4">
        <div className="text-xs uppercase tracking-wide text-muted">This week</div>
        <div className="mt-3 text-sm text-secondary">
          No opponent is scheduled for the current matchup period.
        </div>
      </Panel>
    );
  }
  const kickoff = kickoffLabel(matchup.next_kickoff_at);
  const allZero = [matchup.home.points, matchup.away.points].every(
    (points) => points == null || points === 0,
  );
  return (
    <Panel className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
        <span className="text-xs uppercase tracking-wide text-muted">
          Week {matchup.matchup_period} matchup
        </span>
        <span className="mono text-[11px] text-muted">
          {allZero ? (kickoff ? `First kickoff ${kickoff}` : "Pre-game") : "Current score"}
        </span>
      </div>
      <div className="grid gap-4 p-4 md:grid-cols-[1fr_auto_1fr] md:items-center">
        <MatchupSide side={matchup.away} leagueId={leagueId} returnTo={returnTo} />
        <span className="mono text-center text-[11px] uppercase text-muted">at</span>
        <MatchupSide side={matchup.home} leagueId={leagueId} returnTo={returnTo} />
      </div>
    </Panel>
  );
}

function EmptyPlayer() {
  return (
    <div className="flex items-center gap-2 text-secondary">
      <span className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-dashed border-line text-xs text-muted">
        +
      </span>
      <span>Empty</span>
    </div>
  );
}

function MobileRosterRow({ slot }: { slot: RosterSlotOut }) {
  const injury = injuryLabel(slot.injury_status);
  return (
    <div className="border-b border-line/60 px-3 py-3 last:border-b-0">
      <div className="flex items-start gap-3">
        <span className="mono w-12 shrink-0 pt-1 text-xs text-secondary">
          {slot.slot_label}
        </span>
        <div className="min-w-0 flex-1">
          {slot.espn_player_id == null ? (
            <EmptyPlayer />
          ) : (
            <PlayerIdentity player={playerFor(slot)} />
          )}
          {slot.espn_player_id != null && (
            <div className="mono mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted">
              <span>{slot.nfl_team ?? DASH}{slot.opponent ? ` vs ${slot.opponent}` : ""}</span>
              <span>{gameLabel(slot)}</span>
              <span>Actual {num(slot.actual_points)}</span>
              <span>Proj {num(slot.projected_points)}</span>
              {injury && <span className="text-red">Injury {injury}</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RosterSection({
  title,
  rows,
}: {
  title: string;
  rows: RosterSlotOut[];
}) {
  const columns = useMemo<ColumnDef<RosterSlotOut>[]>(
    () => [
      {
        accessorKey: "slot_label",
        header: "Slot",
        cell: (cell) => (
          <span className="mono text-secondary">{cell.getValue<string>()}</span>
        ),
      },
      {
        id: "player",
        header: "Player",
        accessorFn: (slot) => slot.player_name ?? "",
        cell: (cell) =>
          cell.row.original.espn_player_id == null ? (
            <EmptyPlayer />
          ) : (
            <PlayerIdentity player={playerFor(cell.row.original)} />
          ),
      },
      {
        accessorKey: "nfl_team",
        header: "NFL",
        cell: (cell) => <span className="mono">{cell.getValue<string | null>() ?? DASH}</span>,
      },
      {
        accessorKey: "opponent",
        header: "Opp",
        cell: (cell) => <span className="mono">{cell.getValue<string | null>() ?? DASH}</span>,
      },
      {
        id: "game",
        header: "Game",
        accessorFn: (slot) => slot.kickoff_at ?? slot.game_status ?? "",
        cell: (cell) => <span className="mono text-muted">{gameLabel(cell.row.original)}</span>,
      },
      {
        accessorKey: "actual_points",
        header: "Actual",
        cell: (cell) => <span className="mono">{num(cell.getValue<number | null>())}</span>,
      },
      {
        accessorKey: "projected_points",
        header: "Proj",
        cell: (cell) => <span className="mono text-secondary">{num(cell.getValue<number | null>())}</span>,
      },
      {
        accessorKey: "injury_status",
        header: "Injury",
        cell: (cell) => {
          const label = injuryLabel(cell.getValue<string | null>());
          return <span className="mono text-red">{label ?? ""}</span>;
        },
      },
    ],
    [],
  );

  if (rows.length === 0) return null;
  return (
    <Panel className="overflow-hidden">
      <div className="border-b border-line px-4 py-2 text-xs uppercase tracking-wide text-muted">
        {title}
      </div>
      <div className="hidden md:block">
        <DataTable data={rows} columns={columns} ariaLabel={`${title} roster`} />
      </div>
      <div className="md:hidden">
        {rows.map((slot) => (
          <MobileRosterRow
            key={`${slot.section}-${slot.slot_id}-${slot.slot_index}`}
            slot={slot}
          />
        ))}
      </div>
    </Panel>
  );
}

export default function TeamDetail() {
  const params = useParams();
  const leagueId = Number(params.leagueId);
  const teamId = Number(params.teamId);
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as TeamDetailLocationState | null;
  const fallbackReturn = `/league/${leagueId}?tab=teams`;
  const returnTo = state?.returnTo ?? fallbackReturn;
  const [detail, setDetail] = useState<TeamDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDetail(await getLeagueTeamDetail(leagueId, teamId));
    } catch (loadError) {
      setError(String(loadError));
    }
  }, [leagueId, teamId]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (state?.returnTo) navigate(-1);
      else navigate(fallbackReturn);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [fallbackReturn, navigate, state?.returnTo]);

  async function sync() {
    setSyncing(true);
    setWarning(null);
    try {
      const summary = await syncLeague(leagueId);
      setWarning(syncSummaryMessage(summary));
      await load();
    } catch (syncError) {
      setWarning(String(syncError));
    } finally {
      setSyncing(false);
    }
  }

  const backLink = (
    <Link
      to={returnTo}
      onClick={(event) => {
        if (!state?.returnTo) return;
        event.preventDefault();
        navigate(-1);
      }}
      className="text-sm text-secondary hover:text-primary"
    >
      ← Back to league
    </Link>
  );

  if (!Number.isInteger(leagueId) || !Number.isInteger(teamId)) {
    return (
      <div>
        {backLink}
        <div className="mt-4 text-sm text-red">Invalid team link.</div>
      </div>
    );
  }
  if (error) {
    return (
      <div>
        {backLink}
        <Panel className="mt-4 p-4">
          <div className="text-sm text-red">{error}</div>
          <div className="mt-3">
            <Button onClick={() => void load()}>Retry</Button>
          </div>
        </Panel>
      </div>
    );
  }
  if (!detail) {
    return (
      <div>
        {backLink}
        <div className="mt-4">
          <TeamDetailSkeleton />
        </div>
      </div>
    );
  }

  const league = detail.league;
  const currentPath = location.pathname;
  return (
    <div>
      {backLink}
      <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold">
            <TeamIdentity team={detail.team} size="lg" />
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 pl-16">
            <span className="text-sm text-secondary">
              {league.name ?? `League ${league.espn_league_id}`}
            </span>
            <SizePill size={league.size} />
            <LifecycleBadge
              lifecycle={league.lifecycle}
              label={LIFECYCLE_LABEL[league.lifecycle] ?? league.lifecycle}
            />
            {detail.scoring && (
              <span className="mono rounded bg-rowhover px-1.5 py-0.5 text-[11px] text-secondary">
                {detail.scoring}
              </span>
            )}
          </div>
          <div className="mono mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 pl-16 text-[11px] text-muted">
            <span>{record(detail.team.wins, detail.team.losses, detail.team.ties)}</span>
            <span>· {detail.account_label ?? "public"}</span>
            <span>· {league.season}</span>
            <span>· synced {relTime(league.last_synced_at)}</span>
          </div>
        </div>
        <Button variant="primary" onClick={() => void sync()} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync now"}
        </Button>
      </div>

      {warning && (
        <div className="mt-4">
          <WarningNote
            title="Sync completed with an issue"
            messages={[warning]}
            onDismiss={() => setWarning(null)}
          />
        </div>
      )}
      {detail.roster_status !== "current" && (
        <div className="mt-4" data-testid="roster-stale-note">
          <WarningNote
            title={detail.roster_status === "stale" ? "Roster snapshot may be stale" : "Roster unavailable"}
            messages={[
              detail.roster_status === "stale"
                ? `Showing the last successful roster from ${relTime(detail.roster_synced_at)}.`
                : "Sync this league to load its current roster.",
            ]}
          />
        </div>
      )}

      <div className="mt-5 space-y-5">
        <MatchupPanel detail={detail} leagueId={leagueId} returnTo={currentPath} />
        <RosterSection title="Starting lineup" rows={detail.starters} />
        <RosterSection title="Bench" rows={detail.bench} />
        <RosterSection title="IR / Reserve" rows={detail.ir} />
      </div>
    </div>
  );
}
