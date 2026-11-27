// A screen that is loaded on demand lives in its own file, named by a hash of its contents.
// When the site is deployed while a tab is open -- or a browser reuses an old copy of the
// shell -- that tab asks for a file name the new deployment no longer has, and the import
// fails. On 2026-11-20 the operator's own browser met exactly that on Analytics, two and a
// half hours after a deploy, as the router's raw "Unexpected Application Error!" (WU-12).
//
// The cure is the current shell, so: reload once. A reload revalidates the shell whatever the
// browser had cached, and the new shell names files that exist. If the import fails again
// straight after that reload, the cause is something a reload does not fix (no connection, a
// deploy caught half-way), and reloading again would loop -- so the failure goes to the
// route's error screen instead, which says what happened and leaves the reload to the person.

const MARKER = "espn-edge.chunk-reload";

/** How long after an automatic reload a second failure counts as "still failing". */
const LOOP_WINDOW_MS = 60_000;

/** An on-demand screen that could not be loaded even after one automatic reload. */
export class ChunkLoadError extends Error {
  /** What the import itself said; for the console, never for the page. */
  readonly reason: unknown;

  constructor(reason: unknown) {
    super("an on-demand screen could not be loaded");
    this.name = "ChunkLoadError";
    this.reason = reason;
  }
}

function reloadedJustNow(): boolean {
  try {
    const at = Number(window.sessionStorage.getItem(MARKER));
    return Number.isFinite(at) && at > 0 && Date.now() - at < LOOP_WINDOW_MS;
  } catch {
    // No storage means no way to know a reload already happened; assume it did. A page that
    // cannot remember must not reload itself.
    return true;
  }
}

function rememberReload(): boolean {
  try {
    window.sessionStorage.setItem(MARKER, String(Date.now()));
    return true;
  } catch {
    return false;
  }
}

/** Forget the automatic reload: a screen loaded, or the person asked for a reload themselves. */
export function forgetReload(): void {
  try {
    window.sessionStorage.removeItem(MARKER);
  } catch {
    // Nothing was remembered, so there is nothing to forget.
  }
}

/**
 * Load an on-demand screen; if its file is gone, reload the page once to get the current
 * shell. Every `lazy()` in the router goes through this (a pin test holds that).
 */
export async function loadChunk<T>(load: () => Promise<T>): Promise<T> {
  try {
    const loaded = await load();
    forgetReload();
    return loaded;
  } catch (reason) {
    if (!reloadedJustNow() && rememberReload()) {
      window.location.reload();
      // The page is leaving. Never settle, so nothing renders in the meantime -- not the
      // screen, and not an error about a problem that is already being fixed.
      return new Promise<T>(() => {});
    }
    throw new ChunkLoadError(reason);
  }
}
