import { Link } from "react-router";
import type { TeamOut } from "../api";
import { TeamIdentity } from "./TeamIdentity";

export interface TeamDetailLocationState {
  returnTo: string;
}

export function TeamDetailLink({
  leagueId,
  team,
  returnTo,
  size = "xs",
  showMe = true,
  fallback = "Unknown team",
}: {
  leagueId: number;
  team: TeamOut;
  returnTo: string;
  size?: "xs" | "sm" | "md" | "lg";
  showMe?: boolean;
  fallback?: string;
}) {
  const label = team.name ?? fallback;
  return (
    <Link
      to={`/league/${leagueId}/teams/${team.id}`}
      state={{ returnTo } satisfies TeamDetailLocationState}
      aria-label={`Open ${label} roster`}
      className="inline-flex min-w-0 rounded-sm no-underline hover:brightness-110"
    >
      <TeamIdentity team={team} size={size} showMe={showMe} fallback={fallback} />
    </Link>
  );
}
