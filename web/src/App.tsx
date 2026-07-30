import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import Layout from "./components/Layout";
import PortfolioBoard from "./pages/PortfolioBoard";
import LeagueDetail from "./pages/LeagueDetail";
import TeamDetail from "./pages/TeamDetail";
import Manage from "./pages/Manage";
import Status from "./pages/Status";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <PortfolioBoard /> },
      { path: "league/:id", element: <LeagueDetail /> },
      { path: "league/:leagueId/teams/:teamId", element: <TeamDetail /> },
      { path: "manage", element: <Manage /> },
      { path: "status", element: <Status /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
