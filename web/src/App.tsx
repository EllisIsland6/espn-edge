import { lazy, Suspense } from "react";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import Layout from "./components/Layout";
import RouteError from "./components/RouteError";
import { loadChunk } from "./lib/chunk";
import PortfolioBoard from "./pages/PortfolioBoard";
import LeagueDetail from "./pages/LeagueDetail";
import TeamDetail from "./pages/TeamDetail";
import Manage from "./pages/Manage";
import Status from "./pages/Status";

// An on-demand screen is a separate file with a hashed name; a tab that outlives a deploy
// asks for a name that is gone. loadChunk reloads once to get the current shell (WU-12).
const Analytics = lazy(() => loadChunk(() => import("./pages/Analytics")));

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    // The layout itself could not render: the error screen alone.
    errorElement: <RouteError />,
    children: [
      {
        // A page could not render: the error screen in the page's place, the menu still there.
        errorElement: <RouteError />,
        children: [
          { index: true, element: <PortfolioBoard /> },
          {
            path: "analytics/*",
            element: (
              <Suspense fallback={<div className="px-4 py-8 text-sm text-secondary">Loading analytics…</div>}>
                <Analytics />
              </Suspense>
            ),
          },
          { path: "league/:id", element: <LeagueDetail /> },
          { path: "league/:leagueId/teams/:teamId", element: <TeamDetail /> },
          { path: "manage", element: <Manage /> },
          { path: "status", element: <Status /> },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
