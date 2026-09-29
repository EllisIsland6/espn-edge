import {
  Component,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type ReactElement,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import {
  getPlayerOpportunity,
  getPortfolioOpportunityCharts,
  type OpportunityChartDefinition,
  type OpportunityChartId,
  type OpportunityChartPoint,
  type OpportunityChartPosition,
  type OpportunityView,
  type PlayerOpportunity,
  type PortfolioOpportunityCharts,
} from "../api";
import { DASH, num } from "../lib/format";
import { EmptyState, Panel, Spinner } from "./ui";

const MARGIN = { top: 40, right: 28, bottom: 58, left: 68 };
const ZOOM_LEVELS = [1, 1.5, 2, 3, 4] as const;
const CHART_LABELS: Record<OpportunityChartId, string> = {
  target_air: "Targets + air yards",
  yards_tds: "Yards + TDs",
  adot_targets: "aDOT + volume",
  opportunity_production: "Role + production",
  passing_environment: "Passing environment",
};
const SIGNAL_LABELS: Record<OpportunityChartPoint["signal"], string> = {
  opportunity_ahead: "Opportunity ahead",
  production_ahead: "Production ahead",
  aligned: "Aligned",
  pending: "Pending",
};
const SIGNAL_STROKES: Record<OpportunityChartPoint["signal"], string> = {
  opportunity_ahead: "var(--color-green)",
  production_ahead: "var(--color-red)",
  aligned: "var(--color-ice)",
  pending: "var(--color-muted)",
};

type Size = { width: number; height: number };
type ScatterShapeProps = {
  cx?: number;
  cy?: number;
  payload?: OpportunityChartPoint;
};
type LabelPlacement = {
  id: number;
  text: string;
  x: number;
  y: number;
  lineX: number;
  lineY: number;
  width: number;
};
type ZoomCenter = { x: number; y: number };

export class OpportunityChartBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <Panel className="mt-4 border-red/35 bg-red/5 px-3 py-3 text-xs text-red">
          <span className="mono mr-2 text-[9px]">OPP-CHART-RENDER</span>
          The chart could not be displayed; the data table is still available.
        </Panel>
      );
    }
    return this.props.children;
  }
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function metric(point: OpportunityChartPoint, key: string): number | null {
  const value = point[key];
  return finite(value) ? value : null;
}

function metricText(key: string, value: number | null): string {
  if (value == null) return DASH;
  if (key.includes("share") || key.includes("percentile") || key === "opportunity_score") {
    return `${num(value)}%`;
  }
  if (key === "receiving_tds_per_game") return num(value, 2);
  return num(value, 1);
}

function project(value: number, low: number, high: number, start: number, end: number): number {
  if (high === low) return (start + end) / 2;
  return start + ((value - low) / (high - low)) * (end - start);
}

function zoomedDomain(
  domain: OpportunityChartDefinition["domain"],
  zoom: number,
  center: ZoomCenter | null,
): OpportunityChartDefinition["domain"] {
  if (zoom <= 1) return domain;
  const xSpan = (domain.x_max - domain.x_min) / zoom;
  const ySpan = (domain.y_max - domain.y_min) / zoom;
  const xCenter = center?.x ?? (domain.x_min + domain.x_max) / 2;
  const yCenter = center?.y ?? (domain.y_min + domain.y_max) / 2;
  const xMin = Math.min(
    domain.x_max - xSpan,
    Math.max(domain.x_min, xCenter - xSpan / 2),
  );
  const yMin = Math.min(
    domain.y_max - ySpan,
    Math.max(domain.y_min, yCenter - ySpan / 2),
  );
  return { x_min: xMin, x_max: xMin + xSpan, y_min: yMin, y_max: yMin + ySpan };
}

function initials(name: string | null): string {
  return (name ?? "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "?";
}

function overlap(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  return !(
    a.x + a.width + 3 <= b.x ||
    b.x + b.width + 3 <= a.x ||
    a.y + a.height + 2 <= b.y ||
    b.y + b.height + 2 <= a.y
  );
}

function layoutLabels(
  points: OpportunityChartPoint[],
  chart: OpportunityChartDefinition,
  size: Size,
  selectedId: number | null,
): LabelPlacement[] {
  if (selectedId == null) return [];
  const plot = {
    left: MARGIN.left,
    right: size.width - MARGIN.right,
    top: MARGIN.top,
    bottom: size.height - MARGIN.bottom,
  };
  const uniqueCoordinates = new Set<string>();
  const candidates = points
    .filter((point) => metric(point, chart.x_key) != null && metric(point, chart.y_key) != null)
    .filter((point) => point.espn_player_id === selectedId)
    .sort((a, b) => {
      const score = (point: OpportunityChartPoint) => (
        (point.espn_player_id === selectedId ? 1_000_000 : 0) +
        (point.mine_leagues > 0 ? 100_000 : 0) +
        (point.available_leagues > 0 ? 10_000 : 0) +
        Math.abs(point.opportunity_gap ?? 0) * 10
      );
      return score(b) - score(a) ||
        String(a.player_name ?? "").localeCompare(String(b.player_name ?? "")) ||
        a.espn_player_id - b.espn_player_id;
    })
    .filter((point) => {
      const x = metric(point, chart.x_key) as number;
      const y = metric(point, chart.y_key) as number;
      const key = `${x.toFixed(6)}:${y.toFixed(6)}`;
      if (uniqueCoordinates.has(key) && point.espn_player_id !== selectedId) return false;
      uniqueCoordinates.add(key);
      return true;
    });
  const accepted: Array<{ x: number; y: number; width: number; height: number }> = [];
  const output: LabelPlacement[] = [];
  for (const point of candidates) {
    const x = project(
      metric(point, chart.x_key) as number,
      chart.domain.x_min,
      chart.domain.x_max,
      plot.left,
      plot.right,
    );
    const y = project(
      metric(point, chart.y_key) as number,
      chart.domain.y_min,
      chart.domain.y_max,
      plot.bottom,
      plot.top,
    );
    const text = point.player_name ?? point.nfl_team ?? "Unknown";
    const width = Math.min(148, Math.max(46, text.length * 6.2 + 12));
    const height = 18;
    const anchors = [
      { dx: 13, dy: -9 },
      { dx: -width - 13, dy: -9 },
      { dx: -width / 2, dy: -29 },
      { dx: -width / 2, dy: 12 },
      { dx: 11, dy: -28 },
      { dx: -width - 11, dy: -28 },
      { dx: 11, dy: 11 },
      { dx: -width - 11, dy: 11 },
    ];
    const chosen = anchors.find(({ dx, dy }) => {
      const box = { x: x + dx, y: y + dy, width, height };
      return box.x >= plot.left && box.x + width <= plot.right &&
        box.y >= plot.top && box.y + height <= plot.bottom &&
        accepted.every((prior) => !overlap(box, prior));
    });
    if (!chosen) continue;
    const box = { x: x + chosen.dx, y: y + chosen.dy, width, height };
    accepted.push(box);
    output.push({
      id: point.espn_player_id,
      text,
      x: box.x,
      y: box.y,
      lineX: x,
      lineY: y,
      width,
    });
  }
  return output;
}

function playerPortraitUrl(espnPlayerId: number): string {
  return `/api/players/${espnPlayerId}/portrait`;
}

function PlayerMarker({
  cx,
  cy,
  point,
  selected,
  clusterSize,
  onPreview,
  onSelect,
}: {
  cx: number;
  cy: number;
  point: OpportunityChartPoint;
  selected: boolean;
  clusterSize: number;
  onPreview: (point: OpportunityChartPoint) => void;
  onSelect: (point: OpportunityChartPoint) => void;
}) {
  const [portraitReady, setPortraitReady] = useState(false);
  const source = playerPortraitUrl(point.espn_player_id);
  const name = `${point.player_name ?? "Unknown player"}, ${point.nfl_team ?? "team unknown"}, ESPN PPR rank ${point.espn_rank_ppr}`;
  const markerSize = selected ? 58 : 50;
  const clipId = `opportunity-portrait-${point.espn_player_id}`;
  function handleKeyDown(event: ReactKeyboardEvent<SVGGElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(point);
    }
  }
  return (
    <g
      transform={`translate(${cx} ${cy})`}
      role="button"
      tabIndex={0}
      aria-label={name}
      onMouseEnter={() => onPreview(point)}
      onFocus={() => onPreview(point)}
      onClick={() => onSelect(point)}
      onKeyDown={handleKeyDown}
      className="cursor-pointer outline-none"
      data-testid="opportunity-chart-marker"
    >
      <defs>
        <clipPath id={clipId}>
          <circle r={markerSize / 2 - 2} />
        </clipPath>
      </defs>
      <circle
        r={markerSize / 2}
        fill="var(--color-panel)"
        stroke={selected ? "var(--color-frost)" : SIGNAL_STROKES[point.signal]}
        strokeWidth={selected ? 3 : 2}
      />
      <text
        textAnchor="middle"
        dominantBaseline="central"
        fill="var(--color-primary)"
        fontSize="11"
        fontWeight="700"
        opacity={portraitReady ? 0 : 1}
        data-portrait-fallback="true"
      >
        {initials(point.player_name)}
      </text>
      <image
        href={source}
        x={-markerSize / 2 + 2}
        y={-markerSize / 2 + 2}
        width={markerSize - 4}
        height={markerSize - 4}
        preserveAspectRatio="xMidYMid slice"
        clipPath={`url(#${clipId})`}
        opacity={portraitReady ? 1 : 0}
        onLoad={() => setPortraitReady(true)}
        onError={() => setPortraitReady(false)}
        data-testid="opportunity-chart-portrait"
      />
      {clusterSize > 1 && (
        <g transform={`translate(${markerSize / 2 - 4} ${-markerSize / 2 + 4})`} data-testid="opportunity-chart-cluster">
          <circle r="9" fill="var(--color-frost)" stroke="var(--color-panel)" />
          <text textAnchor="middle" dominantBaseline="central" fontSize="8" fontWeight="800" fill="var(--color-bg)">
            {clusterSize}
          </text>
        </g>
      )}
    </g>
  );
}

function ChartTooltip({
  active,
  payload,
  chart,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: OpportunityChartPoint }>;
  chart: OpportunityChartDefinition;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div
      className="max-w-64 rounded-md border border-coldline bg-panel/95 px-3 py-2 text-[11px] shadow-xl"
      data-testid="opportunity-chart-tooltip"
    >
      <div className="font-semibold text-primary">{point.player_name ?? "Unknown player"}</div>
      <div className="mono text-[9px] text-muted">
        {point.nfl_team ?? DASH} · ESPN PPR #{num(point.espn_rank_ppr, 0)} · {point.sample_games} games · through W{point.through_week}
      </div>
      <div className="mt-1 text-secondary">
        {chart.x_label}: <span className="mono text-primary">{metricText(chart.x_key, metric(point, chart.x_key))}</span>
      </div>
      <div className="text-secondary">
        {chart.y_label}: <span className="mono text-primary">{metricText(chart.y_key, metric(point, chart.y_key))}</span>
      </div>
      <div className="mt-1 text-muted">
        {SIGNAL_LABELS[point.signal]} · Mine {point.mine_leagues} · Field {point.field_leagues} · Free {point.available_leagues}
      </div>
    </div>
  );
}

function LabelOverlay({
  points,
  chart,
  size,
  selectedId,
  season,
  throughWeek,
}: {
  points: OpportunityChartPoint[];
  chart: OpportunityChartDefinition;
  size: Size;
  selectedId: number | null;
  season: number;
  throughWeek: number | null;
}) {
  const placements = useMemo(
    () => layoutLabels(points, chart, size, selectedId),
    [points, chart, size, selectedId],
  );
  if (!size.width || !size.height) return null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
      viewBox={`0 0 ${size.width} ${size.height}`}
      aria-hidden="true"
      data-testid="opportunity-chart-label-layer"
    >
      <text x={MARGIN.left} y={18} fill="var(--color-primary)" fontSize="13" fontWeight="700">
        {chart.title}
      </text>
      <text x={size.width - MARGIN.right} y={18} textAnchor="end" fill="var(--color-muted)" fontSize="9">
        {season} · last 3 observed games · through W{throughWeek ?? DASH}
      </text>
      {chart.quadrants.map((quadrant) => {
        const x = quadrant.x_side === "low" ? MARGIN.left + 8 : size.width - MARGIN.right - 8;
        const y = quadrant.y_side === "high" ? MARGIN.top + 14 : size.height - MARGIN.bottom - 8;
        return (
          <text
            key={quadrant.key}
            x={x}
            y={y}
            textAnchor={quadrant.x_side === "low" ? "start" : "end"}
            fill="var(--color-muted)"
            fontSize="8"
            data-testid="opportunity-chart-quadrant"
          >
            {quadrant.label}
          </text>
        );
      })}
      {placements.map((label) => (
        <g key={label.id} data-testid="opportunity-chart-label">
          <line
            x1={label.lineX}
            y1={label.lineY}
            x2={label.x + (label.lineX < label.x ? 0 : label.lineX > label.x + label.width ? label.width : label.width / 2)}
            y2={label.y + 9}
            stroke="var(--color-muted)"
            strokeWidth="0.7"
          />
          <rect
            x={label.x}
            y={label.y}
            width={label.width}
            height="18"
            rx="3"
            fill="var(--color-panel)"
            fillOpacity="0.88"
            stroke="var(--color-line)"
          />
          <text x={label.x + 6} y={label.y + 12} fill="var(--color-primary)" fontSize="9">
            {label.text.length > 22 ? `${label.text.slice(0, 21)}…` : label.text}
          </text>
        </g>
      ))}
      <text
        x={size.width - MARGIN.right}
        y={size.height - 5}
        textAnchor="end"
        fill="var(--color-muted)"
        fontSize="8"
      >
        NFL data via nflverse · CC BY 4.0
      </text>
    </svg>
  );
}

async function blobDataUrl(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function exportPlot(
  host: HTMLDivElement,
  chart: OpportunityChartDefinition,
  position: OpportunityChartPosition,
  season: number,
  throughWeek: number | null,
): Promise<void> {
  const surface = host.querySelector<SVGSVGElement>("svg.recharts-surface");
  const overlay = host.querySelector<SVGSVGElement>("[data-testid='opportunity-chart-label-layer']");
  if (!surface || !overlay) throw new Error("plot SVG unavailable");
  const clone = surface.cloneNode(true) as SVGSVGElement;
  const overlayClone = overlay.cloneNode(true) as SVGSVGElement;
  for (const child of Array.from(overlayClone.childNodes)) clone.appendChild(child);
  const width = Math.max(1, Math.round(surface.getBoundingClientRect().width));
  const height = Math.max(1, Math.round(surface.getBoundingClientRect().height));
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  clone.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const rootStyles = getComputedStyle(document.documentElement);
  const variables = [
    "--color-bg", "--color-panel", "--color-line", "--color-primary", "--color-muted",
    "--color-frost", "--color-green", "--color-red", "--color-ice",
  ].map((name) => `${name}:${rootStyles.getPropertyValue(name).trim()}`).join(";");
  clone.setAttribute("style", `${variables};background:var(--color-panel);font-family:ui-sans-serif,system-ui,sans-serif`);
  const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  background.setAttribute("width", "100%");
  background.setAttribute("height", "100%");
  background.setAttribute("fill", "var(--color-panel)");
  clone.insertBefore(background, clone.firstChild);
  for (const image of Array.from(clone.querySelectorAll("image"))) {
    const href = image.getAttribute("href");
    if (!href) continue;
    try {
      const response = await fetch(href);
      if (!response.ok) throw new Error("logo unavailable");
      image.setAttribute("href", await blobDataUrl(await response.blob()));
    } catch {
      const parent = image.parentElement;
      image.remove();
      const fallback = parent?.querySelector<SVGTextElement>("[data-logo-fallback='true']");
      fallback?.setAttribute("opacity", "1");
    }
  }
  const serialized = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([serialized], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const bitmap = new Image();
    bitmap.decoding = "async";
    bitmap.src = url;
    await bitmap.decode();
    const canvas = document.createElement("canvas");
    canvas.width = width * 2;
    canvas.height = height * 2;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("canvas unavailable");
    context.scale(2, 2);
    context.drawImage(bitmap, 0, 0, width, height);
    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((value) => value ? resolve(value) : reject(new Error("PNG unavailable")), "image/png");
    });
    const download = document.createElement("a");
    download.href = URL.createObjectURL(png);
    download.download = `opportunity-${chart.id}-${position}-${season}-w${throughWeek ?? "pending"}.png`;
    download.click();
    setTimeout(() => URL.revokeObjectURL(download.href), 0);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function OpportunityChartExplorer({
  view,
  season,
  sourceRunId,
}: {
  view: OpportunityView;
  season: number;
  sourceRunId: string | null;
}) {
  const [position, setPosition] = useState<OpportunityChartPosition>("WR");
  const [activeId, setActiveId] = useState<OpportunityChartId>("target_air");
  const [team, setTeam] = useState("ALL");
  const [zoomIndex, setZoomIndex] = useState(0);
  const [zoomCenter, setZoomCenter] = useState<ZoomCenter | null>(null);
  const [data, setData] = useState<PortfolioOpportunityCharts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PlayerOpportunity | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [exportError, setExportError] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const detailCache = useRef(new Map<string, PlayerOpportunity>());
  const plotRef = useRef<HTMLDivElement>(null);
  const wheelDeltaRef = useRef(0);

  useEffect(() => {
    let active = true;
    setError(null);
    setSelectedId(null);
    setDetail(null);
    setTeam("ALL");
    setZoomIndex(0);
    setZoomCenter(null);
    getPortfolioOpportunityCharts(view, position, season)
      .then((result) => {
        if (!active) return;
        setData(result);
        setActiveId((current) => (
          result.charts.some((chart) => chart.id === current)
            ? current
            : result.charts[0]?.id ?? "opportunity_production"
        ));
      })
      .catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [view, position, season, sourceRunId]);

  useEffect(() => {
    function clear(event: KeyboardEvent) {
      if (event.key === "Escape") setSelectedId(null);
    }
    window.addEventListener("keydown", clear);
    return () => window.removeEventListener("keydown", clear);
  }, []);

  const chart = data?.charts.find((item) => item.id === activeId) ?? data?.charts[0] ?? null;
  const chartDrawable = Boolean(chart && chart.point_count >= 2 && chart.population_point_count >= 3);
  const teams = useMemo(() => (
    [...new Set((data?.points ?? []).map((point) => point.nfl_team).filter((value): value is string => Boolean(value)))]
      .sort((left, right) => left.localeCompare(right))
  ), [data]);
  const chartPoints = useMemo(() => (
    chart && data
      ? data.points.filter((point) => (
          point.sample_games >= 2 &&
          point.espn_rank_ppr <= data.coverage.rank_limit &&
          (team === "ALL" || point.nfl_team === team) &&
          metric(point, chart.x_key) != null &&
          metric(point, chart.y_key) != null
        ))
      : []
  ), [data, chart, team]);
  const selected = chartPoints.find((point) => point.espn_player_id === selectedId) ?? null;
  const activeDomain = useMemo(() => (
    chart ? zoomedDomain(chart.domain, ZOOM_LEVELS[zoomIndex], zoomCenter) : null
  ), [chart, zoomIndex, zoomCenter]);
  const visiblePoints = useMemo(() => (
    activeDomain
      ? chartPoints.filter((point) => {
          if (!chart) return false;
          const x = metric(point, chart.x_key);
          const y = metric(point, chart.y_key);
          return x != null && y != null &&
            x >= activeDomain.x_min && x <= activeDomain.x_max &&
            y >= activeDomain.y_min && y <= activeDomain.y_max;
        })
      : []
  ), [activeDomain, chart, chartPoints]);
  const pointClusters = useMemo(() => {
    const clusters = new Map<string, OpportunityChartPoint[]>();
    if (!chart) return clusters;
    for (const point of visiblePoints) {
      const key = `${metric(point, chart.x_key)?.toFixed(6)}:${metric(point, chart.y_key)?.toFixed(6)}`;
      const members = clusters.get(key) ?? [];
      members.push(point);
      clusters.set(key, members);
    }
    return clusters;
  }, [chart, visiblePoints]);
  const renderedPoints = useMemo(() => (
    [...pointClusters.values()].map((members) => (
      members.find((point) => point.espn_player_id === selectedId) ?? members[0]
    ))
  ), [pointClusters, selectedId]);

  function changeZoom(nextIndex: number, centerOverride?: ZoomCenter) {
    if (!chart) return;
    if (nextIndex <= 0) {
      setZoomIndex(0);
      setZoomCenter(null);
      return;
    }
    if (centerOverride) {
      setZoomCenter(centerOverride);
      setZoomIndex(Math.min(ZOOM_LEVELS.length - 1, nextIndex));
      return;
    }
    const selectedX = selected ? metric(selected, chart.x_key) : null;
    const selectedY = selected ? metric(selected, chart.y_key) : null;
    if (selectedX != null && selectedY != null) {
      setZoomCenter({ x: selectedX, y: selectedY });
    }
    setZoomIndex(Math.min(ZOOM_LEVELS.length - 1, nextIndex));
  }

  useEffect(() => {
    const host = plotRef.current;
    if (!host || !activeDomain || !chartPoints.length) return;
    const chartHost = host;
    const domain = activeDomain;
    function handleTrackpadZoom(event: WheelEvent) {
      // Chrome/Safari expose trackpad pinch as a cancelable ctrl+wheel gesture.
      // Leave ordinary two-finger scrolling untouched so the page remains usable.
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const normalizedDelta = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? event.deltaY * 16
        : event.deltaY;
      wheelDeltaRef.current += normalizedDelta;
      if (Math.abs(wheelDeltaRef.current) < 24) return;
      const direction = wheelDeltaRef.current < 0 ? 1 : -1;
      wheelDeltaRef.current = 0;
      const nextIndex = Math.max(
        0,
        Math.min(ZOOM_LEVELS.length - 1, zoomIndex + direction),
      );
      if (nextIndex === zoomIndex) return;
      const rect = chartHost.getBoundingClientRect();
      const plotWidth = Math.max(1, rect.width - MARGIN.left - MARGIN.right);
      const plotHeight = Math.max(1, rect.height - MARGIN.top - MARGIN.bottom);
      const pointerX = Math.min(plotWidth, Math.max(0, event.clientX - rect.left - MARGIN.left));
      const pointerY = Math.min(plotHeight, Math.max(0, event.clientY - rect.top - MARGIN.top));
      changeZoom(nextIndex, {
        x: domain.x_min + (pointerX / plotWidth) * (domain.x_max - domain.x_min),
        y: domain.y_max - (pointerY / plotHeight) * (domain.y_max - domain.y_min),
      });
    }
    chartHost.addEventListener("wheel", handleTrackpadZoom, { passive: false });
    return () => chartHost.removeEventListener("wheel", handleTrackpadZoom);
  }, [activeDomain, chartPoints.length, zoomIndex]);

  function selectTeam(value: string) {
    setTeam(value);
    setSelectedId(null);
    setDetail(null);
    setZoomIndex(0);
    setZoomCenter(null);
  }

  function selectPoint(point: OpportunityChartPoint) {
    const cacheKey = `${season}:${point.espn_player_id}`;
    setSelectedId(point.espn_player_id);
    setDetail(null);
    const cached = detailCache.current.get(cacheKey);
    if (cached) {
      setDetail(cached);
      return;
    }
    setDetailLoading(true);
    getPlayerOpportunity(point.espn_player_id, season)
      .then((result) => {
        detailCache.current.set(cacheKey, result);
        setDetail(result);
      })
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  }

  function previewPoint(point: OpportunityChartPoint) {
    setSelectedId(point.espn_player_id);
    setDetail(detailCache.current.get(`${season}:${point.espn_player_id}`) ?? null);
    setDetailLoading(false);
  }

  async function handleExport() {
    if (!plotRef.current || !chart || !data || !chartDrawable) return;
    setExporting(true);
    setExportError(false);
    try {
      await exportPlot(plotRef.current, chart, position, data.season, data.through_week);
    } catch {
      setExportError(true);
    } finally {
      setExporting(false);
    }
  }

  return (
    <Panel className="mt-4 min-w-0 overflow-hidden border-coldline bg-cold/60" data-testid="opportunity-chart-explorer">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-coldline px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-coldline" aria-label="Chart position">
            {(["WR", "RB", "TE"] as OpportunityChartPosition[]).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => { setPosition(value); setSelectedId(null); }}
                className={`mono border-l border-coldline px-2.5 py-1.5 text-[10px] first:border-l-0 ${
                  position === value ? "bg-icechip text-icesoft" : "bg-cold text-secondary hover:text-primary"
                }`}
              >
                {value}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-[10px] text-muted">
            <span className="display-face font-semibold uppercase tracking-wide">Team</span>
            <select
              value={team}
              onChange={(event) => selectTeam(event.target.value)}
              aria-label="Opportunity team"
              className="rounded border border-coldline bg-cold px-2 py-1.5 text-xs text-primary"
            >
              <option value="ALL">All teams</option>
              {teams.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <span className="mono rounded border border-coldline bg-row/40 px-2 py-1.5 text-[9px] uppercase text-muted">
            ESPN PPR top {data?.coverage.rank_limit ?? 250}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-coldline" aria-label="Chart zoom">
            <button
              type="button"
              aria-label="Zoom out"
              onClick={() => changeZoom(zoomIndex - 1)}
              disabled={zoomIndex === 0}
              className="mono bg-cold px-2.5 py-1.5 text-xs text-secondary hover:text-primary disabled:opacity-35"
            >
              −
            </button>
            <button
              type="button"
              aria-label="Reset zoom"
              onClick={() => changeZoom(0)}
              disabled={zoomIndex === 0}
              className="mono border-x border-coldline bg-cold px-2.5 py-1.5 text-[10px] text-secondary hover:text-primary disabled:opacity-60"
            >
              {ZOOM_LEVELS[zoomIndex]}×
            </button>
            <button
              type="button"
              aria-label="Zoom in"
              onClick={() => changeZoom(zoomIndex + 1)}
              disabled={zoomIndex === ZOOM_LEVELS.length - 1}
              className="mono bg-cold px-2.5 py-1.5 text-xs text-secondary hover:text-primary disabled:opacity-35"
            >
              +
            </button>
          </div>
          <span className="mono text-[9px] uppercase text-muted">Pinch to zoom</span>
          {data?.source.stale && <span className="mono text-[9px] uppercase text-gold">Stale source</span>}
          <button
            type="button"
            onClick={handleExport}
            disabled={!chartDrawable || chartPoints.length < 1 || exporting}
            className="display-face rounded border border-coldline bg-cold px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-secondary hover:text-primary disabled:opacity-50"
            data-testid="opportunity-chart-export"
          >
            {exporting ? "Exporting…" : "PNG 2×"}
          </button>
        </div>
      </div>

      {data && (
        <div className="flex overflow-x-auto border-b border-coldline px-2" role="tablist" aria-label="Opportunity charts">
          {data.charts.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === chart?.id}
              onClick={() => {
                setActiveId(item.id);
                setSelectedId(null);
                setZoomIndex(0);
                setZoomCenter(null);
              }}
              className={`whitespace-nowrap border-b-2 px-3 py-2 text-[10px] font-semibold uppercase tracking-wide ${
                item.id === chart?.id ? "border-red text-primary" : "border-transparent text-muted hover:text-primary"
              }`}
            >
              {CHART_LABELS[item.id]} <span className="mono opacity-70">{item.point_count}</span>
            </button>
          ))}
        </div>
      )}
      {data?.warnings
        .filter((warning) => (
          warning.code.startsWith("OPP-CHART-") &&
          (warning.chart_id == null || warning.chart_id === chart?.id)
        ))
        .map((warning) => (
          <div key={`${warning.code}-${warning.chart_id}`} className="border-b border-gold/30 bg-gold/5 px-3 py-2 text-[10px] text-gold">
            <span className="mono mr-2 text-[9px]">{warning.code}</span>
            {warning.message}{warning.count == null ? "" : ` (${warning.count})`}
          </div>
        ))}

      {error && (
        <div className="m-4 rounded border border-red/40 bg-red/5 px-3 py-2 text-xs text-red">
          <span className="mono mr-2 text-[9px]">OPP-CHART-RENDER</span>
          The chart could not be displayed; the data table is still available.
        </div>
      )}
      {!error && !data && <Spinner label="Loading opportunity charts…" />}
      {data && chart && (!chartDrawable || chartPoints.length < 1) && (
        <div className="m-4" data-testid="opportunity-chart-empty">
          <EmptyState
            title={!chartDrawable || team === "ALL" ? "Not enough comparable players to draw this chart yet" : `No ${team} players have both chart metrics`}
            hint={`${chart.point_count} top-250 players valid · ${chart.omitted_count} omitted. Reset the team filter or use the table below.`}
          />
        </div>
      )}
      {data && chart && chartDrawable && activeDomain && chartPoints.length >= 1 && (
        <>
          <div
            ref={plotRef}
            className="relative h-[23rem] min-w-0 sm:h-[30rem]"
            role="img"
            aria-label={`${chart.title} for ${position}, ${data.season}, through week ${data.through_week ?? "pending"}`}
            data-testid="opportunity-chart-plot"
          >
            <ResponsiveContainer
              width="100%"
              height="100%"
              onResize={(width, height) => setSize({ width, height })}
            >
              <ScatterChart margin={MARGIN}>
                <CartesianGrid stroke="var(--color-line)" strokeOpacity={0.55} vertical horizontal />
                <XAxis
                  type="number"
                  dataKey={chart.x_key}
                  domain={[activeDomain.x_min, activeDomain.x_max]}
                  tick={{ fill: "var(--color-muted)", fontSize: 9 }}
                  tickLine={{ stroke: "var(--color-line)" }}
                  axisLine={{ stroke: "var(--color-line)" }}
                  label={{ value: chart.x_label, position: "insideBottom", offset: -40, fill: "var(--color-secondary)", fontSize: 10 }}
                  tickFormatter={(value) => metricText(chart.x_key, Number(value))}
                  allowDataOverflow
                />
                <YAxis
                  type="number"
                  dataKey={chart.y_key}
                  domain={[activeDomain.y_min, activeDomain.y_max]}
                  tick={{ fill: "var(--color-muted)", fontSize: 9 }}
                  tickLine={{ stroke: "var(--color-line)" }}
                  axisLine={{ stroke: "var(--color-line)" }}
                  label={{ value: chart.y_label, angle: -90, position: "insideLeft", offset: -54, fill: "var(--color-secondary)", fontSize: 10 }}
                  tickFormatter={(value) => metricText(chart.y_key, Number(value))}
                  allowDataOverflow
                />
                <ZAxis range={[90, 90]} />
                {chart.references.map((reference, index) => {
                  const common = {
                    key: `${reference.kind}-${index}`,
                    stroke: index === 0 ? "var(--color-ice)" : "var(--color-muted)",
                    strokeDasharray: index === 0 ? "6 4" : "3 4",
                    strokeOpacity: 0.8,
                    ifOverflow: "hidden" as const,
                  };
                  if (reference.kind === "x" && reference.value != null) {
                    return <ReferenceLine {...common} x={reference.value} data-testid="opportunity-chart-reference" />;
                  }
                  if (reference.kind === "y" && reference.value != null) {
                    return <ReferenceLine {...common} y={reference.value} data-testid="opportunity-chart-reference" />;
                  }
                  if (
                    reference.kind === "line" && reference.x1 != null && reference.y1 != null &&
                    reference.x2 != null && reference.y2 != null
                  ) {
                    return <ReferenceLine {...common} segment={[{ x: reference.x1, y: reference.y1 }, { x: reference.x2, y: reference.y2 }]} data-testid="opportunity-chart-reference" />;
                  }
                  return null;
                })}
                <Tooltip
                  cursor={false}
                  isAnimationActive={false}
                  content={(props) => (
                    <ChartTooltip
                      active={props.active}
                      payload={props.payload as unknown as ReadonlyArray<{ payload?: OpportunityChartPoint }> | undefined}
                      chart={chart}
                    />
                  )}
                  wrapperStyle={{ zIndex: 20, pointerEvents: "none" }}
                  allowEscapeViewBox={{ x: false, y: false }}
                />
                <Scatter
                  data={renderedPoints}
                  isAnimationActive={false}
                  shape={(raw: unknown): ReactElement<SVGElement> => {
                    const shape = raw as ScatterShapeProps;
                    const point = shape.payload;
                    if (!point) return <g aria-hidden="true" />;
                    const x = metric(point, chart.x_key);
                    const y = metric(point, chart.y_key);
                    if (x == null || y == null) return <g aria-hidden="true" />;
                    const clusterKey = `${x.toFixed(6)}:${y.toFixed(6)}`;
                    const members = pointClusters.get(clusterKey) ?? [point];
                    const currentIndex = members.findIndex((member) => member.espn_player_id === selectedId);
                    return (
                      <PlayerMarker
                        cx={shape.cx ?? 0}
                        cy={shape.cy ?? 0}
                        point={point}
                        selected={point.espn_player_id === selectedId}
                        clusterSize={members.length}
                        onPreview={previewPoint}
                        onSelect={() => selectPoint(members[(currentIndex + 1) % members.length])}
                      />
                    );
                  }}
                />
              </ScatterChart>
            </ResponsiveContainer>
            <LabelOverlay
              points={visiblePoints}
              chart={{ ...chart, domain: activeDomain }}
              size={size}
              selectedId={selectedId}
              season={data.season}
              throughWeek={data.through_week}
            />
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-coldline px-3 py-2 text-[9px] text-muted">
            {(Object.keys(SIGNAL_LABELS) as OpportunityChartPoint["signal"][]).map((signal) => (
              <span key={signal} className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full border-2" style={{ borderColor: SIGNAL_STROKES[signal] }} />
                {SIGNAL_LABELS[signal]}
              </span>
            ))}
            <span className="ml-auto mono">
              {visiblePoints.length} visible · {chartPoints.length} filtered · {chart.point_count} top-250 valid · {chart.omitted_count} omitted
            </span>
          </div>
        </>
      )}

      {selected && chart && (
        <div className="border-t border-coldline bg-row/40 px-3 py-2.5" data-testid="opportunity-chart-selection">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
            <strong className="text-primary">{selected.player_name ?? "Unknown player"}</strong>
            <span className="mono text-[10px] text-muted">{selected.nfl_team ?? DASH}</span>
            <span className="mono text-[10px] text-muted">ESPN PPR #{num(selected.espn_rank_ppr, 0)}</span>
            <span className="text-secondary">{chart.x_label} <b className="mono text-primary">{metricText(chart.x_key, metric(selected, chart.x_key))}</b></span>
            <span className="text-secondary">{chart.y_label} <b className="mono text-primary">{metricText(chart.y_key, metric(selected, chart.y_key))}</b></span>
            <span className="text-muted">{SIGNAL_LABELS[selected.signal]}</span>
            <button type="button" onClick={() => setSelectedId(null)} className="ml-auto text-[10px] text-muted hover:text-primary">Clear</button>
          </div>
          <div className="mt-1 text-[10px] text-muted">
            {(() => {
              const x = metric(selected, chart.x_key);
              const y = metric(selected, chart.y_key);
              const members = x == null || y == null
                ? []
                : pointClusters.get(`${x.toFixed(6)}:${y.toFixed(6)}`) ?? [];
              return members.length > 1
                ? <span className="mr-3">{members.length} players share this coordinate · activate the marker again to cycle.</span>
                : null;
            })()}
            {detailLoading && "Loading recent games…"}
            {!detailLoading && detail && detail.weeks.slice(-3).reverse().map((week) => (
              <span key={week.game_id} className="mr-3 inline-block">
                W{week.week}: {num(week.targets)} tgt · {num(week.receiving_yards)} yd · {num(week.fantasy_points_ppr)} PPR
              </span>
            ))}
          </div>
        </div>
      )}
      {exportError && (
        <div className="border-t border-red/30 px-3 py-2 text-xs text-red">
          <span className="mono mr-2 text-[9px]">OPP-CHART-EXPORT</span>
          The chart image could not be created. The chart and table remain available.
        </div>
      )}
    </Panel>
  );
}
