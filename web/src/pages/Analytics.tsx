import { type ColumnDef } from "@tanstack/react-table";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import {
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  downloadAnalyticsCsv,
  downloadExport,
  getPortfolioDraftAdp,
  getPortfolioExposure,
  getPortfolioStrategies,
  type DraftAdpPick,
  type DraftAdpTeam,
  type ExposureScope,
  type ExposureView,
  type PlayerExposure,
  type PlayerReference,
  type PortfolioDraftAdp,
  type PortfolioExposure,
  type PortfolioStrategies,
} from "../api";
import { DataTable } from "../components/DataTable";
import { PlayerAvatar } from "../components/PlayerIdentity";
import { EmptyState, ErrorNote, InfoTip, Panel, PositionPill, Spinner } from "../components/ui";
import { DASH, num } from "../lib/format";

const POSITION_ORDER = ["QB", "RB", "WR", "TE", "D/ST", "K"] as const;
const POSITION_COLORS: Record<string, string> = {
  QB: "var(--color-qb)",
  RB: "var(--color-rb)",
  WR: "var(--color-wr)",
  TE: "var(--color-te)",
  "D/ST": "var(--color-dst)",
  K: "var(--color-k)",
};
const STRATEGY_COLORS = [
  "var(--color-red)",
  "var(--color-green)",
  "var(--color-gold)",
  "var(--color-grade-b)",
  "var(--color-qb)",
];

const COVERAGE_HELP: Record<string, string> = {
  "my teams": "This is how many of your teams were used for this report. More teams usually make the answer more steady.",
  "field teams": "These are the other teams used for comparison. They show what the rest of your leagues did.",
  "drafted leagues": "These leagues already had a draft. Leagues that have not drafted yet cannot add player picks to this report.",
  "auction teams included": "These teams picked players with auction money instead of draft turns. They are counted where the math can compare them fairly.",
  "keepers included": "These are players a team kept from an older season. A keeper was not picked in the usual way, so it can change draft numbers.",
  "pre-draft excluded": "These leagues have not drafted yet, so they were left out. They will join the report after their draft is saved.",
  "snake picks": "These are picks from drafts where the pick order goes forward and then backward. Auction picks are not part of this number.",
  "teams in scope": "This is how many teams were used in this part of the report. A team must have the needed data to be counted.",
  "auction teams excluded": "These auction teams were left out because auction money does not work like numbered draft picks.",
  "keeper picks excluded": "These keeper picks were left out because the team did not choose them during the normal draft.",
  "ESPN picks without ADP": "ADP means average draft position. These ESPN picks had no ESPN ADP, so they could not be scored against that guide.",
  "FFC picks without ADP": "These picks had no current ADP from Fantasy Football Calculator. They could not be compared with today's market.",
  "not listed in FFC snapshot": "These players were missing from the current Fantasy Football Calculator list. Their current market value could not be checked.",
  "FFC resolver failures": "The app could not safely match these ESPN players to the FFC list. They were left out so the report would not use the wrong player.",
  "qualifying snake teams": "These teams used a snake draft and had enough picks to name a draft plan. Only these teams are used for strategy groups.",
  "missing classifications": "The app could not name a draft plan for these teams. This can happen when picks are missing or the team used an auction draft.",
};

const CARD_HELP = {
  analytics: "This page turns all your league data into simple clues. Use it to see what makes your teams different and how well your drafts matched player value.",
  exposure: "This section shows which players you have more or less often than other teams. Green means you have more than the field, and red means you have less.",
  adp: "ADP means average draft position. This section checks if you picked players earlier or later than draft guides said they would go.",
  strategy: "This section groups teams by the way they were built in the draft. It can show which draft plans were common and how those teams scored.",
  highestLeverage: "This player gives your teams the biggest difference from the field. You roster the player much more often than the other teams do.",
  mostUnderowned: "This is the player the field has much more often than you do. A large red number means this player could hurt many of your teams if he does well.",
  rbCapital: "Draft capital tells how much valuable draft space was spent. This card compares how much you spent on running backs with how much the field spent.",
  nflConcentration: "This shows the NFL team that appears most across your fantasy teams. A very high number means one real NFL team can affect many of your fantasy teams at once.",
  marketMove: "This player moved the most between your draft day and today's market. The card shows whether the player now gets picked earlier or later.",
  corePlayers: "Core players are on several of your teams. They matter more because one player's good or bad week can change many team results.",
  darts: "One-league darts are players you took on only one team. They are small bets that do not affect your whole portfolio.",
  positionalCapital: "This card compares how much draft value you spent at each position with the field. The first bar is your teams, and the second bar is the field.",
  playerExposure: "This table shows how often each player is on your teams and on field teams. Leverage is the gap between those two rates.",
  draftFingerprint: "These charts show the average number of players picked at each position in each draft round. The bottom axis is the draft round, and the side axis is picks per team. Compare your solid line with the field's dashed line.",
  teamConcentration: "This card shows how often players from each NFL team appear on your fantasy teams. It helps you spot when too many teams depend on the same NFL club.",
  draftTimeAdp: "This card compares your picks with ESPN's draft guide from draft day. A positive number means you got players later than the guide expected.",
  currentAdp: "This card compares your picks with today's market guide. A positive number means your old picks look like good value now.",
  teamCapture: "This table gives each fantasy team its own draft value score. Use it to see which teams found value and which teams reached early.",
  biggestValues: "These players were picked later than the draft guide expected. A bigger green number means more value was found.",
  biggestReaches: "These players were picked earlier than the draft guide expected. A bigger red number means the pick cost more than the guide suggested.",
  primaryRb: "This card names each team's main running back draft plan. Click a group to see only the teams that used that plan.",
  secondarySignals: "These are extra timing clues in a draft, like taking a wide receiver or tight end early. They add more detail to the main draft plan.",
  meanEdge: "This table shows the average Edge Index for each main draft plan. It can show a pattern, but it does not prove that one plan caused better teams.",
  classifications: "This table shows the draft plan picked for each team and the picks that caused that name. Confidence tells how clearly the team matched the plan.",
} as const;

type FingerprintSeries = {
  position: string;
  data: Array<{
    bucket: string;
    picks_per_team: number;
    field_picks_per_team: number;
    pick_count: number;
    field_pick_count: number;
  }>;
};

function fingerprintSeries(rows: PortfolioExposure["round_fingerprint"]): FingerprintSeries[] {
  const byPosition = new Map<string, FingerprintSeries["data"]>();
  for (const row of rows) {
    const data = byPosition.get(row.position) ?? [];
    data.push({
      bucket: row.bucket,
      picks_per_team: row.picks_per_team,
      field_picks_per_team: row.field_picks_per_team,
      pick_count: row.pick_count,
      field_pick_count: row.field_pick_count,
    });
    byPosition.set(row.position, data);
  }
  return [...byPosition.entries()]
    .sort(([a], [b]) => (
      (POSITION_ORDER as readonly string[]).indexOf(a) -
      (POSITION_ORDER as readonly string[]).indexOf(b)
    ))
    .map(([position, data]) => ({
      position,
      data: data.sort((a, b) => Number.parseInt(a.bucket) - Number.parseInt(b.bucket)),
    }));
}

function signed(value: number | null): string {
  if (value == null) return DASH;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function pp(value: number | null): string {
  if (value == null) return DASH;
  return `${signed(value)} pp`;
}

function shortDate(value: string | null): string {
  if (!value) return "snapshot unavailable";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

function playerReference(
  espnPlayerId: number | null,
  playerName: string | null,
  position: string | null,
): PlayerReference {
  return {
    espn_player_id: espnPlayerId,
    name: playerName ?? "Unknown player",
    position,
  };
}

export default function Analytics() {
  const [scope, setScope] = useState<ExposureScope>("me");
  const [exposureView, setExposureView] = useState<ExposureView>("rostered");
  const [exposure, setExposure] = useState<PortfolioExposure | null>(null);
  const [adp, setAdp] = useState<PortfolioDraftAdp | null>(null);
  const [strategies, setStrategies] = useState<PortfolioStrategies | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setExposureView(scope === "me" ? "rostered" : "all");
    setExposure(null);
    getPortfolioExposure(scope)
      .then((result) => active && setExposure(result))
      .catch((reason) => active && setError(String(reason)));
    return () => {
      active = false;
    };
  }, [scope]);

  useEffect(() => {
    let active = true;
    Promise.all([getPortfolioDraftAdp(), getPortfolioStrategies()])
      .then(([draftAdp, strategyData]) => {
        if (!active) return;
        setAdp(draftAdp);
        setStrategies(strategyData);
      })
      .catch((reason) => active && setError(String(reason)));
    return () => {
      active = false;
    };
  }, []);

  const loading = !error && (!exposure || !adp || !strategies);

  return (
    <div className="cold-grid overflow-hidden rounded-lg border border-coldline bg-cold/35">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-coldline bg-header/55 px-4 py-5 sm:px-5">
        <div>
          <div className="mono text-[9px] uppercase tracking-[0.24em] text-ice">Portfolio intelligence</div>
          <h1 className="display-face mt-1 flex items-center gap-2 text-2xl font-bold uppercase tracking-[0.04em] text-frost">
            Analytics
            <InfoTip label="Analytics" description={CARD_HELP.analytics} />
          </h1>
          <p className="mt-1 text-xs text-secondary">
            Draft exposure, market capture, and strategy mix for the active portfolio.
          </p>
        </div>
        <ExportControls scope={scope} exposureView={exposureView} />
      </div>

      <nav className="flex overflow-x-auto border-b border-coldline bg-coldpanel/40 px-4" aria-label="Analytics sections">
        <a href="#exposure-heading" className="display-face border-b-2 border-red px-3 py-2 text-xs font-bold uppercase tracking-wide text-primary">
          Leverage
        </a>
        <a href="#adp-heading" className="display-face border-b-2 border-transparent px-3 py-2 text-xs font-bold uppercase tracking-wide text-secondary hover:text-primary">
          ADP capture
        </a>
        <a href="#strategy-heading" className="display-face border-b-2 border-transparent px-3 py-2 text-xs font-bold uppercase tracking-wide text-secondary hover:text-primary">
          Strategy
        </a>
      </nav>

      {error && <div className="m-5"><ErrorNote message={error} /></div>}
      {loading && <Spinner label="Loading portfolio analytics…" />}
      {!loading && !error && exposure && adp && strategies && (
        <div className="divide-y divide-coldline px-4 sm:px-5">
          <ExposureSection
            data={exposure}
            scope={scope}
            setScope={setScope}
            view={exposureView}
            setView={setExposureView}
          />
          <AdpSection data={adp} />
          <StrategySection data={strategies} />
        </div>
      )}
    </div>
  );
}

function ExportControls({
  scope,
  exposureView,
}: {
  scope: ExposureScope;
  exposureView: ExposureView;
}) {
  return (
    <div className="flex max-w-full flex-wrap items-center overflow-hidden rounded-md border border-coldline bg-cold/70">
      <span className="mono px-2 py-1.5 text-[8px] uppercase tracking-[0.14em] text-muted">
        Export
      </span>
      <ExportButton
        label="Exposure CSV"
        title={`Download ${scope === "me" ? "my" : "opponent"} exposure as CSV`}
        onClick={() => downloadAnalyticsCsv("exposure", scope, exposureView)}
      />
      <ExportButton
        label="ADP CSV"
        title="Download team ADP capture as CSV"
        onClick={() => downloadAnalyticsCsv("draft-adp")}
      />
      <ExportButton
        label="Strategy CSV"
        title="Download team strategy labels as CSV"
        onClick={() => downloadAnalyticsCsv("strategies")}
      />
      <ExportButton
        label="JSON"
        title="Download master portfolio JSON with analytics"
        onClick={() => downloadExport("json")}
      />
      <ExportButton
        label="XLSX"
        title="Download portfolio workbook with analytics sheets"
        onClick={() => downloadExport("xlsx")}
      />
    </div>
  );
}

function ExportButton({
  label,
  title,
  onClick,
}: {
  label: string;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="display-face border-l border-coldline px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-secondary transition-colors duration-150 hover:bg-icechip hover:text-icesoft"
    >
      <span aria-hidden="true" className="mr-1 text-ice">⇩</span>
      {label}
    </button>
  );
}

function SectionHeader({
  eyebrow,
  title,
  description,
  titleId,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  titleId?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-coldline pb-3">
      <div>
        <div className="mono text-[8px] uppercase tracking-[0.22em] text-ice">{eyebrow}</div>
        <h2 id={titleId} className="display-face mt-1 flex items-center gap-2 text-base font-bold uppercase tracking-wide text-frost">
          {title}
          <InfoTip label={title} description={description} />
        </h2>
      </div>
      {action}
    </div>
  );
}

function CardTitle({ title, description, className = "" }: { title: string; description: string; className?: string }) {
  return (
    <h3 className={`display-face flex items-center gap-2 text-sm font-bold uppercase tracking-wide ${className}`}>
      {title}
      <InfoTip label={title} description={description} />
    </h3>
  );
}

function CoverageChip({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  const description = COVERAGE_HELP[label] ?? `This number tells how many ${label} were used in this report.`;
  return (
    <span
      className={`mono inline-flex items-center gap-1 rounded border px-2 py-1 text-[8px] uppercase tracking-wide ${
        warn ? "border-gold/40 bg-gold/5 text-gold" : "border-coldline bg-cold/65 text-secondary"
      }`}
    >
      <span className="text-muted">{label}</span>
      <InfoTip label={label} description={description} />
      {value}
    </span>
  );
}

function ScopeControl({ scope, setScope }: { scope: ExposureScope; setScope: (scope: ExposureScope) => void }) {
  return (
    <div className="flex overflow-hidden rounded-md border border-coldline bg-cold" aria-label="Exposure scope">
      {([
        ["me", "Combined"],
        ["opponents", "Opponents"],
      ] as const).map(([value, label]) => (
        <button
          type="button"
          key={value}
          onClick={() => setScope(value)}
          className={`display-face px-3 py-1.5 text-[11px] font-bold uppercase tracking-wide transition-colors duration-150 first:border-r first:border-coldline ${
            scope === value ? "bg-icechip text-icesoft" : "text-secondary hover:bg-rowhover hover:text-primary"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function ExposureViewControl({
  view,
  setView,
  data,
}: {
  view: ExposureView;
  setView: (view: ExposureView) => void;
  data: PortfolioExposure;
}) {
  const options: Array<[ExposureView, string]> = [
    ["rostered", "Rostered"],
    ["field_owned", "Field owns"],
    ["all", "All players"],
  ];
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-coldline bg-coldpanel/45 px-3 py-2">
      <div className="flex overflow-hidden rounded-md border border-coldline" aria-label="Exposure table view">
        {options.map(([value, label], index) => (
          <button
            type="button"
            key={value}
            onClick={() => setView(value)}
            className={`display-face px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-colors duration-150 ${
              index > 0 ? "border-l border-coldline" : ""
            } ${view === value ? "bg-icechip text-icesoft" : "bg-cold text-secondary hover:text-primary"}`}
          >
            {label}
            <span className="mono ml-1 text-[10px] opacity-80">
              {data.views[value].row_count}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function ExposureSection({
  data,
  scope,
  setScope,
  view,
  setView,
}: {
  data: PortfolioExposure;
  scope: ExposureScope;
  setScope: (scope: ExposureScope) => void;
  view: ExposureView;
  setView: (view: ExposureView) => void;
}) {
  const activeView = scope === "me" ? view : "all";
  const tableRows = useMemo(() => {
    if (activeView === "rostered") {
      return data.players.filter((row) => row.my_exposure_pct > 0);
    }
    if (activeView === "field_owned") {
      return data.players
        .filter((row) => row.my_exposure_pct === 0 && row.field_exposure_pct > 0)
        .sort((a, b) => (
          b.field_exposure_pct - a.field_exposure_pct ||
          b.field_slot_pct - a.field_slot_pct ||
          (a.player_name ?? "").localeCompare(b.player_name ?? "")
        ));
    }
    return data.players;
  }, [activeView, data.players]);
  const initialSort = activeView === "field_owned"
    ? [{ id: "field_exposure_pct", desc: true }]
    : [{ id: "leverage_pp", desc: true }];
  const columns = useMemo<ColumnDef<PlayerExposure>[]>(
    () => [
      {
        accessorKey: "player_name",
        header: "Player",
        cell: ({ row }) => (
          <details>
            <summary className="cursor-pointer font-medium text-primary">
              <span className="ml-1 inline-flex min-w-0 items-center gap-2 align-middle">
                <PlayerAvatar
                  player={playerReference(
                    row.original.espn_player_id,
                    row.original.player_name,
                    row.original.position,
                  )}
                  size="md"
                  variant="portrait"
                />
                <span className="min-w-0">
                  <span className="block max-w-32 truncate">{row.original.player_name ?? "Unknown player"}</span>
                  <span className="mono mt-1 block text-[9px] font-normal text-muted md:hidden">
                    {row.original.nfl_team ?? DASH} · {row.original.position ?? DASH}
                  </span>
                </span>
              </span>
            </summary>
            <div className="mt-2 min-w-64 space-y-1 border-l border-line pl-3 text-[11px] text-secondary">
              {row.original.leagues.length ? row.original.leagues.map((league) => (
                <div key={`${league.league_id}-${league.team_id}`} className="flex justify-between gap-4">
                  <Link
                    to={`/league/${league.league_id}/teams/${league.team_id}`}
                    state={{ returnTo: "/analytics" }}
                    className="min-w-0 truncate text-secondary hover:text-red"
                  >
                    {league.league_name ?? league.team_name ?? "League"}
                  </Link>
                  <span className="mono shrink-0">
                    {league.draft_type?.toUpperCase() === "AUCTION"
                      ? "Auction"
                      : league.overall == null
                        ? DASH
                        : `#${league.overall}`}
                    {league.keeper ? " keeper" : ""}
                  </span>
                </div>
              )) : (
                <div className="text-muted">Not rostered on my teams in this filter.</div>
              )}
            </div>
          </details>
        ),
      },
      {
        accessorKey: "position",
        header: "Pos",
        cell: ({ row }) => <PositionPill pos={row.original.position} />,
      },
      { accessorKey: "nfl_team", header: "NFL", cell: ({ getValue }) => <span className="mono text-xs">{String(getValue() ?? DASH)}</span> },
      {
        accessorKey: "my_exposure_pct",
        header: "Exposure",
        cell: ({ row }) => (
          <div className="mono text-right">
            <div className="font-semibold text-primary">{num(row.original.my_exposure_pct)}%</div>
            <div className="text-[10px] text-muted">{row.original.my_share} leagues</div>
          </div>
        ),
      },
      {
        accessorKey: "field_exposure_pct",
        header: "Field",
        cell: ({ row }) => (
          <div className="mono text-right">
            <div className="font-semibold text-secondary">{num(row.original.field_exposure_pct)}%</div>
            <div className="text-[10px] text-muted">{row.original.field_share} leagues</div>
          </div>
        ),
      },
      {
        accessorKey: "field_slot_pct",
        header: "Field slots",
        cell: ({ row }) => (
          <div className="mono text-right">
            <div className="font-semibold text-secondary">{num(row.original.field_slot_pct)}%</div>
            <div className="text-[10px] text-muted">{row.original.field_slot_share} slots</div>
          </div>
        ),
      },
      {
        accessorKey: "leverage_pp",
        header: "Leverage",
        cell: ({ row }) => (
          <div className="text-right">
            <LeverageValue value={row.original.leverage_pp} />
          </div>
        ),
      },
      { accessorKey: "avg_overall", header: "Avg pick", cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null)}</span> },
      {
        id: "range",
        header: "Pick range",
        accessorFn: (row) => row.min_overall,
        cell: ({ row }) => (
          <span className="mono text-xs">
            {row.original.min_overall == null ? DASH : `${row.original.min_overall}–${row.original.max_overall}`}
          </span>
        ),
      },
      { accessorKey: "avg_pick_value", header: "Draft capital", cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null, 2)}</span> },
      { accessorKey: "auction_rosters", header: "Auction", cell: ({ getValue }) => <span className="mono">{String(getValue())}</span> },
    ],
    [],
  );

  return (
    <section className="scroll-mt-28 py-6" aria-labelledby="exposure-heading">
      <SectionHeader
        eyebrow="Exposure"
        title={scope === "me" ? "Portfolio leverage against the local field" : "Opponent roster census"}
        description={CARD_HELP.exposure}
        titleId="exposure-heading"
        action={<ScopeControl scope={scope} setScope={setScope} />}
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <CoverageChip label="my teams" value={String(data.coverage.my_teams_in_scope)} />
        <CoverageChip label="field teams" value={String(data.coverage.field_teams_in_scope)} />
        <CoverageChip label="drafted leagues" value={String(data.coverage.league_count)} />
        <CoverageChip label="auction teams included" value={String(data.coverage.auction_teams)} />
        <CoverageChip label="keepers included" value={String(data.coverage.keeper_picks)} />
        <CoverageChip
          label="pre-draft excluded"
          value={String(data.coverage.pre_draft_leagues_excluded)}
        />
      </div>
      {data.coverage.teams_in_scope > 0 && data.coverage.teams_in_scope < 3 && (
        <LowSampleNote />
      )}

      <ExposureHeadlines data={data} />
      <div className="mt-4 grid items-start gap-4 xl:grid-cols-[minmax(17rem,0.72fr)_minmax(0,1.65fr)]">
        <PositionCapitalPanel data={data} />
        {data.players.length ? (
          <Panel className="min-w-0 overflow-hidden border-coldline bg-cold/60">
            <div className="flex items-center justify-between border-b border-coldline px-3 py-3">
              <CardTitle
                title="Portfolio player exposure"
                description={CARD_HELP.playerExposure}
                className="text-frost"
              />
              <span className="mono text-[8px] uppercase tracking-wide text-muted">
                {tableRows.length} players
              </span>
            </div>
            {scope === "me" && (
              <ExposureViewControl view={activeView} setView={setView} data={data} />
            )}
            <div className="max-h-[30rem] overflow-y-auto">
              <DataTable
                key={`${scope}-${activeView}`}
                data={tableRows}
                columns={columns}
                initialSort={initialSort}
                ariaLabel="Portfolio player exposure"
                columnClassName={(id) => (
                  ["my_exposure_pct", "field_exposure_pct", "field_slot_pct", "leverage_pp"].includes(id)
                    ? "text-right"
                    : ""
                )}
              />
            </div>
          </Panel>
        ) : (
          <EmptyState
            title="No drafted teams in scope"
            hint="Pre-draft leagues remain outside exposure denominators until they contain real picks."
          />
        )}
      </div>

      {data.players.length > 0 && (
        <div className="mt-4 grid items-stretch gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(18rem,0.8fr)]">
          <RoundFingerprint data={data} />
          <ExposureRollups data={data} />
        </div>
      )}
    </section>
  );
}

function LeverageValue({ value }: { value: number | null }) {
  const tone = value == null ? "text-muted" : value >= 0 ? "text-green" : "text-red";
  return <span className={`mono font-semibold ${tone}`}>{pp(value)}</span>;
}

function HeadlineCard({
  label,
  description,
  primary,
  secondary,
}: {
  label: string;
  description: string;
  primary: ReactNode;
  secondary?: ReactNode;
}) {
  return (
    <Panel className="min-w-0 border-coldline bg-coldpanel/45 p-3">
      <div className="display-face flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-muted">
        {label}
        <InfoTip label={label} description={description} />
      </div>
      <div className="mt-2 min-w-0 text-sm font-semibold text-frost">{primary}</div>
      {secondary && <div className="mono mt-2 text-[9px] leading-relaxed text-secondary">{secondary}</div>}
    </Panel>
  );
}

function ExposureHeadlines({ data }: { data: PortfolioExposure }) {
  const highest = data.headlines.highest_leverage;
  const underowned = data.headlines.most_underowned;
  const capital = data.headlines.positional_capital_vs_field;
  const concentration = data.headlines.most_concentrated_nfl_team;
  const marketMove = data.headlines.largest_market_move;
  return (
    <div className="mt-5">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <HeadlineCard
          label="Highest leverage"
          description={CARD_HELP.highestLeverage}
          primary={
            highest ? (
              <span className="flex min-w-0 flex-col items-start gap-2">
                <span className="inline-flex min-w-0 items-center gap-2">
                  <PlayerAvatar
                    player={playerReference(highest.espn_player_id, highest.player_name, highest.position)}
                    size="md"
                    variant="portrait"
                  />
                  <PositionPill pos={highest.position} />
                  <span className="truncate">{highest.player_name ?? "Unknown player"}</span>
                </span>
                <span className="text-base"><LeverageValue value={highest.leverage_pp} /></span>
              </span>
            ) : DASH
          }
          secondary={
            highest
              ? `${num(highest.exposure_pct)}% (${highest.share}) vs ${num(highest.field_exposure_pct)}% (${highest.field_share})`
              : undefined
          }
        />
        <HeadlineCard
          label="Most under-owned"
          description={CARD_HELP.mostUnderowned}
          primary={
            underowned ? (
              <span className="flex min-w-0 flex-col items-start gap-2">
                <span className="inline-flex min-w-0 items-center gap-2">
                  <PlayerAvatar
                    player={playerReference(underowned.espn_player_id, underowned.player_name, underowned.position)}
                    size="md"
                    variant="portrait"
                  />
                  <PositionPill pos={underowned.position} />
                  <span className="truncate">{underowned.player_name ?? "Unknown player"}</span>
                </span>
                <span className="text-base"><LeverageValue value={underowned.leverage_pp} /></span>
              </span>
            ) : DASH
          }
          secondary={
            underowned
              ? `${num(underowned.exposure_pct)}% (${underowned.share}) vs ${num(underowned.field_exposure_pct)}% (${underowned.field_share})`
              : undefined
          }
        />
        <HeadlineCard
          label="RB capital vs field"
          description={CARD_HELP.rbCapital}
          primary={capital ? <LeverageValue value={capital.leverage_pp} /> : DASH}
          secondary={
            capital
              ? `${num(capital.pick_value_pct)}% (${capital.pick_count} picks) vs ${num(capital.field_pick_value_pct)}% (${capital.field_pick_count} picks)`
              : undefined
          }
        />
        <HeadlineCard
          label="Most concentrated NFL team"
          description={CARD_HELP.nflConcentration}
          primary={
            concentration ? (
              <span className="mono">
                {concentration.nfl_team} · {num(concentration.penetration_pct)}%
              </span>
            ) : DASH
          }
          secondary={
            concentration
              ? `${concentration.penetration_share} teams · ${num(concentration.players_per_team, 2)}x players/team`
              : undefined
          }
        />
        <HeadlineCard
          label="Largest market move"
          description={CARD_HELP.marketMove}
          primary={
            marketMove ? (
              <span className="flex min-w-0 flex-col items-start gap-2">
                <span className="inline-flex min-w-0 items-center gap-2">
                  <PlayerAvatar
                    player={playerReference(marketMove.espn_player_id, marketMove.player_name, marketMove.position)}
                    size="md"
                    variant="portrait"
                  />
                  <PositionPill pos={marketMove.position} />
                  <span className="truncate">{marketMove.player_name ?? "Unknown player"}</span>
                </span>
                <span className="mono text-[11px] font-semibold leading-snug text-green">
                  {marketMove.market_move_label}
                </span>
              </span>
            ) : DASH
          }
          secondary={
            marketMove
              ? `FFC ${num(marketMove.current_ffc_adp)} vs draft ${num(marketMove.draft_time_adp)} · ${marketMove.share}`
              : undefined
          }
        />
      </div>
      <Panel className="mt-3 border-coldline bg-cold/60 px-4 py-3">
        <div className="grid gap-4 sm:grid-cols-2 sm:divide-x sm:divide-coldline">
          <Stat
            label="Core players"
            value={String(data.core_dart.core_players)}
            description={CARD_HELP.corePlayers}
          />
          <div className="sm:pl-4">
            <Stat
              label="One-league darts"
              value={String(data.core_dart.dart_players)}
              description={CARD_HELP.darts}
            />
          </div>
        </div>
      </Panel>
    </div>
  );
}

function PositionCapitalPanel({ data }: { data: PortfolioExposure }) {
  return (
    <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <CardTitle title="Positional draft capital vs field" description={CARD_HELP.positionalCapital} />
        <span className="mono text-[10px] text-muted">
          {data.coverage.my_teams_in_scope} my teams · {data.coverage.field_teams_in_scope} field teams
        </span>
      </div>
      <div className="mt-4 grid gap-3">
        {data.positional_spend.map((row) => (
          <div key={row.position} className="grid grid-cols-[2.75rem_minmax(0,1fr)_auto] items-center gap-2">
            <PositionPill pos={row.position} />
            <div className="space-y-1">
              <div className="h-1.5 overflow-hidden rounded bg-rowhover">
                <div
                  className="h-full rounded"
                  style={{ width: `${row.pick_value_pct}%`, backgroundColor: POSITION_COLORS[row.position] }}
                />
              </div>
              <div className="h-1.5 overflow-hidden rounded bg-rowhover">
                <div
                  className="h-full rounded"
                  style={{ width: `${row.field_pick_value_pct}%`, backgroundColor: "var(--color-muted)" }}
                />
              </div>
            </div>
            <div className="mono text-right text-[10px]">
              <div className="text-primary">{num(row.pick_value_pct)}%</div>
              <div className="text-muted">{num(row.field_pick_value_pct)}%</div>
              <LeverageValue value={row.leverage_pp} />
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function RoundFingerprint({ data }: { data: PortfolioExposure }) {
  const series = fingerprintSeries(data.round_fingerprint);
  return (
    <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <CardTitle title="Draft fingerprint vs field" description={CARD_HELP.draftFingerprint} />
        <CoverageChip label="snake picks" value={String(data.coverage.pick_value_picks)} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-4 rounded-sm bg-primary" />
          My teams
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-px w-4 border-t border-dashed border-muted" />
          Field
        </span>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="round-fingerprint-chart">
        {series.map((item) => (
          <div key={item.position} className="min-w-0 border-t border-coldline pt-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <PositionPill pos={item.position} />
              <LeverageValue
                value={data.positional_spend.find((row) => row.position === item.position)?.leverage_pp ?? null}
              />
            </div>
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={item.data} margin={{ top: 6, right: 8, bottom: 2, left: 0 }}>
                  <CartesianGrid stroke="var(--color-line)" vertical={false} />
                  <XAxis
                    dataKey="bucket"
                    height={34}
                    label={{ value: "Draft round", position: "insideBottom", offset: -2 }}
                    tick={{ fill: "var(--color-muted)", fontSize: 9 }}
                    interval={1}
                  />
                  <YAxis
                    width={48}
                    domain={[0, "auto"]}
                    allowDecimals
                    label={{ value: "Picks / team", angle: -90, position: "insideLeft" }}
                    tickFormatter={(value) => num(Number(value), 1)}
                    tick={{ fill: "var(--color-muted)", fontSize: 9 }}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-panel)",
                      border: "1px solid var(--color-line)",
                      borderRadius: 6,
                      color: "var(--color-primary)",
                      fontSize: 12,
                    }}
                    formatter={(value, name, chart) => {
                      const payload = chart.payload as FingerprintSeries["data"][number];
                      const isField = name === "field_picks_per_team";
                      const pickCount = isField ? payload.field_pick_count : payload.pick_count;
                      return [
                        `${num(Number(value), 2)} picks/team (${pickCount} total picks)`,
                        isField ? "Field" : "My teams",
                      ];
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="picks_per_team"
                    stroke={POSITION_COLORS[item.position] ?? "var(--color-primary)"}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="field_picks_per_team"
                    stroke="var(--color-muted)"
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function ExposureRollups({ data }: { data: PortfolioExposure }) {
  return (
    <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
      <CardTitle title="NFL team concentration" description={CARD_HELP.teamConcentration} />
      <div className="mt-3 space-y-3">
        {data.nfl_team_concentration.slice(0, 7).map((row) => (
          <div key={row.nfl_team}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
              <span className="mono text-secondary">{row.nfl_team}</span>
              <span className="mono text-primary">
                {num(row.penetration_pct)}% · {row.penetration_share} teams
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-rowhover">
              <div
                className="h-full rounded bg-greenbar"
                style={{ width: `${row.penetration_pct}%` }}
              />
            </div>
            <div className="mono mt-1 text-right text-[10px] text-muted">
              {num(row.players_per_team, 2)}x · {row.players_per_team_share} players/team
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function AdpSection({ data }: { data: PortfolioDraftAdp }) {
  const teamColumns = useMemo<ColumnDef<DraftAdpTeam>[]>(
    () => [
      {
        accessorKey: "league_name",
        header: "League",
        cell: ({ row }) => (
          <Link to={`/league/${row.original.league_id}`} className="text-primary hover:text-red">
            {row.original.league_name ?? "League"}
          </Link>
        ),
      },
      { accessorKey: "team_name", header: "Team" },
      { accessorKey: "draft_type", header: "Draft" },
      {
        accessorKey: "draft_value_capture_espn",
        header: "vs. draft-time ADP",
        cell: ({ getValue }) => <Delta value={getValue() as number | null} />,
      },
      {
        accessorKey: "draft_value_capture_ffc",
        header: "vs. current market ADP",
        cell: ({ getValue }) => <Delta value={getValue() as number | null} />,
      },
      {
        accessorKey: "draft_adp_source_disagreement",
        header: "Source disagreement",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null)}</span>,
      },
      {
        accessorKey: "draft_value_capture_espn_vs_portfolio_median",
        header: "ESPN vs median",
        cell: ({ getValue }) => <Delta value={getValue() as number | null} />,
      },
    ],
    [],
  );
  const sourceSet = data.source_sets[0] ?? null;
  const ffcReady = sourceSet?.pulled_at != null;
  const ffcStale = data.source_sets.some((source) => source.stale);

  return (
    <section className="scroll-mt-28 py-6" aria-labelledby="adp-heading">
      <SectionHeader
        eyebrow="ADP"
        title="Draft value capture by source"
        description={CARD_HELP.adp}
        titleId="adp-heading"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <CoverageChip label="teams in scope" value={String(data.coverage.teams_in_scope)} />
        <CoverageChip label="auction teams excluded" value={String(data.coverage.auction_teams)} />
        <CoverageChip label="keeper picks excluded" value={String(data.coverage.keeper_picks)} />
        <CoverageChip
          label="ESPN picks without ADP"
          value={`${data.coverage.picks_without_espn_adp} / ${data.coverage.eligible_picks}`}
          warn={data.coverage.picks_without_espn_adp > 0}
        />
        <CoverageChip
          label="FFC picks without ADP"
          value={`${data.coverage.picks_without_ffc_adp} / ${data.coverage.eligible_picks}`}
          warn={data.coverage.picks_without_ffc_adp > 0}
        />
        <CoverageChip
          label="not listed in FFC snapshot"
          value={String(data.coverage.ffc_snapshot_excluded_players)}
          warn={data.coverage.ffc_snapshot_excluded_players > 0}
        />
        <CoverageChip
          label="FFC resolver failures"
          value={String(data.coverage.ffc_resolution_failures)}
          warn={data.coverage.ffc_resolution_failures > 0}
        />
      </div>

      {data.unmatched_players.length > 0 && (
        <details className="mt-3 max-w-2xl rounded-md border border-line bg-row px-3 py-2 text-xs">
          <summary className="cursor-pointer text-secondary">
            Inspect {data.coverage.ffc_unmatched_players} players without current FFC ADP
          </summary>
          <div className="mt-2 space-y-1 border-t border-line pt-2">
            {data.unmatched_players.map((player) => (
              <div key={player.espn_player_id} className="flex flex-wrap items-center gap-2">
                <PlayerAvatar
                  player={playerReference(player.espn_player_id, player.player_name, player.position)}
                  size="md"
                  variant="portrait"
                />
                <PositionPill pos={player.position} />
                <span className="text-primary">{player.player_name ?? "Unknown player"}</span>
                <span className="mono text-muted">{player.nfl_team ?? DASH}</span>
                <span className="ml-auto text-muted">
                  {player.reason === "not_in_snapshot"
                    ? "not listed in current snapshot"
                    : player.reason === "missing_adp"
                      ? "listed without ADP"
                      : "resolver failure"}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      {!ffcReady && (
        <div className="mt-4">
          <EmptyState
            title="FFC snapshot unavailable"
            hint="ESPN draft-time capture remains available; current-market figures stay pending."
          />
        </div>
      )}
      {ffcReady && ffcStale && (
        <div className="mt-4 rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs text-gold">
          The latest FFC snapshot is stale. Current-market values are shown with their pull date.
        </div>
      )}

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <AdpSourcePanel
          heading="vs. draft-time ADP"
          source="ESPN"
          stamp="captured at sync"
          rows={data.by_position.filter((row) => row.source === "espn")}
        />
        <AdpSourcePanel
          heading="vs. current market ADP"
          source={ffcSourceLabel(sourceSet)}
          stamp={shortDate(sourceSet?.pulled_at ?? null)}
          rows={data.by_position.filter((row) => row.source === "ffc")}
        />
      </div>

      <div className="mt-5">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle title="Team capture" description={CARD_HELP.teamCapture} />
          {data.teams[0] && (
            <span className="mono text-[10px] text-muted">
              Portfolio median ESPN {num(data.teams[0].draft_value_capture_espn_portfolio_median)}
              {" · "}FFC {num(data.teams[0].draft_value_capture_ffc_portfolio_median)}
            </span>
          )}
        </div>
        {data.teams.length ? (
          <Panel className="overflow-hidden border-coldline bg-cold/60">
            <div className="max-h-[28rem] overflow-y-auto">
              <DataTable
                data={data.teams}
                columns={teamColumns}
                initialSort={[{ id: "draft_value_capture_espn", desc: true }]}
                ariaLabel="Draft value capture by team"
              />
            </div>
          </Panel>
        ) : (
          <EmptyState title="No snake-draft ADP rows in scope" />
        )}
      </div>

      {(data.biggest_values.length > 0 || data.biggest_reaches.length > 0) && (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <PickList title="Biggest values" rows={data.biggest_values} />
          <PickList title="Biggest reaches" rows={data.biggest_reaches} />
        </div>
      )}
    </section>
  );
}

function AdpSourcePanel({
  heading,
  source,
  stamp,
  rows,
}: {
  heading: string;
  source: string;
  stamp: string;
  rows: PortfolioDraftAdp["by_position"];
}) {
  const description = heading.includes("draft-time") ? CARD_HELP.draftTimeAdp : CARD_HELP.currentAdp;
  return (
    <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <CardTitle title={heading} description={description} />
          <div className="mt-0.5 text-[11px] text-muted">{source}</div>
        </div>
        <span className="mono text-[10px] text-secondary">{stamp}</span>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.bucket} className="border-l border-line pl-2">
            <div className="flex items-center gap-2">
              <PositionPill pos={row.bucket} />
              <Delta value={row.avg_delta} />
            </div>
            <div className="mt-2 h-1.5 rounded bg-rowhover">
              <div
                className="h-full rounded bg-greenbar"
                style={{ width: `${row.mean_percentile ?? 0}%` }}
                title={`Mean percentile ${num(row.mean_percentile)}%`}
              />
            </div>
            <div className="mono mt-1 text-[9px] text-muted">
              {row.picks_with_adp} / {row.eligible_picks} picks · median {signed(row.portfolio_median_delta)}
            </div>
            <div className="mono mt-0.5 text-[9px] text-muted">
              p25 {signed(row.portfolio_p25_delta)} · p75 {signed(row.portfolio_p75_delta)}
            </div>
          </div>
        ))}
        {!rows.length && <div className="text-xs text-muted">Pending</div>}
      </div>
    </Panel>
  );
}

function ffcSourceLabel(source: PortfolioDraftAdp["source_sets"][number] | null): string {
  if (!source) return "FFC";
  const used = `FFC · ${source.used_teams}-team ${source.used_format.toUpperCase()}`;
  if (source.exact_match) return used;
  return `${used} fallback for ${source.requested_teams}-team ${source.requested_format.toUpperCase()}`;
}

function PickList({ title, rows }: { title: string; rows: DraftAdpPick[] }) {
  const description = title === "Biggest values" ? CARD_HELP.biggestValues : CARD_HELP.biggestReaches;
  return (
    <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
      <CardTitle title={title} description={description} />
      <div className="mt-3 space-y-2">
        {rows.map((row, index) => (
          <div key={`${row.source}-${row.league_id}-${row.espn_player_id}-${index}`} className="flex items-center gap-2 text-xs">
            <PlayerAvatar
              player={playerReference(row.espn_player_id, row.player_name, row.position)}
              size="md"
              variant="portrait"
            />
            <PositionPill pos={row.position} />
            <span className="min-w-0 flex-1 truncate" title={row.player_name ?? "Unknown player"}>
              {row.player_name ?? "Unknown player"}
              <span className="ml-1 text-muted">· {row.league_name}</span>
            </span>
            <span className="text-[10px] text-muted">{row.source_label}</span>
            <Delta value={row.delta} />
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Delta({ value }: { value: number | null }) {
  const tone = value == null ? "text-muted" : value >= 0 ? "text-green" : "text-red";
  return <span className={`mono font-semibold ${tone}`}>{signed(value)}</span>;
}

function StrategySection({ data }: { data: PortfolioStrategies }) {
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null);
  const filteredTeams = useMemo(
    () => (
      selectedStrategy
        ? data.teams.filter((team) => team.primary_label === selectedStrategy)
        : data.teams
    ),
    [data.teams, selectedStrategy],
  );
  const edgeColumns = useMemo<ColumnDef<PortfolioStrategies["mean_edge_index_by_primary"][number]>[]>(
    () => [
      { accessorKey: "label", header: "Primary strategy" },
      {
        accessorKey: "mean_edge_index_score",
        header: "Mean Edge Index",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null)}</span>,
      },
      {
        accessorKey: "mean_edge_index_score_unrounded",
        header: "Raw mean",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null, 4)}</span>,
      },
      {
        accessorKey: "stddev_edge_index_score",
        header: "Std dev",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null, 2)}</span>,
      },
      {
        id: "ci",
        header: "95% CI",
        accessorFn: (row) => row.ci95_low,
        cell: ({ row }) => (
          <span className="mono text-xs">
            {row.original.ci95_low == null
              ? DASH
              : `${num(row.original.ci95_low)}-${num(row.original.ci95_high)}`}
          </span>
        ),
      },
      {
        accessorKey: "teams_with_edge_index",
        header: "Teams scored",
        cell: ({ getValue }) => <span className="mono">{String(getValue())}</span>,
      },
    ],
    [],
  );
  const teamColumns = useMemo<ColumnDef<PortfolioStrategies["teams"][number]>[]>(
    () => [
      {
        accessorKey: "league_name",
        header: "League",
        cell: ({ row }) => (
          <Link to={`/league/${row.original.league_id}`} className="text-primary hover:text-red">
            {row.original.league_name ?? "League"}
          </Link>
        ),
      },
      { accessorKey: "team_name", header: "Team" },
      { accessorKey: "primary_label", header: "Primary" },
      {
        accessorKey: "primary_confidence",
        header: "Confidence",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number, 2)}</span>,
      },
      { accessorKey: "secondary_label", header: "Secondary", cell: ({ getValue }) => String(getValue() ?? DASH) },
      {
        accessorKey: "edge_index_score",
        header: "Edge Index",
        cell: ({ getValue }) => <span className="mono">{num(getValue() as number | null)}</span>,
      },
      {
        id: "triggers",
        header: "Triggering picks",
        enableSorting: false,
        cell: ({ row }) => (
          <span className="text-xs text-secondary">
            {Object.entries(row.original.triggering_picks)
              .flatMap(([label, picks]) => picks.map((pick) => `${label}: #${pick.overall} ${pick.player_name ?? "?"}`))
              .join(" · ") || DASH}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <section className="scroll-mt-28 py-6" aria-labelledby="strategy-heading">
      <SectionHeader
        eyebrow="Strategy"
        title="Draft strategy distribution"
        description={CARD_HELP.strategy}
        titleId="strategy-heading"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <CoverageChip label="teams in scope" value={String(data.coverage.teams_in_scope)} />
        <CoverageChip label="qualifying snake teams" value={String(data.coverage.qualifying_teams)} />
        <CoverageChip label="auction teams excluded" value={String(data.coverage.auction_teams)} />
        <CoverageChip label="keeper picks excluded" value={String(data.coverage.keeper_picks)} />
        <CoverageChip
          label="missing classifications"
          value={String(data.coverage.missing_strategy_teams)}
          warn={data.coverage.missing_strategy_teams > data.coverage.auction_teams}
        />
      </div>
      {data.coverage.qualifying_teams > 0 && data.coverage.qualifying_teams < 3 && (
        <LowSampleNote />
      )}

      {data.coverage.qualifying_teams === 0 ? (
        <div className="mt-5"><EmptyState title="No classified snake drafts in scope" /></div>
      ) : (
        <>
          <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(18rem,0.7fr)]">
            <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
              <CardTitle title="Primary RB structure" description={CARD_HELP.primaryRb} />
              <div className="mt-2 grid items-center gap-4 sm:grid-cols-[minmax(14rem,1fr)_minmax(15rem,0.8fr)]">
                <div className="h-64" data-testid="strategy-distribution-chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Tooltip
                        contentStyle={{
                          background: "var(--color-panel)",
                          border: "1px solid var(--color-line)",
                          borderRadius: 6,
                          color: "var(--color-primary)",
                          fontSize: 12,
                        }}
                        formatter={(value, _name, item) => [
                          `${item.payload.pct}% (${value} / ${data.coverage.qualifying_teams} teams)`,
                          item.payload.label,
                        ]}
                      />
                      <Pie
                        data={data.primary_distribution}
                        dataKey="count"
                        nameKey="label"
                        innerRadius={62}
                        outerRadius={96}
                        paddingAngle={2}
                        isAnimationActive={false}
                        onClick={(entry) => {
                          const label = (entry as { label?: string }).label;
                          if (label) setSelectedStrategy(label);
                        }}
                      >
                        {data.primary_distribution.map((row, index) => (
                          <Cell key={row.label} fill={STRATEGY_COLORS[index % STRATEGY_COLORS.length]} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="space-y-2">
                  {data.primary_distribution.map((row, index) => (
                    <div key={row.label} className="grid grid-cols-[0.65rem_minmax(0,1fr)_auto] items-center gap-2 text-xs">
                      <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: STRATEGY_COLORS[index % STRATEGY_COLORS.length] }} />
                      <button
                        type="button"
                        onClick={() => setSelectedStrategy(row.label)}
                        className={`min-w-0 truncate text-left hover:text-red ${
                          selectedStrategy === row.label ? "text-primary" : "text-secondary"
                        }`}
                      >
                        {row.label}
                      </button>
                      <span className="mono text-primary">{num(row.pct)}% · {row.count} / {data.coverage.qualifying_teams}</span>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>

            <Panel className="min-w-0 border-coldline bg-cold/60 p-4">
              <CardTitle title="Secondary timing signals" description={CARD_HELP.secondarySignals} />
              <div className="mt-4 space-y-4">
                {data.secondary_distribution.map((row, index) => (
                  <div key={row.label}>
                    <div className="flex items-baseline justify-between gap-3 text-xs">
                      <span className="text-secondary">{row.label}</span>
                      <span className="mono">{num(row.pct)}% · {row.count} / {data.coverage.qualifying_teams}</span>
                    </div>
                    <div className="mt-1.5 h-2 overflow-hidden rounded bg-rowhover">
                      <div
                        className="h-full rounded"
                        style={{
                          width: `${row.pct}%`,
                          backgroundColor: STRATEGY_COLORS[(index + 1) % STRATEGY_COLORS.length],
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <CardTitle title="Mean Edge Index by primary strategy" description={CARD_HELP.meanEdge} />
              <span className="max-w-3xl text-right text-[11px] text-muted">
                {data.comparison_note} {data.uncertainty_note}
              </span>
            </div>
            <Panel className="overflow-hidden border-coldline bg-cold/60">
              <DataTable
                data={data.mean_edge_index_by_primary}
                columns={edgeColumns}
                ariaLabel="Mean Edge Index by primary draft strategy"
              />
            </Panel>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <CardTitle title="Team classifications" description={CARD_HELP.classifications} />
              {selectedStrategy && (
                <button
                  type="button"
                  onClick={() => setSelectedStrategy(null)}
                  className="mono rounded border border-line px-2 py-1 text-[10px] text-secondary hover:bg-rowhover hover:text-primary"
                >
                  All · showing {selectedStrategy}
                </button>
              )}
            </div>
            <Panel className="overflow-hidden border-coldline bg-cold/60">
              <div className="max-h-[28rem] overflow-y-auto">
                <DataTable
                  data={filteredTeams}
                  columns={teamColumns}
                  initialSort={[{ id: "primary_label", desc: false }]}
                  ariaLabel="Draft strategy classifications by team"
                />
              </div>
            </Panel>
          </div>
        </>
      )}
    </section>
  );
}

function Stat({ label, value, description }: { label: string; value: string; description: string }) {
  return (
    <div>
      <div className="mono text-lg font-semibold text-frost">{value}</div>
      <div className="display-face mt-0.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
        {label}
        <InfoTip label={label} description={description} />
      </div>
    </div>
  );
}

function LowSampleNote() {
  return (
    <div className="mt-3 rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs text-gold">
      Low sample: fewer than 3 teams are in scope. Percentages are shown with their raw denominator.
    </div>
  );
}
