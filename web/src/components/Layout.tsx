import { useState, type ReactNode } from "react";
import { NavLink, Outlet, ScrollRestoration, useLocation } from "react-router";
import { applyTheme, currentTheme, storeTheme, type ThemeMode } from "../lib/theme";

const SIDEBAR_COLLAPSED_KEY = "espn-edge.sidebar-collapsed";

type IconName = "portfolio" | "analytics" | "manage" | "status" | "collapse" | "expand";

const sidebarItems: { to: string; label: string; description: string; icon: IconName; end?: boolean }[] = [
  {
    to: "/",
    label: "Portfolio",
    description: "See all your fantasy teams and how they are doing.",
    icon: "portfolio",
    end: true,
  },
  {
    to: "/analytics",
    label: "Analytics",
    description: "See easy charts about your teams and draft picks.",
    icon: "analytics",
  },
  {
    to: "/manage",
    label: "Manage",
    description: "Add your ESPN accounts and leagues, then sync them.",
    icon: "manage",
  },
  {
    to: "/status",
    label: "Status",
    description: "Check if ESPN Edge and its tools are working.",
    icon: "status",
  },
];

function topNavClass({ isActive }: { isActive: boolean }): string {
  return `display-face relative whitespace-nowrap rounded-md px-2 py-1.5 text-xs font-semibold tracking-wide transition-colors duration-150 sm:px-3 sm:text-sm ${
    isActive ? "bg-rowhover text-primary" : "text-secondary hover:bg-rowhover/60 hover:text-primary"
  }`;
}

function sidebarNavClass(isActive: boolean, collapsed: boolean): string {
  return `group relative flex h-11 items-center border-l-2 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors duration-150 ${
    collapsed ? "justify-center px-0" : "gap-3 px-4"
  } ${
    isActive
      ? "border-red bg-icechip/75 text-red"
      : "border-transparent text-secondary hover:bg-rowhover/70 hover:text-primary"
  }`;
}

function initialSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export default function Layout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(initialSidebarCollapsed);
  const [theme, setTheme] = useState<ThemeMode>(currentTheme);
  const location = useLocation();

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      const next = !collapsed;
      try {
        window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch {
        // The sidebar still works when browser storage is unavailable.
      }
      return next;
    });
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    storeTheme(next);
    setTheme(next);
  }

  const themeToggleLabel = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-header/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-2 px-3 sm:gap-6 sm:px-6">
          <NavLink to="/" className="display-face shrink-0 text-lg font-bold tracking-tight text-frost">
            <span className="hidden sm:inline">ESPN </span><span className="text-red">Edge</span>
          </NavLink>
          <span aria-hidden="true" className="hidden h-6 w-px bg-line sm:block" />
          <nav className="flex min-w-0 items-center gap-0.5 overflow-x-auto sm:gap-1" aria-label="Top navigation">
            {sidebarItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={topNavClass}
                title={item.description}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <button
            type="button"
            onClick={toggleTheme}
            className={`ml-auto inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-panel/80 transition duration-150 hover:-translate-y-px hover:border-coldline hover:bg-rowhover ${
              theme === "dark" ? "text-icesoft" : "text-gold"
            }`}
            aria-label={themeToggleLabel}
            aria-pressed={theme === "dark"}
            title={themeToggleLabel}
          >
            <ThemeIcon theme={theme} />
          </button>
        </div>
      </header>

      <div className="flex min-h-[calc(100vh-3.5rem)] flex-1">
        <aside
          className={`sticky top-14 hidden h-[calc(100vh-3.5rem)] shrink-0 self-start border-r border-line bg-header/70 transition-[width] duration-200 lg:flex lg:flex-col ${
            sidebarCollapsed ? "w-[68px]" : "w-52"
          }`}
          aria-label="Sidebar"
        >
          <nav className="flex flex-1 flex-col gap-1 py-4" aria-label="Primary navigation">
            {sidebarItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={item.description}
                className={({ isActive }) =>
                  sidebarNavClass(
                    isActive || (item.to === "/" && location.pathname.startsWith("/league/")),
                    sidebarCollapsed,
                  )
                }
              >
                <NavIcon name={item.icon} />
                <SidebarLabel collapsed={sidebarCollapsed}>{item.label}</SidebarLabel>
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute left-[calc(100%+0.5rem)] top-1/2 z-50 w-56 -translate-y-1/2 rounded-md border border-coldline bg-coldpanel px-3 py-2 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-primary opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
                >
                  {item.description}
                </span>
              </NavLink>
            ))}
          </nav>

          <div className="border-t border-line p-2">
            <button
              type="button"
              onClick={toggleSidebar}
              className={`flex h-10 w-full items-center rounded-md text-[10px] font-medium uppercase tracking-[0.08em] text-secondary transition-colors duration-150 hover:bg-rowhover hover:text-primary ${
                sidebarCollapsed ? "justify-center" : "gap-3 px-2"
              }`}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!sidebarCollapsed}
              title={sidebarCollapsed ? "Expand sidebar" : undefined}
            >
              <NavIcon name={sidebarCollapsed ? "expand" : "collapse"} />
              <SidebarLabel collapsed={sidebarCollapsed}>Collapse</SidebarLabel>
            </button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <main className="mx-auto w-full max-w-[1440px] flex-1 px-3 py-5 sm:px-6 sm:py-7">
            <Outlet />
          </main>
          <footer className="border-t border-line">
            <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-2 px-3 py-4 text-[10px] text-muted sm:px-6">
              <span>ESPN Edge · local portfolio analytics</span>
              <span className="flex flex-wrap items-center gap-x-3">
                <span>
                  Current-market ADP by{" "}
                <a
                  href="https://fantasyfootballcalculator.com/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-secondary hover:text-primary"
                >
                  Fantasy Football Calculator
                </a>
                </span>
                <span>
                  NFL data via{" "}
                  <a
                    href="https://nflverse.nflverse.com/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-secondary hover:text-primary"
                  >
                    nflverse (CC BY 4.0)
                  </a>
                </span>
              </span>
            </div>
          </footer>
        </div>
      </div>
      <ScrollRestoration />
    </div>
  );
}

function SidebarLabel({ collapsed, children }: { collapsed: boolean; children: ReactNode }) {
  return (
    <span className={collapsed ? "sr-only" : "min-w-0 truncate whitespace-nowrap"}>
      {children}
    </span>
  );
}

function ThemeIcon({ theme }: { theme: ThemeMode }) {
  const shared = {
    className: "h-[18px] w-[18px]",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (theme === "dark") {
    return (
      <svg {...shared}>
        <path d="M20.4 14.3A8.5 8.5 0 0 1 9.7 3.6 8.5 8.5 0 1 0 20.4 14.3Z" />
      </svg>
    );
  }

  return (
    <svg {...shared}>
      <circle cx="12" cy="12" r="3.5" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function NavIcon({ name }: { name: IconName }) {
  const shared = {
    className: "h-[18px] w-[18px] shrink-0",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (name === "portfolio") {
    return (
      <svg {...shared}>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    );
  }

  if (name === "analytics") {
    return (
      <svg {...shared}>
        <path d="M4 20V10" />
        <path d="M9 20V4" />
        <path d="M14 20v-7" />
        <path d="M19 20V7" />
        <path d="M2.5 20.5h19" />
      </svg>
    );
  }

  if (name === "manage") {
    return (
      <svg {...shared}>
        <path d="M4 6h10" />
        <path d="M18 6h2" />
        <circle cx="16" cy="6" r="2" />
        <path d="M4 12h2" />
        <path d="M10 12h10" />
        <circle cx="8" cy="12" r="2" />
        <path d="M4 18h7" />
        <path d="M15 18h5" />
        <circle cx="13" cy="18" r="2" />
      </svg>
    );
  }

  if (name === "status") {
    return (
      <svg {...shared}>
        <path d="m12 3 9 4.5-9 4.5-9-4.5L12 3Z" />
        <path d="m3 12 9 4.5 9-4.5" />
        <path d="m3 16.5 9 4.5 9-4.5" />
      </svg>
    );
  }

  return (
    <svg {...shared}>
      {name === "collapse" ? (
        <>
          <path d="m13 6-6 6 6 6" />
          <path d="m20 6-6 6 6 6" />
        </>
      ) : (
        <>
          <path d="m11 6 6 6-6 6" />
          <path d="m4 6 6 6-6 6" />
        </>
      )}
    </svg>
  );
}
