// Shared visual primitives — SPEC Section 9.2. All colors come from the @theme
// tokens; no ad-hoc hex here.
import type { ReactNode } from "react";
import { DASH, type Verdict } from "../lib/format";

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-panel ${className}`}>{children}</div>
  );
}

type BtnVariant = "primary" | "secondary" | "danger";
export function Button({
  children,
  onClick,
  variant = "secondary",
  disabled,
  type = "button",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: BtnVariant;
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
}) {
  const base =
    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed";
  const styles: Record<BtnVariant, string> = {
    primary: "bg-red text-white hover:bg-redhover",
    secondary: "border border-line bg-panel text-primary hover:bg-rowhover",
    danger: "border border-red/40 text-red hover:bg-red/10",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} className={`${base} ${styles[variant]}`}>
      {children}
    </button>
  );
}

// League size pill, e.g. "12-TEAM".
export function SizePill({ size }: { size: number | null }) {
  return (
    <span className="mono rounded bg-rowhover px-1.5 py-0.5 text-[11px] text-secondary">
      {size ? `${size}-TEAM` : "—"}
    </span>
  );
}

const LIFECYCLE_STYLE: Record<string, string> = {
  pre_draft: "text-secondary bg-rowhover",
  drafted: "text-wr bg-wr/12",
  in_season: "text-green bg-greenchip",
  complete: "text-muted bg-rowhover",
};
export function LifecycleBadge({ lifecycle, label }: { lifecycle: string; label: string }) {
  const s = LIFECYCLE_STYLE[lifecycle] ?? "text-secondary bg-rowhover";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] uppercase tracking-wide ${s}`}>
      {label}
    </span>
  );
}

// Dual-value green chip: primary mono number over a smaller secondary line.
export function ValueChip({
  primary,
  secondary,
  muted = false,
}: {
  primary: ReactNode;
  secondary?: ReactNode;
  muted?: boolean;
}) {
  return (
    <div
      className={`mono inline-flex min-w-14 flex-col items-end rounded-md px-2 py-1 leading-tight ${
        muted ? "bg-rowhover text-secondary" : "bg-greenchip text-green"
      }`}
    >
      <span className="text-sm font-semibold">{primary}</span>
      {secondary != null && <span className="text-[10px] text-muted">{secondary}</span>}
    </div>
  );
}

// Edge letter grade pill; `null` grade renders a muted pending pill.
export function GradePill({ grade }: { grade: string | null }) {
  if (!grade) {
    return (
      <span className="mono rounded-md bg-rowhover px-2 py-0.5 text-xs text-muted">{DASH}</span>
    );
  }
  const g = grade.toUpperCase();
  const cls = g.startsWith("A")
    ? "text-grade-a bg-grade-a/12"
    : g.startsWith("B")
      ? "text-grade-b bg-grade-b/12"
      : g.startsWith("C")
        ? "text-grade-c bg-grade-c/12"
        : "text-grade-df bg-grade-df/12";
  return <span className={`mono rounded-md px-2 py-0.5 text-xs font-semibold ${cls}`}>{g}</span>;
}

const POS_COLOR: Record<string, string> = {
  QB: "text-qb bg-qb/15",
  RB: "text-rb bg-rb/15",
  WR: "text-wr bg-wr/15",
  TE: "text-te bg-te/15",
  "D/ST": "text-dst bg-dst/15",
  DST: "text-dst bg-dst/15",
  K: "text-k bg-k/15",
};
export function PositionPill({ pos }: { pos: string | null }) {
  const p = (pos ?? "").toUpperCase();
  const cls = POS_COLOR[p] ?? "text-secondary bg-rowhover";
  return (
    <span className={`mono rounded px-1.5 py-0.5 text-[11px] font-semibold ${cls}`}>
      {p || "?"}
    </span>
  );
}

const TIER_META: Record<Verdict, string> = {
  advantaged: "Tier 1 — Advantaged",
  neutral: "Tier 2 — Neutral",
  disadvantaged: "Tier 3 — Disadvantaged",
  pending: "Not yet scored",
};
// 1px gold rule with an uppercase letter-spaced tier label (SPEC 9.2).
export function TierDivider({ verdict, count }: { verdict: Verdict; count: number }) {
  return (
    <div className="flex items-center gap-3 px-4 pt-5 pb-2">
      <span className="mono text-[11px] uppercase tracking-[0.18em] text-gold">
        {TIER_META[verdict]}
      </span>
      <span className="h-px flex-1 bg-gold/40" />
      <span className="mono text-[11px] text-muted">{count}</span>
    </div>
  );
}

// Horizontal fill bar behind a mono numeral (the 680/662/656 pattern, SPEC 9.2).
export function ValueBar({ value, max, label }: { value: number; max: number; label?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="relative h-6 w-full overflow-hidden rounded bg-row">
      <div className="absolute inset-y-0 left-0 bg-greenbar" style={{ width: `${pct}%` }} />
      <span className="mono absolute inset-0 flex items-center justify-end px-2 text-xs text-primary">
        {label ?? value}
      </span>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-linedash px-6 py-10 text-center">
      <div className="text-sm text-secondary">{title}</div>
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="px-4 py-8 text-sm text-secondary">{label}</div>;
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-red/40 bg-red/5 px-3 py-2 text-sm text-red">
      {message}
    </div>
  );
}

// Non-fatal sync warnings (needs_reauth / partial errors). Gold, dismissible.
export function WarningNote({
  title,
  messages,
  onDismiss,
}: {
  title: string;
  messages: string[];
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-sm text-gold">
      <div className="flex items-center gap-2">
        <span className="font-medium">{title}</span>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="ml-auto text-xs text-gold/80 hover:text-gold"
          >
            Dismiss
          </button>
        )}
      </div>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-gold/90">
        {messages.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    </div>
  );
}
