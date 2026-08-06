export type ThemeMode = "dark" | "light";

export const THEME_STORAGE_KEY = "espn-edge.theme";

const THEME_TOKENS: Record<ThemeMode, Record<string, string>> = {
  dark: {
    "--color-page": "#070b14",
    "--color-header": "#04070d",
    "--color-panel": "#0c1120",
    "--color-row": "#0a0f1b",
    "--color-rowhover": "#111828",
    "--color-cold": "#061820",
    "--color-coldpanel": "#0a2028",
    "--color-coldline": "#17404a",
    "--color-highlight": "#202b36",
    "--color-ice": "#68a9ff",
    "--color-icesoft": "#a8d0ff",
    "--color-icechip": "#10283d",
    "--color-icebar": "#1b2c47",
    "--color-mint": "#c3f8d8",
    "--color-mintchip": "#18312d",
    "--color-ash": "#a5b0b6",
    "--color-ashbar": "#26313a",
    "--color-frost": "#eef7f4",
    "--color-line": "#1b2233",
    "--color-linedash": "#232c42",
    "--color-primary": "#e7ecf4",
    "--color-secondary": "#8b93a7",
    "--color-muted": "#5b6478",
    "--color-red": "#e5484d",
    "--color-redhover": "#f2555a",
    "--color-syncstart": "#f25b63",
    "--color-syncend": "#dba42e",
    "--color-syncink": "#080a0d",
    "--color-green": "#86efac",
    "--color-greenchip": "#12241a",
    "--color-greenbar": "#26331f",
    "--color-gold": "#d9a62e",
    "--color-grade-a": "#4ade80",
    "--color-grade-b": "#38bdf8",
    "--color-grade-c": "#fbbf24",
    "--color-grade-df": "#f87171",
    "--color-qb": "#f472b6",
    "--color-rb": "#2dd4bf",
    "--color-wr": "#60a5fa",
    "--color-te": "#fbbf24",
    "--color-dst": "#a78bfa",
    "--color-k": "#94a3b8",
  },
  light: {
    "--color-page": "#edf2f4",
    "--color-header": "#f8fafb",
    "--color-panel": "#ffffff",
    "--color-row": "#f4f7f8",
    "--color-rowhover": "#e7edef",
    "--color-cold": "#e8f1f2",
    "--color-coldpanel": "#dcebed",
    "--color-coldline": "#b7ccd0",
    "--color-highlight": "#dce5e8",
    "--color-ice": "#2563a9",
    "--color-icesoft": "#174d7a",
    "--color-icechip": "#dcecff",
    "--color-icebar": "#cbd9ea",
    "--color-mint": "#12613d",
    "--color-mintchip": "#dff4e8",
    "--color-ash": "#53646d",
    "--color-ashbar": "#d8e0e3",
    "--color-frost": "#172126",
    "--color-line": "#d4dde1",
    "--color-linedash": "#c3cfd5",
    "--color-primary": "#172126",
    "--color-secondary": "#50616c",
    "--color-muted": "#74838b",
    "--color-red": "#d73440",
    "--color-redhover": "#c12632",
    "--color-syncstart": "#f25b63",
    "--color-syncend": "#dba42e",
    "--color-syncink": "#080a0d",
    "--color-green": "#16824b",
    "--color-greenchip": "#e2f4e8",
    "--color-greenbar": "#d6e9d9",
    "--color-gold": "#946400",
    "--color-grade-a": "#16824b",
    "--color-grade-b": "#2569bb",
    "--color-grade-c": "#946400",
    "--color-grade-df": "#c9333e",
    "--color-qb": "#a9226c",
    "--color-rb": "#087d72",
    "--color-wr": "#2569bb",
    "--color-te": "#946400",
    "--color-dst": "#7047b5",
    "--color-k": "#556577",
  },
};

export function readStoredTheme(): ThemeMode {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function currentTheme(): ThemeMode {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(theme: ThemeMode): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  for (const [token, value] of Object.entries(THEME_TOKENS[theme])) {
    root.style.setProperty(token, value);
  }
}

export function storeTheme(theme: ThemeMode): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The toggle still works for this session when browser storage is unavailable.
  }
}

export function initializeTheme(): ThemeMode {
  const theme = readStoredTheme();
  applyTheme(theme);
  return theme;
}
