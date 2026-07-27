import { NavLink, Outlet } from "react-router";

function navClass({ isActive }: { isActive: boolean }): string {
  return `rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ${
    isActive ? "bg-rowhover text-primary" : "text-secondary hover:text-primary"
  }`;
}

export default function Layout() {
  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line bg-header/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-5">
          <NavLink to="/" className="text-lg font-semibold tracking-tight">
            ESPN <span className="text-red">Edge</span>
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navClass}>
              Portfolio
            </NavLink>
            <NavLink to="/manage" className={navClass}>
              Manage
            </NavLink>
            <NavLink to="/status" className={navClass}>
              Status
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-5 py-6">
        <Outlet />
      </main>
    </div>
  );
}
