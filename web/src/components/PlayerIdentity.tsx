import type { PlayerReference } from "../api";
import { useEffect, useState } from "react";
import { PositionPill } from "./ui";

export function espnPlayerImageUrl(espnPlayerId: number | null): string | null {
  return espnPlayerId == null ? null : `/api/players/${espnPlayerId}/portrait`;
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

export function PlayerAvatar({
  player,
  size = "sm",
}: {
  player: PlayerReference;
  size?: "xs" | "sm" | "md";
}) {
  const src = espnPlayerImageUrl(player.espn_player_id);
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [src]);
  const defense = (player.espn_player_id ?? 0) < 0;
  const dimensions = {
    xs: "h-6 w-6 text-[9px]",
    sm: "h-11 w-11 text-xs",
    md: "h-14 w-14 text-sm",
  }[size];
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-line bg-rowhover font-semibold text-muted ${dimensions}`}
      title={player.name}
    >
      {(!src || imageFailed) && <span aria-hidden="true">{initials(player.name)}</span>}
      {src && (
        <img
          src={src}
          alt={`${player.name} ESPN portrait`}
          loading="lazy"
          referrerPolicy="no-referrer"
          decoding="async"
          className={`absolute inset-0 h-full w-full ${imageFailed ? "hidden" : ""} ${defense ? "object-contain p-1" : "object-cover object-top"}`}
          onError={() => setImageFailed(true)}
        />
      )}
    </span>
  );
}

export function PlayerIdentity({
  player,
  tone = "text-primary",
  size = "sm",
  showPosition = true,
}: {
  player: PlayerReference;
  tone?: string;
  size?: "xs" | "sm" | "md";
  showPosition?: boolean;
}) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <PlayerAvatar player={player} size={size} />
      <span className="min-w-0">
        <span className={`block truncate ${tone}`}>{player.name}</span>
        {showPosition && player.position && (
          <span className="mt-0.5 block"><PositionPill pos={player.position} /></span>
        )}
      </span>
    </span>
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function PlayerMentionText({
  text,
  players,
}: {
  text: string;
  players: PlayerReference[];
}) {
  const refs = new Map<string, PlayerReference>();
  for (const player of players) {
    if (player.name) refs.set(player.name.toLowerCase(), player);
  }
  const names = [...refs.values()].map((player) => player.name).sort((a, b) => b.length - a.length);
  if (names.length === 0) return <>{text}</>;
  const parts = text.split(new RegExp(`(${names.map(escapeRegExp).join("|")})`, "gi"));
  return (
    <>
      {parts.map((part, index) => {
        const player = refs.get(part.toLowerCase());
        return player ? (
          <span key={`${part}-${index}`} className="inline-flex items-center gap-1.5 whitespace-nowrap align-middle">
            <PlayerAvatar player={player} size="xs" />
            <span>{part}</span>
          </span>
        ) : part;
      })}
    </>
  );
}
