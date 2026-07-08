// Display formatters. UI never computes analytics — it only formats DB values.

export const DASH = "—";

export function record(w: number | null, l: number | null, t: number | null): string {
  if (w == null || l == null || t == null) return DASH;
  return `${w}-${l}-${t}`;
}

export function num(x: number | null | undefined, dp = 1): string {
  if (x == null) return DASH;
  return x.toFixed(dp);
}

export function int(x: number | null | undefined): string {
  if (x == null) return DASH;
  return String(x);
}

export function ordinal(n: number | null | undefined): string {
  if (n == null || n === 0) return DASH;
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

export function relTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

export const LIFECYCLE_LABEL: Record<string, string> = {
  pre_draft: "Pre-draft",
  drafted: "Drafted",
  in_season: "In season",
  complete: "Complete",
};

// Edge verdict tiers (SPEC 6). Verdict is null until Phase 3 → "pending".
export type Verdict = "advantaged" | "neutral" | "disadvantaged" | "pending";

export function verdictOf(v: string | null | undefined): Verdict {
  if (v === "advantaged" || v === "neutral" || v === "disadvantaged") return v;
  return "pending";
}
