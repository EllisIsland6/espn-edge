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

/**
 * What the AI key panel says when a save is refused. `AI_KEY_REFUSED` is the
 * server's fixed sentence (api/services/ai_keys.py), matched exactly so the
 * hint can follow it and nothing else; a pin test holds the two together. The
 * hint says what to check, because the commonest refusal is a paste that is
 * not the whole key: the first one on the invited site (2026-11-20) was 31
 * characters long, and the server's sentence alone -- shown at the top of the
 * page, away from the field -- did not say what to do about it (WU-11).
 */
export const AI_KEY_REFUSED = "the key was refused by Anthropic";
export const AI_KEY_REFUSED_HINT =
  "Nothing was stored. Paste the whole key: it starts with sk-ant- and is much longer than its name or ID. The Claude Console shows it only once, when the key is created; if it is lost, create a new one.";

export const SYNTHETIC_NOTICE =
  "This hosted instance runs on synthetic data. It does not hold ESPN accounts and does not talk to ESPN, so there is nothing to add here and nothing to sync.";

export const LOCAL_NOTICE =
  "Cookies are stored encrypted and never leave your machine. All ESPN traffic goes through the backend.";

/**
 * What the AI panels say when no key answers, by mode. The local site's cure
 * is an environment variable; the invited site's is the key panel on Manage,
 * and the sentence says who pays; the synthetic site has nothing to add. An
 * unknown mode says only that AI is off. One place, because the first G7
 * attempt (2026-10-27) met the local sentence on the invited site (WU-10).
 */
export const AI_OFF_HINT: Record<Mode, string> = {
  private_operator:
    "Set ANTHROPIC_API_KEY in .env and restart the API to enable draft recaps, league briefs, and advantage verdicts.",
  public_synthetic: "AI is not enabled on this instance.",
  private_invited:
    "Add your Anthropic API key on Manage to enable draft recaps, league briefs, and advantage verdicts. Anthropic bills you directly for those calls.",
};

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
