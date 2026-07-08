import { createBrowserRouter, RouterProvider } from "react-router-dom";
import Layout from "./components/Layout";
import PortfolioBoard from "./pages/PortfolioBoard";
import LeagueDetail from "./pages/LeagueDetail";
import Manage from "./pages/Manage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <PortfolioBoard /> },
      { path: "league/:id", element: <LeagueDetail /> },
      { path: "manage", element: <Manage /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
