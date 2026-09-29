// Shared visual primitives — SPEC Section 9.2. All colors come from the @theme
// tokens; no ad-hoc hex here.
import { useCallback, useEffect, useId, useRef, useState, type ComponentPropsWithoutRef, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DASH, type Verdict } from "../lib/format";

export function Panel({ children, className = "", ...props }: ComponentPropsWithoutRef<"div">) {
  return (
    <div {...props} className={`panel-shadow rounded-lg border border-line bg-panel/95 ${className}`}>{children}</div>
  );
}

export function InfoTip({ label, description }: { label: string; description: string }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState<CSSProperties | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const tooltipId = useId();

  const updatePosition = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const width = Math.min(288, window.innerWidth - 24);
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left + rect.width / 2 - width / 2));
    const placeAbove = rect.bottom > window.innerHeight * 0.66;
    setPosition(
      placeAbove
        ? { bottom: window.innerHeight - rect.top + 8, left, width }
        : { top: rect.bottom + 8, left, width },
    );
  }, []);

  useEffect(() => {
    if (!open) return;
    const reposition = () => updatePosition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, updatePosition]);

  useEffect(() => {
    if (!pinned) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || popupRef.current?.contains(target)) return;
      setPinned(false);
      setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPinned(false);
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [pinned]);

  const show = () => {
    updatePosition();
    setOpen(true);
  };

  return (
    <span className="inline-flex shrink-0 align-middle">
      <button
        ref={buttonRef}
        type="button"
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-ice/70 bg-icechip text-[10px] font-bold normal-case leading-none tracking-normal text-icesoft transition-colors hover:border-icesoft hover:bg-icebar hover:text-frost"
        aria-label={`About ${label}`}
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={pinned}
        onMouseEnter={show}
        onMouseLeave={() => !pinned && setOpen(false)}
        onFocus={show}
        onBlur={() => !pinned && setOpen(false)}
        onClick={() => {
          const next = !pinned;
          setPinned(next);
          if (next) show();
          else setOpen(false);
        }}
      >
        i
      </button>
      {open && position && typeof document !== "undefined" && createPortal(
        <div
          ref={popupRef}
          id={tooltipId}
          role="tooltip"
          style={position}
          className="fixed z-[100] max-h-60 overflow-y-auto rounded-lg border border-coldline bg-panel p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-primary shadow-2xl"
        >
          <div className="mb-1 font-semibold text-icesoft">{label}</div>
          <div>{description}</div>
          {pinned && <div className="mt-2 text-[10px] text-muted">Click the i again or press Escape to close.</div>}
        </div>,
        document.body,
      )}
    </span>
  );
}

type BtnVariant = "primary" | "secondary" | "danger" | "sync" | "discover";
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
    "display-face inline-flex items-center justify-center gap-1.5 transition duration-150 disabled:cursor-not-allowed disabled:opacity-40";
  const styles: Record<BtnVariant, string> = {
    primary: "min-h-9 rounded-md bg-red px-3 py-1.5 text-sm font-semibold tracking-wide text-white hover:bg-redhover",
    secondary: "min-h-9 rounded-md border border-line bg-panel px-3 py-1.5 text-sm font-semibold tracking-wide text-primary hover:bg-rowhover",
    danger: "min-h-9 rounded-md border border-red/40 px-3 py-1.5 text-sm font-semibold tracking-wide text-red hover:bg-red/10",
    sync: "h-[30px] rounded-lg bg-gradient-to-r from-syncstart to-syncend px-4 text-[10px] font-black uppercase tracking-[0.15em] text-syncink shadow-[0_0_16px_color-mix(in_srgb,var(--color-syncstart)_18%,transparent)] hover:brightness-110 active:translate-y-px active:brightness-95 disabled:translate-y-0 disabled:shadow-none disabled:brightness-75",
    discover: "h-[30px] rounded-lg bg-gradient-to-r from-ice to-rb px-4 text-[10px] font-black uppercase tracking-[0.15em] text-header shadow-[0_0_16px_color-mix(in_srgb,var(--color-ice)_18%,transparent)] hover:brightness-110 active:translate-y-px active:brightness-95 disabled:translate-y-0 disabled:shadow-none disabled:brightness-75",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-variant={variant}
      className={`${base} ${styles[variant]}`}
    >
      {children}
    </button>
  );
}

// League size pill, e.g. "12-TEAM".
export function SizePill({ size }: { size: number | null }) {
  return (
    <span className="mono rounded border border-line/70 bg-rowhover px-1.5 py-0.5 text-[9px] text-secondary">
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
    <span className={`display-face rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${s}`}>
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
type BarTone = "mint" | "ice" | "ash";
const BAR_TONE: Record<BarTone, string> = {
  mint: "bg-greenbar",
  ice: "bg-icebar",
  ash: "bg-ashbar",
};

export function ValueBar({
  value,
  max,
  label,
  tone = "mint",
}: {
  value: number;
  max: number;
  label?: string;
  tone?: BarTone;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="relative h-6 w-full overflow-hidden rounded bg-rowhover/80">
      <div className={`absolute inset-y-0 left-0 ${BAR_TONE[tone]}`} style={{ width: `${pct}%` }} />
      <span className="absolute inset-y-0 w-px bg-icesoft" style={{ left: `${pct}%` }} aria-hidden="true" />
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
