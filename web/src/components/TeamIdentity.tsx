import type { TeamOut } from "../api";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export interface TeamIdentityData {
  name: string | null;
  logo_url: string | null;
  is_me?: boolean;
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "?";
}

export function TeamAvatar({
  name,
  logoUrl,
  size = "sm",
}: {
  name: string | null;
  logoUrl: string | null;
  size?: "xs" | "sm" | "md" | "lg";
}) {
  const label = name ?? "Unknown team";
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [logoUrl]);
  const dimensions = {
    xs: "h-7 w-7 text-[9px]",
    sm: "h-9 w-9 text-[10px]",
    md: "h-11 w-11 text-xs",
    lg: "h-14 w-14 text-sm",
  }[size];
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-coldline bg-coldpanel font-semibold text-icesoft ${dimensions}`}
      title={label}
      data-testid="team-avatar"
    >
      {(!logoUrl || imageFailed) && <span aria-hidden="true">{initials(label)}</span>}
      {logoUrl && (
        <img
          src={logoUrl}
          alt={`${label} team logo`}
          loading="lazy"
          referrerPolicy="no-referrer"
          decoding="async"
          className={`absolute inset-0 h-full w-full object-contain p-0.5 ${imageFailed ? "hidden" : ""}`}
          onError={() => setImageFailed(true)}
        />
      )}
    </span>
  );
}

export function TeamIdentity({
  team,
  size = "sm",
  tone = "text-primary",
  showMe = true,
  fallback = "Unknown team",
}: {
  team: TeamIdentityData | Pick<TeamOut, "name" | "logo_url" | "is_me"> | null | undefined;
  size?: "xs" | "sm" | "md" | "lg";
  tone?: string;
  showMe?: boolean;
  fallback?: string;
}) {
  const name = team?.name ?? fallback;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <TeamAvatar name={name} logoUrl={team?.logo_url ?? null} size={size} />
      <span className={`display-face min-w-0 truncate tracking-tight ${team?.is_me ? "font-bold" : "font-semibold"} ${tone}`}>
        {name}
        {showMe && team?.is_me && <span className="text-red"> ·me</span>}
      </span>
    </span>
  );
}

export function TeamSelect({
  teams,
  value,
  onChange,
  label,
}: {
  teams: TeamOut[];
  value: number | null;
  onChange: (teamId: number) => void;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selected = teams.find((team) => team.id === value) ?? null;
  const selectedName = selected?.name ?? "Select team";

  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => {
      const options = rootRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitemradio"]',
      );
      const selectedIndex = teams.findIndex((team) => team.id === value);
      options?.[selectedIndex >= 0 ? selectedIndex : 0]?.focus();
    });
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, teams, value]);

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, direction: 1 | -1) {
    const options = Array.from(
      rootRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [],
    );
    if (options.length === 0) return;
    event.preventDefault();
    const current = options.indexOf(event.currentTarget);
    options[(current + direction + options.length) % options.length]?.focus();
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${label}: ${selectedName}`}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className="flex min-h-9 w-52 max-w-[65vw] items-center justify-between gap-2 rounded-md border border-line bg-panel px-2 py-1 text-left hover:bg-rowhover"
      >
        <TeamIdentity
          team={selected}
          size="xs"
          showMe={false}
          fallback="Select team"
        />
        <span aria-hidden="true" className="mono text-[10px] text-muted">v</span>
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          className="absolute right-0 top-full z-30 mt-1 max-h-64 w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-md border border-line bg-panel p-1 shadow-xl"
        >
          {teams.map((team) => (
            <button
              key={team.id}
              type="button"
              role="menuitemradio"
              aria-checked={team.id === value}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown") moveFocus(event, 1);
                if (event.key === "ArrowUp") moveFocus(event, -1);
                if (event.key === "Home" || event.key === "End") {
                  event.preventDefault();
                  const options = rootRef.current?.querySelectorAll<HTMLButtonElement>(
                    '[role="menuitemradio"]',
                  );
                  options?.[event.key === "Home" ? 0 : (options.length ?? 1) - 1]?.focus();
                }
              }}
              onClick={() => {
                onChange(team.id);
                setOpen(false);
                triggerRef.current?.focus();
              }}
              className={`flex w-full items-center rounded px-2 py-1.5 text-left hover:bg-rowhover ${
                team.id === value ? "bg-rowhover" : ""
              }`}
            >
              <TeamIdentity team={team} size="xs" showMe={false} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
