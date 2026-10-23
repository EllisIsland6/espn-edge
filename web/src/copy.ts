// What the page says about custody, by mode. One place, plain strings, so the
// backend's pin test (tests/test_spa_csrf.py) can hold the words by literal and
// the page cannot drift from the decision it carries (ADR-012, WU-4).

import type { Health } from "./api";

export type Mode = Health["mode"];

/** The three modes the backend admits. Order is the order they were decided. */
export const MODES: readonly Mode[] = ["private_operator", "public_synthetic", "private_invited"];

/**
 * The person's own ESPN session, held on this server: every word a person reads
 * before handing it over. The second sentence is what makes it theirs to undo;
 * the third is the decision ADR-012 records as the person's, not the site's.
 */
export const INVITED_CONSENT = {
  storage:
    "Your ESPN session (SWID and espn_s2) will be stored encrypted on this server and used only to read your leagues.",
  erase: "Deleting the account here erases it.",
  terms: "ESPN's terms do not permit this kind of access; you are choosing to do it.",
} as const;

/**
 * The person's own AI provider key, held on this server (ADR-012, G7; the
 * operator's decision of 2026-10-23). Three sentences for the same three
 * reasons as INVITED_CONSENT: what is kept, who pays, how to undo it.
 */
export const AI_KEY_CONSENT = {
  storage:
    "Your Anthropic API key will be stored encrypted on this server and used only for the reports you ask for.",
  billing: "Anthropic bills you directly for those calls.",
  erase: "Deleting the key here erases it.",
} as const;

export const SYNTHETIC_NOTICE =
  "This hosted instance runs on synthetic data. It does not hold ESPN accounts and does not talk to ESPN, so there is nothing to add here and nothing to sync.";

export const LOCAL_NOTICE =
  "Cookies are stored encrypted and never leave your machine. All ESPN traffic goes through the backend.";

export const FOOTER_LABEL: Record<Mode, string> = {
  private_operator: "ESPN Edge · local portfolio analytics",
  public_synthetic: "ESPN Edge · hosted on synthetic data",
  private_invited: "ESPN Edge · hosted for an invited group",
};

/** Only a KNOWN credentialed mode shows the account form: the operator's own
 * machine, or the invited site that holds sessions under ADR-012's key. An
 * unknown mode -- health not yet answered, or a mocked answer without one --
 * shows nothing, which is the honest default. */
export function holdsCredentials(mode: Mode | null | undefined): mode is Mode {
  return mode === "private_operator" || mode === "private_invited";
}
