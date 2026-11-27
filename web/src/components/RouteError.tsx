import { useEffect } from "react";
import { useRouteError } from "react-router";
import { ROUTE_ERROR } from "../copy";
import { ChunkLoadError, forgetReload } from "../lib/chunk";
import { Button } from "./ui";

/**
 * The screen a route shows when it cannot show itself (WU-12): two sentences from copy.ts
 * and a reload, in place of the router's developer fallback. Mounted twice in App.tsx --
 * inside the layout, so a failed page leaves the menu standing, and on the layout itself
 * for the case where the layout is what failed.
 */
export default function RouteError() {
  const error = useRouteError();
  const words = error instanceof ChunkLoadError ? ROUTE_ERROR.chunk : ROUTE_ERROR.other;

  useEffect(() => {
    // For whoever opens the console; the page itself never shows exception text.
    console.error(error instanceof ChunkLoadError ? error.reason : error);
  }, [error]);

  return (
    <div role="alert" data-testid="route-error" className="mx-auto max-w-xl px-4 py-12">
      <h1 className="display-face text-lg font-semibold text-primary">{words.title}</h1>
      <p className="mt-2 text-sm text-secondary">{words.body}</p>
      <div className="mt-4">
        <Button
          variant="primary"
          onClick={() => {
            // The person asked: this reload is theirs, so the next failure may reload once
            // more on its own before it reports back here.
            forgetReload();
            window.location.reload();
          }}
        >
          {ROUTE_ERROR.reload}
        </Button>
      </div>
    </div>
  );
}
