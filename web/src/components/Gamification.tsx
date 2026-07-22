import { useEffect, useState, type ReactNode } from "react";
import type { Achievement, MetricMomentum } from "../api";
import { usePrefersReducedMotion } from "../lib/motion";

const RING_TONE: Record<string, { stroke: string; text: string }> = {
  A: { stroke: "stroke-grade-a", text: "text-grade-a" },
  B: { stroke: "stroke-grade-b", text: "text-grade-b" },
  C: { stroke: "stroke-grade-c", text: "text-grade-c" },
  D: { stroke: "stroke-grade-df", text: "text-grade-df" },
  F: { stroke: "stroke-grade-df", text: "text-grade-df" },
};

export function ScoreRing({
  value,
  grade,
  label,
  size = "md",
  testId,
}: {
  value: number | null;
  grade: string | null;
  label: string;
  size?: "sm" | "md";
  testId?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const target = value == null ? 0 : Math.max(0, Math.min(100, value));
  const [progress, setProgress] = useState(reduced ? target : 0);
  const pixels = size === "sm" ? 58 : 92;
  const stroke = size === "sm" ? 5 : 7;
  const radius = (pixels - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const gradeKey = grade?.toUpperCase().charAt(0) ?? "";

  useEffect(() => {
    if (value == null) {
      setProgress(0);
      return;
    }
    if (reduced) {
      setProgress(target);
      return;
    }
    setProgress(0);
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => setProgress(target));
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
    };
  }, [target, value, reduced]);

  const pending = value == null;
  return (
    <div
      className="inline-flex shrink-0 flex-col items-center gap-1"
      data-testid={testId}
      data-state={pending ? "pending" : "scored"}
      data-motion={reduced ? "reduced" : "animated"}
      aria-label={pending ? `${label} pending` : `${label} ${Math.round(value)} grade ${grade ?? "pending"}`}
    >
      <div className="relative" style={{ width: pixels, height: pixels }}>
        <svg className="-rotate-90" width={pixels} height={pixels} viewBox={`0 0 ${pixels} ${pixels}`} aria-hidden="true">
          <circle
            cx={pixels / 2}
            cy={pixels / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            className="stroke-line"
          />
          <circle
            data-testid={testId ? `${testId}-progress` : undefined}
            cx={pixels / 2}
            cy={pixels / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={pending ? "3 5" : circumference}
            strokeDashoffset={pending ? 0 : circumference * (1 - progress / 100)}
            className={pending ? "stroke-muted" : RING_TONE[gradeKey]?.stroke ?? "stroke-grade-b"}
            style={{ transition: reduced ? "none" : "stroke-dashoffset 650ms cubic-bezier(0.22, 1, 0.36, 1)" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {pending ? (
            <span className="mono text-[9px] uppercase text-muted">Pending</span>
          ) : (
            <>
              <span className={`mono font-bold text-primary ${size === "sm" ? "text-base" : "text-2xl"}`}>
                {Math.round(value)}
              </span>
              <span className={`mono font-semibold ${RING_TONE[gradeKey]?.text ?? "text-secondary"} ${size === "sm" ? "text-[9px]" : "text-[11px]"}`}>
                {grade ?? "-"}
              </span>
            </>
          )}
        </div>
      </div>
      <span className="mono text-[9px] uppercase text-muted">{label}</span>
    </div>
  );
}

export function MomentumBadges({
  momentum,
  testId,
}: {
  momentum: MetricMomentum | null | undefined;
  testId?: string;
}) {
  if (!momentum) return null;
  const status = momentum.status;
  const chip =
    status === "pending"
      ? { text: "pending", tone: "border-line text-muted" }
      : status === "first_sync"
        ? { text: "first sync", tone: "border-line text-secondary" }
        : status === "flat"
          ? { text: "no change", tone: "border-line text-secondary" }
          : status === "up"
            ? { text: `▲ +${Math.abs(momentum.delta ?? 0).toFixed(1)}`, tone: "border-green/30 text-green" }
            : { text: `▼ -${Math.abs(momentum.delta ?? 0).toFixed(1)}`, tone: "border-red/30 text-red" };
  const showStreak = momentum.streak_count >= 2 && momentum.streak_direction != null;
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid={testId}>
      <span className={`mono rounded border px-1.5 py-0.5 text-[9px] uppercase ${chip.tone}`}>
        {chip.text}
      </span>
      {showStreak && (
        <span className="mono rounded border border-gold/30 px-1.5 py-0.5 text-[9px] uppercase text-gold">
          {momentum.streak_count} {momentum.streak_direction}
        </span>
      )}
    </div>
  );
}

const ACHIEVEMENT_META: Record<string, { glyph: string; tone: string }> = {
  sync_healthy: { glyph: "S", tone: "border-green/30 text-green" },
  lineup_elite: { glyph: "L", tone: "border-grade-a/30 text-grade-a" },
  draft_value: { glyph: "D", tone: "border-gold/30 text-gold" },
  all_play_edge: { glyph: "A", tone: "border-grade-b/30 text-grade-b" },
};

export function AchievementRow({
  achievements,
  testId,
}: {
  achievements: Achievement[] | null | undefined;
  testId?: string;
}) {
  if (!achievements?.length) return null;
  return (
    <div className="flex items-center gap-1" data-testid={testId} aria-label="Achievements">
      {achievements.map((achievement) => {
        const meta = ACHIEVEMENT_META[achievement.key] ?? {
          glyph: "+",
          tone: "border-line text-secondary",
        };
        return (
          <span
            key={achievement.key}
            className={`mono inline-flex h-5 min-w-5 items-center justify-center rounded-full border px-1 text-[9px] font-semibold ${meta.tone}`}
            title={`${achievement.label}: ${achievement.detail}`}
            aria-label={achievement.label}
          >
            {meta.glyph}
          </span>
        );
      })}
    </div>
  );
}

export function ContentReveal({
  token,
  children,
  testId,
}: {
  token: string;
  children: ReactNode;
  testId?: string;
}) {
  const reduced = usePrefersReducedMotion();
  return (
    <div
      key={token}
      className={reduced ? "" : "ai-reveal"}
      data-testid={testId}
      data-motion={reduced ? "reduced" : "animated"}
    >
      {children}
    </div>
  );
}
